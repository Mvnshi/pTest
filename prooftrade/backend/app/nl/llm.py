"""Optional LLM translator.

The security boundary is the whole point of this file: **the model's only job is to
fill in a JSON object, and nothing it returns is trusted until Pydantic has validated
it into a `Strategy`.** There is no code generation, no expression string, no `eval`.
A malformed or adversarial response is a hard reject - the caller falls back to the
deterministic rule-based parser rather than trying to repair it.

Disabled by default. Enable with `PROOFTRADE_LLM=anthropic` and an API key in the
environment. The demo never needs it; it exists to show the architecture is the same
either way, because the DSL is the contract in both cases.
"""

from __future__ import annotations

import json
import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..dsl import (
    INDICATOR_SPECS,
    Condition,
    ConditionGroup,
    ConstantOperand,
    IndicatorOperand,
    PositionRules,
    RiskRules,
    Strategy,
)
from .rules import Assumption, ParseResult, TranslationError, translate as rules_translate

MODEL = os.environ.get("PROOFTRADE_LLM_MODEL", "claude-opus-5")
MAX_TOKENS = 4000


class LLMUnavailable(RuntimeError):
    """The adapter is not configured, or the call failed. Always recoverable."""


# --------------------------------------------------------------------------------------
# A deliberately flat draft schema. Flat because structured outputs work best without
# $ref indirection, and because a smaller surface is a smaller thing to validate.
# --------------------------------------------------------------------------------------


class DraftOperand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["indicator", "constant"]
    name: str | None = Field(default=None, description="Indicator name; null for a constant")
    period: int | None = None
    k: float | None = None
    fast: int | None = None
    slow: int | None = None
    signal: int | None = None
    value: float | None = Field(default=None, description="Numeric value; null for an indicator")


class DraftCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left: DraftOperand
    op: Literal["<", "<=", ">", ">=", "crosses_above", "crosses_below"]
    right: DraftOperand


class DraftStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    direction: Literal["long", "short"] = "long"
    universe: list[str] = Field(default_factory=list)
    max_positions: int = 5
    entry_logic: Literal["all", "any"] = "all"
    entry: list[DraftCondition]
    exit_logic: Literal["all", "any"] = "any"
    exit: list[DraftCondition] = Field(default_factory=list)
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    trailing_stop_pct: float | None = None
    max_holding_days: int | None = None
    assumptions: list[str] = Field(default_factory=list)
    unparsed: list[str] = Field(default_factory=list)
    confidence: float = 0.8


def _system_prompt(symbols: list[str]) -> str:
    registry = "\n".join(
        f"  {spec.name}: {spec.description} params={list(spec.params) or 'none'}"
        for spec in INDICATOR_SPECS.values()
    )
    return (
        "You translate a plain-English description of a stock trading strategy into a "
        "strict JSON object. You are a parser, not an adviser.\n\n"
        "Rules you must follow:\n"
        "1. Use ONLY the indicators listed below. Never invent an indicator name.\n"
        "2. Never output code, expressions, or formulae - only the fields in the schema.\n"
        "3. Do not improve the strategy. Translate exactly what was written. If the user "
        "did not specify something, leave it null and say so in `assumptions`.\n"
        "4. Put any part of the request you could not represent into `unparsed`.\n"
        "5. `universe` must be a subset of the allowed tickers, or empty to mean all.\n"
        "6. Set `confidence` honestly: 1.0 for an unambiguous instruction, below 0.5 when "
        "you had to guess.\n\n"
        f"Allowed tickers: {', '.join(symbols)}\n\n"
        f"Available indicators:\n{registry}\n"
    )


def _to_operand(draft: DraftOperand):
    if draft.kind == "constant":
        if draft.value is None:
            raise TranslationError("constant operand with no value")
        return ConstantOperand(value=draft.value)
    if not draft.name or draft.name not in INDICATOR_SPECS:
        raise TranslationError(f"unknown indicator {draft.name!r}")
    fields = {
        f: getattr(draft, f)
        for f in ("period", "k", "fast", "slow", "signal")
        if getattr(draft, f) is not None
    }
    return IndicatorOperand(name=draft.name, **fields)


def _to_strategy(draft: DraftStrategy, text: str, symbols: list[str]) -> Strategy:
    universe = [s for s in draft.universe if s in symbols] or sorted(symbols)
    entry = ConditionGroup(
        logic=draft.entry_logic,
        conditions=[
            Condition(left=_to_operand(c.left), op=c.op, right=_to_operand(c.right))
            for c in draft.entry[:6]
        ],
    )
    exit_group = ConditionGroup(
        logic="any",
        conditions=[
            Condition(left=_to_operand(c.left), op=c.op, right=_to_operand(c.right))
            for c in draft.exit[:6]
        ],
    )
    return Strategy(
        name=draft.name[:120] or "Untitled strategy",
        source_text=text[:4000],
        universe=universe,
        position=PositionRules(direction=draft.direction,
                               max_positions=max(1, min(draft.max_positions, 20))),
        entry=entry,
        exit=exit_group,
        risk=RiskRules(
            stop_loss_pct=draft.stop_loss_pct,
            take_profit_pct=draft.take_profit_pct,
            trailing_stop_pct=draft.trailing_stop_pct,
            max_holding_days=draft.max_holding_days,
        ),
    )


def available() -> bool:
    if os.environ.get("PROOFTRADE_LLM", "rules").lower() != "anthropic":
        return False
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def translate(
    text: str, available_symbols: list[str], answers: dict[str, str] | None = None
) -> ParseResult:
    """LLM translation with a deterministic fallback.

    The rule-based parser runs first regardless: it supplies the clarification
    questions, and it is the answer if the model is unavailable or returns anything
    that does not validate.
    """
    fallback: ParseResult | None = None
    try:
        fallback = rules_translate(text, available_symbols, answers)
    except TranslationError:
        fallback = None

    if not available():
        if fallback is None:
            raise TranslationError(
                "Could not parse that. The LLM translator is not configured, so only the "
                "built-in vocabulary is available."
            )
        return fallback

    try:
        import anthropic

        client = anthropic.Anthropic()
        response = client.messages.parse(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=_system_prompt(available_symbols),
            messages=[{"role": "user", "content": text[:4000]}],
            output_format=DraftStrategy,
        )
        draft = response.parsed_output
        if draft is None:
            raise LLMUnavailable("model returned no parsable output")
        strategy = _to_strategy(draft, text, available_symbols)
    except (ValidationError, TranslationError, LLMUnavailable, json.JSONDecodeError) as exc:
        # A rejected translation is a normal outcome, not an error path to paper over.
        if fallback is None:
            raise TranslationError(f"The model's output failed validation: {exc}") from exc
        fallback.notes.append(
            f"The LLM translation was rejected by schema validation ({type(exc).__name__}); "
            "the deterministic parser's result is shown instead."
        )
        return fallback
    except Exception as exc:  # noqa: BLE001 - network/SDK failures must not break the demo
        if fallback is None:
            raise TranslationError(f"The LLM translator failed: {exc}") from exc
        fallback.notes.append(f"The LLM translator was unreachable ({type(exc).__name__}).")
        return fallback

    return ParseResult(
        strategy=strategy,
        questions=fallback.questions if fallback else [],
        assumptions=[Assumption("llm", a) for a in draft.assumptions],
        confidence=max(0.05, min(1.0, draft.confidence)),
        unparsed=draft.unparsed,
        translator="anthropic",
        notes=["Translated by the model, then validated against the typed DSL. Any field "
               "that failed validation would have rejected the whole translation."],
    )
