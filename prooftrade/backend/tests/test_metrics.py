"""Metrics are checked against closed-form cases where the answer is known exactly."""

from __future__ import annotations

import numpy as np
import pytest

from app.config import TRADING_DAYS_PER_YEAR as PERIODS
from app.metrics import _drawdown, compute_metrics


def dates(n: int) -> list[str]:
    import datetime as dt

    day = dt.date(2020, 1, 1)
    out = []
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += dt.timedelta(days=1)
    return out


def test_cagr_of_a_doubling_over_exactly_one_year():
    equity = np.linspace(100.0, 200.0, PERIODS)
    m = compute_metrics(dates(PERIODS), equity, [])
    assert m.total_return_pct == pytest.approx(100.0)
    assert m.cagr_pct == pytest.approx(100.0, rel=0.01)


def test_a_flat_curve_has_no_return_no_volatility_and_no_drawdown():
    equity = np.full(500, 100.0)
    m = compute_metrics(dates(500), equity, [])
    assert m.total_return_pct == 0
    assert m.volatility_pct == 0
    assert m.sharpe == 0
    assert m.max_drawdown_pct == 0


def test_a_zero_variance_curve_reports_no_sharpe_rather_than_a_huge_one():
    """A constant 1% a day has (to floating-point noise) no dispersion. Dividing by
    that noise would print a Sharpe in the trillions; it must report 0 instead."""
    equity = 100.0 * (1.01 ** np.arange(300))
    m = compute_metrics(dates(300), equity, [])
    assert m.sharpe == 0.0
    assert m.sortino == 0.0
    assert m.total_return_pct > 0


def test_sharpe_matches_the_manual_calculation():
    rng = np.random.default_rng(42)  # test-local only; the engine itself has no RNG
    returns = rng.normal(0.0004, 0.01, 1000)
    equity = 100.0 * np.cumprod(1 + returns)
    m = compute_metrics(dates(1000), equity, [])
    realised = np.diff(equity) / equity[:-1]
    expected = realised.mean() / realised.std(ddof=1) * np.sqrt(PERIODS)
    assert m.sharpe == pytest.approx(expected)


def test_max_drawdown_is_measured_from_the_running_peak():
    equity = np.array([100.0, 120.0, 60.0, 90.0, 200.0])
    underwater, worst, longest = _drawdown(equity)
    assert worst == pytest.approx(-50.0)
    assert underwater[2] == pytest.approx(-50.0)
    assert underwater[4] == pytest.approx(0.0)
    assert longest == 2


def test_sortino_ignores_upside_volatility():
    up_only = np.array([100.0, 101.0, 103.0, 106.0, 110.0])
    m = compute_metrics(dates(5), up_only, [])
    assert m.sortino == 0.0, "no downside days means no downside deviation"

    mixed = 100.0 * np.cumprod(1 + np.array([0.02, -0.01, 0.02, -0.01] * 50))
    mm = compute_metrics(dates(200), mixed, [])
    assert mm.sortino > mm.sharpe, "penalising only downside must not be harsher"


def test_trade_statistics():
    from app.engine import Trade

    def trade(net: float, ret: float, days: int = 5) -> Trade:
        return Trade(
            symbol="X", direction="long", entry_date="2020-01-01", exit_date="2020-01-08",
            entry_price=100, exit_price=100 + net, shares=1, notional=100,
            gross_pnl=net + 1, costs=1.0, net_pnl=net, return_pct=ret,
            holding_days=days, exit_reason="signal", mae_pct=-1, mfe_pct=1,
        )

    trades = [trade(10, 10), trade(20, 20), trade(-5, -5), trade(-15, -15)]
    m = compute_metrics(dates(100), np.full(100, 100.0), trades)
    assert m.trades == 4
    assert m.win_rate_pct == pytest.approx(50.0)
    assert m.profit_factor == pytest.approx(30 / 20)
    assert m.avg_win_pct == pytest.approx(15.0)
    assert m.avg_loss_pct == pytest.approx(-10.0)
    assert m.expectancy_pct == pytest.approx(2.5)
    assert m.total_costs == pytest.approx(4.0)


def test_empty_input_does_not_explode():
    m = compute_metrics([], np.zeros(0), [])
    assert m.trades == 0 and m.sharpe == 0
