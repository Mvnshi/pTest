"""Compile a strategy's condition groups into boolean signal arrays.

The compiled result for one symbol is two boolean arrays aligned to that symbol's bars:
`entry[i]` and `exit[i]` are the truth of the strategy's rules **as of the close of bar
`i`**. The engine acts on them one bar later.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .dsl import Comparator, ConditionGroup, ConstantOperand, IndicatorOperand, Operand, Strategy
from .indicators import compute


def _values(operand: Operand, frame: pd.DataFrame, cache: dict[str, np.ndarray]) -> np.ndarray:
    if isinstance(operand, ConstantOperand):
        return np.full(len(frame), float(operand.value))
    key = operand.cache_key
    if key not in cache:
        cache[key] = compute(operand, frame)
    return cache[key]


def _apply(left: np.ndarray, op: Comparator, right: np.ndarray) -> np.ndarray:
    """Evaluate one comparator. NaN on either side yields False, never a fired signal."""
    with np.errstate(invalid="ignore"):
        if op == "<":
            out = left < right
        elif op == "<=":
            out = left <= right
        elif op == ">":
            out = left > right
        elif op == ">=":
            out = left >= right
        elif op in ("crosses_above", "crosses_below"):
            prev_left = np.roll(left, 1)
            prev_right = np.roll(right, 1)
            prev_left[0] = np.nan
            prev_right[0] = np.nan
            if op == "crosses_above":
                out = (prev_left <= prev_right) & (left > right)
            else:
                out = (prev_left >= prev_right) & (left < right)
            # A crossing needs both bars defined.
            out = out & np.isfinite(prev_left) & np.isfinite(prev_right)
        else:  # pragma: no cover - Comparator is a closed Literal
            raise ValueError(f"unknown comparator {op!r}")
    return np.asarray(out, dtype=bool) & np.isfinite(left) & np.isfinite(right)


def evaluate_group(
    group: ConditionGroup, frame: pd.DataFrame, cache: dict[str, np.ndarray]
) -> np.ndarray:
    if not group.conditions:
        return np.zeros(len(frame), dtype=bool)
    results = [
        _apply(_values(c.left, frame, cache), c.op, _values(c.right, frame, cache))
        for c in group.conditions
    ]
    stacked = np.vstack(results)
    return stacked.all(axis=0) if group.logic == "all" else stacked.any(axis=0)


class SymbolSignals:
    """Per-symbol arrays the engine consumes. Plain numpy for loop speed."""

    __slots__ = ("symbol", "dates", "open", "high", "low", "close", "entry", "exit", "ready")

    def __init__(self, symbol: str, frame: pd.DataFrame, strategy: Strategy) -> None:
        cache: dict[str, np.ndarray] = {}
        self.symbol = symbol
        self.dates = frame.index
        self.open = frame["open"].to_numpy(dtype=float)
        self.high = frame["high"].to_numpy(dtype=float)
        self.low = frame["low"].to_numpy(dtype=float)
        self.close = frame["close"].to_numpy(dtype=float)

        entry = evaluate_group(strategy.entry, frame, cache)
        exit_ = evaluate_group(strategy.exit, frame, cache)

        # Suppress everything inside the warm-up window: indicators there are either
        # undefined or still converging, and trading them flatters the backtest.
        warmup = min(strategy.warmup_bars(), len(frame))
        ready = np.zeros(len(frame), dtype=bool)
        ready[warmup:] = True
        self.ready = ready
        self.entry = entry & ready
        self.exit = exit_ & ready


def compile_signals(strategy: Strategy, frames: dict[str, pd.DataFrame]) -> dict[str, SymbolSignals]:
    return {sym: SymbolSignals(sym, frames[sym], strategy) for sym in sorted(frames)}
