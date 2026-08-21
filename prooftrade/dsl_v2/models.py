"""ProofTrade Strategy DSL v2.0.0 - typed models.

This module is the single source of truth for the DSL. `strategy.schema.json` is
generated from it and `strategy.ts` mirrors it; tests assert all three agree.

Four properties are held above expressiveness:

* **Small.** Eight indicators, seven comparisons, six exit types, four sizing methods.
  Every enum is closed. Adding a case is a version bump, not a config change.
* **Explainable.** Every node renders to one English clause, so a parsed strategy can
  be shown back to a non-technical user as prose rather than as JSON.
* **Deterministic.** Execution assumptions - fill timing, intrabar priority, gap
  handling, candidate tie-breaks - are fields in the document, not conventions buried
  in an engine. Two engines reading the same document must produce the same trades.
* **Safe.** A strategy is data. There is no expression string, no formula field, no
  callback, nothing an evaluator would need `eval` to interpret. Every number is
  bounded, every list is length-capped, and unknown fields are rejected rather than
  ignored.

Not supported, deliberately: nested boolean logic, intraday bars, multi-leg or options
positions, pyramiding, cross-symbol conditions, and parameter optimisation.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Annotated, ClassVar, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

DSL_VERSION = "2.0.0"

MIN_PERIOD = 1
MAX_PERIOD = 500
MAX_CONDITIONS = 8
MAX_SYMBOLS = 50
MAX_EXITS = 6

_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def coded(code: str, message: str) -> str:
    """Tag a validator message with its catalogue code.

    Pydantic flattens a validator's `ValueError` into an untyped `value_error`, so
    without this the error mapper would have to guess the code by sniffing the message
    text. The `[CODE]` prefix is stripped before the message reaches a reader.
    """
    return f"[{code}] {message}"


class _Node(BaseModel):
    """Base for every DSL node: unknown fields are an error, not a shrug."""

    model_config = ConfigDict(extra="forbid")


# ======================================================================================
# Units
# ======================================================================================
# Comparing an RSI reading to a dollar price is meaningless. Every series operand
# carries a unit and the validator refuses to compare across incompatible ones.

Unit = Literal["price", "volume", "oscillator", "percent"]


# ======================================================================================
# Operands
# ======================================================================================

PriceField = Literal["open", "high", "low", "close", "volume"]

IndicatorName = Literal[
    "sma",            # simple moving average of close
    "ema",            # exponential moving average of close
    "rsi",            # Wilder's relative strength index, 0-100
    "atr",            # Wilder's average true range, in price units
    "highest_high",   # highest high of the PRIOR `period` bars
    "lowest_low",     # lowest low of the PRIOR `period` bars
    "pct_change",     # percent change of close over `period` bars
    "volume_sma",     # simple moving average of volume
]

INDICATOR_UNITS: dict[str, Unit] = {
    "sma": "price",
    "ema": "price",
    "rsi": "oscillator",
    "atr": "price",
    "highest_high": "price",
    "lowest_low": "price",
    "pct_change": "percent",
    "volume_sma": "volume",
}

# Indicators whose value on bar t must not include bar t itself.
#
# ENGINE-FACING CONTRACT, INTENTIONALLY NOT ENFORCEABLE HERE. This package validates
# documents; it never computes an indicator, so it has nothing to check this against.
# It is exported so an engine implementer has one authoritative place to read the rule
# rather than inferring it from the indicator's name: `highest_high(20)` on bar t is the
# highest high of bars t-20..t-1, EXCLUDING bar t. A breakout reference that included
# today's own high could never be broken, which would make every `crosses_above` against
# it silently dead. A conforming engine must exclude the current bar; a document cannot
# express anything that would violate it.
EXCLUDES_CURRENT_BAR = frozenset({"highest_high", "lowest_low"})

# Minimum period per indicator, enforced by IndicatorOperand below and mirrored into the
# generated JSON Schema so a non-Python consumer rejects the same documents.
#
# Two is the floor for anything that averages, smooths or spans a window: SMA(1) is just
# the close, RSI(1) is a constant 0 or 100, ATR(1) is the bar's own true range, and a
# one-bar highest high is the bar's own high. Those are not useful readings, they are
# ways of writing something else by accident, and a DSL that promises to be explainable
# should not accept a rule whose plain-English reading is a lie.
#
# `pct_change` is the exception: a one-bar percent change is the daily return, which is
# both meaningful and commonly wanted.
DEFAULT_MIN_INDICATOR_PERIOD = 2
MIN_PERIOD_BY_INDICATOR: dict[str, int] = {
    "sma": 2,
    "ema": 2,
    "rsi": 2,
    "atr": 2,
    "highest_high": 2,
    "lowest_low": 2,
    "pct_change": 1,
    "volume_sma": 2,
}

# What a one-period reading would degenerate into, quoted back in the error so the
# message explains the rule instead of merely asserting it.
_DEGENERATE_AT_ONE: dict[str, str] = {
    "sma": "the close itself",
    "ema": "the close itself",
    "rsi": "a constant 0 or 100",
    "atr": "the bar's own true range",
    "highest_high": "the bar's own high",
    "lowest_low": "the bar's own low",
    "volume_sma": "the bar's own volume",
}


class PriceOperand(_Node):
    """A raw field of the current bar."""

    kind: Literal["price"] = "price"
    field: PriceField

    @property
    def unit(self) -> Unit:
        return "volume" if self.field == "volume" else "price"

    def render(self) -> str:
        return self.field


class IndicatorOperand(_Node):
    """One indicator reading on the current bar."""

    kind: Literal["indicator"] = "indicator"
    name: IndicatorName
    period: int = Field(ge=MIN_PERIOD, le=MAX_PERIOD)

    @model_validator(mode="after")
    def _period_is_meaningful(self) -> "IndicatorOperand":
        minimum = MIN_PERIOD_BY_INDICATOR.get(self.name, DEFAULT_MIN_INDICATOR_PERIOD)
        if self.period >= minimum:
            return self
        parts = [
            f"{self.name} needs a period of at least {minimum}, not {self.period}."
        ]
        if self.name in _DEGENERATE_AT_ONE:
            parts.append(
                f"At {self.period} it is {_DEGENERATE_AT_ONE[self.name]} written the long way."
            )
        if self.name in ("sma", "ema"):
            parts.append('Use {"kind": "price", "field": "close"} if that is what you meant.')
        raise ValueError(coded("E_PERIOD_TOO_SHORT", " ".join(parts)))

    @property
    def unit(self) -> Unit:
        return INDICATOR_UNITS[self.name]

    def render(self) -> str:
        pretty = {
            "sma": "SMA", "ema": "EMA", "rsi": "RSI", "atr": "ATR",
            "highest_high": "highest high", "lowest_low": "lowest low",
            "pct_change": "% change", "volume_sma": "average volume",
        }[self.name]
        if self.name in ("highest_high", "lowest_low"):
            return f"the {self.period}-bar {pretty}"
        if self.name == "pct_change":
            return f"the {self.period}-bar {pretty}"
        return f"{pretty}({self.period})"


class ConstantOperand(_Node):
    """A fixed number: the 30 in "RSI less than 30"."""

    kind: Literal["constant"] = "constant"
    value: float = Field(ge=-1e12, le=1e12)

    def render(self) -> str:
        return f"{self.value:g}"


class RangeOperand(_Node):
    """An inclusive numeric interval, used only by the `between` comparison."""

    kind: Literal["range"] = "range"
    low: float = Field(ge=-1e12, le=1e12)
    high: float = Field(ge=-1e12, le=1e12)

    def render(self) -> str:
        return f"{self.low:g} and {self.high:g}"


# The left side of a comparison must be a series: a constant cannot cross anything, and
# "30 above RSI" is a way of writing a condition backwards rather than a new one.
SeriesOperand = Annotated[
    Union[PriceOperand, IndicatorOperand], Field(discriminator="kind")
]
RightOperand = Annotated[
    Union[PriceOperand, IndicatorOperand, ConstantOperand, RangeOperand],
    Field(discriminator="kind"),
]


# ======================================================================================
# Comparisons
# ======================================================================================
# `above`/`below` and `greater_than`/`less_than` are NOT synonyms. The pair you use
# declares what you are comparing against, and the validator enforces it:
#
#   above / below                 -> the right side must be another SERIES
#                                    ("close above SMA(200)")
#   greater_than / less_than      -> the right side must be a CONSTANT
#                                    ("RSI greater than 70")
#   crosses_above / crosses_below -> the right side must be a SERIES; both sides must
#                                    be defined on the previous bar
#   between                       -> the right side must be a RANGE
#
# The redundancy in the vocabulary is spent on catching mistakes, not on aliases.

Comparison = Literal[
    "above", "below",
    "crosses_above", "crosses_below",
    "greater_than", "less_than",
    "between",
]

SERIES_COMPARISONS = frozenset({"above", "below", "crosses_above", "crosses_below"})
CONSTANT_COMPARISONS = frozenset({"greater_than", "less_than"})
RANGE_COMPARISONS = frozenset({"between"})

# Used by the `opposite_signal` exit. Each comparison maps to its mirror, so "exit on
# the opposite signal" has one unambiguous meaning instead of a negation whose truth
# value would flip on almost every bar.
MIRROR: dict[str, str] = {
    "above": "below",
    "below": "above",
    "crosses_above": "crosses_below",
    "crosses_below": "crosses_above",
    "greater_than": "less_than",
    "less_than": "greater_than",
    "between": "between",   # mirrored as "outside", see docs
}

_PROSE: dict[str, str] = {
    "above": "is above",
    "below": "is below",
    "crosses_above": "crosses above",
    "crosses_below": "crosses below",
    "greater_than": "is greater than",
    "less_than": "is less than",
    "between": "is between",
}


class Condition(_Node):
    """`left <comparison> right`, evaluated on the close of each bar, per symbol."""

    left: SeriesOperand
    op: Comparison
    right: RightOperand

    def render(self) -> str:
        return f"{self.left.render()} {_PROSE[self.op]} {self.right.render()}"


class ConditionGroup(_Node):
    """A flat conjunction or disjunction.

    Flat on purpose. A nested boolean tree needs a nested editor to author, a nested
    renderer to explain, and a reader who can hold precedence in their head. Two levels
    of `all`/`any` covers every strategy in the example set and every one a demo user
    has typed. Anything more belongs in code, not in a DSL that promises to be readable.
    """

    logic: Literal["all", "any"] = "all"
    conditions: list[Condition] = Field(min_length=1, max_length=MAX_CONDITIONS)

    def render(self) -> str:
        joiner = " AND " if self.logic == "all" else " OR "
        return joiner.join(c.render() for c in self.conditions)


# ======================================================================================
# Exits
# ======================================================================================
# Exits are a list of typed rules, not a bag of optional fields, so "this strategy has
# an ATR stop and a 2R target" is visible in the shape of the document.
#
# When several fire on the same bar the winner is fixed by EXIT_PRIORITY below, not by
# list order - reordering the array can never change a backtest.

ExitKind = Literal[
    "atr_stop", "percent_stop",
    "r_multiple_target", "percent_target",
    "time_exit",
    "signal_exit", "opposite_signal",
]

# Base precedence between exit KINDS. Lower number wins.
#
# This table is the default only. The stop-versus-target pair is additionally governed
# by `execution.intrabar_priority`, and a document that sets `target_first` overrides
# these two bands for itself. Do not read this table directly when implementing an
# engine - call `Strategy.effective_exit_priority()`, which folds the two rules into one
# answer. See Strategy.exits_in_priority_order.
EXIT_PRIORITY: dict[str, int] = {
    "atr_stop": 0,
    "percent_stop": 0,
    "r_multiple_target": 1,
    "percent_target": 1,
    "time_exit": 2,
    "signal_exit": 3,
    "opposite_signal": 3,
}

STOP_KINDS = frozenset({"atr_stop", "percent_stop"})
TARGET_KINDS = frozenset({"r_multiple_target", "percent_target"})
SIGNAL_KINDS = frozenset({"signal_exit", "opposite_signal"})


class AtrStop(_Node):
    """Stop placed `multiple` x ATR(period) away from entry.

    With `trail: true` the stop follows the best price reached since entry (a
    Chandelier stop) and never moves against the position.
    """

    kind: Literal["atr_stop"] = "atr_stop"
    atr_period: int = Field(default=14, ge=2, le=MAX_PERIOD)
    multiple: float = Field(default=2.0, gt=0, le=20)
    trail: bool = False

    def render(self) -> str:
        what = "trailing ATR stop" if self.trail else "ATR stop"
        return f"{what} at {self.multiple:g}x ATR({self.atr_period})"


class PercentStop(_Node):
    """Stop `percent` away from entry (or from the best price, when trailing)."""

    kind: Literal["percent_stop"] = "percent_stop"
    percent: float = Field(gt=0, le=90)
    trail: bool = False

    def render(self) -> str:
        what = "trailing stop" if self.trail else "stop loss"
        return f"{what} at {self.percent:g}%"


class RMultipleTarget(_Node):
    """Target at `multiple` x the initial risk.

    1R is the distance from the entry fill to the INITIAL stop level - not to a trailing
    stop, and not to the sizing model's risk estimate. A strategy therefore cannot
    declare an R-multiple target without declaring exactly one stop to measure R from.
    """

    kind: Literal["r_multiple_target"] = "r_multiple_target"
    multiple: float = Field(gt=0, le=20)

    def render(self) -> str:
        return f"take profit at {self.multiple:g}R"


class PercentTarget(_Node):
    kind: Literal["percent_target"] = "percent_target"
    percent: float = Field(gt=0, le=1000)

    def render(self) -> str:
        return f"take profit at {self.percent:g}%"


class TimeExit(_Node):
    """Maximum holding period, in bars held.

    The exit is *signalled* on the close of the `max_bars`-th bar and *fills* under the
    document's `execution.fill_timing`, so a position lives one bar longer than
    `max_bars` when filling at the next open. Stated here because off-by-one in holding
    period is the most common silent disagreement between two backtesters.
    """

    kind: Literal["time_exit"] = "time_exit"
    max_bars: int = Field(ge=1, le=2000)

    def render(self) -> str:
        return f"time exit after {self.max_bars} bars"


class SignalExit(_Node):
    """An explicit exit rule set, evaluated like the entry group."""

    kind: Literal["signal_exit"] = "signal_exit"
    logic: Literal["all", "any"] = "any"
    conditions: list[Condition] = Field(min_length=1, max_length=MAX_CONDITIONS)

    def render(self) -> str:
        joiner = " AND " if self.logic == "all" else " OR "
        return "exit when " + joiner.join(c.render() for c in self.conditions)


class OppositeSignal(_Node):
    """Exit when the mirror of the entry rule fires.

    Defined as the entry group with every comparison replaced by its MIRROR (see
    `MIRROR`), keeping the same logic. It is deliberately not "the entry rule is no
    longer true": the negation of `crosses_above` is true on almost every bar, so that
    reading would close a position the day after it opened, every time.
    """

    kind: Literal["opposite_signal"] = "opposite_signal"

    def render(self) -> str:
        return "exit on the opposite entry signal"


ExitRule = Annotated[
    Union[
        AtrStop, PercentStop, RMultipleTarget, PercentTarget,
        TimeExit, SignalExit, OppositeSignal,
    ],
    Field(discriminator="kind"),
]


# ======================================================================================
# Sizing
# ======================================================================================


class Sizing(_Node):
    """How much to buy, and how many positions may be open at once.

    `atr_risk` is the only method that ties size to volatility: it buys the number of
    shares whose loss at the stop equals `risk_fraction` of equity. It is what makes
    R-multiples comparable across symbols.
    """

    method: Literal["equal_weight", "fixed_fraction", "fixed_notional", "atr_risk"]
    max_open_positions: int = Field(default=5, ge=1, le=50)
    max_positions_per_symbol: Literal[1] = 1     # no pyramiding in v2
    allow_fractional_shares: bool = False

    # method-specific; exactly the ones for the chosen method must be present
    fraction: float | None = Field(default=None, gt=0, le=1)
    notional: float | None = Field(default=None, gt=0, le=1e9)
    risk_fraction: float | None = Field(default=None, gt=0, le=0.25)
    atr_period: int | None = Field(default=None, ge=2, le=MAX_PERIOD)
    atr_multiple: float | None = Field(default=None, gt=0, le=20)

    def render(self) -> str:
        cap = f", at most {self.max_open_positions} open positions"
        if self.method == "equal_weight":
            return f"equal weight across {self.max_open_positions} slots{cap}"
        if self.method == "fixed_fraction":
            return f"{self.fraction:.1%} of equity per position{cap}"
        if self.method == "fixed_notional":
            return f"{self.notional:,.0f} per position{cap}"
        return (
            f"risk {self.risk_fraction:.2%} of equity per trade, sized on "
            f"{self.atr_multiple:g}x ATR({self.atr_period}){cap}"
        )


SIZING_PARAMS: dict[str, tuple[str, ...]] = {
    "equal_weight": (),
    "fixed_fraction": ("fraction",),
    "fixed_notional": ("notional",),
    "atr_risk": ("risk_fraction", "atr_period", "atr_multiple"),
}
ALL_SIZING_PARAMS = ("fraction", "notional", "risk_fraction", "atr_period", "atr_multiple")


# ======================================================================================
# Universe, calendar
# ======================================================================================


class Liquidity(_Node):
    """A structural screen applied per symbol per bar, before any entry is considered.

    Dollar volume rather than share volume: a million shares of a $3 stock and a million
    shares of a $300 stock are not the same market.
    """

    min_avg_dollar_volume: float = Field(gt=0, le=1e12)
    lookback: int = Field(default=20, ge=2, le=MAX_PERIOD)

    def render(self) -> str:
        return (
            f"only symbols averaging over {self.min_avg_dollar_volume:,.0f} in dollar "
            f"volume over {self.lookback} bars"
        )


class Universe(_Node):
    symbols: list[str] = Field(min_length=1, max_length=MAX_SYMBOLS)
    liquidity: Liquidity | None = None

    @model_validator(mode="after")
    def _normalise(self) -> "Universe":
        seen: list[str] = []
        for raw in self.symbols:
            symbol = raw.strip().upper()
            if not _TICKER.match(symbol):
                raise ValueError(coded(
                    "E_BAD_TICKER",
                    f"Invalid ticker {raw!r}. Tickers are 1-10 characters, starting with "
                    f"a letter, using A-Z, 0-9, '.' and '-' only."))
            if symbol not in seen:
                seen.append(symbol)
        object.__setattr__(self, "symbols", sorted(seen))
        return self

    def render(self) -> str:
        base = ", ".join(self.symbols)
        return base if self.liquidity is None else f"{base} ({self.liquidity.render()})"


class DateRange(_Node):
    start: dt.date
    end: dt.date

    def render(self) -> str:
        return f"between {self.start.isoformat()} and {self.end.isoformat()}"


class Calendar(_Node):
    """Date and session filters.

    These gate ENTRIES ONLY. Exits are never calendar-gated: a filter that could block
    an exit would be able to trap a position in a stopped-out trade indefinitely, which
    is a way to lose money that no backtest should be able to hide.

    On daily bars a "session" is one trading day, so `days_of_week` is limited to
    Monday-Friday (1-5, ISO numbering).
    """

    date_range: DateRange | None = None
    days_of_week: list[int] | None = Field(default=None, min_length=1, max_length=5)
    months: list[int] | None = Field(default=None, min_length=1, max_length=12)

    @model_validator(mode="after")
    def _bounds(self) -> "Calendar":
        if self.days_of_week is not None:
            if any(not 1 <= d <= 5 for d in self.days_of_week):
                raise ValueError(coded(
                    "E_CALENDAR_WEEKDAY",
                    "days_of_week must be 1-5 (Mon-Fri). Daily bars have no weekend "
                    "session, so 6 and 7 could never match a bar."))
            object.__setattr__(self, "days_of_week", sorted(set(self.days_of_week)))
        if self.months is not None:
            if any(not 1 <= m <= 12 for m in self.months):
                raise ValueError(coded("E_CALENDAR_MONTH", "months must be 1-12."))
            object.__setattr__(self, "months", sorted(set(self.months)))
        return self

    def render(self) -> str:
        parts = []
        if self.date_range:
            parts.append(self.date_range.render())
        if self.days_of_week:
            names = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri"}
            parts.append("on " + ", ".join(names[d] for d in self.days_of_week))
        if self.months:
            parts.append("in months " + ", ".join(str(m) for m in self.months))
        return "; ".join(parts) if parts else "no calendar restriction"


# ======================================================================================
# Execution and costs
# ======================================================================================


class Execution(_Node):
    """Execution assumptions, stated in the document rather than assumed by the engine.

    Every field here changes the reported result. Making them explicit is what lets two
    people compare two backtests and know they are comparing the same thing.
    """

    # Signals are read from the close of bar t. There is no other option in v2: any
    # other value would permit same-bar execution, which is how backtests lie.
    signal_timing: Literal["close"] = "close"
    fill_timing: Literal["next_open", "next_close"] = "next_open"
    # When one bar's range covers both a stop and a target, which is assumed to have
    # filled. A daily bar records no intrabar path, so this is an assumption either way.
    #
    # `stop_first` (default) is the CONSERVATIVE assumption: it books the outcome that
    # hurts, and cannot flatter a result.
    #
    # `target_first` is the AGGRESSIVE assumption. It books the favourable outcome from
    # a bar that contains no evidence for it, and it improves every affected trade, so
    # it inflates win rate, profit factor and return together. It is offered because
    # some strategies genuinely fill the target first and refusing to model that would
    # be its own distortion - but a document that sets it is making a claim the data
    # cannot support, and the validator warns about it every time.
    #
    # Whichever is set, it governs the stop/target pair - see
    # Strategy.effective_exit_priority.
    intrabar_priority: Literal["stop_first", "target_first"] = "stop_first"
    # When a bar opens beyond a stop or target level, where the order fills.
    gap_policy: Literal["fill_at_open", "fill_at_level"] = "fill_at_open"
    # When more entry candidates fire than there are free slots.
    candidate_priority: Literal["alphabetical", "highest_dollar_volume"] = "alphabetical"

    def render(self) -> str:
        return (
            f"signal on the {self.signal_timing}, fill at the "
            f"{self.fill_timing.replace('_', ' ')}; {self.intrabar_priority.replace('_', ' ')} "
            f"inside a bar; gaps {self.gap_policy.replace('_', ' ')}"
        )


class Costs(_Node):
    """Transaction costs and slippage.

    Slippage moves the fill price against the position; commission is charged on the
    notional of both legs. `atr_fraction` slippage scales with the symbol's own
    volatility, which is closer to how real spreads behave than a flat number.
    """

    commission_bps: float = Field(default=1.0, ge=0, le=500)
    commission_min_usd: float = Field(default=0.0, ge=0, le=1000)
    slippage_model: Literal["fixed_bps", "atr_fraction"] = "fixed_bps"
    slippage_bps: float | None = Field(default=None, ge=0, le=500)
    slippage_atr_fraction: float | None = Field(default=None, gt=0, le=2)
    slippage_atr_period: int | None = Field(default=None, ge=2, le=MAX_PERIOD)

    # A model-specific parameter cannot carry a plain default: defaulting slippage_bps
    # would make it "unexpectedly present" on every atr_fraction document. It is filled
    # in only for the model it belongs to, and materialised on the parsed object so the
    # value is visible in the round-trip rather than hidden in the engine.
    DEFAULT_SLIPPAGE_BPS: ClassVar[float] = 5.0

    @model_validator(mode="after")
    def _default_for_chosen_model(self) -> "Costs":
        if self.slippage_model == "fixed_bps" and self.slippage_bps is None:
            object.__setattr__(self, "slippage_bps", self.DEFAULT_SLIPPAGE_BPS)
        return self

    def render(self) -> str:
        if self.slippage_model == "fixed_bps":
            slip = f"{self.slippage_bps:g} bps slippage"
        else:
            slip = (
                f"slippage of {self.slippage_atr_fraction:g}x "
                f"ATR({self.slippage_atr_period})"
            )
        return f"{self.commission_bps:g} bps commission per leg, {slip}"


SLIPPAGE_PARAMS: dict[str, tuple[str, ...]] = {
    "fixed_bps": ("slippage_bps",),
    "atr_fraction": ("slippage_atr_fraction", "slippage_atr_period"),
}
ALL_SLIPPAGE_PARAMS = ("slippage_bps", "slippage_atr_fraction", "slippage_atr_period")


# ======================================================================================
# Strategy
# ======================================================================================


class Strategy(_Node):
    """A complete, self-describing strategy.

    Self-describing means the document carries its own execution assumptions and cost
    model, so a result can be reproduced from the strategy alone. A runner may override
    costs to sweep them for sensitivity analysis, but the override has to be recorded
    alongside the result - the document is the default, not a suggestion.
    """

    dsl_version: Literal["2.0.0"] = DSL_VERSION
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    source_text: str = Field(default="", max_length=4000)

    universe: Universe
    direction: Literal["long", "short"] = "long"
    entry: ConditionGroup
    exits: list[ExitRule] = Field(min_length=1, max_length=MAX_EXITS)
    sizing: Sizing
    calendar: Calendar = Field(default_factory=Calendar)
    execution: Execution = Field(default_factory=Execution)
    costs: Costs = Field(default_factory=Costs)

    def effective_exit_priority(self) -> dict[str, int]:
        """Exit precedence for THIS document, with the intrabar rule already applied.

        Two rules govern which exit wins, and this folds them into one number per kind
        so that an engine never has to reconcile them itself:

        1. `EXIT_PRIORITY` orders the kinds in general: stops, then targets, then the
           time exit, then signal exits.
        2. `execution.intrabar_priority` decides the stop-versus-target pair when a
           single daily bar's range covers both levels. `stop_first` (the default) keeps
           the base order. `target_first` moves targets ahead of stops - and ONLY that
           pair; the time and signal bands are untouched.

        Rule 2 wins where the two overlap, because a field that could never change an
        outcome would be a lie. That is the whole of the precedence contract: two
        conforming engines calling this function get identical numbers, so they cannot
        disagree about which exit filled.
        """
        priority = dict(EXIT_PRIORITY)
        if self.execution.intrabar_priority == "target_first":
            stop_band = min(priority[k] for k in STOP_KINDS)
            for kind in TARGET_KINDS:
                priority[kind] = stop_band - 1
        return priority

    def exits_in_priority_order(self) -> list[ExitRule]:
        """This document's exits in the exact order an engine must test them.

        THIS is the authoritative order, not the `exits` array and not `EXIT_PRIORITY`
        on its own. Sorted by effective priority then by kind name, so the array's own
        order can never change a backtest and two documents differing only in exit order
        are the same strategy.
        """
        priority = self.effective_exit_priority()
        return sorted(self.exits, key=lambda e: (priority[e.kind], e.kind))

    def initial_stop(self) -> ExitRule | None:
        """The stop that defines 1R, if there is one."""
        stops = [e for e in self.exits if e.kind in STOP_KINDS]
        return stops[0] if len(stops) == 1 else None

    def render(self) -> dict[str, object]:
        """Plain-language rendering. Every field of the DSL appears somewhere here."""
        return {
            "summary": f"{self.direction.capitalize()} {self.universe.render()}",
            "entry": (
                f"Enter when {'ALL' if self.entry.logic == 'all' else 'ANY'} of: "
                + "; ".join(c.render() for c in self.entry.conditions)
            ),
            "calendar": self.calendar.render(),
            "exits": [e.render() for e in self.exits_in_priority_order()],
            "sizing": self.sizing.render(),
            "execution": self.execution.render(),
            "costs": self.costs.render(),
        }
