#!/usr/bin/env python3
"""Regenerate strategy.schema.json from the Pydantic models.

The schema is generated, never hand-edited: two hand-maintained descriptions of the
same grammar drift within a week, and the drift is invisible until something malformed
gets through. `tests/test_dsl_v2.py` asserts the committed file matches this output.

    python -m dsl_v2.generate_schema        # writes the file
    python -m dsl_v2.generate_schema --check # exits 1 if it is stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .models import DSL_VERSION, Strategy

SCHEMA_PATH = Path(__file__).parent / "strategy.schema.json"
SCHEMA_ID = "https://prooftrade.dev/schema/strategy/v2.0.0.json"


def build() -> dict:
    schema = Strategy.model_json_schema(mode="validation")
    ordered = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": f"ProofTrade Strategy DSL {DSL_VERSION}",
        "description": (
            "A rule-based daily-bar trading strategy. Data only: there is no expression "
            "string, formula field or callback anywhere in this grammar, so a conforming "
            "document can be executed without an evaluator. Structural validity is "
            "necessary but not sufficient - see the semantic rules in README.md."
        ),
    }
    ordered.update(schema)
    return ordered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if the file is stale")
    args = parser.parse_args()

    rendered = json.dumps(build(), indent=2, sort_keys=False) + "\n"
    if args.check:
        current = SCHEMA_PATH.read_text() if SCHEMA_PATH.exists() else ""
        if current != rendered:
            print("strategy.schema.json is stale; run python -m dsl_v2.generate_schema",
                  file=sys.stderr)
            return 1
        print("schema up to date")
        return 0

    SCHEMA_PATH.write_text(rendered)
    print(f"wrote {SCHEMA_PATH} ({len(rendered):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
