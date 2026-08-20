"""Paths, versions and defaults. No behaviour lives here."""

from __future__ import annotations

import os
from pathlib import Path

# Bumping ENGINE_VERSION invalidates the reproducibility claim of older stored runs,
# so it is recorded alongside every result.
ENGINE_VERSION = "0.1.0"

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
DATA_DIR = Path(os.environ.get("PROOFTRADE_DATA_DIR", PROJECT_DIR / "data" / "bars"))
DB_PATH = Path(os.environ.get("PROOFTRADE_DB", BACKEND_DIR / "prooftrade.db"))
FRONTEND_DIST = Path(os.environ.get("PROOFTRADE_FRONTEND", PROJECT_DIR / "frontend" / "dist"))

TRADING_DAYS_PER_YEAR = 252

# Which translator handles /api/parse. "rules" is deterministic and offline and is the
# default for the demo; "anthropic" additionally consults a model, but its output is
# still validated against the DSL before use.
TRANSLATOR = os.environ.get("PROOFTRADE_LLM", "rules").lower()
