"""The backend/frontend contract.

The response shape is the seam most likely to break silently: a field renamed in
Python still type-checks in TypeScript, and the UI just renders a blank cell. This
test generates a real report and asserts that every interface in `frontend/src/types.ts`
declares every field the backend actually sends.

It reads the TypeScript with a regex rather than a parser on purpose - the alternative
is a Node toolchain dependency inside the Python test suite, which is a worse trade for
a check this narrow.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TYPES = Path(__file__).resolve().parents[2] / "frontend" / "src" / "types.ts"

# interface name in types.ts -> path into the report payload
INTERFACES: dict[str, tuple[str, ...]] = {
    "BacktestReport": (),
    "Metrics": ("summary",),
    "Evidence": ("evidence",),
    "ScoreComponent": ("evidence", "components", "0"),
    "EvidenceWarning": ("evidence", "warnings", "0"),
    "SeriesPoint": ("series", "0"),
    "Trade": ("trades", "0"),
    "SymbolStat": ("per_symbol", "0"),
    "PeriodStat": ("per_year", "0"),
    "RegimeStat": ("per_regime", "0"),
    "Concentration": ("concentration",),
    "SplitValidation": ("validation", "split"),
    "CostSensitivity": ("validation", "cost_sensitivity"),
    "ParameterRobustness": ("validation", "parameter_robustness"),
    "StrategyRender": ("strategy_render",),
    "Reproducibility": ("reproducibility",),
}


def declared_fields(source: str, interface: str) -> set[str] | None:
    match = re.search(rf"(?:export\s+)?interface\s+{interface}\s*\{{(.*?)\n\}}", source, re.S)
    if match is None:
        return None
    return set(re.findall(r"^\s*(?:readonly\s+)?([A-Za-z_][A-Za-z0-9_]*)\??\s*:", match.group(1), re.M))


def dig(payload, path: tuple[str, ...]):
    node = payload
    for step in path:
        node = node[int(step)] if step.isdigit() else node[step]
    return node


@pytest.fixture(scope="module")
def report():
    from app.data import get_store
    from app.dsl import BacktestConfig
    from app.nl.rules import translate
    from app.report import run_report

    store = get_store()
    parsed = translate(
        "Buy when RSI(14) drops below 35 and the price is above the 200-day moving "
        "average, sell when RSI goes above 55 or after 20 days. 8% stop loss.",
        store.symbols,
    )
    return run_report(parsed.strategy, BacktestConfig(), store)


@pytest.mark.skipif(not TYPES.exists(), reason="frontend types.ts not present")
def test_every_response_field_is_declared_in_typescript(report):
    source = TYPES.read_text()
    problems: list[str] = []
    for interface, path in INTERFACES.items():
        declared = declared_fields(source, interface)
        if declared is None:
            problems.append(f"types.ts has no interface {interface}")
            continue
        actual = set(dig(report, path).keys())
        missing = sorted(actual - declared)
        if missing:
            problems.append(f"{interface} does not declare: {missing}")
    assert problems == [], "frontend types have drifted from the API:\n  " + "\n  ".join(problems)


@pytest.mark.skipif(not TYPES.exists(), reason="frontend types.ts not present")
def test_the_report_carries_no_empty_sections(report):
    """A section that is silently empty renders as a blank panel, which reads as a bug."""
    for key in ("summary", "benchmark", "evidence", "concentration", "strategy_render",
                "reproducibility", "diagnostics"):
        assert report[key], f"{key} is empty"
    for key in ("series", "trades", "per_symbol", "per_year", "per_regime"):
        assert len(report[key]) > 0, f"{key} is empty"
    assert len(report["evidence"]["components"]) == 6
    assert report["validation"]["walk_forward"]["folds"], "walk-forward produced no folds"
    assert report["validation"]["cost_sensitivity"]["points"], "cost sweep produced no points"
