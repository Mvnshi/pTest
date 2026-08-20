"""Validation and robustness probes.

The base backtest is one run. Everything here exists to attack it:

* **IS/OOS split** - does the second act look like the first?
* **Walk-forward** - is the result carried by one lucky stretch?
* **Cost sensitivity** - how much of the edge is an artefact of cheap assumptions?
* **Parameter neighbourhood** - does the strategy only work at the exact numbers the
  user happened to type?

The MVP fits no parameters, so the first two measure regime stability rather than
classical overfitting; the fourth is the one that actually probes curve fitting. That
distinction is stated in the report rather than glossed over.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from .data import BarStore
from .dsl import BacktestConfig, Strategy, numeric_parameters, perturb
from .engine import BacktestResult, run_backtest
from .metrics import Metrics, metrics_for, metrics_for_slice

# Cost multiples probed, in ascending order. 1.0 is the configured level and reuses the
# base run rather than repeating it.
COST_MULTIPLES = (0.0, 1.0, 2.0, 3.0, 5.0)
# Relative perturbations applied to each numeric knob, nearest first.
PERTURBATION_FACTORS = (0.9, 1.1, 0.8, 1.2)
MAX_NEIGHBOURS = 8


def _as_dict(obj) -> dict:
    return asdict(obj)


@dataclass
class SplitValidation:
    split_date: str = ""
    train: dict = field(default_factory=dict)
    test: dict = field(default_factory=dict)
    sharpe_retention: float | None = None   # OOS Sharpe / IS Sharpe
    degradation_pct: float | None = None    # (1 - retention) * 100
    note: str = ""


def split_validation(result: BacktestResult, train_fraction: float) -> SplitValidation:
    """Slice the single continuous run into in-sample and out-of-sample halves.

    Slicing rather than re-running keeps the two halves on one capital path, so the
    comparison is not distorted by the OOS half restarting from flat.
    """
    n = result.n_days
    out = SplitValidation()
    if n < 60:
        out.note = "Not enough history to split."
        return out
    cut = int(n * train_fraction)
    train = metrics_for_slice(result, 0, cut)
    test = metrics_for_slice(result, cut, n)
    out.split_date = result.dates[cut]
    out.train = train.as_dict()
    out.test = test.as_dict()
    if train.sharpe > 0.05:
        retention = test.sharpe / train.sharpe
        out.sharpe_retention = round(float(retention), 4)
        out.degradation_pct = round(float((1.0 - retention) * 100.0), 2)
    else:
        out.note = (
            "In-sample Sharpe is at or below zero, so there is no edge to degrade from; "
            "the retention ratio is not meaningful here."
        )
    return out


@dataclass
class Fold:
    index: int
    start: str
    end: str
    metrics: dict


@dataclass
class WalkForward:
    folds: list[dict] = field(default_factory=list)
    positive_folds: int = 0
    total_folds: int = 0
    median_sharpe: float = 0.0
    worst_fold_return_pct: float = 0.0
    note: str = ""


def walk_forward(
    strategy: Strategy, config: BacktestConfig, store: BarStore, base: BacktestResult
) -> WalkForward:
    """Backtest N sequential, non-overlapping windows independently, each from flat.

    Indicators still warm up on all history before each window, so a later fold is not
    penalised for starting mid-series; but no *position* survives a fold boundary, so a
    fold's result cannot be propped up by a trade opened in the previous one.
    """
    out = WalkForward(total_folds=config.walk_forward_folds)
    n = base.n_days
    if n < config.walk_forward_folds * 120:
        out.note = "Not enough history for the requested number of folds."
        return out

    edges = np.linspace(0, n, config.walk_forward_folds + 1).astype(int)
    sharpes: list[float] = []
    returns: list[float] = []
    for i in range(config.walk_forward_folds):
        lo, hi = int(edges[i]), int(edges[i + 1])
        fold_config = config.model_copy(update={
            "start": _to_date(base.dates[lo]),
            "end": _to_date(base.dates[hi - 1]),
            "run_robustness": False,
        })
        fold_result = run_backtest(strategy, fold_config, store)
        m = metrics_for(fold_result)
        sharpes.append(m.sharpe)
        returns.append(m.total_return_pct)
        out.folds.append(_as_dict(Fold(
            index=i + 1, start=base.dates[lo], end=base.dates[hi - 1], metrics=m.as_dict()
        )))

    out.positive_folds = sum(1 for r in returns if r > 0)
    out.median_sharpe = round(float(np.median(sharpes)), 4) if sharpes else 0.0
    out.worst_fold_return_pct = round(float(min(returns)), 4) if returns else 0.0
    return out


def _to_date(text: str):
    import datetime as dt

    return dt.date.fromisoformat(text)


@dataclass
class CostPoint:
    multiple: float
    round_trip_bps: float       # total cost of a full round trip at this level
    cagr_pct: float
    sharpe: float
    total_return_pct: float
    trades: int


@dataclass
class CostSensitivity:
    points: list[dict] = field(default_factory=list)
    base_sharpe: float = 0.0
    sharpe_at_3x: float = 0.0
    sharpe_retained_at_3x: float | None = None
    breakeven_round_trip_bps: float | None = None
    survives_5x: bool = False
    note: str = ""


def cost_sensitivity(
    strategy: Strategy, config: BacktestConfig, store: BarStore, base: BacktestResult
) -> CostSensitivity:
    """Re-run at several cost levels and find where the edge dies."""
    out = CostSensitivity()
    base_metrics = metrics_for(base)
    out.base_sharpe = round(base_metrics.sharpe, 4)
    per_leg_bps = config.commission_bps + config.slippage_bps

    measured: list[CostPoint] = []
    for multiple in COST_MULTIPLES:
        result = base if multiple == 1.0 else run_backtest(
            strategy, config, store, cost_multiple=multiple
        )
        m = metrics_for(result)
        measured.append(CostPoint(
            multiple=multiple,
            round_trip_bps=round(per_leg_bps * multiple * 2, 2),
            cagr_pct=round(m.cagr_pct, 4),
            sharpe=round(m.sharpe, 4),
            total_return_pct=round(m.total_return_pct, 4),
            trades=m.trades,
        ))
    out.points = [_as_dict(p) for p in measured]

    at_3x = next(p for p in measured if p.multiple == 3.0)
    out.sharpe_at_3x = at_3x.sharpe
    if out.base_sharpe > 0.05:
        out.sharpe_retained_at_3x = round(at_3x.sharpe / out.base_sharpe, 4)
    out.survives_5x = measured[-1].cagr_pct > 0

    # Break-even: the round-trip cost at which CAGR first crosses zero, found by
    # scanning the measured levels and interpolating linearly inside the bracket that
    # contains the crossing. It is a scan, not a solve.
    for lower, upper in zip(measured, measured[1:]):
        if lower.cagr_pct > 0 >= upper.cagr_pct:
            span = lower.cagr_pct - upper.cagr_pct
            weight = lower.cagr_pct / span if span > 0 else 0.0
            out.breakeven_round_trip_bps = round(
                lower.round_trip_bps + weight * (upper.round_trip_bps - lower.round_trip_bps), 2
            )
            break
    else:
        if measured[0].cagr_pct <= 0:
            out.breakeven_round_trip_bps = 0.0
            out.note = "The strategy loses money even with zero transaction costs."
        elif out.survives_5x:
            out.note = (
                f"Still profitable at 5x the configured costs "
                f"({measured[-1].round_trip_bps:g} bps round trip); no break-even found in range."
            )
    return out


@dataclass
class Neighbour:
    label: str
    path: str
    base_value: float
    value: float
    factor: float
    sharpe: float
    cagr_pct: float
    trades: int


@dataclass
class ParameterRobustness:
    neighbours: list[dict] = field(default_factory=list)
    base_sharpe: float = 0.0
    median_sharpe: float = 0.0
    worst_sharpe: float = 0.0
    profitable_share_pct: float = 0.0
    fragility_ratio: float | None = None   # base Sharpe / neighbourhood median
    parameters_probed: int = 0
    parameters_total: int = 0
    note: str = ""


def parameter_robustness(
    strategy: Strategy, config: BacktestConfig, store: BarStore, base: BacktestResult
) -> ParameterRobustness:
    """Perturb each numeric knob and see whether the result survives its neighbours."""
    out = ParameterRobustness()
    base_metrics = metrics_for(base)
    out.base_sharpe = round(base_metrics.sharpe, 4)
    refs = numeric_parameters(strategy)
    out.parameters_total = len(refs)
    if not refs:
        out.note = "The strategy has no numeric parameters to perturb."
        return out

    # Interleave so the budget covers as many distinct parameters as possible at the
    # nearest offsets, instead of exhausting all four offsets of the first parameter.
    ordered: list[tuple] = []
    for pair in (PERTURBATION_FACTORS[:2], PERTURBATION_FACTORS[2:]):
        for ref in refs:
            for factor in pair:
                ordered.append((ref, factor))

    sharpes: list[float] = []
    probed: set[str] = set()
    for ref, factor in ordered:
        if len(out.neighbours) >= MAX_NEIGHBOURS:
            break
        variant = perturb(strategy, ref, factor)
        if variant is None:
            continue
        result = run_backtest(variant, config, store)
        m = metrics_for(result)
        sharpes.append(m.sharpe)
        probed.add(ref.path)
        out.neighbours.append(_as_dict(Neighbour(
            label=ref.label,
            path=ref.path,
            base_value=ref.value,
            value=round(ref.value * factor) if ref.integral else round(ref.value * factor, 6),
            factor=factor,
            sharpe=round(m.sharpe, 4),
            cagr_pct=round(m.cagr_pct, 4),
            trades=m.trades,
        )))

    out.parameters_probed = len(probed)
    if sharpes:
        out.median_sharpe = round(float(np.median(sharpes)), 4)
        out.worst_sharpe = round(float(min(sharpes)), 4)
        out.profitable_share_pct = round(
            float(np.mean([s > 0 for s in sharpes])) * 100.0, 2
        )
        if out.median_sharpe > 0.05:
            out.fragility_ratio = round(out.base_sharpe / out.median_sharpe, 4)
        elif out.base_sharpe > 0.05:
            # The base works and essentially none of its neighbours do: the strongest
            # possible fragility signal, so cap rather than divide by ~zero.
            out.fragility_ratio = 9.99
    else:
        out.note = "No perturbation produced a materially different strategy."
    return out
