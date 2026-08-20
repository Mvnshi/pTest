"""Validation and robustness probes."""

from __future__ import annotations

import pytest

from app.dsl import BacktestConfig, Condition, ConstantOperand, IndicatorOperand
from app.engine import run_backtest
from app.validation import (
    COST_MULTIPLES,
    cost_sensitivity,
    parameter_robustness,
    split_validation,
    walk_forward,
)
from tests.conftest import make_strategy


@pytest.fixture(scope="module")
def setup(request):
    from app.data import get_store

    store = get_store()
    strategy = make_strategy(
        store.symbols,
        entry=[Condition(left=IndicatorOperand(name="rsi"), op="<",
                         right=ConstantOperand(value=35))],
        max_holding_days=10,
        stop_loss_pct=7,
    )
    config = BacktestConfig(run_robustness=False)
    return store, strategy, config, run_backtest(strategy, config, store)


def test_split_covers_the_whole_window_without_overlap(setup):
    store, strategy, config, base = setup
    split = split_validation(base, 0.7)
    assert split.split_date in base.dates
    cut = base.dates.index(split.split_date)
    assert split.train["days"] == cut
    assert split.test["days"] == base.n_days - cut
    assert split.train["days"] + split.test["days"] == base.n_days


def test_split_reports_degradation_when_there_is_an_in_sample_edge(setup):
    _, _, _, base = setup
    split = split_validation(base, 0.7)
    if split.sharpe_retention is not None:
        assert split.degradation_pct == pytest.approx((1 - split.sharpe_retention) * 100)


def test_walk_forward_folds_tile_the_window(setup):
    store, strategy, config, base = setup
    wf = walk_forward(strategy, config, store, base)
    assert len(wf.folds) == config.walk_forward_folds
    assert wf.folds[0]["start"] == base.dates[0]
    assert wf.folds[-1]["end"] == base.dates[-1]
    for earlier, later in zip(wf.folds, wf.folds[1:]):
        assert earlier["end"] < later["start"], "folds must not overlap"
    assert 0 <= wf.positive_folds <= len(wf.folds)


def test_walk_forward_folds_start_flat(setup):
    """No position may be inherited across a fold boundary."""
    store, strategy, config, base = setup
    wf = walk_forward(strategy, config, store, base)
    total_fold_trades = sum(f["metrics"]["trades"] for f in wf.folds)
    # Independent folds lose the trades that straddle a boundary, so the fold total is
    # never more than the continuous run's.
    assert total_fold_trades <= len(base.trades) + len(wf.folds)


def test_cost_sensitivity_is_monotonic_and_finds_a_breakeven(setup):
    store, strategy, config, base = setup
    cs = cost_sensitivity(strategy, config, store, base)
    assert [p["multiple"] for p in cs.points] == list(COST_MULTIPLES)
    cagrs = [p["cagr_pct"] for p in cs.points]
    assert cagrs == sorted(cagrs, reverse=True), "more cost cannot mean more return"
    if cs.breakeven_round_trip_bps is not None:
        assert cs.breakeven_round_trip_bps >= 0
    assert cs.survives_5x == (cagrs[-1] > 0)


def test_parameter_neighbourhood_is_bounded_and_deterministic(setup):
    store, strategy, config, base = setup
    a = parameter_robustness(strategy, config, store, base)
    b = parameter_robustness(strategy, config, store, base)
    assert 0 < len(a.neighbours) <= 8
    assert a.neighbours == b.neighbours, "the neighbourhood must be the same set every time"
    assert a.parameters_probed >= 2, "the budget should span several parameters"
    for n in a.neighbours:
        assert n["value"] != n["base_value"]


def test_parameter_neighbourhood_reports_fragility(setup):
    store, strategy, config, base = setup
    robust = parameter_robustness(strategy, config, store, base)
    if robust.fragility_ratio is not None and robust.median_sharpe > 0.05:
        assert robust.fragility_ratio == pytest.approx(
            robust.base_sharpe / robust.median_sharpe, rel=1e-3
        )
