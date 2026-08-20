"""Vectorised indicator implementations for the DSL registry.

Every function takes the adjusted OHLCV frame for one symbol and returns a float array
aligned to it. Two rules hold everywhere:

* **No look-ahead.** A value at index `i` may only use bars `<= i`. The two exceptions
  are `donchian_high`/`donchian_low`, which deliberately exclude the current bar so
  that "close breaks above the 20-bar high" is a real breakout rather than a tautology.
* **Warm-up is NaN, not zero.** Undefined leading values stay NaN so conditions
  evaluate to False there instead of firing on a fabricated number.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .dsl import IndicatorOperand


def _wilder(values: np.ndarray, period: int) -> np.ndarray:
    """Wilder's smoothing (an EMA with alpha = 1/period), seeded by a simple mean."""
    out = np.full(values.shape, np.nan)
    if len(values) < period:
        return out
    # Seed with the mean of the first `period` finite observations.
    seed_slice = values[:period]
    if np.isnan(seed_slice).any():
        return out
    acc = float(seed_slice.mean())
    out[period - 1] = acc
    alpha = 1.0 / period
    for i in range(period, len(values)):
        acc += alpha * (values[i] - acc)
        out[i] = acc
    return out


def _ema(series: pd.Series, span: int) -> np.ndarray:
    return series.ewm(span=span, adjust=False, min_periods=span).mean().to_numpy(dtype=float)


def _true_range(frame: pd.DataFrame) -> np.ndarray:
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    prev_close = frame["close"].shift(1).to_numpy(dtype=float)
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    tr[0] = high[0] - low[0]
    return tr


def _atr(frame: pd.DataFrame, period: int) -> np.ndarray:
    return _wilder(_true_range(frame), period)


def _rsi(frame: pd.DataFrame, period: int) -> np.ndarray:
    close = frame["close"].to_numpy(dtype=float)
    delta = np.diff(close, prepend=close[0])
    delta[0] = np.nan
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)
    gains[0] = np.nan
    losses[0] = np.nan
    # Wilder seeds from bars 1..period, so shift the smoothing off the NaN at index 0.
    avg_gain = np.full(close.shape, np.nan)
    avg_loss = np.full(close.shape, np.nan)
    avg_gain[1:] = _wilder(gains[1:], period)
    avg_loss[1:] = _wilder(losses[1:], period)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.where(avg_loss > 0, avg_gain / avg_loss, np.inf)
        rsi = 100.0 - 100.0 / (1.0 + rs)
    # All-gain windows give an infinite RS, which is RSI 100 by definition.
    rsi = np.where(np.isfinite(avg_gain) & (avg_loss == 0), 100.0, rsi)
    rsi[~np.isfinite(avg_gain)] = np.nan
    return rsi


def _bollinger(frame: pd.DataFrame, period: int, k: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    close = frame["close"]
    mid = close.rolling(period, min_periods=period).mean()
    # Population standard deviation, matching the conventional Bollinger definition.
    sd = close.rolling(period, min_periods=period).std(ddof=0)
    upper = (mid + k * sd).to_numpy(dtype=float)
    lower = (mid - k * sd).to_numpy(dtype=float)
    width = upper - lower
    with np.errstate(divide="ignore", invalid="ignore"):
        pctb = np.where(width > 0, (close.to_numpy(dtype=float) - lower) / width * 100.0, np.nan)
    return upper, lower, pctb


def compute(operand: IndicatorOperand, frame: pd.DataFrame) -> np.ndarray:
    """Evaluate one operand over one symbol's bars."""
    name = operand.name
    period = int(operand.period) if operand.period is not None else None

    if name in ("open", "high", "low", "close", "volume"):
        return frame[name].to_numpy(dtype=float)

    if name == "sma":
        return frame["close"].rolling(period, min_periods=period).mean().to_numpy(dtype=float)
    if name == "ema":
        return _ema(frame["close"], period)

    if name in ("macd", "macd_signal", "macd_hist"):
        fast, slow = int(operand.fast), int(operand.slow)
        line = _ema(frame["close"], fast) - _ema(frame["close"], slow)
        if name == "macd":
            return line
        signal = pd.Series(line, index=frame.index).ewm(
            span=int(operand.signal), adjust=False, min_periods=int(operand.signal)
        ).mean().to_numpy(dtype=float)
        return signal if name == "macd_signal" else line - signal

    if name == "donchian_high":
        # `closed="left"` excludes the current bar: this is the level to break, not a
        # level that already includes today's own high.
        return frame["high"].rolling(period, min_periods=period, closed="left").max().to_numpy(dtype=float)
    if name == "donchian_low":
        return frame["low"].rolling(period, min_periods=period, closed="left").min().to_numpy(dtype=float)

    if name == "dist_from_sma_pct":
        sma = frame["close"].rolling(period, min_periods=period).mean().to_numpy(dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            return (frame["close"].to_numpy(dtype=float) / sma - 1.0) * 100.0

    if name == "rsi":
        return _rsi(frame, period)
    if name == "roc":
        return frame["close"].pct_change(period).to_numpy(dtype=float) * 100.0

    if name == "atr":
        return _atr(frame, period)
    if name == "atr_pct":
        with np.errstate(divide="ignore", invalid="ignore"):
            return _atr(frame, period) / frame["close"].to_numpy(dtype=float) * 100.0
    if name == "stdev_pct":
        returns = frame["close"].pct_change()
        return returns.rolling(period, min_periods=period).std(ddof=1).to_numpy(dtype=float) * 100.0

    if name in ("bb_upper", "bb_lower", "bb_pctb"):
        upper, lower, pctb = _bollinger(frame, period, float(operand.k))
        return {"bb_upper": upper, "bb_lower": lower, "bb_pctb": pctb}[name]

    if name == "drawdown_pct":
        peak = frame["close"].rolling(period, min_periods=1).max().to_numpy(dtype=float)
        return (frame["close"].to_numpy(dtype=float) / peak - 1.0) * 100.0

    if name == "volume_sma":
        return frame["volume"].rolling(period, min_periods=period).mean().to_numpy(dtype=float)
    if name == "rel_volume":
        avg = frame["volume"].rolling(period, min_periods=period).mean().to_numpy(dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(avg > 0, frame["volume"].to_numpy(dtype=float) / avg, np.nan)

    raise ValueError(f"no implementation for indicator {name!r}")  # pragma: no cover
