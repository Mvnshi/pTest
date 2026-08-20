"""SQLite persistence for completed runs.

Runs are immutable rows. Nothing is ever updated in place, because a stored run is
supposed to be a record of what the engine produced at a point in time - if the engine
changes, the old row must keep telling the truth about the old engine.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id            TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,
    name              TEXT NOT NULL,
    source_text       TEXT NOT NULL DEFAULT '',
    engine_version    TEXT NOT NULL,
    dsl_version       TEXT NOT NULL,
    data_hash         TEXT NOT NULL,
    config_hash       TEXT NOT NULL,
    result_hash       TEXT NOT NULL,
    score             REAL NOT NULL,
    grade             TEXT NOT NULL,
    trades            INTEGER NOT NULL,
    cagr_pct          REAL NOT NULL,
    sharpe            REAL NOT NULL,
    max_drawdown_pct  REAL NOT NULL,
    payload           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_created_at ON runs (created_at DESC);
"""


# Paths whose schema this process has already ensured. Creating the table lazily on
# first connect keeps storage working under a test client that never runs the ASGI
# lifespan, and makes `init()` an optimisation rather than a precondition.
_INITIALISED: set[str] = set()


def _connect(path: Path | None = None) -> sqlite3.Connection:
    target = Path(path or DB_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    key = str(target.resolve())
    if key not in _INITIALISED:
        with conn:
            conn.executescript(SCHEMA)
        _INITIALISED.add(key)
    return conn


def init(path: Path | None = None) -> None:
    _connect(path).close()


def save_run(report: dict[str, Any], path: Path | None = None) -> str:
    summary = report.get("summary", {})
    evidence = report.get("evidence", {})
    strategy = report.get("strategy", {})
    row = (
        report["run_id"],
        report["created_at"],
        strategy.get("name", "Untitled strategy"),
        strategy.get("source_text", ""),
        report["engine_version"],
        report["dsl_version"],
        report["data_snapshot_hash"],
        report["config_hash"],
        report["result_hash"],
        float(evidence.get("score", 0.0)),
        evidence.get("grade", "F"),
        int(summary.get("trades", 0)),
        float(summary.get("cagr_pct", 0.0)),
        float(summary.get("sharpe", 0.0)),
        float(summary.get("max_drawdown_pct", 0.0)),
        json.dumps(report, separators=(",", ":")),
    )
    with _connect(path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO runs (run_id, created_at, name, source_text, "
            "engine_version, dsl_version, data_hash, config_hash, result_hash, score, "
            "grade, trades, cagr_pct, sharpe, max_drawdown_pct, payload) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            row,
        )
    return report["run_id"]


def list_runs(limit: int = 25, path: Path | None = None) -> list[dict]:
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT run_id, created_at, name, score, grade, trades, cagr_pct, sharpe, "
            "max_drawdown_pct, result_hash FROM runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_run(run_id: str, path: Path | None = None) -> dict | None:
    with _connect(path) as conn:
        row = conn.execute("SELECT payload FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return json.loads(row["payload"]) if row else None
