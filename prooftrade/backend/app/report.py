"""Orchestrates one full evidence report.

`run_report` is the only entry point the API needs. It is deterministic: the same
strategy, config and data snapshot always produce the same `result_hash`, which is
returned in the payload so a viewer can watch it stay identical across re-runs.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from dataclasses import asdict
from typing import Any

import numpy as np

from .config import ENGINE_VERSION
from .data import BarStore
from .dsl import DSL_VERSION, BacktestConfig, Strategy, canonical_json, stable_hash
from .engine import run_backtest
from .evidence import build_evidence
from .metrics import (
    Metrics,
    compute_metrics,
    concentration,
    metrics_for,
    per_regime,
    per_symbol,
    per_year,
)
from .validation import cost_sensitivity, parameter_robustness, split_validation, walk_forward


def _jsonable(value: Any) -> Any:
    """Convert numpy scalars and arrays to plain Python for the JSON layer."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return [_jsonable(v) for v in value.tolist()]
    if isinstance(value, np.generic):
        return value.item()
    return value


def _underwater(equity: np.ndarray) -> np.ndarray:
    if len(equity) == 0:
        return equity
    peaks = np.maximum.accumulate(equity)
    return (equity / np.where(peaks > 0, peaks, 1.0) - 1.0) * 100.0


def _series(result) -> list[dict]:
    """The equity/drawdown series the charts consume."""
    strat_dd = _underwater(result.equity)
    bench_dd = _underwater(result.benchmark_equity)
    return [
        {
            "date": result.dates[i],
            "equity": round(float(result.equity[i]), 2),
            "benchmark": round(float(result.benchmark_equity[i]), 2),
            "drawdown": round(float(strat_dd[i]), 3),
            "benchmark_drawdown": round(float(bench_dd[i]), 3),
            "exposure": round(float(result.exposure[i]) * 100.0, 2),
        }
        for i in range(result.n_days)
    ]


def snapshot_is_synthetic(store: BarStore) -> bool:
    manifest = store.directory / "manifest.json"
    if not manifest.exists():
        return False
    try:
        source = json.loads(manifest.read_text()).get("source", "")
    except (json.JSONDecodeError, OSError):
        return False
    return "synthetic" in str(source).lower()


def run_report(strategy: Strategy, config: BacktestConfig, store: BarStore) -> dict:
    timings: dict[str, float] = {}

    t0 = time.perf_counter()
    base = run_backtest(strategy, config, store)
    timings["backtest_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    summary = metrics_for(base)
    benchmark_metrics = compute_metrics(base.dates, base.benchmark_equity, [])
    symbols = per_symbol(base.trades, strategy.universe)
    years = per_year(base)
    regimes = per_regime(base)
    conc = concentration(base, strategy.universe)

    t0 = time.perf_counter()
    split = split_validation(base, config.train_fraction)
    wf = walk_forward(strategy, config, store, base)
    timings["validation_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    t0 = time.perf_counter()
    if config.run_robustness:
        costs = cost_sensitivity(strategy, config, store, base)
        robust = parameter_robustness(strategy, config, store, base)
    else:
        from .validation import CostSensitivity, ParameterRobustness

        costs, robust = CostSensitivity(), ParameterRobustness()
    timings["robustness_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    regime_dicts = [asdict(r) for r in regimes]
    years_covered = len({t.exit_date[:4] for t in base.trades})
    is_synthetic = snapshot_is_synthetic(store)

    evidence = build_evidence(
        metrics=summary,
        conc=conc,
        split=split,
        wf=wf,
        costs=costs,
        robust=robust,
        regimes=regime_dicts,
        years_covered=years_covered,
        benchmark=benchmark_metrics,
        skipped_signals=base.skipped_signals,
        is_synthetic=is_synthetic,
    )

    payload: dict[str, Any] = {
        "engine_version": ENGINE_VERSION,
        "dsl_version": DSL_VERSION,
        "data_snapshot_hash": store.snapshot_hash,
        "data_is_synthetic": is_synthetic,
        "config_hash": stable_hash(config),
        "strategy": strategy.model_dump(mode="json"),
        "strategy_render": strategy.render(),
        "config": config.model_dump(mode="json"),
        "summary": summary.as_dict(),
        "benchmark": benchmark_metrics.as_dict() | {"symbol": config.benchmark},
        "series": _series(base),
        "trades": [t.as_dict() for t in base.trades],
        "per_symbol": [asdict(s) for s in symbols],
        "per_year": [asdict(y) for y in years],
        "per_regime": regime_dicts,
        "concentration": asdict(conc),
        "validation": {
            "split": asdict(split),
            "walk_forward": asdict(wf),
            "cost_sensitivity": asdict(costs),
            "parameter_robustness": asdict(robust),
        },
        "evidence": evidence.as_dict(),
        "diagnostics": {
            "skipped_entry_signals": base.skipped_signals,
            "warmup_bars": strategy.warmup_bars(),
            "universe_size": len(strategy.universe),
            "bars_evaluated": base.n_days,
            "backtests_run": 1
            + (len(wf.folds))
            + (len(costs.points) - 1 if costs.points else 0)
            + len(robust.neighbours),
        },
    }
    payload = _jsonable(payload)

    # The hash covers everything that should be reproducible - deliberately excluding
    # run id, timestamps and timings, which are not.
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]

    payload["result_hash"] = digest
    payload["run_id"] = stable_hash(strategy, config, extra=f"{store.snapshot_hash}|{digest}")
    payload["created_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    payload["timings_ms"] = timings
    payload["reproducibility"] = {
        "deterministic": True,
        "note": (
            "Re-running this strategy against the same data snapshot reproduces "
            f"result hash {digest} exactly. The hash covers the strategy, the config, "
            "every metric and every trade."
        ),
        "canonical_strategy": canonical_json(strategy),
    }
    return payload
