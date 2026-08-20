"""The deterministic backtest engine.

Design commitments, in the order they matter:

1. **Signal on close, fill on next open.** Signals are read from bar `t`; the resulting
   orders sit in a pending queue that is only drained at bar `t + 1`'s open. Same-bar
   execution is structurally impossible here, not merely avoided by convention.
2. **Costs move the fill price.** Slippage is applied to the price the trade actually
   gets; commission is charged separately. Per trade we report gross P&L on the
   unslipped prices and net P&L after both, so the drag is visible rather than implied.
3. **Determinism.** No wall-clock, no RNG, no reliance on set or dict ordering. When
   more entry candidates fire than there are free slots, they are taken in alphabetical
   order by symbol - arbitrary, but fixed and documented.
4. **Conservative intrabar assumptions.** When a stop and a target are both reachable
   within one bar, the stop is assumed to fill first. A gap through a stop fills at the
   open, not at the stop level. A trailing stop trails the peak established *before*
   the current bar, so a new high made later in the day cannot retroactively protect
   the position.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .data import BarStore
from .dsl import BacktestConfig, Strategy
from .signals import compile_signals

# "signal" | "stop_loss" | "take_profit" | "trailing_stop" | "time_exit"
# | "data_end" | "end_of_backtest"
ExitReason = str


@dataclass
class Trade:
    symbol: str
    direction: str
    entry_date: str
    exit_date: str
    entry_price: float          # fill price, slippage included
    exit_price: float
    shares: float
    notional: float             # entry notional at the fill price
    gross_pnl: float            # P&L on unslipped prices, before any cost
    costs: float                # commission + slippage, both legs
    net_pnl: float              # what the account actually gained or lost
    return_pct: float           # net, on the entry notional
    holding_days: int           # trading days held
    exit_reason: ExitReason
    mae_pct: float              # worst adverse excursion while held (<= 0)
    mfe_pct: float              # best favourable excursion while held (>= 0)

    def as_dict(self) -> dict:
        # Explicit float()/int() casts: numpy scalars leak in through the price arrays
        # and np.int64 / np.bool_ are not JSON serialisable.
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_date": self.entry_date,
            "exit_date": self.exit_date,
            "entry_price": float(round(self.entry_price, 4)),
            "exit_price": float(round(self.exit_price, 4)),
            "shares": float(round(self.shares, 4)),
            "notional": float(round(self.notional, 2)),
            "gross_pnl": float(round(self.gross_pnl, 2)),
            "costs": float(round(self.costs, 2)),
            "net_pnl": float(round(self.net_pnl, 2)),
            "return_pct": float(round(self.return_pct, 4)),
            "holding_days": int(self.holding_days),
            "exit_reason": self.exit_reason,
            "mae_pct": float(round(self.mae_pct, 4)),
            "mfe_pct": float(round(self.mfe_pct, 4)),
        }


@dataclass
class _OpenPosition:
    symbol: str
    col: int
    shares: float               # positive long, negative short
    entry_price: float          # slipped fill
    entry_raw: float            # unslipped open, used for gross P&L
    entry_index: int
    entry_date: str
    notional: float
    entry_commission: float
    peak: float                 # highest high seen since entry
    trough: float               # lowest low seen since entry
    stop_price: float | None
    target_price: float | None


@dataclass
class BacktestResult:
    dates: list[str]
    equity: np.ndarray
    cash: np.ndarray
    exposure: np.ndarray
    position_count: np.ndarray
    benchmark_equity: np.ndarray
    trades: list[Trade] = field(default_factory=list)
    initial_capital: float = 0.0
    benchmark_symbol: str = ""
    skipped_signals: int = 0        # entry signals dropped because every slot was full
    unaffordable_signals: int = 0   # entries that could not buy one whole share
    ruined: bool = False            # equity reached zero; the account stopped trading
    ruin_date: str = ""

    @property
    def n_days(self) -> int:
        return len(self.dates)

    def daily_returns(self) -> np.ndarray:
        if len(self.equity) < 2:
            return np.zeros(0)
        prior = self.equity[:-1]
        with np.errstate(divide="ignore", invalid="ignore"):
            out = np.where(prior > 0, np.diff(self.equity) / np.where(prior > 0, prior, 1.0), 0.0)
        return out


class _Panel:
    """Dense arrays aligned to one shared calendar. NaN marks 'no bar for this symbol'."""

    def __init__(self, store: BarStore, strategy: Strategy, dates: pd.DatetimeIndex) -> None:
        self.symbols = sorted(strategy.universe)
        n, m = len(dates), len(self.symbols)
        self.dates = dates
        self.open = np.full((n, m), np.nan)
        self.high = np.full((n, m), np.nan)
        self.low = np.full((n, m), np.nan)
        self.close = np.full((n, m), np.nan)
        self.entry = np.zeros((n, m), dtype=bool)
        self.exit = np.zeros((n, m), dtype=bool)

        # Signals are computed over each symbol's FULL history and only then sliced to
        # the window, so a backtest starting in 2012 warms its indicators on 2006-2011
        # data instead of throwing away the first year of the window.
        frames = {s: store.bars(s) for s in self.symbols}
        signals = compile_signals(strategy, frames)
        for col, symbol in enumerate(self.symbols):
            sig = signals[symbol]
            located = dates.get_indexer(sig.dates)
            keep = located >= 0
            rows = located[keep]
            self.open[rows, col] = sig.open[keep]
            self.high[rows, col] = sig.high[keep]
            self.low[rows, col] = sig.low[keep]
            self.close[rows, col] = sig.close[keep]
            self.entry[rows, col] = sig.entry[keep]
            self.exit[rows, col] = sig.exit[keep]

        # Forward-filled close, used only to mark an open position on a day where its
        # symbol has no bar. Never used to generate a signal or a fill.
        self.mark = pd.DataFrame(self.close).ffill().to_numpy()
        # Last row on which each symbol has a real bar, so a position in a symbol whose
        # data ends mid-backtest is closed rather than silently carried.
        #
        # A symbol that merely runs past the window edge has NOT ended: treating it as
        # ended would use knowledge of where the window stops to manufacture an exit,
        # and would give the same calendar day a different outcome in a walk-forward
        # fold than in the full run.
        self.last_bar = np.full(m, -1, dtype=int)
        window_end = dates[-1]
        for col, symbol in enumerate(self.symbols):
            valid = np.flatnonzero(np.isfinite(self.close[:, col]))
            if not len(valid):
                continue
            history_end = frames[symbol].index[-1]
            self.last_bar[col] = n - 1 if history_end > window_end else int(valid[-1])


def _resolve_window(store: BarStore, strategy: Strategy, config: BacktestConfig) -> pd.DatetimeIndex:
    calendar = store.calendar(strategy.universe)
    if config.start is not None:
        calendar = calendar[calendar >= pd.Timestamp(config.start)]
    if config.end is not None:
        calendar = calendar[calendar <= pd.Timestamp(config.end)]
    return calendar


def _benchmark_curve(
    store: BarStore, symbol: str, dates: pd.DatetimeIndex, capital: float, cost_rate: float
) -> np.ndarray:
    """Buy and hold the benchmark, charged one entry cost so the comparison is not free."""
    if len(dates) == 0:
        return np.zeros(0)
    if not store.has(symbol):
        return np.full(len(dates), capital)
    close = store.bars(symbol).reindex(dates)["close"].ffill().to_numpy(dtype=float)
    valid = np.flatnonzero(np.isfinite(close))
    if len(valid) == 0:
        return np.full(len(dates), capital)
    start = int(valid[0])
    curve = np.full(len(dates), capital)
    curve[start:] = capital * (1.0 - cost_rate) * close[start:] / close[start]
    return curve


def run_backtest(
    strategy: Strategy,
    config: BacktestConfig,
    store: BarStore,
    *,
    cost_multiple: float = 1.0,
) -> BacktestResult:
    """Run one backtest. A pure function of (strategy, config, snapshot, cost_multiple)."""
    dates = _resolve_window(store, strategy, config)
    commission = config.commission_bps / 10_000.0 * cost_multiple
    slippage = config.slippage_bps / 10_000.0 * cost_multiple

    if len(dates) == 0:
        empty = np.zeros(0)
        return BacktestResult(
            dates=[], equity=empty, cash=empty, exposure=empty, position_count=empty,
            benchmark_equity=empty, initial_capital=config.initial_capital,
            benchmark_symbol=config.benchmark,
        )

    panel = _Panel(store, strategy, dates)
    n_days, n_symbols = len(dates), len(panel.symbols)
    date_strings = [d.strftime("%Y-%m-%d") for d in dates]

    is_long = strategy.position.direction == "long"
    sign = 1.0 if is_long else -1.0
    max_positions = strategy.position.max_positions
    risk = strategy.risk

    cash = float(config.initial_capital)
    positions: dict[int, _OpenPosition] = {}
    trades: list[Trade] = []
    equity_curve = np.zeros(n_days)
    cash_curve = np.zeros(n_days)
    exposure_curve = np.zeros(n_days)
    count_curve = np.zeros(n_days)
    pending_exits: dict[int, ExitReason] = {}   # col -> reason, filled at the next open
    pending_entries: list[int] = []
    skipped_signals = 0
    unaffordable_signals = 0
    ruined = False
    ruin_date = ""

    def close_position(pos: _OpenPosition, index: int, raw_price: float, reason: ExitReason) -> None:
        """Exit at `raw_price` before slippage, book the trade, release the slot."""
        nonlocal cash
        # Exiting a long means selling; exiting a short means buying back.
        fill = raw_price * (1.0 - slippage) if is_long else raw_price * (1.0 + slippage)
        # The price the trade actually got is by definition inside its own excursion
        # range. Without this a signal exit - which fills on a bar the trackers never
        # see - can report a 1% adverse excursion on a trade that lost 50%.
        pos.peak = max(pos.peak, fill)
        pos.trough = min(pos.trough, fill)
        quantity = abs(pos.shares)
        exit_commission = quantity * fill * commission
        cash += pos.shares * fill - exit_commission

        gross = (raw_price - pos.entry_raw) * pos.shares
        net = (fill - pos.entry_price) * pos.shares - pos.entry_commission - exit_commission
        best = pos.peak if is_long else pos.trough
        worst = pos.trough if is_long else pos.peak
        trades.append(Trade(
            symbol=pos.symbol,
            direction=strategy.position.direction,
            entry_date=pos.entry_date,
            exit_date=date_strings[index],
            entry_price=pos.entry_price,
            exit_price=fill,
            shares=quantity,
            notional=pos.notional,
            gross_pnl=gross,
            costs=gross - net,
            net_pnl=net,
            return_pct=net / pos.notional * 100.0 if pos.notional else 0.0,
            holding_days=index - pos.entry_index,
            exit_reason=reason,
            mae_pct=min((worst - pos.entry_price) / pos.entry_price * 100.0 * sign, 0.0),
            mfe_pct=max((best - pos.entry_price) / pos.entry_price * 100.0 * sign, 0.0),
        ))
        positions.pop(pos.col, None)

    for di in range(n_days):
        opens, highs, lows, closes = panel.open[di], panel.high[di], panel.low[di], panel.close[di]
        marks = panel.mark[di]
        # Marks available at the OPEN: yesterday's closes. Sizing an order that fills at
        # today's open against today's close would let the size depend on price action
        # that has not happened yet - the same-bar leak this engine exists to avoid.
        prior_marks = panel.mark[di - 1] if di > 0 else np.zeros(n_symbols)

        # -- 1. queued exits fill at today's open ----------------------------------
        for col in sorted(pending_exits):
            pos = positions.get(col)
            if pos is None:
                pending_exits.pop(col, None)
                continue
            raw = opens[col]
            if not math.isfinite(raw):
                continue  # no bar today; the order stays queued for the next one
            close_position(pos, di, raw, pending_exits.pop(col))

        # -- 2. queued entries fill at today's open --------------------------------
        if pending_entries:
            held = np.zeros(n_symbols)
            for col, pos in positions.items():
                held[col] = pos.shares
            equity_now = cash + float(np.dot(held, np.nan_to_num(prior_marks, nan=0.0)))
            slot_notional = max(equity_now, 0.0) / max_positions
            for col in pending_entries:
                if len(positions) >= max_positions or col in positions:
                    skipped_signals += 1
                    continue
                raw = opens[col]
                if not math.isfinite(raw) or raw <= 0:
                    continue
                fill = raw * (1.0 + slippage) if is_long else raw * (1.0 - slippage)
                shares = math.floor(slot_notional / fill)
                if is_long:
                    # A long is cash constrained; a short generates cash instead.
                    affordable = math.floor(cash / (fill * (1.0 + commission)))
                    shares = min(shares, affordable)
                if shares <= 0:
                    # The slot cannot buy one whole share. Silently dropping this would
                    # make the universe quietly narrower than the user asked for.
                    unaffordable_signals += 1
                    continue
                notional = shares * fill
                entry_commission = notional * commission
                signed = shares * sign
                cash -= signed * fill + entry_commission
                positions[col] = _OpenPosition(
                    symbol=panel.symbols[col],
                    col=col,
                    shares=signed,
                    entry_price=fill,
                    entry_raw=raw,
                    entry_index=di,
                    entry_date=date_strings[di],
                    notional=notional,
                    entry_commission=entry_commission,
                    peak=fill,
                    trough=fill,
                    stop_price=None if risk.stop_loss_pct is None else (
                        fill * (1 - risk.stop_loss_pct / 100.0) if is_long
                        else fill * (1 + risk.stop_loss_pct / 100.0)
                    ),
                    target_price=None if risk.take_profit_pct is None else (
                        fill * (1 + risk.take_profit_pct / 100.0) if is_long
                        else fill * (1 - risk.take_profit_pct / 100.0)
                    ),
                )
            pending_entries = []

        # -- 3. intrabar risk exits against today's range --------------------------
        for col in sorted(positions):
            pos = positions[col]
            low, high, op = lows[col], highs[col], opens[col]
            if not (math.isfinite(low) and math.isfinite(high)):
                continue

            # The trail references the peak from BEFORE this bar.
            trail_level = None
            if risk.trailing_stop_pct is not None:
                ref = pos.peak if is_long else pos.trough
                trail_level = (
                    ref * (1 - risk.trailing_stop_pct / 100.0) if is_long
                    else ref * (1 + risk.trailing_stop_pct / 100.0)
                )
            adverse = low if is_long else high
            favourable = high if is_long else low
            levels = [(lvl, tag) for lvl, tag in
                      ((pos.stop_price, "stop_loss"), (trail_level, "trailing_stop"))
                      if lvl is not None]
            stop_level, stop_tag = (None, "stop_loss")
            if levels:
                stop_level, stop_tag = max(levels) if is_long else min(levels)

            hit_stop = stop_level is not None and (
                adverse <= stop_level if is_long else adverse >= stop_level
            )
            hit_target = pos.target_price is not None and (
                favourable >= pos.target_price if is_long else favourable <= pos.target_price
            )

            if hit_stop:
                # A gap through the stop fills at the open, not at the stop level.
                gapped = math.isfinite(op) and ((op < stop_level) if is_long else (op > stop_level))
                close_position(pos, di, op if gapped else stop_level, stop_tag)
                continue
            if hit_target:
                gapped = math.isfinite(op) and (
                    (op > pos.target_price) if is_long else (op < pos.target_price)
                )
                close_position(pos, di, op if gapped else pos.target_price, "take_profit")
                continue

            # The position survived the bar, so its whole range is a real excursion.
            # (On a bar that closes the position, close_position folds in the fill
            # instead: we assumed the trade ended there, so what the price did
            # afterwards was not something the position lived through.)
            pos.peak = max(pos.peak, high)
            pos.trough = min(pos.trough, low)

        # -- 4. mark to market at today's close ------------------------------------
        holdings_value = 0.0
        gross_exposure = 0.0
        for col in sorted(positions):
            pos = positions[col]
            price = marks[col] if math.isfinite(marks[col]) else pos.entry_price
            holdings_value += pos.shares * price
            gross_exposure += abs(pos.shares) * price
        equity = cash + holdings_value
        equity_curve[di] = equity
        cash_curve[di] = cash
        exposure_curve[di] = (gross_exposure / equity) if equity > 0 else 0.0
        count_curve[di] = len(positions)

        # An unlevered long book cannot get here, but an unconstrained short can. Once
        # equity is gone the account is gone: liquidate, freeze the curve and stop.
        # Carrying on would compound from a negative base and quietly report every
        # later day as a flat 0% return.
        if equity <= 0:
            for col in sorted(positions):
                pos = positions[col]
                price = closes[col] if math.isfinite(closes[col]) else marks[col]
                if math.isfinite(price):
                    close_position(pos, di, price, "account_ruined")
            ruined = True
            ruin_date = date_strings[di]
            equity_curve[di:] = equity
            cash_curve[di:] = cash
            exposure_curve[di:] = 0.0
            count_curve[di:] = 0.0
            break

        if di == n_days - 1:
            break

        # -- 5. read signals at today's close, queue orders for tomorrow -----------
        for col in sorted(positions):
            if col in pending_exits:
                continue
            pos = positions[col]
            if di >= panel.last_bar[col]:
                # This symbol's data ends here; close at the final close rather than
                # carrying a position that can never be traded out.
                if math.isfinite(closes[col]):
                    close_position(pos, di, closes[col], "data_end")
                continue
            if panel.exit[di, col]:
                pending_exits[col] = "signal"
            elif risk.max_holding_days is not None and (di - pos.entry_index) >= risk.max_holding_days:
                pending_exits[col] = "time_exit"

        free_slots = max_positions - len(positions) + len(pending_exits)
        candidates = [
            int(col) for col in np.flatnonzero(panel.entry[di])
            if col not in positions and col not in pending_exits
        ]
        if free_slots > 0:
            pending_entries = candidates[:free_slots]     # columns are alphabetical
            skipped_signals += max(0, len(candidates) - free_slots)
        else:
            pending_entries = []
            skipped_signals += len(candidates)

    # -- liquidate anything still open on the final bar ----------------------------
    if positions and not ruined:
        last = n_days - 1
        for col in sorted(positions):
            pos = positions[col]
            price = panel.close[last, col]
            if not math.isfinite(price):
                price = panel.mark[last, col]
            if math.isfinite(price):
                close_position(pos, last, price, "end_of_backtest")
        equity_curve[last] = cash
        cash_curve[last] = cash
        exposure_curve[last] = 0.0
        count_curve[last] = 0.0

    return BacktestResult(
        dates=date_strings,
        equity=equity_curve,
        cash=cash_curve,
        exposure=exposure_curve,
        position_count=count_curve,
        benchmark_equity=_benchmark_curve(
            store, config.benchmark, dates, config.initial_capital, commission + slippage
        ),
        trades=trades,
        initial_capital=float(config.initial_capital),
        benchmark_symbol=config.benchmark,
        skipped_signals=skipped_signals,
        unaffordable_signals=unaffordable_signals,
        ruined=ruined,
        ruin_date=ruin_date,
    )
