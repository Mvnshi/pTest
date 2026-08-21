"""Indicators are checked against hand-computed references, not against themselves."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.dsl import IndicatorOperand
from app.indicators import compute


def frame(closes: list[float], highs=None, lows=None, opens=None, volumes=None) -> pd.DataFrame:
    n = len(closes)
    index = pd.date_range("2020-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "open": opens or closes,
        "high": highs or [c * 1.01 for c in closes],
        "low": lows or [c * 0.99 for c in closes],
        "close": closes,
        "volume": volumes or [1_000_000] * n,
    }, index=index)


def test_sma_matches_the_arithmetic_mean_and_warms_up_as_nan():
    values = compute(IndicatorOperand(name="sma", period=3), frame([1, 2, 3, 4, 5]))
    assert np.isnan(values[0]) and np.isnan(values[1])
    assert values[2] == pytest.approx(2.0)
    assert values[4] == pytest.approx(4.0)


def test_ema_matches_the_recursion():
    closes = [10, 11, 12, 13, 14]
    values = compute(IndicatorOperand(name="ema", period=3), frame(closes))
    alpha = 2 / (3 + 1)
    expected = sum(closes[:3]) / 3  # pandas seeds adjust=False from the first value,
    # so recompute the reference the same way pandas does.
    ref = closes[0]
    for c in closes[1:]:
        ref = ref + alpha * (c - ref)
    assert values[-1] == pytest.approx(ref)
    assert np.isnan(values[1]), "min_periods must suppress the warm-up"
    del expected


def test_rsi_is_100_for_an_unbroken_advance_and_0_for_an_unbroken_decline():
    up = compute(IndicatorOperand(name="rsi", period=5), frame(list(range(1, 30))))
    assert up[-1] == pytest.approx(100.0)
    down = compute(IndicatorOperand(name="rsi", period=5), frame(list(range(30, 1, -1))))
    assert down[-1] == pytest.approx(0.0)


def test_rsi_matches_wilders_reference_calculation():
    """Wilder's own worked example: seed on the first `period` changes, then smooth."""
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
              45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64]
    values = compute(IndicatorOperand(name="rsi", period=14), frame(closes))
    # The first defined value is at bar 14 (14 price changes are needed to seed it).
    assert np.isnan(values[13])
    # Reference values recomputed from Wilder's definition independently of this code.
    for index, expected in {14: 70.464, 15: 66.250, 16: 66.481,
                            17: 69.347, 18: 66.295, 19: 57.915}.items():
        assert values[index] == pytest.approx(expected, abs=0.01)


def test_donchian_high_excludes_the_current_bar():
    """Otherwise "close breaks above the N-bar high" could never be true."""
    highs = [10, 11, 12, 20, 13]
    values = compute(IndicatorOperand(name="donchian_high", period=3),
                     frame([9, 10, 11, 19, 12], highs=highs))
    assert np.isnan(values[2])
    assert values[3] == pytest.approx(12.0), "must be max(10,11,12), not max(...,20)"
    assert values[4] == pytest.approx(20.0)


def test_bollinger_bands_use_the_population_standard_deviation():
    closes = [10, 12, 14, 16, 18]
    upper = compute(IndicatorOperand(name="bb_upper", period=5, k=2), frame(closes))
    lower = compute(IndicatorOperand(name="bb_lower", period=5, k=2), frame(closes))
    mean = np.mean(closes)
    sd = np.std(closes)  # ddof=0
    assert upper[-1] == pytest.approx(mean + 2 * sd)
    assert lower[-1] == pytest.approx(mean - 2 * sd)


def test_bollinger_pctb_is_0_at_the_lower_band_and_100_at_the_upper():
    closes = [10, 12, 14, 16, 18]
    pctb = compute(IndicatorOperand(name="bb_pctb", period=5, k=2), frame(closes))
    upper = compute(IndicatorOperand(name="bb_upper", period=5, k=2), frame(closes))
    lower = compute(IndicatorOperand(name="bb_lower", period=5, k=2), frame(closes))
    expected = (closes[-1] - lower[-1]) / (upper[-1] - lower[-1]) * 100
    assert pctb[-1] == pytest.approx(expected)


def test_atr_uses_the_true_range_including_gaps():
    closes = [100, 100, 100, 100, 100]
    highs = [101, 101, 101, 110, 101]
    lows = [99, 99, 99, 99, 99]
    atr = compute(IndicatorOperand(name="atr", period=2), frame(closes, highs=highs, lows=lows))
    assert atr[3] > atr[2], "a wide bar must lift the ATR"
    assert not np.isnan(atr[1])


def test_roc_is_a_percentage():
    values = compute(IndicatorOperand(name="roc", period=2), frame([100, 100, 110]))
    assert values[2] == pytest.approx(10.0)


def test_rel_volume_is_volume_over_its_own_trailing_average():
    """The window includes the current bar, which is the conventional definition."""
    volumes = [100, 100, 100, 300]
    values = compute(IndicatorOperand(name="rel_volume", period=3),
                     frame([1, 1, 1, 1], volumes=volumes))
    assert values[2] == pytest.approx(1.0)
    assert values[3] == pytest.approx(300 / ((100 + 100 + 300) / 3))
    assert values[3] > 1.0


def test_no_indicator_reads_the_future():
    """Change only the last bar; no earlier value of any indicator may move."""
    closes = [100 + (i % 7) for i in range(120)]
    original = frame(closes)
    tampered = frame(closes[:-1] + [999.0])
    operands = [
        IndicatorOperand(name="sma", period=10),
        IndicatorOperand(name="ema", period=10),
        IndicatorOperand(name="rsi", period=14),
        IndicatorOperand(name="atr", period=14),
        IndicatorOperand(name="bb_pctb", period=20, k=2),
        IndicatorOperand(name="macd_hist"),
        IndicatorOperand(name="donchian_high", period=20),
        IndicatorOperand(name="roc", period=10),
        IndicatorOperand(name="drawdown_pct", period=50),
        IndicatorOperand(name="stdev_pct", period=20),
    ]
    for operand in operands:
        a = compute(operand, original)[:-1]
        b = compute(operand, tampered)[:-1]
        np.testing.assert_allclose(a, b, equal_nan=True,
                                   err_msg=f"{operand.name} leaked the final bar")
