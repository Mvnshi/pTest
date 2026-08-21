"""Tests for the v2 DSL.

Three kinds of test, in order of how much they protect:

1. **Drift.** The Pydantic models, the generated JSON Schema and the TypeScript types
   must describe the same grammar. Three hand-maintained descriptions diverge within a
   week and the divergence is invisible until something malformed gets through.
2. **Rules.** Every documented validation rule fires on a document constructed to break
   it, and does not fire on one that does not.
3. **Round trips.** Parse, dump, reparse must be a fixed point, and exit list order must
   never change meaning.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from dsl_v2 import errors as E
from dsl_v2.generate_schema import build as build_schema
from dsl_v2.models import (
    DEFAULT_MIN_INDICATOR_PERIOD,
    EXIT_PRIORITY,
    MIN_PERIOD_BY_INDICATOR,
    MIRROR,
    Strategy,
)
from dsl_v2.validate import validate, validate_file, validate_json

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = sorted((ROOT / "examples").glob("*.json"))
SCHEMA = json.loads((ROOT / "strategy.schema.json").read_text())
TS = (ROOT / "strategy.ts").read_text()


def minimal(**overrides: Any) -> dict:
    """The smallest document that validates, as a base for negative tests."""
    document: dict[str, Any] = {
        "dsl_version": "2.0.0",
        "name": "minimal",
        "universe": {"symbols": ["SPY", "QQQ", "IWM"]},
        "entry": {
            "logic": "all",
            "conditions": [{
                "left": {"kind": "price", "field": "close"},
                "op": "above",
                "right": {"kind": "indicator", "name": "sma", "period": 50},
            }],
        },
        "exits": [{"kind": "percent_stop", "percent": 5, "trail": False}],
        "sizing": {"method": "equal_weight", "max_open_positions": 3},
    }
    document.update(overrides)
    return document


def codes(result) -> set[str]:
    return {i.code for i in result.errors}


def warning_codes(result) -> set[str]:
    return {i.code for i in result.warnings}


# ======================================================================================
# 1. Drift between the three representations
# ======================================================================================


def test_the_committed_schema_matches_the_models():
    committed = json.loads((ROOT / "strategy.schema.json").read_text())
    assert committed == build_schema(), (
        "strategy.schema.json is stale; run `python -m dsl_v2.generate_schema`"
    )


def test_the_schema_is_a_valid_2020_12_schema():
    Draft202012Validator.check_schema(SCHEMA)


PY_TO_TS = {
    "Strategy": "Strategy", "Universe": "Universe", "Liquidity": "Liquidity",
    "DateRange": "DateRange", "Calendar": "Calendar", "Execution": "Execution",
    "Costs": "Costs", "Sizing": "Sizing", "Condition": "Condition",
    "ConditionGroup": "ConditionGroup", "PriceOperand": "PriceOperand",
    "IndicatorOperand": "IndicatorOperand", "ConstantOperand": "ConstantOperand",
    "RangeOperand": "RangeOperand", "AtrStop": "AtrStop", "PercentStop": "PercentStop",
    "RMultipleTarget": "RMultipleTarget", "PercentTarget": "PercentTarget",
    "TimeExit": "TimeExit", "SignalExit": "SignalExit", "OppositeSignal": "OppositeSignal",
}


def ts_interface_fields(name: str) -> set[str] | None:
    match = re.search(rf"export interface {name}\s*\{{(.*?)\n\}}", TS, re.S)
    if match is None:
        return None
    body = re.sub(r"/\*.*?\*/", "", match.group(1), flags=re.S)
    body = re.sub(r"//.*", "", body)
    return set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\??\s*:", body, re.M))


@pytest.mark.parametrize("py_name,ts_name", sorted(PY_TO_TS.items()))
def test_typescript_declares_every_python_field(py_name: str, ts_name: str):
    import dsl_v2.models as models

    model = getattr(models, py_name)
    expected = set(model.model_fields)
    declared = ts_interface_fields(ts_name)
    assert declared is not None, f"strategy.ts has no interface {ts_name}"
    assert expected <= declared, (
        f"{ts_name} is missing {sorted(expected - declared)}"
    )
    assert declared <= expected, (
        f"{ts_name} declares fields the model does not have: {sorted(declared - expected)}"
    )


@pytest.mark.parametrize("ts_name,py_values", [
    ("IndicatorName", ["sma", "ema", "rsi", "atr", "highest_high", "lowest_low",
                       "pct_change", "volume_sma"]),
    ("Comparison", ["above", "below", "crosses_above", "crosses_below",
                    "greater_than", "less_than", "between"]),
    ("ExitKind", ["atr_stop", "percent_stop", "r_multiple_target", "percent_target",
                  "time_exit", "signal_exit", "opposite_signal"]),
    ("PriceField", ["open", "high", "low", "close", "volume"]),
    ("SizingMethod", ["equal_weight", "fixed_fraction", "fixed_notional", "atr_risk"]),
    ("Unit", ["price", "volume", "oscillator", "percent"]),
])
def test_typescript_enums_match_python(ts_name: str, py_values: list[str]):
    match = re.search(rf"export type {ts_name} =(.*?)(?:\n\n|\nexport )", TS, re.S)
    assert match is not None, f"strategy.ts has no type {ts_name}"
    found = set(re.findall(r"'([a-z_]+)'", match.group(1)))
    assert found == set(py_values), f"{ts_name}: {found ^ set(py_values)} differ"


def test_exit_priority_table_matches_typescript():
    match = re.search(r"EXIT_PRIORITY: Record<ExitKind, number> = \{(.*?)\}", TS, re.S)
    assert match
    found = {k: int(v) for k, v in re.findall(r"(\w+):\s*(\d+)", match.group(1))}
    assert found == EXIT_PRIORITY


def test_mirror_table_matches_typescript():
    match = re.search(r"MIRROR: Record<Comparison, Comparison> = \{(.*?)\n\}", TS, re.S)
    assert match
    found = dict(re.findall(r"(\w+):\s*'(\w+)'", match.group(1)))
    assert found == MIRROR


# ======================================================================================
# 2. Examples
# ======================================================================================


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_every_example_is_valid_with_no_warnings(path: Path):
    result = validate_file(path)
    assert result.ok, result.format()
    assert result.warnings == [], result.format()


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_every_example_passes_the_standalone_json_schema(path: Path):
    document = json.loads(path.read_text())
    errs = list(Draft202012Validator(SCHEMA).iter_errors(document))
    assert errs == [], [f"{list(e.path)}: {e.message}" for e in errs]


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_every_example_renders_to_prose(path: Path):
    strategy = validate_file(path).strategy
    rendered = strategy.render()
    assert rendered["entry"].startswith("Enter when")
    assert rendered["exits"] and all(isinstance(e, str) and e for e in rendered["exits"])
    for key in ("summary", "calendar", "sizing", "execution", "costs"):
        assert rendered[key], f"{key} rendered empty"


def test_the_examples_cover_the_whole_grammar():
    """A feature no example exercises is a feature nobody has checked."""
    seen_ops: set[str] = set()
    seen_indicators: set[str] = set()
    seen_exits: set[str] = set()
    seen_sizing: set[str] = set()
    seen_slippage: set[str] = set()
    seen_calendar: set[str] = set()

    def walk_conditions(conditions):
        for cond in conditions:
            seen_ops.add(cond["op"])
            for side in ("left", "right"):
                node = cond[side]
                if node["kind"] == "indicator":
                    seen_indicators.add(node["name"])

    for path in EXAMPLES:
        doc = json.loads(path.read_text())
        walk_conditions(doc["entry"]["conditions"])
        for rule in doc["exits"]:
            seen_exits.add(rule["kind"])
            if rule["kind"] == "signal_exit":
                walk_conditions(rule["conditions"])
        seen_sizing.add(doc["sizing"]["method"])
        seen_slippage.add(doc.get("costs", {}).get("slippage_model", "fixed_bps"))
        seen_calendar |= set(doc.get("calendar", {}))

    assert seen_ops == {"above", "below", "crosses_above", "crosses_below",
                        "greater_than", "less_than", "between"}
    assert seen_indicators == {"sma", "ema", "rsi", "highest_high", "lowest_low",
                               "pct_change", "volume_sma"}
    assert seen_exits == set(EXIT_PRIORITY)
    assert seen_sizing == {"equal_weight", "fixed_fraction", "fixed_notional", "atr_risk"}
    assert seen_slippage == {"fixed_bps", "atr_fraction"}
    assert seen_calendar == {"date_range", "days_of_week", "months"}


# ======================================================================================
# 3. Structural rejection
# ======================================================================================


def test_a_non_object_is_rejected():
    assert codes(validate([1, 2, 3])) == {E.E_NOT_AN_OBJECT}
    assert codes(validate("a strategy")) == {E.E_NOT_AN_OBJECT}


def test_malformed_json_names_the_line():
    result = validate_json('{"dsl_version": "2.0.0",,}')
    assert codes(result) == {E.E_NOT_AN_OBJECT}
    assert "line 1" in result.errors[0].message


def test_the_wrong_version_is_refused_without_further_parsing():
    result = validate(minimal(dsl_version="1.0.0"))
    assert codes(result) == {E.E_VERSION}
    assert "not interchangeable" in result.errors[0].message


def test_unknown_fields_are_rejected_not_ignored():
    result = validate(minimal(shell_command="rm -rf /"))
    assert codes(result) == {E.E_UNKNOWN_FIELD}
    assert result.errors[0].path == "/shell_command"


def test_unknown_indicator_is_rejected():
    document = minimal()
    document["entry"]["conditions"][0]["right"] = {
        "kind": "indicator", "name": "insider_knowledge", "period": 5}
    assert E.E_NOT_IN_ENUM in codes(validate(document))


def test_a_constant_cannot_be_the_left_side():
    document = minimal()
    document["entry"]["conditions"][0]["left"] = {"kind": "constant", "value": 30}
    assert codes(validate(document))


def test_missing_required_field_names_it():
    document = minimal()
    del document["universe"]
    result = validate(document)
    assert codes(result) == {E.E_MISSING_FIELD}
    assert "universe" in result.errors[0].message


def test_period_bounds_are_enforced():
    document = minimal()
    document["entry"]["conditions"][0]["right"]["period"] = 5000
    assert E.E_OUT_OF_RANGE in codes(validate(document))


def test_an_invalid_ticker_is_rejected():
    assert E.E_BAD_TICKER in codes(validate(minimal(universe={"symbols": ["../etc/passwd"]})))


def test_an_empty_exit_list_is_rejected():
    assert E.E_BAD_LENGTH in codes(validate(minimal(exits=[])))


# ======================================================================================
# 4. Semantic rules
# ======================================================================================


def condition(left: dict, op: str, right: dict) -> dict:
    return {"left": left, "op": op, "right": right}


RSI = {"kind": "indicator", "name": "rsi", "period": 14}
SMA = {"kind": "indicator", "name": "sma", "period": 50}
CLOSE = {"kind": "price", "field": "close"}
VOLUME = {"kind": "price", "field": "volume"}


def with_entry(*conditions: dict) -> dict:
    return minimal(entry={"logic": "all", "conditions": list(conditions)})


def test_greater_than_requires_a_constant():
    # RSI vs SMA is wrong twice over - the comparator wants a number, and an oscillator
    # cannot be compared to a price. Both are reported: the validator runs every rule
    # so a caller fixes the document once instead of rediscovering the next problem.
    result = validate(with_entry(condition(RSI, "greater_than", SMA)))
    assert codes(result) == {E.E_COMPARISON_NEEDS_CONSTANT, E.E_UNIT_MISMATCH}
    comparator_error = next(
        i for i in result.errors if i.code == E.E_COMPARISON_NEEDS_CONSTANT)
    assert "'above'" in comparator_error.message

    # With units that agree, only the comparator rule fires.
    only_comparator = validate(with_entry(condition(SMA, "greater_than", CLOSE)))
    assert codes(only_comparator) == {E.E_COMPARISON_NEEDS_CONSTANT}


def test_above_requires_a_series():
    result = validate(with_entry(condition(RSI, "above", {"kind": "constant", "value": 70})))
    assert codes(result) == {E.E_COMPARISON_NEEDS_SERIES}
    assert "greater_than" in result.errors[0].message


def test_between_requires_a_range():
    result = validate(with_entry(condition(RSI, "between", {"kind": "constant", "value": 30})))
    assert codes(result) == {E.E_COMPARISON_NEEDS_RANGE}


def test_a_range_is_rejected_outside_between():
    document = with_entry(condition(RSI, "less_than", {"kind": "range", "low": 1, "high": 2}))
    assert E.E_RANGE_NOT_ALLOWED in codes(validate(document))


def test_range_bounds_must_be_ordered():
    document = with_entry(condition(RSI, "between", {"kind": "range", "low": 70, "high": 30}))
    assert E.E_RANGE_BOUNDS in codes(validate(document))


def test_units_must_match():
    result = validate(with_entry(condition(RSI, "above", CLOSE)))
    assert codes(result) == {E.E_UNIT_MISMATCH}
    assert "oscillator" in result.errors[0].message


def test_volume_against_a_price_average_is_a_unit_mismatch():
    assert E.E_UNIT_MISMATCH in codes(validate(with_entry(condition(VOLUME, "above", SMA))))


def test_comparing_a_series_with_itself_is_rejected():
    assert E.E_SELF_COMPARISON in codes(validate(with_entry(condition(SMA, "above", SMA))))


def test_duplicate_exit_kinds_are_rejected():
    document = minimal(exits=[
        {"kind": "time_exit", "max_bars": 5},
        {"kind": "time_exit", "max_bars": 10},
    ])
    result = validate(document)
    assert E.E_DUPLICATE_EXIT in codes(result)


def test_signal_exit_and_opposite_signal_conflict():
    document = minimal(exits=[
        {"kind": "opposite_signal"},
        {"kind": "signal_exit", "logic": "any",
         "conditions": [condition(CLOSE, "below", SMA)]},
    ])
    assert E.E_CONFLICTING_SIGNAL_EXITS in codes(validate(document))


def test_r_multiple_without_a_stop_is_rejected():
    document = minimal(exits=[
        {"kind": "r_multiple_target", "multiple": 2},
        {"kind": "time_exit", "max_bars": 10},
    ])
    result = validate(document)
    assert E.E_R_MULTIPLE_NEEDS_STOP in codes(result)
    assert "1R is undefined" in result.errors[0].message


def test_r_multiple_with_two_stops_is_ambiguous():
    document = minimal(exits=[
        {"kind": "r_multiple_target", "multiple": 2},
        {"kind": "percent_stop", "percent": 5, "trail": False},
        {"kind": "atr_stop", "atr_period": 14, "multiple": 2, "trail": False},
    ])
    assert E.E_R_MULTIPLE_AMBIGUOUS in codes(validate(document))


def test_sizing_requires_its_own_parameters():
    result = validate(minimal(sizing={"method": "fixed_fraction", "max_open_positions": 3}))
    assert E.E_SIZING_MISSING_PARAM in codes(result)


def test_sizing_rejects_parameters_from_another_method():
    document = minimal(sizing={
        "method": "equal_weight", "max_open_positions": 3, "notional": 10000})
    result = validate(document)
    assert E.E_SIZING_UNEXPECTED_PARAM in codes(result)
    assert "does not apply" in result.errors[0].message


def test_over_allocation_is_rejected():
    document = minimal(sizing={
        "method": "fixed_fraction", "max_open_positions": 5, "fraction": 0.5})
    result = validate(document)
    assert E.E_SIZING_OVER_ALLOCATION in codes(result)
    assert "250%" in result.errors[0].message


def test_slippage_parameters_must_match_the_model():
    document = minimal(costs={"slippage_model": "atr_fraction", "slippage_bps": 5})
    assert E.E_SLIPPAGE_MISSING_PARAM in codes(validate(document))
    assert E.E_SLIPPAGE_UNEXPECTED_PARAM in codes(validate(document))


def test_calendar_dates_must_be_ordered():
    document = minimal(calendar={"date_range": {"start": "2020-01-01", "end": "2019-01-01"}})
    assert E.E_CALENDAR_DATE_ORDER in codes(validate(document))


def test_weekend_sessions_are_rejected():
    result = validate(minimal(calendar={"days_of_week": [6, 7]}))
    assert result.errors
    assert "Mon-Fri" in result.errors[0].message


def test_every_error_carries_a_path_and_an_actionable_message():
    document = minimal(sizing={"method": "atr_risk", "max_open_positions": 3})
    for issue in validate(document).errors:
        assert issue.code.startswith("E_")
        assert issue.path.startswith("/")
        assert issue.message.endswith(".")
        assert len(issue.message) > 20


# ======================================================================================
# 5. Warnings
# ======================================================================================


def test_price_against_a_bare_number_warns():
    document = with_entry(condition(CLOSE, "greater_than", {"kind": "constant", "value": 50}))
    result = validate(document)
    assert result.ok
    assert E.W_PRICE_VS_CONSTANT in warning_codes(result)


def test_no_stop_warns_but_still_runs():
    document = minimal(exits=[{"kind": "time_exit", "max_bars": 10}])
    result = validate(document)
    assert result.ok
    assert E.W_NO_STOP in warning_codes(result)


def test_a_target_nearer_than_the_stop_warns():
    document = minimal(exits=[
        {"kind": "percent_stop", "percent": 10, "trail": False},
        {"kind": "percent_target", "percent": 3},
    ])
    assert E.W_TARGET_INSIDE_STOP in warning_codes(validate(document))


def test_optimistic_execution_assumptions_warn():
    document = minimal(execution={
        "signal_timing": "close", "fill_timing": "next_open",
        "intrabar_priority": "target_first", "gap_policy": "fill_at_level",
        "candidate_priority": "alphabetical"})
    warned = warning_codes(validate(document))
    assert {E.W_TARGET_FIRST, E.W_FILL_AT_LEVEL} <= warned


def test_zero_costs_warn():
    document = minimal(costs={"commission_bps": 0, "slippage_model": "fixed_bps",
                              "slippage_bps": 0})
    assert E.W_ZERO_COSTS in warning_codes(validate(document))


def test_a_narrow_universe_warns():
    assert E.W_NARROW_UNIVERSE in warning_codes(validate(minimal(universe={"symbols": ["SPY"]})))


def test_sizing_that_disagrees_with_the_stop_warns():
    document = minimal(
        exits=[{"kind": "atr_stop", "atr_period": 14, "multiple": 3, "trail": False}],
        sizing={"method": "atr_risk", "max_open_positions": 3, "risk_fraction": 0.01,
                "atr_period": 14, "atr_multiple": 2},
    )
    result = validate(document)
    assert result.ok
    assert E.W_SIZING_STOP_MISMATCH in warning_codes(result)


def test_a_duplicated_condition_warns():
    document = with_entry(condition(CLOSE, "above", SMA), condition(CLOSE, "above", SMA))
    assert E.W_DUPLICATE_CONDITION in warning_codes(validate(document))


def test_warnings_never_block():
    document = minimal(universe={"symbols": ["SPY"]},
                       exits=[{"kind": "time_exit", "max_bars": 10}])
    result = validate(document)
    assert result.ok and result.warnings and result.strategy is not None


# ======================================================================================
# 6. Round trips and order independence
# ======================================================================================


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_parse_dump_reparse_is_a_fixed_point(path: Path):
    first = Strategy.model_validate(json.loads(path.read_text()))
    dumped = first.model_dump(mode="json")
    second = Strategy.model_validate(dumped)
    assert second == first
    assert second.model_dump(mode="json") == dumped


def test_exit_list_order_cannot_change_meaning():
    forwards = minimal(exits=[
        {"kind": "percent_stop", "percent": 5, "trail": False},
        {"kind": "percent_target", "percent": 10},
        {"kind": "time_exit", "max_bars": 10},
    ])
    backwards = minimal(exits=list(reversed(forwards["exits"])))
    a = validate(forwards).strategy.exits_in_priority_order()
    b = validate(backwards).strategy.exits_in_priority_order()
    assert [e.kind for e in a] == [e.kind for e in b] == [
        "percent_stop", "percent_target", "time_exit"]


def test_symbols_are_normalised_deterministically():
    strategy = validate(minimal(universe={"symbols": ["qqq", "SPY", " spy ", "iwm"]})).strategy
    assert strategy.universe.symbols == ["IWM", "QQQ", "SPY"]


def test_stops_outrank_targets_which_outrank_time_which_outranks_signals():
    assert EXIT_PRIORITY["atr_stop"] == EXIT_PRIORITY["percent_stop"]
    assert EXIT_PRIORITY["percent_stop"] < EXIT_PRIORITY["r_multiple_target"]
    assert EXIT_PRIORITY["r_multiple_target"] < EXIT_PRIORITY["time_exit"]
    assert EXIT_PRIORITY["time_exit"] < EXIT_PRIORITY["signal_exit"]


def test_every_comparison_has_a_mirror_and_mirroring_twice_is_identity():
    from dsl_v2.models import Comparison
    import typing

    for op in typing.get_args(Comparison):
        assert op in MIRROR
        assert MIRROR[MIRROR[op]] == op


def test_the_initial_stop_is_identified_only_when_unambiguous():
    one = validate(minimal(exits=[{"kind": "percent_stop", "percent": 5, "trail": False}]))
    assert one.strategy.initial_stop().kind == "percent_stop"
    none = validate(minimal(exits=[{"kind": "time_exit", "max_bars": 5}]))
    assert none.strategy.initial_stop() is None


# ======================================================================================
# 7. Safety
# ======================================================================================


def test_the_grammar_contains_no_executable_construct():
    """No field in the schema invites a caller to evaluate a string."""
    text = json.dumps(SCHEMA).lower()
    for banned in ("expression", "formula", "script", "eval", "lambda", "code", "exec"):
        assert f'"{banned}"' not in text, f"schema exposes a {banned} field"


def test_no_dsl_module_can_execute_anything():
    import ast

    for path in sorted(ROOT.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"eval", "exec", "compile", "__import__"}, (
                    f"{path.name}:{node.lineno} calls {node.func.id}()"
                )


# ======================================================================================
# 8. Minimum indicator periods
# ======================================================================================
# A one-period window is not a short window, it is a different quantity wearing the
# indicator's name: SMA(1) is the close, RSI(1) is a constant 0 or 100. A DSL that
# promises to be explainable must not accept a rule whose plain-English reading is false.


def indicator_doc(name: str, period: int) -> dict:
    """One condition on `name`, paired with a unit-compatible right side, so the period
    rule is the only thing that can fail."""
    partner = {
        "price": {"kind": "price", "field": "close"},
        "volume": {"kind": "price", "field": "volume"},
        "oscillator": {"kind": "constant", "value": 50},
        "percent": {"kind": "constant", "value": 5},
    }
    import dsl_v2.models as models

    unit = models.INDICATOR_UNITS[name]
    right = partner[unit]
    op = "greater_than" if right["kind"] == "constant" else "above"
    return minimal(entry={"logic": "all", "conditions": [
        {"left": {"kind": "indicator", "name": name, "period": period},
         "op": op, "right": right}]})


@pytest.mark.parametrize("name,minimum", sorted(MIN_PERIOD_BY_INDICATOR.items()))
def test_the_documented_minimum_period_is_enforced(name: str, minimum: int):
    if minimum > 1:
        result = validate(indicator_doc(name, minimum - 1))
        assert not result.ok, f"{name} accepted period {minimum - 1}"
        assert E.E_PERIOD_TOO_SHORT in codes(result)
        message = next(i for i in result.errors if i.code == E.E_PERIOD_TOO_SHORT).message
        assert str(minimum) in message and name in message
    assert validate(indicator_doc(name, minimum)).ok, f"{name} rejected its own minimum"


@pytest.mark.parametrize("name", sorted(MIN_PERIOD_BY_INDICATOR))
def test_ordinary_periods_are_accepted(name: str):
    assert validate(indicator_doc(name, 20)).ok


def test_percent_change_is_the_documented_exception():
    assert MIN_PERIOD_BY_INDICATOR["pct_change"] == 1
    assert validate(indicator_doc("pct_change", 1)).ok, (
        "a one-bar percent change is the daily return, which is meaningful"
    )
    assert {n for n, m in MIN_PERIOD_BY_INDICATOR.items() if m == 1} == {"pct_change"}


def test_every_indicator_has_a_declared_minimum():
    import dsl_v2.models as models

    assert set(MIN_PERIOD_BY_INDICATOR) == set(models.INDICATOR_UNITS)
    assert DEFAULT_MIN_INDICATOR_PERIOD == 2


def test_the_error_explains_what_the_period_would_degenerate_into():
    message = next(
        i.message for i in validate(indicator_doc("sma", 1)).errors
        if i.code == E.E_PERIOD_TOO_SHORT
    )
    assert "the close itself" in message
    assert '"field": "close"' in message, "should name the field the author probably meant"


def test_the_standalone_schema_enforces_the_same_minimums():
    """Otherwise a non-Python consumer would accept documents Pydantic rejects."""
    validator = Draft202012Validator(SCHEMA)
    for name, minimum in MIN_PERIOD_BY_INDICATOR.items():
        if minimum > 1:
            too_short = list(validator.iter_errors(indicator_doc(name, minimum - 1)))
            assert too_short, f"schema accepted {name} period {minimum - 1}"
        assert not list(validator.iter_errors(indicator_doc(name, minimum)))


# ======================================================================================
# 9. Intrabar priority: exactly one interpretation
# ======================================================================================


STOP_AND_TARGET = [
    {"kind": "percent_stop", "percent": 5, "trail": False},
    {"kind": "percent_target", "percent": 10},
    {"kind": "time_exit", "max_bars": 10},
]


def execution(**overrides) -> dict:
    base = {"signal_timing": "close", "fill_timing": "next_open",
            "intrabar_priority": "stop_first", "gap_policy": "fill_at_open",
            "candidate_priority": "alphabetical"}
    base.update(overrides)
    return base


def order_for(document: dict) -> list[str]:
    result = validate(document)
    assert result.ok, result.format()
    return [e.kind for e in result.strategy.exits_in_priority_order()]


def test_omitting_intrabar_priority_defaults_to_stop_first():
    document = minimal(exits=list(STOP_AND_TARGET))
    document.pop("execution", None)
    assert validate(document).strategy.execution.intrabar_priority == "stop_first"
    assert order_for(document) == ["percent_stop", "percent_target", "time_exit"]


def test_explicit_stop_first_matches_the_default():
    explicit = minimal(exits=list(STOP_AND_TARGET), execution=execution())
    implicit = minimal(exits=list(STOP_AND_TARGET))
    implicit.pop("execution", None)
    assert order_for(explicit) == order_for(implicit)


def test_explicit_target_first_governs_the_stop_target_pair():
    document = minimal(exits=list(STOP_AND_TARGET),
                       execution=execution(intrabar_priority="target_first"))
    assert order_for(document) == ["percent_target", "percent_stop", "time_exit"]


def test_target_first_moves_only_the_stop_target_pair():
    """Time and signal exits keep their bands; nothing else is reshuffled."""
    exits = [
        {"kind": "atr_stop", "atr_period": 14, "multiple": 2, "trail": False},
        {"kind": "percent_target", "percent": 10},
        {"kind": "time_exit", "max_bars": 10},
        {"kind": "opposite_signal"},
    ]
    document = minimal(exits=exits, execution=execution(intrabar_priority="target_first"))
    assert order_for(document) == [
        "percent_target", "atr_stop", "time_exit", "opposite_signal"]


def test_the_effective_priority_table_is_the_single_source_of_truth():
    """An engine reading effective_exit_priority cannot disagree with another engine."""
    conservative = validate(minimal(exits=list(STOP_AND_TARGET),
                                    execution=execution())).strategy
    aggressive = validate(minimal(exits=list(STOP_AND_TARGET),
                                  execution=execution(intrabar_priority="target_first"))).strategy

    base = conservative.effective_exit_priority()
    assert base == EXIT_PRIORITY, "stop_first must leave the base table untouched"

    moved = aggressive.effective_exit_priority()
    assert moved["percent_target"] < moved["percent_stop"]
    assert moved["r_multiple_target"] < moved["atr_stop"]
    # Bands that have nothing to do with the stop/target pair are unchanged.
    for kind in ("time_exit", "signal_exit", "opposite_signal"):
        assert moved[kind] == EXIT_PRIORITY[kind]


def test_exit_array_order_is_still_irrelevant_under_target_first():
    forwards = minimal(exits=list(STOP_AND_TARGET),
                       execution=execution(intrabar_priority="target_first"))
    backwards = minimal(exits=list(reversed(STOP_AND_TARGET)),
                        execution=execution(intrabar_priority="target_first"))
    assert order_for(forwards) == order_for(backwards)


def test_target_first_is_flagged_as_the_aggressive_assumption():
    document = minimal(exits=list(STOP_AND_TARGET),
                       execution=execution(intrabar_priority="target_first"))
    result = validate(document)
    assert result.ok, "aggressive is not invalid, only worth saying out loud"
    assert E.W_TARGET_FIRST in warning_codes(result)


def test_target_first_without_a_target_is_an_inert_setting():
    document = minimal(exits=[{"kind": "percent_stop", "percent": 5, "trail": False}],
                       execution=execution(intrabar_priority="target_first"))
    result = validate(document)
    assert result.ok
    assert E.W_INERT_INTRABAR_PRIORITY in warning_codes(result)


def test_invalid_execution_configurations_are_rejected():
    for field, bad in [
        ("intrabar_priority", "whichever_is_better"),
        ("fill_timing", "same_close"),
        ("signal_timing", "open"),
        ("gap_policy", "fill_at_midpoint"),
        ("candidate_priority", "random"),
    ]:
        result = validate(minimal(execution=execution(**{field: bad})))
        assert E.E_NOT_IN_ENUM in codes(result), f"{field}={bad!r} was accepted"
        assert any(i.path == f"/execution/{field}" for i in result.errors)


def test_signal_timing_admits_only_close():
    """Any other value would permit same-bar execution."""
    import typing
    import dsl_v2.models as models

    assert typing.get_args(models.Execution.model_fields["signal_timing"].annotation) == ("close",)


def test_typescript_mirrors_the_effective_priority_rule():
    assert "effectiveExitPriority" in TS
    assert "MIN_PERIOD_BY_INDICATOR" in TS
    match = re.search(r"MIN_PERIOD_BY_INDICATOR: Record<IndicatorName, number> = \{(.*?)\n\}",
                      TS, re.S)
    assert match, "strategy.ts has no MIN_PERIOD_BY_INDICATOR table"
    found = {k: int(v) for k, v in re.findall(r"(\w+):\s*(\d+)", match.group(1))}
    assert found == MIN_PERIOD_BY_INDICATOR
