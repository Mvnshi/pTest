"""The evidence score and the warning engine.

This is the part of ProofTrade that argues with the user.

Two things it is not:

* It is not a prediction. A high score means the *evidence* is trustworthy, not that
  the strategy will make money. A strategy that reliably loses money can score well.
* It is not a black box. Every component reports its raw measurement, the thresholds it
  was scored against, and the points it contributed, so a sceptic can rebuild the total
  by hand.

Sub-scores are piecewise-linear ramps between a "clearly bad" knee and a "clearly fine"
knee. Ramps rather than step functions, so a strategy one trade short of a threshold is
not punished as if it were empty.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

from .metrics import Concentration, Metrics
from .validation import CostSensitivity, ParameterRobustness, SplitValidation, WalkForward

Severity = Literal["critical", "high", "medium", "info"]


def ramp(value: float, bad: float, good: float) -> float:
    """Linear 0..1 ramp. Works in either direction depending on bad vs good."""
    if bad == good:
        return 1.0 if value >= good else 0.0
    t = (value - bad) / (good - bad)
    return max(0.0, min(1.0, t))


@dataclass
class ScoreComponent:
    key: str
    label: str
    weight: float
    score: float                 # 0..1
    points: float                # score * weight
    measurement: str             # human-readable value that drove the score
    detail: str                  # what it means and how it was scored


@dataclass
class Warning_:
    id: str
    severity: Severity
    title: str
    message: str
    measurement: str
    threshold: str


@dataclass
class Evidence:
    score: float = 0.0
    grade: str = "F"
    headline: str = ""
    components: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


GRADE_BANDS = ((80.0, "A"), (65.0, "B"), (50.0, "C"), (35.0, "D"), (0.0, "F"))


def _grade(score: float) -> str:
    for floor, letter in GRADE_BANDS:
        if score >= floor:
            return letter
    return "F"  # pragma: no cover - the last band is a catch-all


def _sample_size(metrics: Metrics) -> ScoreComponent:
    # Log-scaled, because the tenth trade tells you far more than the hundredth. Zero at
    # 10 trades or fewer, full marks at 400 - the point where the standard error of a
    # Sharpe estimate stops dominating everything else about the result.
    import math

    if metrics.trades <= 10:
        score = 0.0
    else:
        score = min(1.0, math.log10(metrics.trades / 10.0) / math.log10(40.0))
    return ScoreComponent(
        key="sample_size",
        label="Sample size",
        weight=20.0,
        score=score,
        points=score * 20.0,
        measurement=f"{metrics.trades} closed trades",
        detail="Log-scaled: zero at 10 trades or fewer, full marks at 400. Below ~30 "
               "trades almost any performance figure is within the range of luck.",
    )


def _oos_consistency(split: SplitValidation, wf: WalkForward) -> ScoreComponent:
    retention = split.sharpe_retention
    if retention is None:
        score = 0.35
        measurement = "not measurable (no in-sample edge)"
        detail = ("The in-sample Sharpe is at or below zero, so there is nothing for the "
                  "out-of-sample half to retain. Scored as inconclusive, not as a pass.")
    elif retention >= 2.0:
        # Out-of-sample beating in-sample several times over is not a bonus. It means
        # the two halves do not look like the same process, which is exactly the
        # instability this component exists to detect.
        score = 0.6
        measurement = (f"out-of-sample Sharpe is {retention:.1f}x in-sample - the two "
                       "halves behave differently")
        detail = ("Wild disagreement between the halves is scored as instability, not as "
                  "strength: whichever half is unrepresentative, one of them is.")
    else:
        # Retaining the full in-sample Sharpe earns full marks. Beating it slightly is
        # not rewarded beyond 1.0 - that is usually noise.
        score = ramp(retention, 0.0, 1.0)
        measurement = f"{retention:.0%} of in-sample Sharpe retained out of sample"
        detail = ("Out-of-sample Sharpe divided by in-sample Sharpe, scored 0 at none "
                  "retained and 1.0 at fully retained.")
    if wf.total_folds and wf.folds:
        fold_share = wf.positive_folds / len(wf.folds)
        # Walk-forward is a secondary check, worth a third of this component.
        score = 0.67 * score + 0.33 * fold_share
        measurement += f"; {wf.positive_folds}/{len(wf.folds)} walk-forward folds positive"
    return ScoreComponent(
        key="oos_consistency", label="Out-of-sample consistency", weight=20.0,
        score=score, points=score * 20.0, measurement=measurement, detail=detail,
    )


def _breadth(conc: Concentration) -> ScoreComponent:
    traded = max(conc.total_symbols, 1)
    profitable_share = conc.profitable_symbols / traded
    # Half the universe profitable is the pass mark for a strategy claiming to be a
    # general rule rather than a single-stock quirk.
    spread = ramp(profitable_share, 0.2, 0.6)
    # Concentration cuts the other way: one symbol carrying 70%+ of gross profit is a
    # story about that symbol, not about the rule.
    focus = ramp(conc.top_symbol_pnl_share_pct, 70.0, 25.0)
    # A narrow universe caps the whole component. "Both my two hand-picked stocks were
    # profitable" is not breadth evidence, however good the ratio looks, and without
    # this cap cherry-picking two winners scores the same as a rule that works on
    # eighteen symbols.
    size_factor = ramp(traded, 1, 8)
    score = size_factor * (0.6 * spread + 0.4 * focus)
    measurement = (f"{conc.profitable_symbols}/{conc.total_symbols} symbols profitable; "
                   f"top symbol {conc.top_symbol or 'n/a'} holds "
                   f"{conc.top_symbol_pnl_share_pct:.0f}% of gross profit")
    if traded < 8:
        measurement += f"; capped at {size_factor:.0%} for a {traded}-symbol universe"
    return ScoreComponent(
        key="breadth", label="Breadth across symbols", weight=15.0,
        score=score, points=score * 15.0,
        measurement=measurement,
        detail="Full marks when 60%+ of the universe is profitable and no symbol holds "
               "more than 25% of gross profit. Scaled down below 8 symbols, because a "
               "narrow universe cannot demonstrate breadth at all.",
    )


def _time_consistency(conc: Concentration, regimes: list[dict]) -> ScoreComponent:
    year_share = (conc.positive_years / conc.total_years) if conc.total_years else 0.0
    years_score = ramp(year_share, 0.25, 0.7)
    positive_regimes = sum(1 for r in regimes if r.get("return_pct", 0) > 0)
    regime_score = ramp(positive_regimes, 0, max(len(regimes), 1))
    score = 0.6 * years_score + 0.4 * regime_score
    return ScoreComponent(
        key="time_consistency", label="Consistency over time", weight=15.0,
        score=score, points=score * 15.0,
        measurement=(f"{conc.positive_years}/{conc.total_years} calendar years positive; "
                     f"{positive_regimes}/{len(regimes)} market regimes positive"),
        detail="Full marks at 70%+ of years positive and every regime positive. A "
               "strategy that only works in one regime is a bet on that regime.",
    )


def _cost_robustness(costs: CostSensitivity) -> ScoreComponent:
    retention = costs.sharpe_retained_at_3x
    if retention is None:
        score = 0.2
        measurement = "not measurable (no edge at the configured costs)"
    else:
        score = ramp(retention, 0.0, 0.8)
        measurement = f"{retention:.0%} of Sharpe retained at 3x costs"
    if costs.survives_5x:
        score = min(1.0, score + 0.1)
        measurement += "; still profitable at 5x"
    return ScoreComponent(
        key="cost_robustness", label="Robustness to costs", weight=15.0,
        score=score, points=score * 15.0, measurement=measurement,
        detail="Sharpe at 3x the configured commission and slippage, divided by Sharpe "
               "at the configured level. Full marks at 80% retained.",
    )


def _parameter_robustness(robust: ParameterRobustness) -> ScoreComponent:
    if not robust.neighbours:
        return ScoreComponent(
            key="parameter_robustness", label="Parameter robustness", weight=15.0,
            score=0.5, points=7.5, measurement="no parameters perturbed",
            detail="Scored neutral: there were no numeric parameters to vary.",
        )
    survival = robust.profitable_share_pct / 100.0
    fragility = robust.fragility_ratio
    # A ratio near 1.0 means the neighbours perform like the base - a plateau, not a
    # spike. Above ~2.0 the chosen parameters are suspiciously special.
    stability = 1.0 if fragility is None else ramp(fragility, 2.5, 1.0)
    score = 0.5 * survival + 0.5 * stability
    ratio_text = "n/a" if fragility is None else f"{fragility:.2f}x"
    return ScoreComponent(
        key="parameter_robustness", label="Parameter robustness", weight=15.0,
        score=score, points=score * 15.0,
        measurement=(f"{robust.profitable_share_pct:.0f}% of {len(robust.neighbours)} "
                     f"perturbed variants profitable; base/median Sharpe {ratio_text}"),
        detail="Each numeric parameter is moved +/-10% and +/-20%. Full marks when the "
               "neighbours stay profitable and perform like the base run.",
    )


def _warnings(
    metrics: Metrics,
    conc: Concentration,
    split: SplitValidation,
    costs: CostSensitivity,
    robust: ParameterRobustness,
    regimes: list[dict],
    years_covered: int,
    is_synthetic: bool,
    benchmark: Metrics,
    skipped_signals: int,
) -> list[Warning_]:
    out: list[Warning_] = []

    # --- required: low trade count --------------------------------------------------
    if metrics.trades < 30:
        out.append(Warning_(
            id="low_trade_count",
            severity="critical" if metrics.trades < 10 else "high",
            title="Too few trades to conclude anything",
            message=("With this few closed trades the performance figures are dominated by "
                     "luck. Widen the universe, loosen the entry rule, or lengthen the "
                     "window before drawing conclusions."),
            measurement=f"{metrics.trades} trades",
            threshold="fewer than 30",
        ))

    # --- required: concentrated returns ---------------------------------------------
    if conc.top_symbol_pnl_share_pct > 50:
        out.append(Warning_(
            id="concentrated_returns_symbol",
            severity="high",
            title=f"Most of the profit came from {conc.top_symbol}",
            message=("The result is a bet on one symbol rather than evidence of a general "
                     "rule. Re-run without it and see whether anything is left."),
            measurement=f"{conc.top_symbol} = {conc.top_symbol_pnl_share_pct:.0f}% of gross profit",
            threshold="more than 50%",
        ))
    if conc.top5_trades_pnl_share_pct > 50 and metrics.trades >= 10:
        out.append(Warning_(
            id="concentrated_returns_trades",
            severity="high",
            title="A handful of trades carried the whole result",
            message=("Remove the five best trades and the strategy looks very different. "
                     "That is a thin base to commit capital on."),
            measurement=f"top 5 trades = {conc.top5_trades_pnl_share_pct:.0f}% of gross profit",
            threshold="more than 50%",
        ))
    if conc.top10_days_return_share_pct > 60 and metrics.days > 250:
        out.append(Warning_(
            id="concentrated_returns_days",
            severity="medium",
            title="Returns hinge on a few days",
            message="Miss those days and the edge disappears. Check they are not data artefacts.",
            measurement=f"top 10 days = {conc.top10_days_return_share_pct:.0f}% of all up-day return",
            threshold="more than 60%",
        ))

    # --- required: cost sensitivity --------------------------------------------------
    if costs.sharpe_retained_at_3x is not None and costs.sharpe_retained_at_3x < 0.5:
        out.append(Warning_(
            id="cost_sensitive",
            severity="high",
            title="The edge does not survive realistic costs",
            message=("Tripling commission and slippage - still well inside what a retail "
                     "account pays on illiquid fills - removes most of the performance."),
            measurement=f"Sharpe {costs.base_sharpe:.2f} -> {costs.sharpe_at_3x:.2f} at 3x costs",
            threshold="less than 50% retained",
        ))
    if costs.breakeven_round_trip_bps is not None and costs.breakeven_round_trip_bps < 50:
        out.append(Warning_(
            id="thin_cost_margin",
            severity="high" if costs.breakeven_round_trip_bps < 25 else "medium",
            title="Very little room between the edge and the costs",
            message=("The strategy stops making money once a round trip costs more than "
                     "this. Real fills on real size routinely exceed it."),
            measurement=f"break-even at {costs.breakeven_round_trip_bps:.0f} bps round trip",
            threshold="less than 50 bps",
        ))

    # --- validation -------------------------------------------------------------------
    if split.degradation_pct is not None and split.degradation_pct > 40:
        out.append(Warning_(
            id="oos_degradation",
            severity="high" if split.degradation_pct > 70 else "medium",
            title="Performance drops sharply out of sample",
            message=("The later part of the record is much weaker than the earlier part. "
                     "Either the edge is decaying or the early period was favourable."),
            measurement=f"{split.degradation_pct:.0f}% of in-sample Sharpe lost after {split.split_date}",
            threshold="more than 40%",
        ))
    if robust.fragility_ratio is not None and robust.fragility_ratio > 1.5:
        out.append(Warning_(
            id="fragile_parameters",
            severity="high" if robust.fragility_ratio > 2.5 else "medium",
            title="The chosen parameters are suspiciously special",
            message=("Nudging the numbers by 10-20% makes the result substantially worse. "
                     "A real edge should sit on a plateau, not on a spike."),
            measurement=(f"base Sharpe {robust.base_sharpe:.2f} vs neighbourhood median "
                         f"{robust.median_sharpe:.2f}"),
            threshold="base more than 1.5x the neighbourhood median",
        ))

    # --- coverage ----------------------------------------------------------------------
    if years_covered < 3:
        out.append(Warning_(
            id="short_history", severity="high",
            title="Too short a history",
            message="Fewer than three years of trading covers too few market conditions.",
            measurement=f"{years_covered} calendar years with trades",
            threshold="fewer than 3",
        ))
    covered_regimes = sum(1 for r in regimes if r.get("trades", 0) > 0)
    if covered_regimes < 2 and regimes:
        out.append(Warning_(
            id="single_regime", severity="medium",
            title="Only tested in one kind of market",
            message="The strategy has not been observed in a different regime. Untested is not safe.",
            measurement=f"{covered_regimes} of {len(regimes)} regimes contain trades",
            threshold="fewer than 2",
        ))
    if metrics.exposure_pct < 10 and metrics.trades > 0:
        out.append(Warning_(
            id="low_exposure", severity="medium",
            title="Capital sits idle almost all the time",
            message=("Risk-adjusted ratios flatter a strategy that is rarely invested, because "
                     "flat days add no volatility. Compare the total return with the "
                     "benchmark, not just the Sharpe."),
            measurement=f"{metrics.exposure_pct:.1f}% average exposure",
            threshold="less than 10%",
        ))
    if metrics.max_drawdown_pct < -35:
        out.append(Warning_(
            id="severe_drawdown", severity="high",
            title="Drawdown most people could not sit through",
            message="A peak-to-trough loss this deep ends most real allocations before recovery.",
            measurement=f"{metrics.max_drawdown_pct:.1f}% max drawdown over "
                        f"{metrics.max_drawdown_days} trading days underwater",
            threshold="worse than -35%",
        ))
    if conc.total_symbols < 5:
        out.append(Warning_(
            id="narrow_universe",
            severity="high" if conc.total_symbols <= 2 else "medium",
            title="Tested on too few symbols to generalise",
            message=("A rule validated on a couple of hand-picked names is a statement "
                     "about those names. Re-run it on the full universe: if the edge is "
                     "real it should survive symbols you did not choose."),
            measurement=f"{conc.total_symbols} symbols in the universe",
            threshold="fewer than 5",
        ))
    if metrics.trades > 0 and metrics.cagr_pct < benchmark.cagr_pct:
        gap = benchmark.cagr_pct - metrics.cagr_pct
        out.append(Warning_(
            id="underperforms_benchmark",
            severity="high" if gap > 3.0 else "medium",
            title="Buying and holding the benchmark beat this",
            message=("Whatever the risk-adjusted figures say, the simplest possible "
                     "alternative made more money over the same window. That is the bar "
                     "any active rule has to clear."),
            measurement=(f"{metrics.cagr_pct:.2f}% CAGR vs {benchmark.cagr_pct:.2f}% for "
                         f"buy-and-hold ({gap:.2f} pts behind)"),
            threshold="strategy CAGR below benchmark CAGR",
        ))
    if metrics.trades > 0 and skipped_signals > 0.2 * metrics.trades:
        out.append(Warning_(
            id="position_cap_binding",
            severity="medium",
            title="The position cap threw away a lot of signals",
            message=("More entry signals fired than there were free slots, so the engine "
                     "kept the alphabetically first symbols and dropped the rest. That "
                     "tie-break is arbitrary: a different one would give a different "
                     "result. Raise the position cap or tighten the entry rule."),
            measurement=f"{skipped_signals} signals dropped against {metrics.trades} trades taken",
            threshold="more than 20% of trade count",
        ))
    if metrics.profit_factor and metrics.profit_factor < 1.1 and metrics.trades >= 20:
        out.append(Warning_(
            id="thin_profit_factor", severity="medium",
            title="Winners barely outweigh losers",
            message="At this profit factor, a small worsening in fills flips the strategy negative.",
            measurement=f"profit factor {metrics.profit_factor:.2f}",
            threshold="less than 1.10",
        ))

    # --- always on: structural caveats ------------------------------------------------
    out.append(Warning_(
        id="universe_hindsight", severity="info",
        title="The universe was chosen with hindsight",
        message=("Every symbol here exists and is liquid today. A strategy tested only on "
                 "survivors looks better than the same strategy run on the full historical "
                 "cross-section, delistings included."),
        measurement="fixed 18-symbol universe",
        threshold="always reported",
    ))
    out.append(Warning_(
        id="single_data_source", severity="info",
        title="One data source, one snapshot",
        message=("Results are not reconciled against a second vendor. Bad prints, "
                 "adjustment errors and calendar quirks would pass through unnoticed."),
        measurement="single frozen snapshot",
        threshold="always reported",
    ))
    if is_synthetic:
        out.append(Warning_(
            id="synthetic_data", severity="critical",
            title="This snapshot is simulated, not real market data",
            message=("The bars are generated by a seeded regime-switching model so the demo "
                     "runs offline and reproducibly. Structure is realistic, but no result "
                     "here is a statement about real markets. Run `make data-real` to "
                     "vendor an actual snapshot."),
            measurement="synthetic snapshot",
            threshold="always reported when data is generated",
        ))

    order = {"critical": 0, "high": 1, "medium": 2, "info": 3}
    return sorted(out, key=lambda w: (order[w.severity], w.id))


def build_evidence(
    metrics: Metrics,
    conc: Concentration,
    split: SplitValidation,
    wf: WalkForward,
    costs: CostSensitivity,
    robust: ParameterRobustness,
    regimes: list[dict],
    years_covered: int,
    benchmark: Metrics,
    skipped_signals: int = 0,
    is_synthetic: bool = False,
) -> Evidence:
    components = [
        _sample_size(metrics),
        _oos_consistency(split, wf),
        _breadth(conc),
        _time_consistency(conc, regimes),
        _cost_robustness(costs),
        _parameter_robustness(robust),
    ]
    total = sum(c.points for c in components)
    evidence = Evidence(
        score=round(total, 1),
        grade=_grade(total),
        components=[asdict(c) | {"score": round(c.score, 4), "points": round(c.points, 2)}
                    for c in components],
        warnings=[asdict(w) for w in _warnings(
            metrics, conc, split, costs, robust, regimes, years_covered, is_synthetic,
            benchmark, skipped_signals,
        )],
    )
    weakest = min(components, key=lambda c: c.score)
    evidence.headline = (
        f"Grade {evidence.grade} - {evidence.score:.1f}/100. "
        f"Weakest evidence: {weakest.label.lower()} ({weakest.measurement}). "
        "This scores how much the backtest can be trusted, not how much money the "
        "strategy will make."
    )
    return evidence
