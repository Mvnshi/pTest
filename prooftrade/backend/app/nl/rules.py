"""Deterministic English -> strategy DSL translation.

This is the default translator: no model, no network, no randomness, so the demo is
reproducible and free. It is a phrase grammar, not a language model - it recognises the
vocabulary traders actually use for daily-bar rules and refuses to guess beyond it.

The design principle is that **failing loudly beats guessing quietly**. Anything the
parser could not interpret is returned in `unparsed` and shown to the user; anything it
had to assume is returned in `assumptions`; anything genuinely ambiguous becomes a
`ClarifyQuestion` with a pre-selected default, so the flow never blocks but the user
always sees what was decided on their behalf.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from ..dsl import (
    Comparator,
    Condition,
    ConditionGroup,
    ConstantOperand,
    IndicatorOperand,
    Operand,
    PositionRules,
    RiskRules,
    Strategy,
)

# --------------------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------------------


@dataclass
class ClarifyOption:
    label: str
    value: str
    description: str = ""


@dataclass
class ClarifyQuestion:
    id: str
    question: str
    why: str
    options: list[ClarifyOption]
    default_value: str


@dataclass
class Assumption:
    field: str
    text: str


@dataclass
class ParseResult:
    strategy: Strategy
    questions: list[ClarifyQuestion] = field(default_factory=list)
    assumptions: list[Assumption] = field(default_factory=list)
    confidence: float = 1.0
    unparsed: list[str] = field(default_factory=list)
    translator: str = "rules"
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "strategy": self.strategy.model_dump(mode="json"),
            "strategy_render": self.strategy.render(),
            "questions": [asdict(q) for q in self.questions],
            "assumptions": [asdict(a) for a in self.assumptions],
            "confidence": round(self.confidence, 3),
            "unparsed": self.unparsed,
            "translator": self.translator,
            "notes": self.notes,
        }


class TranslationError(ValueError):
    """The text could not be turned into a runnable strategy."""


# --------------------------------------------------------------------------------------
# Universe groups
# --------------------------------------------------------------------------------------

UNIVERSE_GROUPS: dict[str, list[str]] = {
    "index_etfs": ["SPY", "QQQ", "IWM", "DIA"],
    "sector_etfs": ["XLE", "XLF", "XLK", "XLV"],
    "etfs": ["SPY", "QQQ", "IWM", "DIA", "TLT", "GLD", "XLE", "XLF", "XLK", "XLV"],
    "megacap": ["AAPL", "MSFT", "NVDA", "AMZN", "JPM", "XOM", "KO", "JNJ"],
    "tech": ["AAPL", "MSFT", "NVDA", "AMZN", "QQQ", "XLK"],
}

_GROUP_PHRASES: list[tuple[str, str]] = [
    (r"\bindex etfs?\b|\bbroad market\b|\bmajor index(?:es|s)?\b", "index_etfs"),
    (r"\bsector etfs?\b|\bsectors?\b", "sector_etfs"),
    (r"\betfs?\b", "etfs"),
    (r"\bmega[- ]?caps?\b|\blarge[- ]?caps?\b|\bbig (?:tech|names)\b", "megacap"),
    (r"\btech(?:nology)? (?:stocks?|names?|sector)\b", "tech"),
    (r"\ball (?:the )?(?:symbols?|stocks?|tickers?|names?)\b|\bwhole universe\b|"
     r"\bentire universe\b|\beverything\b", "all"),
]

# --------------------------------------------------------------------------------------
# Comparators, longest phrase first so "drops below" is not shortened to "below"
# --------------------------------------------------------------------------------------

_COMPARATOR_PATTERNS: list[tuple[str, Comparator]] = [
    (r"\b(?:crosses?\s+(?:back\s+)?(?:above|over)|breaks?\s+(?:out\s+)?above|"
     r"moves?\s+above|golden\s+cross)\b", "crosses_above"),
    (r"\b(?:crosses?\s+(?:back\s+)?(?:below|under|beneath)|breaks?\s+(?:down\s+)?below|"
     r"moves?\s+below|death\s+cross)\b", "crosses_below"),
    (r"\b(?:at\s+least|no\s+less\s+than|at\s+or\s+above)\b|>=", ">="),
    (r"\b(?:at\s+most|no\s+more\s+than|at\s+or\s+below)\b|<=", "<="),
    (r"\b(?:(?:drops?|falls?|dips?|declines?|sinks?|goes?|gets?)\s+(?:back\s+)?"
     r"(?:below|under|beneath)|(?:is\s+)?(?:below|under|beneath|less\s+than|"
     r"lower\s+than|cheaper\s+than))\b|<", "<"),
    (r"\b(?:(?:rises?|climbs?|goes?|gets?|moves?|pushes?)\s+(?:back\s+)?(?:above|over)|"
     r"(?:is\s+)?(?:above|over|greater\s+than|higher\s+than|more\s+than|exceeds?))\b|>", ">"),
]


# --------------------------------------------------------------------------------------
# Indicator phrases. Each entry: regex, builder taking the match -> IndicatorOperand.
# Ordered most specific first; matched spans are consumed so later patterns cannot
# re-read the same words.
# --------------------------------------------------------------------------------------

_MONTH_TO_BARS = 21
_WEEK_TO_BARS = 5


def _period_from(value: str | None, unit: str | None, default: int) -> int:
    if value is None:
        return default
    n = int(value)
    if unit:
        unit = unit.lower()
        if unit.startswith("month"):
            n *= _MONTH_TO_BARS
        elif unit.startswith("week"):
            n *= _WEEK_TO_BARS
    return max(2, min(n, 500))


def _indicator_patterns() -> list[tuple[re.Pattern, callable]]:
    p: list[tuple[str, callable]] = [
        # --- RSI ------------------------------------------------------------------
        (r"\b(\d{1,3})[- ]?(?:day|period|bar)s?\s+rsi\b",
         lambda m: IndicatorOperand(name="rsi", period=_period_from(m.group(1), None, 14))),
        (r"\brsi\s*\(\s*(\d{1,3})\s*\)",
         lambda m: IndicatorOperand(name="rsi", period=_period_from(m.group(1), None, 14))),
        (r"\brsi\b(?:\s*(?:of|over)\s*(\d{1,3}))?",
         lambda m: IndicatorOperand(name="rsi", period=_period_from(m.group(1), None, 14))),
        # --- Bollinger --------------------------------------------------------------
        (r"\b(upper|lower)\s+bollinger(?:\s+band)?\b",
         lambda m: IndicatorOperand(name=f"bb_{m.group(1).lower()}")),
        (r"\bbollinger\s*%?\s*b\b", lambda m: IndicatorOperand(name="bb_pctb")),
        (r"\bbollinger(?:\s+bands?)?\b", lambda m: IndicatorOperand(name="bb_pctb")),
        # --- MACD ---------------------------------------------------------------------
        (r"\bmacd\s+histogram\b", lambda m: IndicatorOperand(name="macd_hist")),
        (r"\bmacd\s+signal(?:\s+line)?\b", lambda m: IndicatorOperand(name="macd_signal")),
        (r"\bmacd(?:\s+line)?\b", lambda m: IndicatorOperand(name="macd")),
        # --- Moving averages ----------------------------------------------------------
        (r"\b(\d{1,3})[- ]?(?:day|period|bar)s?\s+(exponential|simple)?\s*"
         r"(?:moving\s+average|average|ma|sma|ema)\b",
         lambda m: IndicatorOperand(
             name="ema" if (m.group(2) or "").startswith("expon") else "sma",
             period=_period_from(m.group(1), None, 50))),
        (r"\b(sma|ema)\s*\(?\s*(\d{1,3})\s*\)?",
         lambda m: IndicatorOperand(name=m.group(1).lower(),
                                    period=_period_from(m.group(2), None, 50))),
        (r"\b(\d{1,3})\s*d?\s*(sma|ema|ma)\b",
         lambda m: IndicatorOperand(name="ema" if m.group(2).lower() == "ema" else "sma",
                                    period=_period_from(m.group(1), None, 50))),
        (r"\b(?:the\s+)?(exponential|simple)?\s*moving\s+average\b",
         lambda m: IndicatorOperand(
             name="ema" if (m.group(1) or "").startswith("expon") else "sma", period=50)),
        # --- Breakout references --------------------------------------------------------
        (r"\b(\d{1,3})[- ]?(day|week|month)s?\s+high\b",
         lambda m: IndicatorOperand(name="donchian_high",
                                    period=_period_from(m.group(1), m.group(2), 20))),
        (r"\b(\d{1,3})[- ]?(day|week|month)s?\s+low\b",
         lambda m: IndicatorOperand(name="donchian_low",
                                    period=_period_from(m.group(1), m.group(2), 20))),
        # --- Volatility --------------------------------------------------------------
        (r"\batr\s*(?:\(\s*(\d{1,3})\s*\))?\s*(?:as\s+a\s+)?(?:%|percent)",
         lambda m: IndicatorOperand(name="atr_pct",
                                    period=_period_from(m.group(1), None, 14))),
        (r"\batr\s*(?:\(\s*(\d{1,3})\s*\))?",
         lambda m: IndicatorOperand(name="atr", period=_period_from(m.group(1), None, 14))),
        (r"\b(?:realised|realized|historical)?\s*volatility\b",
         lambda m: IndicatorOperand(name="stdev_pct", period=20)),
        (r"\bdrawdown\b", lambda m: IndicatorOperand(name="drawdown_pct", period=252)),
        # --- Momentum ------------------------------------------------------------------
        (r"\b(\d{1,3})[- ]?(day|week|month)s?\s+(?:return|momentum|roc|performance)\b",
         lambda m: IndicatorOperand(name="roc",
                                    period=_period_from(m.group(1), m.group(2), 20))),
        (r"\brate\s+of\s+change\b", lambda m: IndicatorOperand(name="roc", period=20)),
        # --- Volume ----------------------------------------------------------------------
        (r"\brelative\s+volume\b", lambda m: IndicatorOperand(name="rel_volume", period=20)),
        (r"\b(?:average|avg|typical)\s+volume\b",
         lambda m: IndicatorOperand(name="volume_sma", period=20)),
        (r"\bvolume\b", lambda m: IndicatorOperand(name="volume")),
        # --- Distance from a mean ----------------------------------------------------------
        (r"\b(?:%|percent)\s+(?:above|below|from)\s+(?:its\s+)?(?:\d{1,3}[- ]?day\s+)?"
         r"(?:moving\s+)?average\b",
         lambda m: IndicatorOperand(name="dist_from_sma_pct", period=50)),
        # --- Raw price fields ------------------------------------------------------------
        (r"\bclosing\s+price\b|\bthe\s+close\b|\bcloses?\b",
         lambda m: IndicatorOperand(name="close")),
        (r"\b(?:the\s+)?(?:price|stock|share\s+price|shares?)\b",
         lambda m: IndicatorOperand(name="close")),
    ]
    return [(re.compile(rx), fn) for rx, fn in p]


_INDICATORS = _indicator_patterns()
# Pronouns carry no meaning of their own; they inherit the operand from the clause
# before. Matched last so a real indicator always wins.
_PRONOUN = re.compile(r"\b(?:it|they|them|that|this|the\s+same)\b")
_NUMBER = re.compile(r"(-?\d+(?:\.\d+)?)\s*(%)?")

# --------------------------------------------------------------------------------------
# Risk phrases. Matched first and blanked out, so "8% stop" never becomes a condition.
# --------------------------------------------------------------------------------------

_RISK_PATTERNS: list[tuple[str, str]] = [
    ("trailing_stop_pct",
     r"(?:(\d+(?:\.\d+)?)\s*%?\s*trailing\s+stop|trailing\s+stop\s*(?:of|at|:)?\s*"
     r"(\d+(?:\.\d+)?)\s*%)"),
    ("stop_loss_pct",
     r"(?:(\d+(?:\.\d+)?)\s*%\s*stop(?:\s*-?\s*loss)?|stop(?:\s*-?\s*loss)?\s*"
     r"(?:of|at|:)?\s*(\d+(?:\.\d+)?)\s*%)"),
    ("take_profit_pct",
     r"(?:(\d+(?:\.\d+)?)\s*%\s*(?:take[- ]?profit|profit\s*target|target|gain)|"
     r"(?:take[- ]?profit|profit\s*target|target)\s*(?:of|at|:)?\s*(\d+(?:\.\d+)?)\s*%)"),
    # A holding limit needs a trigger word, and must not swallow "20-day average".
    ("max_holding_days",
     r"(?:after|within|hold(?:ing)?(?:\s+for)?|max(?:imum)?(?:\s+hold(?:ing)?)?|"
     r"time\s+exit(?:\s+of)?)\s+(\d{1,4})\s*(?:trading\s+)?(?:day|bar|session)s?"
     r"(?!\s*(?:moving|average|ma\b|sma|ema|high|low|rsi|return|momentum))"),
]

_MAX_POSITIONS = re.compile(
    r"(?:at\s+most|up\s+to|max(?:imum)?(?:\s+of)?|no\s+more\s+than)\s+(\d{1,2})\s*"
    r"(?:concurrent\s+|open\s+|simultaneous\s+)?positions?"
    r"|(\d{1,2})\s*positions?\s*(?:at\s+a\s+time|at\s+once|maximum|max)"
)

# "short" as a verb, but not "short term" / "shorter window".
_SHORT = re.compile(r"\b(?:go\s+short|sell\s+short|shorting|short)\b(?!\s*(?:-?\s*term|er\b))")
# "close" only counts as an exit verb when it has an object ("close the position").
# A bare "close" is the closing price, and treating it as an exit verb splits
# "the close is below the lower band" in the middle of its own condition.
_CLOSE_VERB = r"close\s+(?:out\b|(?:the\s+)?(?:position|trade)s?\b)"
_LONG_EXIT = re.compile(
    rf"\b(?:then\s+)?(?:sell|exit|{_CLOSE_VERB}|get\s+out|take\s+profits?|liquidate|unwind)\b"
)
_SHORT_EXIT = re.compile(
    rf"\b(?:cover|buy\s+back|exit|{_CLOSE_VERB}|take\s+profits?)\b"
)
_ENTRY_MARKER = re.compile(r"\b(?:buy|go\s+long|enter|long|open\s+a\s+position|short)\b")

_STOPWORDS = frozenset("""
a an the and or but if when while then than that this these those with without for from to of on in
at by is are was were be been being do does did will would should could can may might must i we you
it its their there here as so such very just only also both each any some all no not use using
strategy trade trades trading stock stocks share shares position positions signal signals rule rules
day days bar bars week weeks month months year years market markets buy sell hold long short entry
exit enter please want like backtest test again back next over under out up down more less
""".split())


# --------------------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------------------


class _Mask:
    """Tracks which character spans of the text have already been consumed."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.free = [True] * len(text)

    def is_free(self, start: int, end: int) -> bool:
        return all(self.free[start:end])

    def consume(self, start: int, end: int) -> None:
        for i in range(start, end):
            self.free[i] = False

    def remainder(self) -> str:
        return "".join(c if self.free[i] else " " for i, c in enumerate(self.text))


def _find_operands(
    clause: str, mask: _Mask, offset: int
) -> list[tuple[int, int, Operand, bool]]:
    """Returns (start, end, operand, is_pronoun) tuples in clause order."""
    found: list[tuple[int, int, Operand, bool]] = []
    for pattern, build in _INDICATORS:
        for m in pattern.finditer(clause):
            if not mask.is_free(offset + m.start(), offset + m.end()):
                continue
            try:
                operand = build(m)
            except ValueError:
                continue
            mask.consume(offset + m.start(), offset + m.end())
            found.append((m.start(), m.end(), operand, False))
    for m in _PRONOUN.finditer(clause):
        if not mask.is_free(offset + m.start(), offset + m.end()):
            continue
        mask.consume(offset + m.start(), offset + m.end())
        found.append((m.start(), m.end(), IndicatorOperand(name="close"), True))
    for m in _NUMBER.finditer(clause):
        if not mask.is_free(offset + m.start(), offset + m.end()):
            continue
        mask.consume(offset + m.start(), offset + m.end())
        found.append((m.start(), m.end(), ConstantOperand(value=float(m.group(1))), False))
    found.sort(key=lambda t: t[0])
    return found


def _find_comparators(clause: str, mask: _Mask, offset: int) -> list[tuple[int, int, Comparator]]:
    found: list[tuple[int, int, Comparator]] = []
    for pattern, op in _COMPARATOR_PATTERNS:
        for m in re.finditer(pattern, clause):
            if not mask.is_free(offset + m.start(), offset + m.end()):
                continue
            mask.consume(offset + m.start(), offset + m.end())
            found.append((m.start(), m.end(), op))
    found.sort(key=lambda t: t[0])
    return found


def _build_conditions(
    clause: str,
    mask: _Mask,
    offset: int,
    fallback_left: Operand | None = None,
    fallback_right: Operand | None = None,
) -> list[Condition]:
    """Assemble `left op right` triples by proximity within the clause.

    Comparators are located first so an operand is never mistaken for one; each
    comparator then claims the nearest unclaimed operand on its left and on its right.

    Two fallbacks keep ordinary English working:

    * A missing left side reuses the previous condition's left operand, so
      "RSI below 30 and above 20" reads as two conditions on RSI.
    * A missing right side, or a pronoun on the left, borrows from the entry clause,
      so "exit when it crosses back below" resolves against what the entry crossed.
    """
    comparators = _find_comparators(clause, mask, offset)
    operands = _find_operands(clause, mask, offset)
    if not comparators:
        return []

    conditions: list[Condition] = []
    used: set[int] = set()
    last_left: Operand | None = None

    for c_start, c_end, op in comparators:
        left_idx = None
        for i, (s_pos, _e, _o, _p) in enumerate(operands):
            if i in used or s_pos >= c_start:
                continue
            left_idx = i
        right_idx = None
        for i, (s_pos, _e, _o, _p) in enumerate(operands):
            if i in used or s_pos < c_end:
                continue
            right_idx = i
            break

        if right_idx is not None:
            right = operands[right_idx][2]
            used.add(right_idx)
        elif fallback_right is not None:
            right = fallback_right
        else:
            continue  # "sell when it goes above" with nothing to compare against

        if left_idx is not None:
            left = operands[left_idx][2]
            used.add(left_idx)
            if operands[left_idx][3]:  # a pronoun: prefer what the entry rule used
                left = fallback_left or last_left or left
        elif last_left is not None:
            left = last_left
        elif fallback_left is not None:
            left = fallback_left
        else:
            left = IndicatorOperand(name="close")

        if isinstance(left, ConstantOperand) and isinstance(right, IndicatorOperand):
            # "30 above RSI" - the writer meant the indicator on the left.
            left, right = right, left
            op = {"<": ">", ">": "<", "<=": ">=", ">=": "<="}.get(op, op)
        if isinstance(left, ConstantOperand):
            continue  # cannot compare two bare numbers
        if left == right:
            continue  # a pronoun resolved onto its own counterpart

        try:
            conditions.append(Condition(left=left, op=op, right=right))
            last_left = left
        except ValueError:
            continue
    return conditions


def _extract_risk(text: str, mask: _Mask) -> tuple[dict, list[Assumption]]:
    values: dict = {}
    notes: list[Assumption] = []
    for field_name, pattern in _RISK_PATTERNS:
        for m in re.finditer(pattern, text):
            if not mask.is_free(m.start(), m.end()):
                continue
            raw = next((g for g in m.groups() if g), None)
            if raw is None:
                continue
            mask.consume(m.start(), m.end())
            values[field_name] = int(float(raw)) if field_name == "max_holding_days" else float(raw)
            break
    return values, notes


def _extract_universe(
    original: str, lowered: str, available: list[str], mask: _Mask
) -> tuple[list[str], str]:
    """Return (symbols, how) where `how` is 'explicit', a group name, or 'none'."""
    explicit: list[str] = []
    for symbol in available:
        for m in re.finditer(rf"\b{re.escape(symbol)}\b", original):
            explicit.append(symbol)
            mask.consume(m.start(), m.end())
    if explicit:
        return sorted(set(explicit)), "explicit"
    for pattern, group in _GROUP_PHRASES:
        m = re.search(pattern, lowered)
        if m:
            mask.consume(m.start(), m.end())
            if group == "all":
                return sorted(available), "all"
            members = [s for s in UNIVERSE_GROUPS[group] if s in available]
            if members:
                return sorted(members), group
    return sorted(available), "none"


def _split_clauses(lowered: str, direction: str) -> tuple[str, str, int, int]:
    exit_re = _SHORT_EXIT if direction == "short" else _LONG_EXIT
    entry_match = _ENTRY_MARKER.search(lowered)
    entry_from = entry_match.end() if entry_match else 0
    for m in exit_re.finditer(lowered):
        if m.start() >= entry_from:
            return lowered[:m.start()], lowered[m.end():], 0, m.end()
    return lowered, "", 0, len(lowered)


def _unparsed_fragments(mask: _Mask) -> list[str]:
    """Leftover text with enough substance to be worth admitting we ignored."""
    out: list[str] = []
    for chunk in re.split(r"[.,;:!?]|\band\b|\bor\b|\bthen\b", mask.remainder()):
        words = [w for w in re.findall(r"[a-z0-9%$.]+", chunk) if w not in _STOPWORDS]
        if len(words) >= 2:
            out.append(" ".join(words))
    return out[:5]


# --------------------------------------------------------------------------------------
# Clarification questions
# --------------------------------------------------------------------------------------


def _universe_question(available: list[str]) -> ClarifyQuestion:
    return ClarifyQuestion(
        id="universe",
        question="Which symbols should this run on?",
        why="You did not name any tickers, so I defaulted to the whole demo universe.",
        options=[
            ClarifyOption("All 18 symbols", "all",
                          "Broadest test. More trades, and a fairer read on whether the rule generalises."),
            ClarifyOption("Index ETFs only", "index_etfs", "SPY, QQQ, IWM, DIA."),
            ClarifyOption("All ETFs", "etfs", "Index, sector, bonds and gold - 10 symbols."),
            ClarifyOption("Mega-cap stocks only", "megacap",
                          "AAPL, MSFT, NVDA, AMZN, JPM, XOM, KO, JNJ."),
        ],
        default_value="all",
    )


_EXIT_QUESTION = ClarifyQuestion(
    id="exit_rule",
    question="How should a position be closed?",
    why=("You described when to get in but not when to get out. Without an exit rule a "
         "backtest is meaningless, so I need one before running."),
    options=[
        ClarifyOption("Time exit after 20 trading days", "time_20",
                      "Simple and neutral. Does not flatter the strategy."),
        ClarifyOption("8% stop loss only", "stop_8", "Exit only when the trade goes wrong."),
        ClarifyOption("Both: 20-day time exit and an 8% stop", "both", "The usual pairing."),
        ClarifyOption("Mirror the entry rule", "invert",
                      "Exit when the entry condition stops being true."),
    ],
    default_value="both",
)

_STOP_QUESTION = ClarifyQuestion(
    id="stop_loss",
    question="Add a stop loss?",
    why=("You did not specify one. Backtests without stops often hide the worst of the "
         "drawdown, so it is worth deciding deliberately rather than by omission."),
    options=[
        ClarifyOption("No stop loss", "none", "Exit only on your stated rules. Honest, but deeper drawdowns."),
        ClarifyOption("8% stop", "8", "A common retail default."),
        ClarifyOption("5% stop", "5", "Tighter. Expect more stop-outs and more trades."),
        ClarifyOption("15% stop", "15", "Loose. Mostly a disaster brake."),
    ],
    default_value="none",
)

_MA_QUESTION = ClarifyQuestion(
    id="ma_type",
    question="Simple or exponential moving average?",
    why="You wrote \"moving average\" without saying which. They give different signals.",
    options=[
        ClarifyOption("Simple (SMA)", "sma", "Equal weight over the window. The usual reading."),
        ClarifyOption("Exponential (EMA)", "ema", "Weights recent bars more heavily."),
    ],
    default_value="sma",
)

_DIRECTION_QUESTION = ClarifyQuestion(
    id="direction",
    question="Long or short?",
    why="The wording could be read either way.",
    options=[
        ClarifyOption("Long", "long", "Buy first, sell later."),
        ClarifyOption("Short", "short", "Sell first, buy back later. No borrow costs are modelled."),
    ],
    default_value="long",
)


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def translate(
    text: str,
    available_symbols: list[str],
    answers: dict[str, str] | None = None,
) -> ParseResult:
    """Turn free text into a validated Strategy plus everything the user should see."""
    answers = answers or {}
    original = text.strip()
    if not original:
        raise TranslationError("Describe a strategy first.")
    lowered = original.lower()

    mask = _Mask(lowered)
    questions: list[ClarifyQuestion] = []
    assumptions: list[Assumption] = []
    confidence = 1.0

    # --- direction ---------------------------------------------------------------
    direction = "short" if _SHORT.search(lowered) else "long"
    if "direction" in answers:
        direction = answers["direction"]
    elif _SHORT.search(lowered) and re.search(r"\b(?:buy|go\s+long)\b", lowered):
        questions.append(_DIRECTION_QUESTION)
        direction = "long"
        confidence -= 0.1

    # --- risk rules (consumed before conditions so "8% stop" is never a comparison) --
    risk_values, _ = _extract_risk(lowered, mask)

    position_match = _MAX_POSITIONS.search(lowered)
    max_positions = 5
    if position_match:
        mask.consume(position_match.start(), position_match.end())
        raw = next((g for g in position_match.groups() if g), None)
        if raw:
            max_positions = max(1, min(int(raw), 20))
    else:
        assumptions.append(Assumption(
            "position.max_positions",
            "At most 5 concurrent positions, equal weight (20% of equity each). "
            "You did not specify a limit.",
        ))

    # --- universe -----------------------------------------------------------------
    universe, how = _extract_universe(original, lowered, available_symbols, mask)
    if "universe" in answers:
        choice = answers["universe"]
        universe = (sorted(available_symbols) if choice == "all"
                    else sorted(s for s in UNIVERSE_GROUPS.get(choice, available_symbols)
                                if s in available_symbols))
        how = choice
    elif how == "none":
        questions.append(_universe_question(available_symbols))
        assumptions.append(Assumption(
            "universe", f"No tickers named, so all {len(universe)} demo symbols are used."))
        confidence -= 0.05
    elif how != "explicit":
        assumptions.append(Assumption("universe", f"Read \"{how.replace('_', ' ')}\" as {', '.join(universe)}."))
    if not universe:
        raise TranslationError("None of the symbols you named are in the demo universe.")

    # --- clauses -------------------------------------------------------------------
    entry_clause, exit_clause, entry_offset, exit_offset = _split_clauses(lowered, direction)
    entry_conditions = _build_conditions(entry_clause, mask, entry_offset)
    # The exit clause borrows the entry rule's operands when the user leans on a
    # pronoun ("exit when it crosses back below").
    lead = entry_conditions[0] if entry_conditions else None
    exit_conditions = (
        _build_conditions(
            exit_clause, mask, exit_offset,
            fallback_left=lead.left if lead else None,
            fallback_right=lead.right if lead else None,
        )
        if exit_clause else []
    )

    if not entry_conditions:
        raise TranslationError(
            "I could not find an entry condition. Try something like "
            "\"buy when RSI(14) drops below 30 and price is above the 200-day moving average\"."
        )

    entry_logic = "any" if (len(entry_conditions) > 1 and re.search(r"\bor\b", entry_clause)) else "all"

    # --- ambiguity: bare "moving average" ------------------------------------------
    bare_ma = re.search(r"\b(?:the\s+)?moving\s+average\b", lowered) and not re.search(
        r"\b(?:simple|exponential|sma|ema)\b", lowered)
    if bare_ma:
        if "ma_type" in answers and answers["ma_type"] == "ema":
            for group in (entry_conditions, exit_conditions):
                for cond in group:
                    for side in ("left", "right"):
                        operand = getattr(cond, side)
                        if isinstance(operand, IndicatorOperand) and operand.name == "sma":
                            setattr(cond, side, IndicatorOperand(name="ema", period=operand.period))
        elif "ma_type" not in answers:
            questions.append(_MA_QUESTION)
            assumptions.append(Assumption("indicator", "Read \"moving average\" as a simple moving average."))
            confidence -= 0.05

    # --- exits ----------------------------------------------------------------------
    risk = RiskRules(**risk_values)
    has_risk_exit = any(getattr(risk, f) is not None for f in
                        ("stop_loss_pct", "take_profit_pct", "trailing_stop_pct", "max_holding_days"))

    if not exit_conditions and not has_risk_exit:
        choice = answers.get("exit_rule", _EXIT_QUESTION.default_value)
        if "exit_rule" not in answers:
            questions.append(_EXIT_QUESTION)
            confidence -= 0.2
        if choice in ("time_20", "both"):
            risk = risk.model_copy(update={"max_holding_days": 20})
        if choice in ("stop_8", "both"):
            risk = risk.model_copy(update={"stop_loss_pct": 8.0})
        if choice == "invert":
            exit_conditions = [_invert(c) for c in entry_conditions]
        assumptions.append(Assumption(
            "exit", f"No exit was described, so the \"{choice}\" option is applied."))
    elif risk.stop_loss_pct is None and risk.trailing_stop_pct is None:
        choice = answers.get("stop_loss", "none")
        if "stop_loss" not in answers:
            questions.append(_STOP_QUESTION)
        if choice != "none":
            risk = risk.model_copy(update={"stop_loss_pct": float(choice)})
            assumptions.append(Assumption("risk.stop_loss_pct", f"Stop loss set to {choice}%."))
        else:
            assumptions.append(Assumption(
                "risk.stop_loss_pct",
                "No stop loss. Losing trades are held until an exit rule fires."))

    # --- assemble -------------------------------------------------------------------
    name = _derive_name(original, entry_conditions, direction)
    try:
        strategy = Strategy(
            name=name,
            source_text=original[:4000],
            universe=universe,
            position=PositionRules(direction=direction, max_positions=max_positions),
            entry=ConditionGroup(logic=entry_logic, conditions=entry_conditions[:6]),
            exit=ConditionGroup(logic="any", conditions=exit_conditions[:6]),
            risk=risk,
        )
    except ValueError as exc:
        raise TranslationError(str(exc)) from exc

    unparsed = _unparsed_fragments(mask)
    confidence -= min(0.3, 0.08 * len(unparsed))
    if len(entry_conditions) == 1:
        confidence -= 0.05

    notes = []
    if exit_conditions and len(exit_conditions) > 1:
        notes.append("Exit conditions are always treated as ANY: the first one to fire closes the trade.")
    if len(entry_conditions) > 6:
        notes.append("Only the first six entry conditions were kept.")

    return ParseResult(
        strategy=strategy,
        questions=questions,
        assumptions=assumptions,
        confidence=max(0.05, min(1.0, confidence)),
        unparsed=unparsed,
        translator="rules",
        notes=notes,
    )


_INVERSE: dict[Comparator, Comparator] = {
    "<": ">=", "<=": ">", ">": "<=", ">=": "<",
    "crosses_above": "crosses_below", "crosses_below": "crosses_above",
}


def _invert(condition: Condition) -> Condition:
    return Condition(left=condition.left, op=_INVERSE[condition.op], right=condition.right)


def _derive_name(text: str, conditions: list[Condition], direction: str) -> str:
    """A short label built from the rules themselves, not from the raw sentence."""
    lead = conditions[0]
    left = lead.left.render() if not isinstance(lead.left, ConstantOperand) else "Price"
    verb = {"<": "below", "<=": "below", ">": "above", ">=": "above",
            "crosses_above": "breakout", "crosses_below": "breakdown"}[lead.op]
    tail = lead.right.render()
    prefix = "Short" if direction == "short" else "Long"
    return f"{prefix} {left} {verb} {tail}"[:120]
