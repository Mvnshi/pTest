"""The evidence score and the warnings that must always be evaluated."""

from __future__ import annotations

import pytest

from app.evidence import build_evidence, ramp
from app.metrics import Concentration, Metrics
from app.validation import CostSensitivity, ParameterRobustness, SplitValidation, WalkForward


def evidence(**overrides):
    defaults = dict(
        metrics=Metrics(trades=200, cagr_pct=12.0, sharpe=1.0, exposure_pct=60.0,
                        max_drawdown_pct=-15.0, profit_factor=1.6, days=5000),
        conc=Concentration(top_symbol="SPY", top_symbol_pnl_share_pct=15.0,
                           top5_trades_pnl_share_pct=20.0, top10_days_return_share_pct=30.0,
                           profitable_symbols=14, total_symbols=18,
                           positive_years=15, total_years=20),
        split=SplitValidation(split_date="2019-01-02", train={"sharpe": 1.0},
                              test={"sharpe": 0.9}, sharpe_retention=0.9,
                              degradation_pct=10.0),
        wf=WalkForward(folds=[{"metrics": {}} for _ in range(5)], positive_folds=4,
                       total_folds=5, median_sharpe=0.8),
        costs=CostSensitivity(base_sharpe=1.0, sharpe_at_3x=0.85,
                              sharpe_retained_at_3x=0.85, survives_5x=True,
                              breakeven_round_trip_bps=400.0,
                              points=[{"multiple": m} for m in (0, 1, 2, 3, 5)]),
        robust=ParameterRobustness(neighbours=[{"sharpe": 0.9}] * 8, base_sharpe=1.0,
                                   median_sharpe=0.95, profitable_share_pct=100.0,
                                   fragility_ratio=1.05, parameters_probed=4),
        regimes=[{"regime": r, "return_pct": 5.0, "trades": 20}
                 for r in ("crisis", "high_vol", "bull", "sideways")],
        years_covered=20,
        benchmark=Metrics(cagr_pct=9.0),
        skipped_signals=0,
        is_synthetic=False,
    )
    defaults.update(overrides)
    return build_evidence(**defaults)


def warning_ids(ev) -> set[str]:
    return {w["id"] for w in ev.warnings}


def test_a_clean_result_scores_well_and_carries_only_structural_caveats():
    ev = evidence()
    assert ev.score > 80
    assert ev.grade == "A"
    assert warning_ids(ev) == {"universe_hindsight", "single_data_source"}


def test_the_components_sum_to_the_score():
    ev = evidence()
    assert sum(c["points"] for c in ev.components) == pytest.approx(ev.score, abs=0.05)
    assert {c["weight"] for c in ev.components} == {20.0, 15.0}
    assert sum(c["weight"] for c in ev.components) == 100.0


def test_low_trade_count_always_fires_below_thirty():
    assert "low_trade_count" in warning_ids(evidence(
        metrics=Metrics(trades=12, cagr_pct=20.0, sharpe=2.0, exposure_pct=50.0)))
    assert "low_trade_count" not in warning_ids(evidence())


def test_concentrated_returns_fires_for_a_dominant_symbol_and_for_a_few_trades():
    by_symbol = evidence(conc=Concentration(
        top_symbol="NVDA", top_symbol_pnl_share_pct=71.0, top5_trades_pnl_share_pct=20.0,
        profitable_symbols=6, total_symbols=18, positive_years=10, total_years=20))
    assert "concentrated_returns_symbol" in warning_ids(by_symbol)

    by_trade = evidence(conc=Concentration(
        top_symbol="SPY", top_symbol_pnl_share_pct=20.0, top5_trades_pnl_share_pct=65.0,
        profitable_symbols=12, total_symbols=18, positive_years=12, total_years=20))
    assert "concentrated_returns_trades" in warning_ids(by_trade)


def test_cost_sensitivity_fires_when_the_edge_dies_at_three_times_costs():
    ev = evidence(costs=CostSensitivity(
        base_sharpe=1.0, sharpe_at_3x=0.2, sharpe_retained_at_3x=0.2, survives_5x=False,
        breakeven_round_trip_bps=18.0, points=[{"multiple": m} for m in (0, 1, 2, 3, 5)]))
    ids = warning_ids(ev)
    assert "cost_sensitive" in ids
    assert "thin_cost_margin" in ids


def test_out_of_sample_degradation_fires():
    ev = evidence(split=SplitValidation(split_date="2019-01-02", sharpe_retention=0.3,
                                        degradation_pct=70.0))
    assert "oos_degradation" in warning_ids(ev)


def test_fragile_parameters_fire_when_neighbours_are_much_worse():
    ev = evidence(robust=ParameterRobustness(
        neighbours=[{"sharpe": 0.2}] * 8, base_sharpe=1.4, median_sharpe=0.2,
        profitable_share_pct=40.0, fragility_ratio=7.0, parameters_probed=4))
    assert "fragile_parameters" in warning_ids(ev)


def test_a_narrow_universe_is_flagged_and_caps_the_breadth_score():
    ev = evidence(conc=Concentration(
        top_symbol="NVDA", top_symbol_pnl_share_pct=45.0, top5_trades_pnl_share_pct=30.0,
        profitable_symbols=2, total_symbols=2, positive_years=15, total_years=20))
    assert "narrow_universe" in warning_ids(ev)
    breadth = next(c for c in ev.components if c["key"] == "breadth")
    assert breadth["score"] < 0.2, "two symbols cannot demonstrate breadth"


def test_underperforming_the_benchmark_is_always_stated():
    ev = evidence(metrics=Metrics(trades=200, cagr_pct=2.0, sharpe=0.9, exposure_pct=40.0),
                  benchmark=Metrics(cagr_pct=9.0))
    assert "underperforms_benchmark" in warning_ids(ev)


def test_a_binding_position_cap_is_disclosed():
    assert "position_cap_binding" in warning_ids(evidence(skipped_signals=500))


def test_synthetic_data_is_flagged_as_critical():
    ev = evidence(is_synthetic=True)
    warning = next(w for w in ev.warnings if w["id"] == "synthetic_data")
    assert warning["severity"] == "critical"
    assert ev.warnings[0]["severity"] == "critical", "critical warnings sort first"


def test_out_of_sample_beating_in_sample_wildly_is_scored_as_instability():
    ev = evidence(split=SplitValidation(split_date="2019-01-02", sharpe_retention=5.4,
                                        degradation_pct=-440.0))
    component = next(c for c in ev.components if c["key"] == "oos_consistency")
    assert component["score"] < 0.8, "disagreement between halves is not a bonus"


def test_the_headline_refuses_to_promise_returns():
    ev = evidence()
    assert "not how much money" in ev.headline


def test_ramp_is_clamped_in_both_directions():
    assert ramp(5, 0, 10) == pytest.approx(0.5)
    assert ramp(-5, 0, 10) == 0.0
    assert ramp(50, 0, 10) == 1.0
    assert ramp(25, 70, 25) == 1.0, "an inverted ramp works too"
    assert ramp(70, 70, 25) == 0.0
