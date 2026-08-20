"""Performance statistics and breakdowns.

Every formula here is stated in docs/METHODOLOGY.md. Two conventions apply throughout:

* 252 trading days per year.
* A 0% risk-free rate, so the Sharpe ratio is `mean(daily) / std(daily) * sqrt(252)`.
  Stating it is the point: over 2006-2026 a nonzero cash rate would materially change
  the number, and a tool about honesty should not bury that.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from .config import TRADING_DAYS_PER_YEAR as PERIODS
from .engine import BacktestResult, Trade

# Daily return dispersion below this is numerical noise rather than risk. Ratios that
# divide by it are meaningless, so they are reported as zero instead.
_ZERO_VARIANCE = 1e-9


@dataclass
class Metrics:
    start: str = ""
    end: str = ""
    days: int = 0
    years: float = 0.0
    initial_equity: float = 0.0
    final_equity: float = 0.0
    total_return_pct: float = 0.0
    cagr_pct: float = 0.0
    volatility_pct: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    max_drawdown_pct: float = 0.0
    max_drawdown_days: int = 0
    calmar: float = 0.0
    exposure_pct: float = 0.0
    trades: int = 0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    expectancy_pct: float = 0.0
    avg_holding_days: float = 0.0
    total_costs: float = 0.0
    cost_drag_pct: float = 0.0     # costs as a share of gross profit, in percent
    best_day_pct: float = 0.0
    worst_day_pct: float = 0.0

    def as_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def _drawdown(equity: np.ndarray) -> tuple[np.ndarray, float, int]:
    """Underwater series, worst drawdown in percent (negative), longest underwater run."""
    if len(equity) == 0:
        return np.zeros(0), 0.0, 0
    peaks = np.maximum.accumulate(equity)
    safe = np.where(peaks > 0, peaks, 1.0)
    underwater = (equity / safe - 1.0) * 100.0
    worst = float(underwater.min()) if len(underwater) else 0.0
    longest = current = 0
    for value in underwater:
        current = current + 1 if value < -1e-9 else 0
        longest = max(longest, current)
    return underwater, worst, longest


def compute_metrics(
    dates: list[str],
    equity: np.ndarray,
    trades: list[Trade],
    exposure: np.ndarray | None = None,
) -> Metrics:
    m = Metrics()
    if len(equity) == 0:
        return m
    m.start, m.end = dates[0], dates[-1]
    m.days = len(dates)
    m.years = len(dates) / PERIODS
    m.initial_equity = float(equity[0])
    m.final_equity = float(equity[-1])
    if equity[0] > 0:
        m.total_return_pct = float(equity[-1] / equity[0] - 1.0) * 100.0
        if m.years > 0 and equity[-1] > 0:
            m.cagr_pct = float((equity[-1] / equity[0]) ** (1.0 / m.years) - 1.0) * 100.0
        elif equity[-1] <= 0:
            m.cagr_pct = -100.0

    prior = equity[:-1]
    returns = np.where(prior > 0, np.diff(equity) / np.where(prior > 0, prior, 1.0), 0.0)
    if len(returns) > 1:
        sd = float(returns.std(ddof=1))
        mean = float(returns.mean())
        m.volatility_pct = sd * np.sqrt(PERIODS) * 100.0
        # A dispersion this small is floating-point noise, not risk. Dividing by it
        # yields a Sharpe in the trillions, which is worse than reporting nothing.
        sd = sd if sd > _ZERO_VARIANCE else 0.0
        m.sharpe = (mean / sd * np.sqrt(PERIODS)) if sd > 0 else 0.0
        # Downside deviation is normalised by ALL periods, not only the losing ones -
        # the conventional definition. Dividing by the count of negative days instead
        # would understate Sortino for a strategy that is flat most of the time.
        downside = np.minimum(returns, 0.0)
        dd = float(np.sqrt((downside ** 2).mean()))
        dd = dd if dd > _ZERO_VARIANCE else 0.0
        m.sortino = (mean / dd * np.sqrt(PERIODS)) if dd > 0 else 0.0
        m.best_day_pct = float(returns.max()) * 100.0
        m.worst_day_pct = float(returns.min()) * 100.0

    _, worst, longest = _drawdown(equity)
    m.max_drawdown_pct = worst
    m.max_drawdown_days = longest
    m.calmar = (m.cagr_pct / abs(worst)) if worst < -1e-9 else 0.0
    if exposure is not None and len(exposure):
        m.exposure_pct = float(np.mean(np.clip(exposure, 0.0, None))) * 100.0

    m.trades = len(trades)
    if trades:
        pnl = np.array([t.net_pnl for t in trades])
        rets = np.array([t.return_pct for t in trades])
        wins, losses = pnl > 0, pnl < 0
        m.win_rate_pct = float(wins.mean()) * 100.0
        gross_win = float(pnl[wins].sum()) if wins.any() else 0.0
        gross_loss = float(-pnl[losses].sum()) if losses.any() else 0.0
        m.profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (
            float("inf") if gross_win > 0 else 0.0
        )
        m.avg_win_pct = float(rets[wins].mean()) if wins.any() else 0.0
        m.avg_loss_pct = float(rets[losses].mean()) if losses.any() else 0.0
        m.expectancy_pct = float(rets.mean())
        m.avg_holding_days = float(np.mean([t.holding_days for t in trades]))
        m.total_costs = float(sum(t.costs for t in trades))
        gross_total = float(sum(t.gross_pnl for t in trades))
        m.cost_drag_pct = (m.total_costs / abs(gross_total) * 100.0) if abs(gross_total) > 1e-9 else 0.0
    return m


def metrics_for(result: BacktestResult) -> Metrics:
    return compute_metrics(result.dates, result.equity, result.trades, result.exposure)


def metrics_for_slice(result: BacktestResult, lo: int, hi: int) -> Metrics:
    """Metrics over `dates[lo:hi]`, attributing each trade to its exit date."""
    if hi <= lo:
        return Metrics()
    window = set(result.dates[lo:hi])
    trades = [t for t in result.trades if t.exit_date in window]
    return compute_metrics(
        result.dates[lo:hi],
        result.equity[lo:hi],
        trades,
        result.exposure[lo:hi] if len(result.exposure) else None,
    )


# --------------------------------------------------------------------------------------
# Breakdowns
# --------------------------------------------------------------------------------------


@dataclass
class SymbolStat:
    symbol: str
    trades: int
    net_pnl: float
    pnl_share_pct: float          # share of total net profit (of the profitable total)
    win_rate_pct: float
    avg_return_pct: float
    total_return_pct: float       # sum of trade returns, a rough contribution proxy
    best_trade_pct: float
    worst_trade_pct: float


def per_symbol(trades: list[Trade], universe: list[str]) -> list[SymbolStat]:
    buckets: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        buckets[t.symbol].append(t)
    total_abs = sum(abs(t.net_pnl) for t in trades) or 1.0
    out: list[SymbolStat] = []
    for symbol in sorted(universe):
        group = buckets.get(symbol, [])
        if not group:
            out.append(SymbolStat(symbol, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0))
            continue
        pnl = np.array([t.net_pnl for t in group])
        rets = np.array([t.return_pct for t in group])
        out.append(SymbolStat(
            symbol=symbol,
            trades=len(group),
            net_pnl=round(float(pnl.sum()), 2),
            pnl_share_pct=round(float(pnl.sum()) / total_abs * 100.0, 2),
            win_rate_pct=round(float((pnl > 0).mean()) * 100.0, 2),
            avg_return_pct=round(float(rets.mean()), 4),
            total_return_pct=round(float(rets.sum()), 4),
            best_trade_pct=round(float(rets.max()), 4),
            worst_trade_pct=round(float(rets.min()), 4),
        ))
    return out


@dataclass
class PeriodStat:
    label: str
    days: int
    return_pct: float
    benchmark_return_pct: float
    max_drawdown_pct: float
    trades: int
    sharpe: float


def _period_stat(
    label: str,
    dates: list[str],
    equity: np.ndarray,
    benchmark: np.ndarray,
    trades: list[Trade],
) -> PeriodStat:
    if len(equity) < 2 or equity[0] <= 0:
        return PeriodStat(label, len(equity), 0.0, 0.0, 0.0, len(trades), 0.0)
    returns = np.diff(equity) / np.where(equity[:-1] > 0, equity[:-1], 1.0)
    sd = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    _, worst, _ = _drawdown(equity)
    bench = 0.0
    if len(benchmark) >= 2 and benchmark[0] > 0:
        bench = float(benchmark[-1] / benchmark[0] - 1.0) * 100.0
    return PeriodStat(
        label=label,
        days=len(equity),
        return_pct=round(float(equity[-1] / equity[0] - 1.0) * 100.0, 4),
        benchmark_return_pct=round(bench, 4),
        max_drawdown_pct=round(worst, 4),
        trades=len(trades),
        sharpe=round(float(returns.mean() / sd * np.sqrt(PERIODS)) if sd > 0 else 0.0, 4),
    )


def per_year(result: BacktestResult) -> list[PeriodStat]:
    if not result.dates:
        return []
    years = np.array([d[:4] for d in result.dates])
    exits: dict[str, int] = defaultdict(int)
    for t in result.trades:
        exits[t.exit_date[:4]] += 1
    out: list[PeriodStat] = []
    for year in sorted(set(years.tolist())):
        rows = np.flatnonzero(years == year)
        lo, hi = int(rows[0]), int(rows[-1]) + 1
        # Anchor on the prior close so a year's return is not measured from its own
        # first close (which would silently drop the first day of the year).
        anchor = max(lo - 1, 0)
        out.append(_period_stat(
            year,
            result.dates[anchor:hi],
            result.equity[anchor:hi],
            result.benchmark_equity[anchor:hi],
            [t for t in result.trades if t.exit_date[:4] == year],
        ))
    return out


REGIME_LABELS = ("crisis", "high_vol", "bull", "sideways")


def classify_regimes(benchmark: np.ndarray, dates: list[str]) -> np.ndarray:
    """Label each day from the benchmark alone. Descriptive, computed with hindsight."""
    n = len(benchmark)
    labels = np.array(["sideways"] * n, dtype=object)
    if n < 30:
        return labels
    series = pd.Series(benchmark)
    peak = series.cummax()
    drawdown = series / peak - 1.0
    returns = series.pct_change()
    vol = returns.rolling(20, min_periods=20).std()
    threshold = float(vol.quantile(0.8)) if vol.notna().any() else float("inf")
    sma200 = series.rolling(200, min_periods=200).mean()
    slope = sma200.diff(20)

    for i in range(n):
        if drawdown.iloc[i] <= -0.20:
            labels[i] = "crisis"
        elif pd.notna(vol.iloc[i]) and vol.iloc[i] >= threshold:
            labels[i] = "high_vol"
        elif pd.notna(slope.iloc[i]) and slope.iloc[i] > 0:
            labels[i] = "bull"
    return labels


@dataclass
class RegimeStat:
    regime: str
    days: int
    share_of_time_pct: float
    return_pct: float               # compounded strategy return while in this regime
    benchmark_return_pct: float
    hit_rate_pct: float             # share of days with a positive strategy return
    trades: int


def per_regime(result: BacktestResult) -> list[RegimeStat]:
    if len(result.equity) < 2:
        return []
    labels = classify_regimes(result.benchmark_equity, result.dates)
    strat_returns = np.diff(result.equity) / np.where(
        result.equity[:-1] > 0, result.equity[:-1], 1.0
    )
    bench_returns = np.diff(result.benchmark_equity) / np.where(
        result.benchmark_equity[:-1] > 0, result.benchmark_equity[:-1], 1.0
    )
    day_labels = labels[1:]  # a return belongs to the regime of the day it was earned
    exit_index = {d: i for i, d in enumerate(result.dates)}

    out: list[RegimeStat] = []
    total = len(day_labels)
    for regime in REGIME_LABELS:
        mask = day_labels == regime
        count = int(mask.sum())
        if count == 0:
            continue
        chained = float(np.prod(1.0 + strat_returns[mask]) - 1.0) * 100.0
        bench = float(np.prod(1.0 + bench_returns[mask]) - 1.0) * 100.0
        trades = sum(
            1 for t in result.trades
            if t.exit_date in exit_index and labels[exit_index[t.exit_date]] == regime
        )
        out.append(RegimeStat(
            regime=regime,
            days=count,
            share_of_time_pct=round(count / total * 100.0, 2),
            return_pct=round(chained, 4),
            benchmark_return_pct=round(bench, 4),
            hit_rate_pct=round(float((strat_returns[mask] > 0).mean()) * 100.0, 2),
            trades=trades,
        ))
    return out


@dataclass
class Concentration:
    """How much of the result rests on very few observations."""

    top_symbol: str = ""
    top_symbol_pnl_share_pct: float = 0.0
    top5_trades_pnl_share_pct: float = 0.0
    top10_days_return_share_pct: float = 0.0
    profitable_symbols: int = 0
    total_symbols: int = 0
    positive_years: int = 0
    total_years: int = 0
    detail: dict = field(default_factory=dict)


def concentration(result: BacktestResult, universe: list[str]) -> Concentration:
    c = Concentration(total_symbols=len(universe))
    symbol_pnl: dict[str, float] = defaultdict(float)
    for t in result.trades:
        symbol_pnl[t.symbol] += t.net_pnl
    total_profit = sum(v for v in symbol_pnl.values() if v > 0)
    c.profitable_symbols = sum(1 for v in symbol_pnl.values() if v > 0)
    if symbol_pnl:
        top = max(symbol_pnl.items(), key=lambda kv: (kv[1], kv[0]))
        c.top_symbol = top[0]
        # Measured against gross profit, so one huge winner cannot be masked by the
        # losers it is netted against.
        c.top_symbol_pnl_share_pct = round(
            (top[1] / total_profit * 100.0) if total_profit > 0 else 0.0, 2
        )
    if result.trades:
        gains = sorted((t.net_pnl for t in result.trades if t.net_pnl > 0), reverse=True)
        gross = sum(gains)
        c.top5_trades_pnl_share_pct = round(
            (sum(gains[:5]) / gross * 100.0) if gross > 0 else 0.0, 2
        )
    if len(result.equity) > 1:
        returns = np.diff(result.equity) / np.where(
            result.equity[:-1] > 0, result.equity[:-1], 1.0
        )
        positive = np.sort(returns[returns > 0])[::-1]
        total_up = float(np.prod(1.0 + positive) - 1.0)
        top10 = float(np.prod(1.0 + positive[:10]) - 1.0)
        c.top10_days_return_share_pct = round(
            (top10 / total_up * 100.0) if total_up > 1e-9 else 0.0, 2
        )
    years = per_year(result)
    c.total_years = len(years)
    c.positive_years = sum(1 for y in years if y.return_pct > 0)
    c.detail = {"symbol_pnl": {k: round(v, 2) for k, v in sorted(symbol_pnl.items())}}
    return c
