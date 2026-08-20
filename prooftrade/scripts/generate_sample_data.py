#!/usr/bin/env python3
"""Generate a deterministic synthetic daily-bar snapshot for the ProofTrade demo.

`scripts/fetch_data.py` vendors real Yahoo Finance bars. This script is the
fully offline fallback for machines with no outbound network: it writes exactly
the same CSV contract (``data/bars/<SYMBOL>.csv`` plus ``manifest.json``) from a
synthetic price process.

The process is deliberately structural rather than a plain random walk, because
every analytic layered on top of these bars (drawdown, regime attribution,
factor exposure, turnover) would look fake otherwise:

  * one shared market factor with a GARCH(1,1) variance recursion, so
    volatility clusters instead of being i.i.d.;
  * a three-state regime chain (bull / chop / crisis) modulating drift and vol,
    with crisis episodes deep and long enough to be real drawdowns;
  * per-symbol beta to the market factor plus clustered idiosyncratic noise;
  * overnight gaps, volatility-scaled intrabar wicks, and volume that reacts to
    the size of the day's move.

These bars are synthetic. They are not market data and not a forecast. A
backtest run against them is a smoke test of the engine, never evidence about a
strategy.

Standard library only; no numpy, no pandas. The same --seed always produces
byte-identical CSVs.

Usage:
    python scripts/generate_sample_data.py [--out data/bars] [--seed 20260630]
                                           [--start 2006-01-03] [--end 2026-06-30]
                                           [--force]
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
import random
import statistics
import sys
from typing import Iterable, NamedTuple

# --------------------------------------------------------------------------
# Snapshot contract
# --------------------------------------------------------------------------

SNAPSHOT_START = "2006-01-03"
SNAPSHOT_END = "2026-06-30"
DEFAULT_SEED = 20260630

FIELDNAMES = ["date", "open", "high", "low", "close", "adj_close", "volume"]
TRADING_DAYS_PER_YEAR = 252
PRICE_DP = 6


class SymbolSpec(NamedTuple):
    """Static description of one synthetic instrument."""

    symbol: str
    beta: float          # loading on the shared market factor
    drift: float         # annual log drift *in excess of* beta * market drift
    idio_vol: float      # annualised idiosyncratic volatility
    start_price: float
    base_volume: float   # median daily share volume
    div_yield: float     # annual accretion used to build adj_close


# Betas are the structural inputs: index ETFs ~1.0, TLT negative (flight to
# quality), GLD ~0, sector ETFs 0.8-1.3, single names 0.9-1.6. Idiosyncratic
# vol is set so total volatility lands where each instrument's really does; the
# broad index ETFs barely have any, since they *are* close to the factor.
# Drift and start_price are then calibrated against the default seed so the
# default snapshot ends on plausible price levels. Another seed keeps the same
# risk structure but will land somewhere else, exactly as a different twenty
# years of history would.
UNIVERSE: tuple[SymbolSpec, ...] = (
    # Broad market / index ETFs
    SymbolSpec("SPY", 1.00, 0.0057, 0.02, 127.0, 80_000_000, 0.017),
    SymbolSpec("QQQ", 1.12, 0.0745, 0.0746, 45.0, 45_000_000, 0.006),
    SymbolSpec("IWM", 1.18, 0.0013, 0.0571, 68.0, 30_000_000, 0.014),
    SymbolSpec("DIA", 0.94, 0.027, 0.02, 109.0, 4_000_000, 0.019),
    # Cross-asset ETFs (regime diversity: bonds, gold)
    SymbolSpec("TLT", -0.30, -0.0214, 0.1292, 91.0, 15_000_000, 0.032),
    SymbolSpec("GLD", 0.05, 0.0842, 0.1597, 51.0, 9_000_000, 0.004),
    # Sector ETFs
    SymbolSpec("XLE", 1.05, -0.0181, 0.1636, 51.0, 15_000_000, 0.033),
    SymbolSpec("XLF", 1.28, 0.0075, 0.1204, 30.0, 40_000_000, 0.019),
    SymbolSpec("XLK", 1.18, 0.072, 0.0571, 21.0, 8_000_000, 0.009),
    SymbolSpec("XLV", 0.80, 0.0407, 0.0572, 30.0, 9_000_000, 0.015),
    # Mega-cap single names
    SymbolSpec("AAPL", 1.20, 0.0742, 0.2081, 3.40, 70_000_000, 0.006),
    SymbolSpec("MSFT", 1.05, 0.078, 0.1928, 21.0, 28_000_000, 0.009),
    SymbolSpec("NVDA", 1.60, 0.1111, 0.3457, 0.50, 120_000_000, 0.001),
    SymbolSpec("AMZN", 1.30, 0.1014, 0.2326, 3.80, 45_000_000, 0.0005),
    SymbolSpec("JPM", 1.35, -0.0392, 0.1582, 33.0, 12_000_000, 0.026),
    SymbolSpec("XOM", 0.95, -0.0418, 0.1823, 43.0, 18_000_000, 0.038),
    SymbolSpec("KO", 0.90, -0.033, 0.0514, 30.0, 14_000_000, 0.031),
    SymbolSpec("JNJ", 0.92, -0.0069, 0.02, 47.0, 8_000_000, 0.028),
)

# --------------------------------------------------------------------------
# Process parameters
# --------------------------------------------------------------------------

TARGET_ANNUAL_VOL = 0.1582
GARCH_ALPHA = 0.07
GARCH_BETA = 0.88

BULL, CHOP, CRISIS = 0, 1, 2
REGIME_NAMES = {BULL: "bull", CHOP: "chop", CRISIS: "crisis"}

BULL_ANNUAL_DRIFT = 0.16
CHOP_ANNUAL_DRIFT = 0.00
BULL_VOL_MULT = 0.85
CHOP_VOL_MULT = 1.15
CRISIS_VOL_MULT = 2.20

# Per-day probability of switching between the two calm states.
BULL_TO_CHOP = 0.0035
CHOP_TO_BULL = 0.0140
# Per-day probability of falling into a crisis, by originating calm state.
CRISIS_ENTRY = {BULL: 0.0005, CHOP: 0.0035}
CRISIS_DEPTH_RANGE = (0.28, 0.44)      # target peak-to-trough decline before noise
CRISIS_LENGTH_RANGE = (60, 145)        # trading days, i.e. 3-7 months
CRISIS_COOLDOWN = 1000                 # crises stay a full recovery apart
MAX_CALM_DAYS = 1500                   # forced crisis after ~6 calm years

GAP_FRACTION = 0.35      # share of the day's move that happens overnight
WICK_SCALE = 0.45        # wick size relative to that day's volatility
MIN_WICK = 0.0004        # keeps every bar from printing a zero range
VOLUME_LOG_SIGMA = 0.30
VOLUME_MOVE_BETA = 3.2   # volume response to |return|
DIV_ACCRUAL_DAYS = 45    # accrued-but-unpaid dividend, keeps adj_close < close

MIN_DEEP_DRAWDOWNS = 2
DEEP_DRAWDOWN = 0.30


# --------------------------------------------------------------------------
# Trading calendar
# --------------------------------------------------------------------------


def easter_sunday(year: int) -> dt.date:
    """Easter Sunday via the anonymous Gregorian algorithm.

    Needed only to place Good Friday (Easter minus two days), the one moveable
    US market holiday. The integer sequence below is the standard computus: it
    reconciles the 19-year Metonic lunar cycle (`a`) with the Gregorian century
    corrections (`b`..`g`) to get the paschal full moon (`h`), then walks
    forward to the following Sunday (`l`).
    """
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    lunar = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lunar) // 451
    month, day = divmod(h + lunar - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> dt.date:
    """The nth (1-based) `weekday` of a month, Monday==0."""
    first = dt.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + dt.timedelta(days=offset + 7 * (nth - 1))


def _last_weekday(year: int, month: int, weekday: int) -> dt.date:
    """The last `weekday` of a month, Monday==0."""
    if month == 12:
        last = dt.date(year, 12, 31)
    else:
        last = dt.date(year, month + 1, 1) - dt.timedelta(days=1)
    return last - dt.timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: dt.date) -> dt.date:
    """NYSE weekend observance: Saturday shifts back, Sunday shifts forward."""
    if day.weekday() == 5:
        return day - dt.timedelta(days=1)
    if day.weekday() == 6:
        return day + dt.timedelta(days=1)
    return day


def us_market_holidays(year: int) -> set[dt.date]:
    """US equity market closures for a calendar year.

    Ad-hoc closures (hurricanes, state funerals) are not modelled; the demo
    only needs a calendar that is stable, plausible and shared by all symbols.
    """
    holidays: set[dt.date] = set()

    # New Year's Day: a Saturday 1 Jan is *not* observed on 31 Dec, because the
    # exchange never gives up the last trading day of the year.
    new_year = dt.date(year, 1, 1)
    if new_year.weekday() == 6:
        holidays.add(new_year + dt.timedelta(days=1))
    elif new_year.weekday() != 5:
        holidays.add(new_year)

    holidays.add(_nth_weekday(year, 1, 0, 3))            # MLK Day
    holidays.add(_nth_weekday(year, 2, 0, 3))            # Presidents' Day
    holidays.add(easter_sunday(year) - dt.timedelta(days=2))  # Good Friday
    holidays.add(_last_weekday(year, 5, 0))              # Memorial Day
    if year >= 2022:
        holidays.add(_observed(dt.date(year, 6, 19)))    # Juneteenth
    holidays.add(_observed(dt.date(year, 7, 4)))         # Independence Day
    holidays.add(_nth_weekday(year, 9, 0, 1))            # Labor Day
    holidays.add(_nth_weekday(year, 11, 3, 4))           # Thanksgiving
    holidays.add(_observed(dt.date(year, 12, 25)))       # Christmas
    return holidays


def trading_days(start: dt.date, end: dt.date) -> list[dt.date]:
    """Every US equity trading day in [start, end], ascending."""
    holidays: set[dt.date] = set()
    for year in range(start.year, end.year + 1):
        holidays |= us_market_holidays(year)

    days: list[dt.date] = []
    day = start
    step = dt.timedelta(days=1)
    while day <= end:
        if day.weekday() < 5 and day not in holidays:
            days.append(day)
        day += step
    return days


# --------------------------------------------------------------------------
# Regime chain and market factor
# --------------------------------------------------------------------------


class Regime(NamedTuple):
    state: int
    drift: float      # daily log drift
    vol_mult: float


BULL_REGIME = Regime(BULL, BULL_ANNUAL_DRIFT / TRADING_DAYS_PER_YEAR, BULL_VOL_MULT)
CHOP_REGIME = Regime(CHOP, CHOP_ANNUAL_DRIFT / TRADING_DAYS_PER_YEAR, CHOP_VOL_MULT)


def generate_regime_path(rng: random.Random, n_days: int) -> list[Regime]:
    """Three-state chain over `n_days`, emitting crises as whole episodes.

    Bull and chop follow an ordinary Markov step. A crisis is instead drawn as
    an episode with its own target depth and duration, so the drift while it
    lasts is `log(1 - depth) / duration` and the realised drawdown is that
    target plus GARCH noise.

    The chain is deliberately time-inhomogeneous in one place: the crisis
    hazard goes to 1 after MAX_CALM_DAYS of calm. A snapshot whose crises all
    landed outside the window would make the drawdown and regime analytics look
    dishonestly benign, so every snapshot is guaranteed to contain several.
    """
    path: list[Regime] = []
    state = BULL
    cooldown = 0   # trading days remaining before another crisis may start
    calm = 0       # trading days since the last crisis ended

    while len(path) < n_days:
        entry_p = 0.0 if cooldown > 0 else CRISIS_ENTRY[state]
        forced = cooldown == 0 and calm >= MAX_CALM_DAYS
        draw = rng.random()

        if forced or draw < entry_p:
            depth = rng.uniform(*CRISIS_DEPTH_RANGE)
            length = rng.randint(*CRISIS_LENGTH_RANGE)
            drift = math.log(1.0 - depth) / length
            for _ in range(min(length, n_days - len(path))):
                path.append(Regime(CRISIS, drift, CRISIS_VOL_MULT))
            state, cooldown, calm = CHOP, CRISIS_COOLDOWN, 0
            continue

        # `draw` already cleared the crisis mass, so reuse it for the calm step.
        if state == BULL:
            state = CHOP if draw < entry_p + BULL_TO_CHOP else BULL
        else:
            state = BULL if draw < entry_p + CHOP_TO_BULL else CHOP
        path.append(BULL_REGIME if state == BULL else CHOP_REGIME)
        cooldown = max(0, cooldown - 1)
        calm += 1

    return path


def simulate_market(rng: random.Random, regimes: list[Regime]) -> tuple[list[float], list[float]]:
    """Market-factor log returns and their conditional daily volatility.

    Variance follows a GARCH(1,1) recursion

        var_t = omega + alpha * eps_{t-1}^2 + beta * var_{t-1}

    with omega pinned so the unconditional variance equals the daily variance
    implied by TARGET_ANNUAL_VOL. alpha + beta = 0.95 gives clusters with a
    two-week half-life without the near-unit-root tail that would make the
    realised sample volatility of a single path wander far from the target. The recursion is fed the *base*
    innovation, not the regime-scaled one: regimes scale the volatility that is
    realised, but must not feed back into the variance state or a crisis would
    ratchet the process permanently higher.
    """
    var_target = (TARGET_ANNUAL_VOL ** 2) / TRADING_DAYS_PER_YEAR
    omega = var_target * (1.0 - GARCH_ALPHA - GARCH_BETA)

    var = var_target
    returns: list[float] = []
    sigmas: list[float] = []
    for regime in regimes:
        base_sigma = math.sqrt(var)
        eps = base_sigma * rng.gauss(0.0, 1.0)
        returns.append(regime.drift + eps * regime.vol_mult)
        sigmas.append(base_sigma * regime.vol_mult)
        var = omega + GARCH_ALPHA * eps * eps + GARCH_BETA * var
    return returns, sigmas


def symbol_rng(seed: int, symbol: str) -> random.Random:
    """Per-symbol RNG derived from (seed, symbol) so ordering never matters.

    Python's built-in hash() is salted per process, so the digest has to come
    from hashlib for cross-run determinism.
    """
    digest = hashlib.sha256(f"{seed}:{symbol}".encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


def simulate_symbol(
    spec: SymbolSpec,
    rng: random.Random,
    market_returns: list[float],
    market_sigmas: list[float],
) -> tuple[list[float], list[float]]:
    """Per-symbol log returns plus the conditional daily vol of each bar."""
    base_sigma = TARGET_ANNUAL_VOL / math.sqrt(TRADING_DAYS_PER_YEAR)
    idio_sigma = spec.idio_vol / math.sqrt(TRADING_DAYS_PER_YEAR)
    daily_drift = spec.drift / TRADING_DAYS_PER_YEAR

    returns: list[float] = []
    sigmas: list[float] = []
    for r_mkt, sigma_mkt in zip(market_returns, market_sigmas):
        # Idiosyncratic vol breathes with the market's vol state, so single
        # names get noisier in crises instead of only their beta component.
        idio_today = idio_sigma * (sigma_mkt / base_sigma)
        returns.append(daily_drift + spec.beta * r_mkt + idio_today * rng.gauss(0.0, 1.0))
        sigmas.append(math.hypot(spec.beta * sigma_mkt, idio_today))
    return returns, sigmas


# --------------------------------------------------------------------------
# Bar construction
# --------------------------------------------------------------------------


def build_bars(
    spec: SymbolSpec,
    dates: list[dt.date],
    returns: list[float],
    sigmas: list[float],
    rng: random.Random,
    end_date: dt.date,
) -> list[dict]:
    """Turn a return path into OHLCV bars that satisfy the CSV contract."""
    bars: list[dict] = []
    prev_close = spec.start_price

    for day, ret, sigma in zip(dates, returns, sigmas):
        # Part of the day's move happens overnight, so the open is not the
        # prior close; the rest is the intraday body.
        gap = GAP_FRACTION * sigma * rng.gauss(0.0, 1.0)
        open_px = prev_close * math.exp(gap)
        close_px = prev_close * math.exp(ret)

        # Wicks are strictly non-negative extensions beyond the open/close
        # envelope, which is what makes low <= min(o,c) <= max(o,c) <= high
        # true by construction rather than by clamping afterwards.
        body_hi = max(open_px, close_px)
        body_lo = min(open_px, close_px)
        up_wick = MIN_WICK + WICK_SCALE * sigma * abs(rng.gauss(0.0, 1.0))
        dn_wick = MIN_WICK + WICK_SCALE * sigma * abs(rng.gauss(0.0, 1.0))
        high_px = body_hi * math.exp(up_wick)
        low_px = body_lo * math.exp(-dn_wick)

        # Volume is log-normal around the symbol's base and leans on the size
        # of the move; the exponent is clamped so one tail draw cannot print an
        # absurd share count.
        move_term = VOLUME_MOVE_BETA * (abs(ret) - 0.8 * sigma)
        exponent = VOLUME_LOG_SIGMA * rng.gauss(0.0, 1.0) + max(-0.6, min(2.0, move_term))
        volume = int(spec.base_volume * math.exp(exponent))

        # adj_close discounts the dividends still to be paid before the end of
        # the snapshot, plus one accrual period so it stays strictly below the
        # raw close on every bar, including the last one.
        years_left = ((end_date - day).days + DIV_ACCRUAL_DAYS) / 365.25
        adj_close = close_px * math.exp(-spec.div_yield * years_left)

        bar = {
            "date": day.isoformat(),
            "open": round(open_px, PRICE_DP),
            "high": round(high_px, PRICE_DP),
            "low": round(low_px, PRICE_DP),
            "close": round(close_px, PRICE_DP),
            "adj_close": round(adj_close, PRICE_DP),
            "volume": max(1000, volume),
        }
        # Rounding can only move a price by 5e-7, but the contract is absolute,
        # so re-establish the ordering on the rounded values.
        bar["high"] = max(bar["high"], bar["open"], bar["close"])
        bar["low"] = min(bar["low"], bar["open"], bar["close"])
        bars.append(bar)
        prev_close = close_px

    return bars


# --------------------------------------------------------------------------
# Statistics and validation
# --------------------------------------------------------------------------


def drawdown_episodes(levels: Iterable[float]) -> list[float]:
    """Depth of every completed peak-to-trough episode, deepest first.

    An episode runs from a running-maximum high to the trough before the next
    new high; the still-open episode at the end of the series counts too.
    """
    peak = float("-inf")
    trough = float("inf")
    depths: list[float] = []
    for level in levels:
        if level > peak:
            if peak > 0 and trough < peak:
                depths.append(1.0 - trough / peak)
            peak = level
            trough = level
        elif level < trough:
            trough = level
    if peak > 0 and trough < peak:
        depths.append(1.0 - trough / peak)
    return sorted(depths, reverse=True)


def crisis_spans(regimes: list[Regime]) -> list[tuple[int, int]]:
    """Inclusive [start, end] index of every contiguous crisis regime block."""
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for i, regime in enumerate(regimes):
        if regime.state == CRISIS and start is None:
            start = i
        elif regime.state != CRISIS and start is not None:
            spans.append((start, i - 1))
            start = None
    if start is not None:
        spans.append((start, len(regimes) - 1))
    return spans


def crisis_drawdowns(levels: list[float], spans: list[tuple[int, int]], pad: int = 20) -> list[float]:
    """Peak-to-trough decline attributable to each crisis episode.

    Measured against the local high just before the crisis rather than the
    all-time high, so two crises that happen to share one unrecovered peak are
    still counted as the two separate events they are.
    """
    depths: list[float] = []
    for start, end in spans:
        peak = max(levels[max(0, start - pad):start + 1])
        trough = min(levels[start:min(len(levels), end + pad) + 1])
        depths.append(1.0 - trough / peak)
    return depths


def max_drawdown(levels: list[float]) -> float:
    depths = drawdown_episodes(levels)
    return depths[0] if depths else 0.0


def annualised_vol(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    return statistics.pstdev(returns) * math.sqrt(TRADING_DAYS_PER_YEAR)


def validate_bars(symbol: str, bars: list[dict]) -> None:
    """Fail loudly before anything is written to disk."""
    if not bars:
        raise ValueError(f"{symbol}: produced zero bars")

    prev_date = ""
    for i, bar in enumerate(bars):
        if bar["date"] <= prev_date:
            raise ValueError(
                f"{symbol}: dates not strictly ascending at row {i}: "
                f"{prev_date!r} then {bar['date']!r}"
            )
        prev_date = bar["date"]

        o, h, l, c, a = bar["open"], bar["high"], bar["low"], bar["close"], bar["adj_close"]
        if min(o, h, l, c, a) <= 0:
            raise ValueError(f"{symbol}: non-positive price on {bar['date']}: {bar}")
        if not (l <= min(o, c) and max(o, c) <= h):
            raise ValueError(f"{symbol}: OHLC ordering violated on {bar['date']}: {bar}")
        if bar["volume"] <= 0:
            raise ValueError(f"{symbol}: non-positive volume on {bar['date']}: {bar}")


def validate_market(crisis_depths: list[float]) -> None:
    """The snapshot is useless for drawdown analytics without real crises."""
    deep = [d for d in crisis_depths if d > DEEP_DRAWDOWN]
    if len(deep) < MIN_DEEP_DRAWDOWNS:
        raise ValueError(
            f"market factor produced only {len(deep)} crisis drawdown(s) deeper "
            f"than {DEEP_DRAWDOWN:.0%}, need {MIN_DEEP_DRAWDOWNS}. Observed: "
            + ", ".join(f"{d:.1%}" for d in sorted(crisis_depths, reverse=True))
            + ". Re-run with a different --seed or widen CRISIS_DEPTH_RANGE."
        )


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def write_symbol_csv(out_dir: str, symbol: str, bars: list[dict]) -> None:
    path = os.path.join(out_dir, f"{symbol}.csv")
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(bars)


def write_manifest(out_dir: str, start: str, end: str, seed: int, entries: list[dict]) -> None:
    with open(os.path.join(out_dir, "manifest.json"), "w") as fh:
        json.dump(
            {
                "snapshot_start": start,
                "snapshot_end": end,
                "source": (
                    "Synthetic offline generator "
                    f"(scripts/generate_sample_data.py, seed={seed}) - NOT market data"
                ),
                "symbols": entries,
            },
            fh,
            indent=2,
        )
        fh.write("\n")


def existing_csvs(out_dir: str) -> list[str]:
    if not os.path.isdir(out_dir):
        return []
    return sorted(n for n in os.listdir(out_dir) if n.endswith(".csv"))


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    default_out = os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "bars")
    )
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=default_out, help="output directory for CSVs")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED, help="master RNG seed")
    ap.add_argument("--start", default=SNAPSHOT_START, help="first snapshot date, YYYY-MM-DD")
    ap.add_argument("--end", default=SNAPSHOT_END, help="last snapshot date, YYYY-MM-DD")
    ap.add_argument(
        "--force", action="store_true", help="overwrite an output directory that already has CSVs"
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out_dir = os.path.abspath(args.out)
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    if start >= end:
        print(f"error: --start {start} must precede --end {end}", file=sys.stderr)
        return 2

    present = existing_csvs(out_dir)
    if present and not args.force:
        print(
            f"refusing to overwrite {out_dir}: {len(present)} CSV file(s) already present "
            f"({', '.join(present[:4])}{'...' if len(present) > 4 else ''}).\n"
            "This looks like a real vendored data snapshot from scripts/fetch_data.py, and "
            "synthetic bars must never silently replace it.\n"
            "Re-run with --force if you really want synthetic sample data here.",
            file=sys.stderr,
        )
        return 1

    dates = trading_days(start, end)
    print(
        f"ProofTrade synthetic bars | seed={args.seed} | {len(dates)} trading days "
        f"{dates[0]} -> {dates[-1]}"
    )

    try:
        return _generate(args, out_dir, dates)
    except ValueError as exc:
        # Every self-check raises ValueError; surface it as a CLI error rather
        # than a traceback, and leave the output directory untouched.
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _generate(args: argparse.Namespace, out_dir: str, dates: list[dt.date]) -> int:
    """Simulate, self-validate, then write. Raises ValueError on any bad data."""
    end = dates[-1]
    market_rng = random.Random(
        int(hashlib.sha256(f"{args.seed}:__market__".encode("utf-8")).hexdigest()[:16], 16)
    )
    regimes = generate_regime_path(market_rng, len(dates))
    market_returns, market_sigmas = simulate_market(market_rng, regimes)

    level = 1.0
    market_levels = []
    for r in market_returns:
        level *= math.exp(r)
        market_levels.append(level)
    spans = crisis_spans(regimes)
    crisis_depths = crisis_drawdowns(market_levels, spans)
    validate_market(crisis_depths)
    episode_depths = drawdown_episodes(market_levels)

    occupancy = {name: 0 for name in REGIME_NAMES.values()}
    for regime in regimes:
        occupancy[REGIME_NAMES[regime.state]] += 1
    print(
        "  regimes: "
        + "  ".join(f"{k} {v / len(regimes):.0%}" for k, v in occupancy.items())
        + f"  | {len(spans)} crises, depth "
        + ", ".join(f"{d:.1%}" for d in crisis_depths)
        + f"  | worst market drawdown {max(episode_depths, default=0.0):.1%}"
    )
    print()
    print(f"  {'sym':<5} {'bars':>5}  {'start':<10} {'end':<10} {'return':>10} {'vol':>7} {'maxdd':>7}")

    # Generate and validate everything in memory first: a half-written data
    # directory is worse than no data directory.
    generated: list[tuple[SymbolSpec, list[dict]]] = []
    for spec in UNIVERSE:
        rng = symbol_rng(args.seed, spec.symbol)
        returns, sigmas = simulate_symbol(spec, rng, market_returns, market_sigmas)
        bars = build_bars(spec, dates, returns, sigmas, rng, end)
        validate_bars(spec.symbol, bars)
        generated.append((spec, bars))

        closes = [bar["close"] for bar in bars]
        total_return = closes[-1] / spec.start_price - 1.0
        print(
            f"  {spec.symbol:<5} {len(bars):>5}  {bars[0]['date']:<10} {bars[-1]['date']:<10} "
            f"{total_return:>9.1%} {annualised_vol(returns):>7.1%} {max_drawdown(closes):>7.1%}"
        )

    os.makedirs(out_dir, exist_ok=True)
    entries: list[dict] = []
    for spec, bars in generated:
        write_symbol_csv(out_dir, spec.symbol, bars)
        entries.append(
            {
                "symbol": spec.symbol,
                "bars": len(bars),
                "start": bars[0]["date"],
                "end": bars[-1]["date"],
            }
        )
    write_manifest(out_dir, dates[0].isoformat(), dates[-1].isoformat(), args.seed, entries)

    print(f"\nWrote {len(entries)} synthetic symbols to {out_dir}")
    print("These bars are synthetic. Do not present any result from them as a real backtest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
