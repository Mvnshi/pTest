"""FastAPI routes.

Thin by design: every route validates input, calls one function, and returns its
result. All the judgement lives in the modules below it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import storage
from .config import ENGINE_VERSION, TRANSLATOR
from .data import DataError, get_store
from .dsl import DSL_VERSION, BacktestConfig, Strategy, indicator_registry_payload
from .examples import EXAMPLES
from .nl import llm
from .nl.rules import TranslationError
from .nl.rules import translate as rules_translate
from .report import run_report, snapshot_is_synthetic

router = APIRouter(prefix="/api")


class ParseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4000)
    answers: dict[str, str] = Field(default_factory=dict)


class BacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: Strategy
    config: BacktestConfig = Field(default_factory=BacktestConfig)


def _store():
    try:
        return get_store()
    except DataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/health")
def health() -> dict[str, Any]:
    store = _store()
    start, end = store.date_range
    return {
        "status": "ok",
        "engine_version": ENGINE_VERSION,
        "dsl_version": DSL_VERSION,
        "data_snapshot_hash": store.snapshot_hash,
        "data_is_synthetic": snapshot_is_synthetic(store),
        "symbols": len(store.symbols),
        "start": start,
        "end": end,
        "translator": "anthropic" if llm.available() else "rules",
        "translator_configured": TRANSLATOR,
    }


@router.get("/universe")
def universe() -> dict[str, Any]:
    store = _store()
    start, end = store.date_range
    return {
        "symbols": [
            {"symbol": c.symbol, "bars": c.bars, "start": c.start, "end": c.end}
            for c in store.coverage()
        ],
        "start": start,
        "end": end,
        "snapshot_hash": store.snapshot_hash,
        "is_synthetic": snapshot_is_synthetic(store),
    }


@router.get("/examples")
def examples() -> list[dict[str, Any]]:
    return EXAMPLES


@router.get("/schema")
def schema() -> dict[str, Any]:
    return {
        "dsl_version": DSL_VERSION,
        "strategy_schema": Strategy.model_json_schema(),
        "config_schema": BacktestConfig.model_json_schema(),
        "indicators": indicator_registry_payload(),
    }


@router.post("/parse")
def parse(request: ParseRequest) -> dict[str, Any]:
    store = _store()
    translate = llm.translate if llm.available() else rules_translate
    try:
        result = translate(request.text, store.symbols, request.answers)
    except TranslationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result.as_dict()


@router.post("/backtest")
def backtest(request: BacktestRequest) -> dict[str, Any]:
    store = _store()
    unknown = [s for s in request.strategy.universe if not store.has(s)]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Not in the demo universe: {', '.join(unknown)}. "
                   f"Available: {', '.join(store.symbols)}",
        )
    try:
        report = run_report(request.strategy, request.config, store)
    except DataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        storage.save_run(report)
    except Exception as exc:  # noqa: BLE001 - a storage failure must not lose the result
        report.setdefault("notes", []).append(f"Run was not persisted: {exc}")
    return report


@router.get("/runs")
def runs(limit: int = 25) -> list[dict[str, Any]]:
    return storage.list_runs(max(1, min(limit, 200)))


@router.get("/runs/{run_id}")
def run_detail(run_id: str) -> dict[str, Any]:
    report = storage.get_run(run_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"No run with id {run_id}")
    return report
