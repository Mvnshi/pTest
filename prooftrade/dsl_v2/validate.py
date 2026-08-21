"""Validation for the v2 DSL: schema errors in plain English, then semantic rules.

Two passes, in this order:

1. **Structural.** Pydantic parses the document. Its errors are translated into the
   catalogue in `errors.py` - a raw `ValidationError` names a discriminated-union tag
   and a `literal_error` type, which is precise and unreadable.
2. **Semantic.** Rules that a schema cannot express: an R-multiple target needs a stop
   to measure R from; `greater_than` needs a number on its right; an RSI reading cannot
   be compared to a dollar price; a sizing method must carry its own parameters and no
   others.

Both passes always run to completion. A caller gets every problem at once rather than
fixing one and rediscovering the next.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from . import errors as E
from .errors import Issue, ValidationResult, error, warning
from .models import (
    ALL_SIZING_PARAMS,
    ALL_SLIPPAGE_PARAMS,
    CONSTANT_COMPARISONS,
    DSL_VERSION,
    RANGE_COMPARISONS,
    SERIES_COMPARISONS,
    SIZING_PARAMS,
    SLIPPAGE_PARAMS,
    STOP_KINDS,
    TARGET_KINDS,
    Condition,
    ConditionGroup,
    ConstantOperand,
    RangeOperand,
    SignalExit,
    Strategy,
)

# Discriminator tag values Pydantic inserts into error locations. They identify which
# arm of a union failed, which is noise in a path the reader has to find in their file.
_UNION_TAGS = frozenset({
    "price", "indicator", "constant", "range",
    "atr_stop", "percent_stop", "r_multiple_target", "percent_target",
    "time_exit", "signal_exit", "opposite_signal",
})


_CODED = re.compile(r"^\[([A-Z_]+)\]\s*(.*)$", re.S)


def _article(noun: str) -> str:
    """"an indicator", "a constant" - small, but wrong grammar reads as carelessness."""
    return f"{'an' if noun[0] in 'aeiou' else 'a'} {noun}"


def _pointer(loc: tuple[Any, ...]) -> str:
    parts = [
        str(p) for p in loc
        if not (isinstance(p, str) and (p in _UNION_TAGS or "[" in p))
    ]
    return "/" + "/".join(parts) if parts else ""


def _describe_schema_error(err: dict[str, Any]) -> Issue:
    """Turn one Pydantic error into a catalogue Issue with an actionable message."""
    kind = err["type"]
    path = _pointer(err["loc"])
    given = err.get("input")
    ctx = err.get("ctx") or {}
    field = str(err["loc"][-1]) if err["loc"] else "document"

    if kind == "extra_forbidden":
        return error(
            E.E_UNKNOWN_FIELD, path,
            f"Unknown field {field!r}. The DSL rejects fields it does not recognise "
            f"rather than ignoring them, so a typo cannot silently disable a rule. "
            f"Remove it, or check the spelling against the schema.",
            given,
        )
    if kind == "missing":
        return error(
            E.E_MISSING_FIELD, path,
            f"Required field {field!r} is missing.",
        )
    if kind in ("literal_error", "enum"):
        expected = ctx.get("expected", "one of the allowed values")
        if field == "dsl_version":
            return error(
                E.E_VERSION, path,
                f"This document declares dsl_version {given!r}, but this validator "
                f"implements {DSL_VERSION}. Versions are not interchangeable: v1 used "
                f"symbolic comparators and a different exit model. Migrate the document "
                f"or use a v1 validator.",
                given,
            )
        return error(
            E.E_NOT_IN_ENUM, path,
            f"{field!r} must be {expected}. The DSL uses closed vocabularies so that "
            f"every strategy can be explained and executed the same way everywhere.",
            given,
        )
    if kind.startswith("greater_than") or kind.startswith("less_than"):
        bound = ctx.get("gt", ctx.get("ge", ctx.get("lt", ctx.get("le"))))
        return error(
            E.E_OUT_OF_RANGE, path,
            f"{field!r} is out of range ({kind.replace('_', ' ')} {bound}).",
            given,
        )
    if "too_short" in kind or "too_long" in kind:
        minimum, maximum = ctx.get("min_length"), ctx.get("max_length")
        unit = "characters" if kind.startswith("string") else "items"
        if minimum == 1 and maximum is None:
            detail = "must not be empty"
        elif maximum is None:
            detail = f"needs at least {minimum} {unit}"
        elif minimum in (None, 0):
            detail = f"allows at most {maximum} {unit}"
        else:
            detail = f"takes between {minimum} and {maximum} {unit}"
        return error(E.E_BAD_LENGTH, path, f"{field!r} {detail}.", given)
    if kind in ("model_type", "dict_type"):
        return error(E.E_NOT_AN_OBJECT, path, f"{field!r} must be an object.", given)
    if kind == "union_tag_invalid":
        tag = given.get("kind") if isinstance(given, dict) else given
        return error(
            E.E_NOT_IN_ENUM, f"{path}/kind",
            f"Unknown kind {tag!r}. Allowed: {ctx.get('expected_tags', '')}.",
            tag,
        )
    if kind == "union_tag_not_found":
        return error(
            E.E_MISSING_FIELD, path,
            "This object needs a 'kind' field naming which variant it is.",
            given,
        )
    if kind == "value_error":
        message = str(ctx.get("error", err.get("msg", "invalid value")))
        # Validators tag their message with `[CODE]` via models.coded(), so the mapper
        # reads the code rather than guessing it from the wording.
        tagged = _CODED.match(message)
        if tagged:
            return error(tagged.group(1), path, tagged.group(2).strip(), given)
        return error(
            E.E_OUT_OF_RANGE, path, message[0].upper() + message[1:].rstrip(".") + ".", given
        )

    return error(
        E.E_WRONG_TYPE, path,
        err.get("msg", "Invalid value.").rstrip(".") + ".",
        given,
    )


# --------------------------------------------------------------------------------------
# Semantic rules
# --------------------------------------------------------------------------------------


def _check_condition(cond: Condition, path: str, out: ValidationResult) -> None:
    right = cond.right
    is_range = isinstance(right, RangeOperand)
    is_constant = isinstance(right, ConstantOperand)
    is_series = not (is_range or is_constant)

    if cond.op in SERIES_COMPARISONS and not is_series:
        out.errors.append(error(
            E.E_COMPARISON_NEEDS_SERIES, f"{path}/right",
            f"{cond.op!r} compares one series against another, but the right side is "
            f"{_article(right.kind)}. Use 'greater_than'/'less_than' to compare against a fixed "
            f"number, or put a price field or indicator on the right.",
            right.model_dump(mode="json"),
        ))
    if cond.op in CONSTANT_COMPARISONS and not is_constant:
        out.errors.append(error(
            E.E_COMPARISON_NEEDS_CONSTANT, f"{path}/right",
            f"{cond.op!r} compares against a fixed number, but the right side is "
            f"{_article(right.kind)}. Use 'above'/'below' to compare against another series, or "
            f"replace the right side with {{\"kind\": \"constant\", \"value\": ...}}.",
            right.model_dump(mode="json"),
        ))
    if cond.op in RANGE_COMPARISONS and not is_range:
        out.errors.append(error(
            E.E_COMPARISON_NEEDS_RANGE, f"{path}/right",
            "'between' needs a range on the right: "
            "{\"kind\": \"range\", \"low\": ..., \"high\": ...}.",
            right.model_dump(mode="json"),
        ))
    if is_range and cond.op not in RANGE_COMPARISONS:
        out.errors.append(error(
            E.E_RANGE_NOT_ALLOWED, f"{path}/right",
            f"A range is only meaningful with 'between'; {cond.op!r} takes a single value.",
            right.model_dump(mode="json"),
        ))
    if is_range and right.low >= right.high:
        out.errors.append(error(
            E.E_RANGE_BOUNDS, f"{path}/right",
            f"Range low ({right.low:g}) must be below high ({right.high:g}).",
            right.model_dump(mode="json"),
        ))

    if is_series and cond.left.unit != right.unit:
        out.errors.append(error(
            E.E_UNIT_MISMATCH, path,
            f"Cannot compare {cond.left.render()} ({cond.left.unit}) with "
            f"{right.render()} ({right.unit}). Comparing across units is almost always a "
            f"mistake; to relate a price to a volume, compare each to its own average.",
            {"left": cond.left.unit, "right": right.unit},
        ))
    if is_series and cond.left.model_dump() == right.model_dump():
        out.errors.append(error(
            E.E_SELF_COMPARISON, path,
            "Both sides of this comparison are the same series, so it is always true or "
            "always false.",
            cond.left.model_dump(mode="json"),
        ))

    # A price compared to a bare number is scale-dependent: "close greater than 50"
    # means something entirely different for a $3 stock and a $3,000 one.
    if cond.left.unit == "price" and (is_constant or is_range):
        out.warnings.append(warning(
            E.W_PRICE_VS_CONSTANT, path,
            f"{cond.left.render()} is a price compared against a fixed number, so this "
            f"rule means different things for different symbols and will drift as prices "
            f"rise. Prefer 'pct_change', or compare against a moving average.",
            right.model_dump(mode="json"),
        ))


def _check_group(group: ConditionGroup | SignalExit, path: str, out: ValidationResult) -> None:
    seen: list[dict[str, Any]] = []
    for i, cond in enumerate(group.conditions):
        _check_condition(cond, f"{path}/conditions/{i}", out)
        dumped = cond.model_dump(mode="json")
        if dumped in seen:
            out.warnings.append(warning(
                E.W_DUPLICATE_CONDITION, f"{path}/conditions/{i}",
                "This condition is a duplicate of an earlier one in the same group and "
                "has no effect.",
                dumped,
            ))
        seen.append(dumped)


def _check_exits(strategy: Strategy, out: ValidationResult) -> None:
    kinds = [e.kind for e in strategy.exits]

    for kind in set(kinds):
        if kinds.count(kind) > 1:
            out.errors.append(error(
                E.E_DUPLICATE_EXIT, "/exits",
                f"{kinds.count(kind)} exits of kind {kind!r}. Each exit kind may appear "
                f"once: two of the same kind would need a tie-break rule that the DSL "
                f"deliberately does not have. Combine them into one, or use the other "
                f"stop family (a fixed and a trailing stop are the same kind).",
                kind,
            ))

    if "signal_exit" in kinds and "opposite_signal" in kinds:
        out.errors.append(error(
            E.E_CONFLICTING_SIGNAL_EXITS, "/exits",
            "'signal_exit' and 'opposite_signal' are two ways to spell the same slot and "
            "share an evaluation priority. Keep the explicit 'signal_exit' if the exit "
            "rule differs from the entry rule, otherwise keep 'opposite_signal'.",
        ))

    stops = [e for e in strategy.exits if e.kind in STOP_KINDS]
    r_targets = [e for e in strategy.exits if e.kind == "r_multiple_target"]
    if r_targets and not stops:
        out.errors.append(error(
            E.E_R_MULTIPLE_NEEDS_STOP, "/exits",
            "An R-multiple target measures profit in units of the initial risk, and this "
            "strategy has no stop, so 1R is undefined. Add an 'atr_stop' or a "
            "'percent_stop', or use 'percent_target' instead.",
        ))
    if r_targets and len(stops) > 1:
        out.errors.append(error(
            E.E_R_MULTIPLE_AMBIGUOUS, "/exits",
            f"An R-multiple target needs exactly one stop to measure 1R from; this "
            f"strategy has {len(stops)} ({', '.join(s.kind for s in stops)}). Remove one, "
            f"or switch to 'percent_target'.",
            [s.kind for s in stops],
        ))

    if not stops:
        out.warnings.append(warning(
            E.W_NO_STOP, "/exits",
            "No stop loss. Losses on any single trade are bounded only by the other exit "
            "rules, so the drawdown this strategy can produce is not something the "
            "document states.",
        ))

    fixed = next((e for e in strategy.exits if e.kind == "percent_stop" and not e.trail), None)
    percent_target = next((e for e in strategy.exits if e.kind == "percent_target"), None)
    if fixed and percent_target and percent_target.percent < fixed.percent:
        out.warnings.append(warning(
            E.W_TARGET_INSIDE_STOP, "/exits",
            f"The target ({percent_target.percent:g}%) is nearer than the stop "
            f"({fixed.percent:g}%), so each win is smaller than each loss. That can still "
            f"be profitable, but only with a high win rate - check the trade statistics "
            f"before trusting it.",
        ))

    trailing = [e for e in strategy.exits if getattr(e, "trail", False)]
    if trailing and len(stops) > 1:
        out.warnings.append(warning(
            E.W_TRAILING_AND_FIXED, "/exits",
            "A fixed and a trailing stop are both present. The tighter of the two binds "
            "on every bar, which is well defined but easy to misread when reviewing "
            "trades.",
        ))


def _check_sizing(strategy: Strategy, out: ValidationResult) -> None:
    sizing = strategy.sizing
    required = SIZING_PARAMS[sizing.method]
    for name in required:
        if getattr(sizing, name) is None:
            out.errors.append(error(
                E.E_SIZING_MISSING_PARAM, f"/sizing/{name}",
                f"Sizing method {sizing.method!r} requires {name!r}.",
            ))
    for name in ALL_SIZING_PARAMS:
        if name not in required and getattr(sizing, name) is not None:
            out.errors.append(error(
                E.E_SIZING_UNEXPECTED_PARAM, f"/sizing/{name}",
                f"{name!r} does not apply to sizing method {sizing.method!r}. Leaving it "
                f"in would suggest it has an effect that it does not.",
                getattr(sizing, name),
            ))

    if sizing.method == "fixed_fraction" and sizing.fraction is not None:
        total = sizing.fraction * sizing.max_open_positions
        if total > 1.0 + 1e-9:
            out.errors.append(error(
                E.E_SIZING_OVER_ALLOCATION, "/sizing",
                f"{sizing.fraction:.1%} per position across {sizing.max_open_positions} "
                f"slots commits {total:.0%} of equity. The DSL does not model leverage, so "
                f"reduce the fraction to at most "
                f"{1 / sizing.max_open_positions:.1%} or lower max_open_positions.",
                {"fraction": sizing.fraction, "max_open_positions": sizing.max_open_positions},
            ))

    if sizing.method == "atr_risk":
        atr_stop = next((e for e in strategy.exits if e.kind == "atr_stop"), None)
        if atr_stop is not None and (
            atr_stop.atr_period != sizing.atr_period
            or abs(atr_stop.multiple - (sizing.atr_multiple or 0)) > 1e-9
        ):
            out.warnings.append(warning(
                E.W_SIZING_STOP_MISMATCH, "/sizing",
                f"Sizing risks {sizing.atr_multiple:g}x ATR({sizing.atr_period}) but the "
                f"stop sits at {atr_stop.multiple:g}x ATR({atr_stop.atr_period}). The "
                f"amount risked is then not the amount that can be lost, and 1R (which is "
                f"measured from the stop) will not equal the sizing risk.",
            ))


def _check_costs(strategy: Strategy, out: ValidationResult) -> None:
    costs = strategy.costs
    required = SLIPPAGE_PARAMS[costs.slippage_model]
    for name in required:
        if getattr(costs, name) is None:
            out.errors.append(error(
                E.E_SLIPPAGE_MISSING_PARAM, f"/costs/{name}",
                f"Slippage model {costs.slippage_model!r} requires {name!r}.",
            ))
    for name in ALL_SLIPPAGE_PARAMS:
        if name not in required and getattr(costs, name) is not None:
            out.errors.append(error(
                E.E_SLIPPAGE_UNEXPECTED_PARAM, f"/costs/{name}",
                f"{name!r} does not apply to slippage model {costs.slippage_model!r}.",
                getattr(costs, name),
            ))

    slippage_is_zero = costs.slippage_model == "fixed_bps" and not costs.slippage_bps
    if costs.commission_bps == 0 or slippage_is_zero:
        out.warnings.append(warning(
            E.W_ZERO_COSTS, "/costs",
            "Zero commission or zero slippage. Frictionless results are useful as an "
            "upper bound and misleading as anything else.",
            {"commission_bps": costs.commission_bps, "slippage_bps": costs.slippage_bps},
        ))


def _check_execution(strategy: Strategy, out: ValidationResult) -> None:
    if strategy.execution.intrabar_priority == "target_first":
        out.warnings.append(warning(
            E.W_TARGET_FIRST, "/execution/intrabar_priority",
            "Assuming the target fills before the stop when a single bar covers both "
            "flatters every result, and a daily bar contains no evidence for it. "
            "'stop_first' is the honest default.",
        ))
    if strategy.execution.intrabar_priority == "target_first" and not any(
        e.kind in TARGET_KINDS for e in strategy.exits
    ):
        out.warnings.append(warning(
            E.W_INERT_INTRABAR_PRIORITY, "/execution/intrabar_priority",
            "'target_first' decides which of a stop and a target fills when one bar "
            "covers both, but this strategy has no target exit, so the setting can never "
            "apply. Remove it, or add a target if one was intended.",
        ))
    if strategy.execution.gap_policy == "fill_at_level":
        out.warnings.append(warning(
            E.W_FILL_AT_LEVEL, "/execution/gap_policy",
            "Filling at the stop level after the market gapped through it assumes a fill "
            "at a price that never traded. 'fill_at_open' is what actually happens.",
        ))


def _check_universe_and_calendar(strategy: Strategy, out: ValidationResult) -> None:
    if len(strategy.universe.symbols) < 3:
        out.warnings.append(warning(
            E.W_NARROW_UNIVERSE, "/universe/symbols",
            f"{len(strategy.universe.symbols)} symbol(s). A rule tested on one or two "
            f"hand-picked names is a statement about those names, not about the rule.",
            strategy.universe.symbols,
        ))
    date_range = strategy.calendar.date_range
    if date_range is not None and date_range.start >= date_range.end:
        out.errors.append(error(
            E.E_CALENDAR_DATE_ORDER, "/calendar/date_range",
            f"Start ({date_range.start}) must be before end ({date_range.end}).",
            {"start": str(date_range.start), "end": str(date_range.end)},
        ))


# --------------------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------------------


def validate(document: Any) -> ValidationResult:
    """Validate a decoded JSON document. Never raises: everything is an Issue."""
    out = ValidationResult()

    if not isinstance(document, dict):
        out.errors.append(error(
            E.E_NOT_AN_OBJECT, "",
            f"A strategy must be a JSON object, not a {type(document).__name__}.",
        ))
        return out

    declared = document.get("dsl_version")
    if declared is not None and declared != DSL_VERSION:
        out.errors.append(error(
            E.E_VERSION, "/dsl_version",
            f"This document declares dsl_version {declared!r}, but this validator "
            f"implements {DSL_VERSION}. Versions are not interchangeable.",
            declared,
        ))
        return out

    try:
        strategy = Strategy.model_validate(document)
    except ValidationError as exc:
        out.errors.extend(_describe_schema_error(e) for e in exc.errors())
        return out

    out.strategy = strategy
    _check_group(strategy.entry, "/entry", out)
    for i, rule in enumerate(strategy.exits):
        if isinstance(rule, SignalExit):
            _check_group(rule, f"/exits/{i}", out)
    _check_exits(strategy, out)
    _check_sizing(strategy, out)
    _check_costs(strategy, out)
    _check_execution(strategy, out)
    _check_universe_and_calendar(strategy, out)

    if out.errors:
        out.strategy = None
    return out


def validate_json(text: str) -> ValidationResult:
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        out = ValidationResult()
        out.errors.append(error(
            E.E_NOT_AN_OBJECT, "",
            f"The document is not valid JSON: {exc.msg} at line {exc.lineno}, "
            f"column {exc.colno}.",
        ))
        return out
    return validate(document)


def validate_file(path: str | Path) -> ValidationResult:
    return validate_json(Path(path).read_text())
