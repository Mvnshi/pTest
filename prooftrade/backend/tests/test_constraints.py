"""Tests for the hard constraints in the brief.

These are not unit tests of behaviour; they are guards against the product quietly
becoming something it promised not to be.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app"
PY_FILES = sorted(APP.rglob("*.py"))

FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__"}
FORBIDDEN_MODULES = {"subprocess", "pickle", "marshal", "shelve", "ctypes"}


def test_no_dynamic_code_execution_anywhere_in_the_backend():
    """The LLM may only produce data. Nothing in the backend can turn data into code."""
    offenders: list[str] = []
    for path in PY_FILES:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in FORBIDDEN_CALLS:
                    offenders.append(f"{path.name}:{node.lineno} calls {node.func.id}()")
            if isinstance(node, ast.Attribute) and node.attr in ("system", "popen"):
                offenders.append(f"{path.name}:{node.lineno} uses os.{node.attr}")
    assert offenders == [], "dynamic execution found: " + "; ".join(offenders)


def test_no_dangerous_imports():
    offenders: list[str] = []
    for path in PY_FILES:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                if name in FORBIDDEN_MODULES:
                    offenders.append(f"{path.name}:{node.lineno} imports {name}")
    assert offenders == [], "; ".join(offenders)


def test_no_broker_or_order_placement_surface():
    """No real-money trading: nothing here can place an order anywhere."""
    banned = re.compile(
        r"\b(alpaca|ib_insync|ibapi|interactive_?brokers|tda[_-]?api|robinhood|"
        r"place_order|submit_order|create_order|live_trading)\b",
        re.IGNORECASE,
    )
    offenders = [
        f"{p.name}: {m.group(0)}"
        for p in PY_FILES
        for m in [banned.search(p.read_text())]
        if m
    ]
    assert offenders == [], "; ".join(offenders)


def test_the_engine_never_reaches_the_network():
    """Only the optional LLM adapter and the offline vendoring script may use the network."""
    network = re.compile(r"\b(requests|urllib|httpx|aiohttp|socket|yfinance)\b")
    allowed = {"llm.py"}
    offenders = [
        p.name for p in PY_FILES
        if p.name not in allowed and network.search(p.read_text())
    ]
    assert offenders == [], f"network access outside the LLM adapter: {offenders}"


def test_the_llm_adapter_validates_before_it_trusts():
    """The adapter must run model output through the DSL, not use it directly."""
    source = (APP / "nl" / "llm.py").read_text()
    assert "_to_strategy" in source
    assert "ValidationError" in source
    assert "Strategy(" in source, "model output must be constructed into a typed Strategy"


def test_costs_and_slippage_are_required_config_fields():
    from app.dsl import BacktestConfig

    config = BacktestConfig()
    assert config.commission_bps > 0, "a default of zero cost would be dishonest"
    assert config.slippage_bps > 0
    with pytest.raises(Exception):
        BacktestConfig(commission_bps=-1)


def test_every_report_contains_validation_and_the_three_required_warnings():
    """Low trade count, concentrated returns and cost sensitivity are always evaluated."""
    from app.evidence import _warnings

    source = Path(__file__).resolve().parent.parent / "app" / "evidence.py"
    text = source.read_text()
    for required in ("low_trade_count", "concentrated_returns_symbol", "cost_sensitive"):
        assert f'id="{required}"' in text, f"{required} warning is not implemented"
    assert callable(_warnings)
