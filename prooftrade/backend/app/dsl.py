"""The ProofTrade strategy DSL.

This module is the contract the rest of the system is written against. A natural
language description is translated into exactly these types and nothing else; the
backtest engine accepts exactly these types and nothing else.

Two properties matter more than expressiveness:

1. **Nothing here is executable.** A strategy is data: whitelisted indicator names,
   numeric parameters and comparison operators. There is no expression string, no
   lambda, no generated code. A translator (rule based or LLM) can only ever produce
   a value that survives Pydantic validation against these models.
2. **Everything here is finite and enumerable.** The frontend renders the parsed
   strategy from the registry below, and the robustness probe enumerates every
   numeric parameter in a strategy in order to perturb it.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DSL_VERSION = "1.0.0"

# --------------------------------------------------------------------------------------
# Indicator registry
# --------------------------------------------------------------------------------------

Unit = Literal["price", "percent", "oscillator", "ratio", "volume"]


class IndicatorSpec(BaseModel):
    """Static description of one indicator. Drives validation, UI and prose rendering."""

    model_config = ConfigDict(frozen=True)

    name: str
    label: str
    short: str = ""      # compact form used when rendering rules, e.g. "RSI(14)"
    unit: Unit
    params: tuple[str, ...] = ()
    defaults: dict[str, float] = Field(default_factory=dict)
    description: str
    # Comparing a price-unit indicator against a bare number is usually a mistake
    # ("close > 50" means something very different for KO and for NVDA), so the
    # translator prefers percent/oscillator units when it has a choice.
    comparable_to_constant: bool = True


def _spec(name: str, label: str, unit: Unit, description: str, **kw: Any) -> IndicatorSpec:
    kw.setdefault("short", label)
    return IndicatorSpec(name=name, label=label, unit=unit, description=description, **kw)


INDICATOR_SPECS: dict[str, IndicatorSpec] = {
    s.name: s
    for s in [
        # --- raw price fields -----------------------------------------------------
        _spec("close", "Close", "price", "Adjusted closing price of the bar.",
              comparable_to_constant=False),
        _spec("open", "Open", "price", "Adjusted opening price of the bar.",
              comparable_to_constant=False),
        _spec("high", "High", "price", "Adjusted high of the bar.", comparable_to_constant=False),
        _spec("low", "Low", "price", "Adjusted low of the bar.", comparable_to_constant=False),
        _spec("volume", "Volume", "volume", "Share volume for the bar."),
        # --- trend ----------------------------------------------------------------
        _spec("sma", "Simple moving average", "price", "Mean close over the last N bars.",
              params=("period",), defaults={"period": 50}, comparable_to_constant=False),
        _spec("ema", "Exponential moving average", "price",
              "Exponentially weighted mean close, span N.",
              params=("period",), defaults={"period": 20}, comparable_to_constant=False),
        _spec("macd", "MACD line", "price", "EMA(fast) minus EMA(slow) of close.",
              params=("fast", "slow"), defaults={"fast": 12, "slow": 26}),
        _spec("macd_signal", "MACD signal line", "price", "EMA of the MACD line.",
              params=("fast", "slow", "signal"), defaults={"fast": 12, "slow": 26, "signal": 9}),
        _spec("macd_hist", "MACD histogram", "price", "MACD line minus its signal line.",
              params=("fast", "slow", "signal"), defaults={"fast": 12, "slow": 26, "signal": 9}),
        _spec("donchian_high", "N-bar high", "price",
              "Highest high of the N bars BEFORE the current bar (breakout reference).",
              params=("period",), defaults={"period": 20}, comparable_to_constant=False),
        _spec("donchian_low", "N-bar low", "price",
              "Lowest low of the N bars BEFORE the current bar (breakdown reference).",
              params=("period",), defaults={"period": 20}, comparable_to_constant=False),
        _spec("dist_from_sma_pct", "Distance from SMA (%)", "percent",
              "Percent the close sits above (+) or below (-) its N-bar SMA.",
              params=("period",), defaults={"period": 50}),
        # --- momentum -------------------------------------------------------------
        _spec("rsi", "RSI", "oscillator", "Wilder's relative strength index, 0-100.",
              params=("period",), defaults={"period": 14}),
        _spec("roc", "Rate of change (%)", "percent", "Percent change of close over N bars.",
              params=("period",), defaults={"period": 20}),
        # --- volatility -----------------------------------------------------------
        _spec("atr", "Average true range", "price", "Wilder's ATR over N bars, in price units.",
              params=("period",), defaults={"period": 14}, comparable_to_constant=False),
        _spec("atr_pct", "ATR (% of price)", "percent", "ATR expressed as a percent of close.",
              params=("period",), defaults={"period": 14}),
        _spec("stdev_pct", "Return volatility (%)", "percent",
              "Standard deviation of daily returns over N bars, in percent (not annualised).",
              params=("period",), defaults={"period": 20}),
        _spec("bb_upper", "Bollinger upper band", "price", "SMA(N) + k standard deviations.",
              params=("period", "k"), defaults={"period": 20, "k": 2.0},
              comparable_to_constant=False),
        _spec("bb_lower", "Bollinger lower band", "price", "SMA(N) - k standard deviations.",
              params=("period", "k"), defaults={"period": 20, "k": 2.0},
              comparable_to_constant=False),
        _spec("bb_pctb", "Bollinger %B", "oscillator",
              "Position of close within the bands, 0 = lower band, 100 = upper band.",
              params=("period", "k"), defaults={"period": 20, "k": 2.0}),
        _spec("drawdown_pct", "Drawdown from N-bar high (%)", "percent",
              "Percent below the highest close of the last N bars (<= 0).",
              params=("period",), defaults={"period": 252}),
        # --- volume ---------------------------------------------------------------
        _spec("volume_sma", "Average volume", "volume", "Mean volume over the last N bars."),
        _spec("rel_volume", "Relative volume", "ratio",
              "Volume divided by its N-bar average (1.0 = typical).",
              params=("period",), defaults={"period": 20}),
    ]
}

_SHORT_LABELS = {'close': 'Close', 'open': 'Open', 'high': 'High', 'low': 'Low', 'volume': 'Volume', 'sma': 'SMA', 'ema': 'EMA', 'macd': 'MACD', 'macd_signal': 'MACD signal', 'macd_hist': 'MACD histogram', 'donchian_high': 'High', 'donchian_low': 'Low', 'dist_from_sma_pct': '% from SMA', 'rsi': 'RSI', 'roc': 'Return', 'atr': 'ATR', 'atr_pct': 'ATR%', 'stdev_pct': 'Volatility%', 'bb_upper': 'Upper Bollinger band', 'bb_lower': 'Lower Bollinger band', 'bb_pctb': 'Bollinger %B', 'drawdown_pct': 'Drawdown%', 'volume_sma': 'Average volume', 'rel_volume': 'Relative volume'}

for _name, _short in _SHORT_LABELS.items():
    INDICATOR_SPECS[_name] = INDICATOR_SPECS[_name].model_copy(update={"short": _short})

# volume_sma takes a period too; declared here to keep the literal above readable.
INDICATOR_SPECS["volume_sma"] = INDICATOR_SPECS["volume_sma"].model_copy(
    update={"params": ("period",), "defaults": {"period": 20}}
)

IndicatorName = Literal[
    "close", "open", "high", "low", "volume",
    "sma", "ema", "macd", "macd_signal", "macd_hist",
    "donchian_high", "donchian_low", "dist_from_sma_pct",
    "rsi", "roc",
    "atr", "atr_pct", "stdev_pct", "bb_upper", "bb_lower", "bb_pctb", "drawdown_pct",
    "volume_sma", "rel_volume",
]

# Indicators built on an exponential/Wilder recursion rather than a fixed window.
RECURSIVE_INDICATORS = frozenset({"ema", "rsi", "atr", "atr_pct", "macd", "macd_signal", "macd_hist"})

MIN_PERIOD, MAX_PERIOD = 2, 500
MAX_CONDITIONS = 6

# --------------------------------------------------------------------------------------
# Operands
# --------------------------------------------------------------------------------------


class IndicatorOperand(BaseModel):
    """One indicator reading on the current bar."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["indicator"] = "indicator"
    name: IndicatorName
    period: int | None = None
    k: float | None = None
    fast: int | None = None
    slow: int | None = None
    signal: int | None = None

    @model_validator(mode="after")
    def _fill_and_check_params(self) -> "IndicatorOperand":
        spec = INDICATOR_SPECS[self.name]
        for param in spec.params:
            if getattr(self, param) is None:
                object.__setattr__(self, param, spec.defaults[param])
        # Reject parameters the indicator does not take, so a malformed translation
        # surfaces as an error rather than being silently ignored.
        for param in ("period", "k", "fast", "slow", "signal"):
            value = getattr(self, param)
            if value is not None and param not in spec.params:
                raise ValueError(f"{self.name} does not take a '{param}' parameter")
        for param in ("period", "fast", "slow", "signal"):
            value = getattr(self, param)
            if value is not None:
                ivalue = int(value)
                if not MIN_PERIOD <= ivalue <= MAX_PERIOD:
                    raise ValueError(
                        f"{self.name}.{param} must be between {MIN_PERIOD} and {MAX_PERIOD}"
                    )
                object.__setattr__(self, param, ivalue)
        if self.k is not None and not 0.1 <= self.k <= 10:
            raise ValueError("band width k must be between 0.1 and 10")
        if self.fast is not None and self.slow is not None and self.fast >= self.slow:
            raise ValueError("MACD fast period must be shorter than the slow period")
        return self

    @property
    def unit(self) -> Unit:
        return INDICATOR_SPECS[self.name].unit

    @property
    def cache_key(self) -> str:
        parts = [self.name] + [
            f"{p}={getattr(self, p)}" for p in INDICATOR_SPECS[self.name].params
        ]
        return ":".join(parts)

    def render(self) -> str:
        spec = INDICATOR_SPECS[self.name]
        if not spec.params:
            return spec.short
        args = ", ".join(
            f"{getattr(self, p):g}" if p == "k" else f"{int(getattr(self, p))}"
            for p in spec.params
        )
        # "20-bar High" reads better than "High(20)" for breakout references.
        if self.name in ("donchian_high", "donchian_low"):
            return f"{int(self.period)}-bar {spec.short.lower()}"
        return f"{spec.short}({args})"


class ConstantOperand(BaseModel):
    """A fixed number, e.g. the 30 in "RSI below 30"."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["constant"] = "constant"
    value: float

    @property
    def unit(self) -> Unit | None:
        return None

    @property
    def cache_key(self) -> str:
        return f"const:{self.value}"

    def render(self) -> str:
        return f"{self.value:g}"


Operand = Annotated[Union[IndicatorOperand, ConstantOperand], Field(discriminator="kind")]

# --------------------------------------------------------------------------------------
# Conditions
# --------------------------------------------------------------------------------------

Comparator = Literal["<", "<=", ">", ">=", "crosses_above", "crosses_below"]

_COMPARATOR_PROSE: dict[Comparator, str] = {
    "<": "is below",
    "<=": "is at or below",
    ">": "is above",
    ">=": "is at or above",
    "crosses_above": "crosses above",
    "crosses_below": "crosses below",
}


class Condition(BaseModel):
    """`left <op> right`, evaluated on each bar of one symbol."""

    model_config = ConfigDict(extra="forbid")

    left: Operand
    op: Comparator
    right: Operand

    @model_validator(mode="after")
    def _check_operands(self) -> "Condition":
        if isinstance(self.left, ConstantOperand) and isinstance(self.right, ConstantOperand):
            raise ValueError("a condition comparing two constants is always true or always false")
        # A crossing needs a series on the left; a constant cannot cross anything.
        if self.op.startswith("crosses") and isinstance(self.left, ConstantOperand):
            raise ValueError("the left side of a crossing must be an indicator, not a constant")
        return self

    def render(self) -> str:
        return f"{self.left.render()} {_COMPARATOR_PROSE[self.op]} {self.right.render()}"


class ConditionGroup(BaseModel):
    """A flat conjunction or disjunction of conditions.

    Deliberately flat: nested boolean trees would need a nested editor in the UI and a
    nested renderer in prose, and no demo strategy needs one. Documented as a limitation.
    """

    model_config = ConfigDict(extra="forbid")

    logic: Literal["all", "any"] = "all"
    conditions: list[Condition] = Field(default_factory=list)

    @field_validator("conditions")
    @classmethod
    def _limit(cls, v: list[Condition]) -> list[Condition]:
        if len(v) > MAX_CONDITIONS:
            raise ValueError(f"at most {MAX_CONDITIONS} conditions per group")
        return v

    def __bool__(self) -> bool:
        return bool(self.conditions)

    def render(self) -> list[str]:
        return [c.render() for c in self.conditions]


# --------------------------------------------------------------------------------------
# Risk and position rules
# --------------------------------------------------------------------------------------


class RiskRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stop_loss_pct: float | None = Field(default=None, gt=0, le=90)
    take_profit_pct: float | None = Field(default=None, gt=0, le=1000)
    trailing_stop_pct: float | None = Field(default=None, gt=0, le=90)
    max_holding_days: int | None = Field(default=None, ge=1, le=2000)

    @model_validator(mode="after")
    def _sane(self) -> "RiskRules":
        if (
            self.stop_loss_pct is not None
            and self.take_profit_pct is not None
            and self.take_profit_pct <= self.stop_loss_pct / 10
        ):
            raise ValueError("take profit is implausibly small relative to the stop loss")
        return self


class PositionRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: Literal["long", "short"] = "long"
    sizing: Literal["equal_weight"] = "equal_weight"
    max_positions: int = Field(default=5, ge=1, le=20)
    max_positions_per_symbol: Literal[1] = 1  # no pyramiding in the MVP


# --------------------------------------------------------------------------------------
# Strategy
# --------------------------------------------------------------------------------------

_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


class Strategy(BaseModel):
    """A complete, executable-by-the-engine strategy definition."""

    model_config = ConfigDict(extra="forbid")

    dsl_version: Literal["1.0.0"] = DSL_VERSION
    name: str = Field(default="Untitled strategy", max_length=120)
    source_text: str = Field(default="", max_length=4000)
    universe: list[str] = Field(min_length=1, max_length=50)
    position: PositionRules = Field(default_factory=PositionRules)
    entry: ConditionGroup
    exit: ConditionGroup = Field(default_factory=ConditionGroup)
    risk: RiskRules = Field(default_factory=RiskRules)

    @field_validator("universe")
    @classmethod
    def _clean_universe(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for raw in v:
            sym = raw.strip().upper()
            if not _SYMBOL_RE.match(sym):
                raise ValueError(f"invalid ticker: {raw!r}")
            if sym not in out:
                out.append(sym)
        return sorted(out)

    @model_validator(mode="after")
    def _must_be_able_to_enter_and_leave(self) -> "Strategy":
        if not self.entry.conditions:
            raise ValueError("a strategy needs at least one entry condition")
        has_exit = bool(self.exit.conditions) or any(
            getattr(self.risk, f) is not None
            for f in ("stop_loss_pct", "take_profit_pct", "trailing_stop_pct", "max_holding_days")
        )
        if not has_exit:
            raise ValueError(
                "a strategy needs an exit: an exit condition, a stop, a target, or a holding limit"
            )
        return self

    def indicator_operands(self) -> list[IndicatorOperand]:
        """Every distinct indicator the strategy reads, for precomputation."""
        seen: dict[str, IndicatorOperand] = {}
        for group in (self.entry, self.exit):
            for cond in group.conditions:
                for side in (cond.left, cond.right):
                    if isinstance(side, IndicatorOperand):
                        seen.setdefault(side.cache_key, side)
        return [seen[k] for k in sorted(seen)]

    def warmup_bars(self) -> int:
        """Bars of history needed before signals are trustworthy.

        Window indicators (SMA, Donchian, Bollinger) are exact after `period` bars.
        Recursive ones (EMA, Wilder's RSI/ATR, MACD) only converge asymptotically, so
        they get a multiple of their span before we trust them.
        """
        longest = 0
        for op in self.indicator_operands():
            multiple = 4 if op.name in RECURSIVE_INDICATORS else 1
            for param in ("period", "slow", "signal"):
                value = getattr(op, param, None)
                if value:
                    longest = max(longest, int(value) * multiple)
        return max(20, longest)

    def render(self) -> dict[str, Any]:
        """Plain-language rendering used by the UI and the run summary."""
        joiner = {"all": "ALL of", "any": "ANY of"}
        exits = self.exit.render()
        if self.risk.stop_loss_pct is not None:
            exits.append(f"Stop loss at {self.risk.stop_loss_pct:g}% adverse move")
        if self.risk.trailing_stop_pct is not None:
            exits.append(f"Trailing stop {self.risk.trailing_stop_pct:g}% below the peak")
        if self.risk.take_profit_pct is not None:
            exits.append(f"Take profit at {self.risk.take_profit_pct:g}% favourable move")
        if self.risk.max_holding_days is not None:
            exits.append(f"Time exit after {self.risk.max_holding_days} trading days")
        return {
            "direction": self.position.direction,
            "universe": self.universe,
            "entry_logic": joiner[self.entry.logic],
            "entry": self.entry.render(),
            "exit_logic": "ANY of",  # exits are always disjunctive at runtime
            "exit": exits,
            "sizing": (
                f"Equal weight, at most {self.position.max_positions} concurrent positions "
                f"({100 / self.position.max_positions:.0f}% of equity each)"
            ),
        }


# --------------------------------------------------------------------------------------
# Backtest configuration
# --------------------------------------------------------------------------------------


class BacktestConfig(BaseModel):
    """Everything about a run that is not the strategy itself."""

    model_config = ConfigDict(extra="forbid")

    start: dt.date | None = None
    end: dt.date | None = None
    initial_capital: float = Field(default=100_000.0, ge=1_000, le=1e9)
    commission_bps: float = Field(default=1.0, ge=0, le=200)
    slippage_bps: float = Field(default=5.0, ge=0, le=200)
    benchmark: str = "SPY"
    train_fraction: float = Field(default=0.7, ge=0.3, le=0.9)
    walk_forward_folds: int = Field(default=5, ge=2, le=10)
    run_robustness: bool = True
    seed: int = Field(default=7, ge=0, le=2**31 - 1)

    @model_validator(mode="after")
    def _ordered(self) -> "BacktestConfig":
        if self.start and self.end and self.start >= self.end:
            raise ValueError("start must be before end")
        return self

    @property
    def cost_rate(self) -> float:
        """Round-trip-agnostic per-leg cost as a fraction of notional."""
        return (self.commission_bps + self.slippage_bps) / 10_000.0


# --------------------------------------------------------------------------------------
# Deterministic hashing and parameter perturbation
# --------------------------------------------------------------------------------------


def canonical_json(model: BaseModel) -> str:
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def stable_hash(*models: BaseModel, extra: str = "") -> str:
    payload = "|".join(canonical_json(m) for m in models) + "|" + extra
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# Fields that are meaningful to perturb when probing parameter fragility. Ordered, so
# the neighbourhood is the same set every time for a given strategy.
_PERTURBABLE_OPERAND_FIELDS = ("period", "k", "fast", "slow", "signal")
_PERTURBABLE_RISK_FIELDS = (
    "stop_loss_pct", "take_profit_pct", "trailing_stop_pct", "max_holding_days",
)


class ParameterRef(BaseModel):
    """A pointer to one numeric knob inside a strategy."""

    model_config = ConfigDict(frozen=True)

    path: str          # human readable, e.g. "entry[0].left.period"
    label: str         # e.g. "RSI period"
    value: float
    integral: bool


def numeric_parameters(strategy: Strategy) -> list[ParameterRef]:
    """Enumerate every numeric knob, in a stable order."""
    refs: list[ParameterRef] = []
    for group_name in ("entry", "exit"):
        group: ConditionGroup = getattr(strategy, group_name)
        for i, cond in enumerate(group.conditions):
            for side in ("left", "right"):
                operand = getattr(cond, side)
                if isinstance(operand, ConstantOperand):
                    refs.append(ParameterRef(
                        path=f"{group_name}[{i}].{side}.value",
                        label=f"{group_name} rule {i + 1} threshold",
                        value=float(operand.value),
                        integral=False,
                    ))
                    continue
                for field in _PERTURBABLE_OPERAND_FIELDS:
                    value = getattr(operand, field, None)
                    if value is None:
                        continue
                    refs.append(ParameterRef(
                        path=f"{group_name}[{i}].{side}.{field}",
                        label=f"{INDICATOR_SPECS[operand.name].label} {field}",
                        value=float(value),
                        integral=field != "k",
                    ))
    for field in _PERTURBABLE_RISK_FIELDS:
        value = getattr(strategy.risk, field)
        if value is not None:
            refs.append(ParameterRef(
                path=f"risk.{field}",
                label=field.replace("_", " "),
                value=float(value),
                integral=field == "max_holding_days",
            ))
    return refs


_PATH_RE = re.compile(r"^(entry|exit)\[(\d+)\]\.(left|right)\.(\w+)$")


def perturb(strategy: Strategy, ref: ParameterRef, factor: float) -> Strategy | None:
    """Return a copy of `strategy` with one parameter scaled by `factor`.

    Returns None when the perturbed value is not materially different (integer knobs
    round back onto themselves) or when the result fails validation, so the caller can
    skip it rather than double-count the base case.
    """
    new_value = ref.value * factor
    if ref.integral:
        new_value = float(round(new_value))
        if new_value == ref.value:
            return None
    else:
        new_value = round(new_value, 6)
        if new_value == ref.value:
            return None

    data = copy.deepcopy(strategy.model_dump(mode="json"))
    if ref.path.startswith("risk."):
        data["risk"][ref.path.split(".", 1)[1]] = new_value
    else:
        match = _PATH_RE.match(ref.path)
        if not match:  # pragma: no cover - paths are produced by numeric_parameters
            raise ValueError(f"unroutable parameter path {ref.path!r}")
        group, index, side, field = match.group(1), int(match.group(2)), match.group(3), match.group(4)
        data[group]["conditions"][index][side][field] = new_value

    try:
        return Strategy.model_validate(data)
    except Exception:
        return None


def indicator_registry_payload() -> list[dict[str, Any]]:
    """Serialisable registry for the frontend."""
    return [
        {
            "name": s.name,
            "label": s.label,
            "short": s.short,
            "unit": s.unit,
            "params": list(s.params),
            "defaults": s.defaults,
            "description": s.description,
        }
        for s in INDICATOR_SPECS.values()
    ]
