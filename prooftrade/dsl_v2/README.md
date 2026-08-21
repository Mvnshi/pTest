# ProofTrade Strategy DSL — v2.0.0

A typed, versioned JSON grammar for rule-based **daily-bar** trading strategies.

A strategy is **data**. There is no expression string, formula field, or callback
anywhere in this grammar, so a conforming document can be executed without an
evaluator. That is the whole security model: nothing needs `eval` because there is
nothing to evaluate.

```
strategy.schema.json   generated from models.py  (never hand-edited)
models.py              Pydantic — the source of truth
strategy.ts            TypeScript mirror
validate.py            semantic rules + human error messages
cli.py                 python -m dsl_v2.cli strategy.json [--explain] [--json]
examples/              five strategies covering the whole grammar
tests/                 97 tests, including drift checks across all three
```

From the `prooftrade` directory:

```bash
make verify            # python tests + schema freshness + tsc --strict + CLI on examples
make test-dsl          # just this package's tests
make dsl-schema        # fail if strategy.schema.json is stale
make dsl-typecheck     # real tsc, not the Python regex drift check
make dsl-cli           # validate every example
```

The CLI runs two ways, and they differ in where you have to be standing:

```bash
# From `prooftrade`, where the package is importable:
python -m dsl_v2.cli examples/*.json --explain

# From anywhere - running the file directly bootstraps its own import path:
python /path/to/prooftrade/dsl_v2/cli.py strategy.json
cat strategy.json | python /path/to/prooftrade/dsl_v2/cli.py -
```

---

## 1. Design principles

**Small.** Eight indicators, seven comparisons, six exit kinds, four sizing methods.
Every enum is closed. Adding a case is a version bump, not a config change.

**Explainable.** Every node renders to one English clause, so a parsed strategy can be
shown back to a non-technical author as prose. `--explain` prints it:

```
Long AAPL, AMZN, GLD, JPM, MSFT, NVDA, TLT, XOM (only symbols averaging over
  50,000,000 in dollar volume over 20 bars)
Enter when ALL of: close crosses above the 20-bar highest high;
  volume is above average volume(20)
exit: trailing ATR stop at 2x ATR(14)
exit: take profit at 3R
exit: time exit after 120 bars
exit: exit when close crosses below the 10-bar lowest low
risk 1.00% of equity per trade, sized on 2x ATR(14), at most 5 open positions
signal on the close, fill at the next open; stop first inside a bar; gaps fill at open
1 bps commission per leg, slippage of 0.05x ATR(14)
```

**Deterministic.** Execution assumptions are *fields in the document*, not conventions
buried in an engine: fill timing, intrabar priority, gap handling, and the tie-break
when more candidates fire than there are slots. Exit precedence is fixed by a table, so
reordering the `exits` array can never change a backtest. Two conforming engines reading
the same document must produce the same trades.

**Safe.** Unknown fields are rejected rather than ignored, so a typo cannot silently
disable a rule. Every number is bounded, every list is length-capped, and every union is
discriminated by a literal `kind`.

---

## 2. Document shape

```jsonc
{
  "dsl_version": "2.0.0",       // required, exact
  "name": "RSI pullback in an uptrend",
  "description": "",            // optional prose
  "source_text": "",            // optional: the English this was translated from

  "universe":  { "symbols": [...], "liquidity": {...} },
  "direction": "long",          // or "short"
  "entry":     { "logic": "all", "conditions": [...] },
  "exits":     [ ... ],         // 1-6 typed rules, at most one of each kind
  "sizing":    { "method": "...", "max_open_positions": 4, ... },
  "calendar":  { "date_range": {...}, "days_of_week": [...], "months": [...] },
  "execution": { ... },
  "costs":     { ... }
}
```

`calendar`, `execution` and `costs` may be omitted; their documented defaults are
materialised on parse, so a round-tripped document shows exactly what will run.

---

## 3. Operands

Three operand kinds, plus a range used only by `between`:

| `kind` | Shape | Unit |
|---|---|---|
| `price` | `{"kind":"price","field":"open\|high\|low\|close\|volume"}` | `price`, or `volume` for the volume field |
| `indicator` | `{"kind":"indicator","name":...,"period":N}` | see below |
| `constant` | `{"kind":"constant","value":30}` | adopts the other side |
| `range` | `{"kind":"range","low":20,"high":30}` | `between` only |

### Indicators

| `name` | Meaning | Unit | Min period |
|---|---|---|---|
| `sma` | Simple moving average of close | price | 2 |
| `ema` | Exponential moving average of close | price | 2 |
| `rsi` | Wilder's RSI, 0–100 | oscillator | 2 |
| `atr` | Wilder's average true range | price | 2 |
| `highest_high` | Highest high of the **prior** N bars | price | 2 |
| `lowest_low` | Lowest low of the **prior** N bars | price | 2 |
| `pct_change` | Percent change of close over N bars | percent | 1 |
| `volume_sma` | Simple moving average of volume | volume | 2 |

**Minimum periods are enforced**, by the Pydantic models and by the generated JSON
Schema alike, so a standalone consumer rejects exactly what Python rejects. Two is the
floor for anything that averages, smooths or spans a window: `SMA(1)` is the close,
`RSI(1)` is a constant 0 or 100, `ATR(1)` is the bar's own true range, a one-bar highest
high is the bar's own high. Those are not short windows, they are different quantities
wearing the indicator's name, and a grammar that promises to be explainable should not
accept a rule whose plain-English reading is false. `pct_change` is the exception: a
one-bar percent change is the daily return.

`highest_high` and `lowest_low` **exclude the current bar**: `highest_high(20)` on bar
`t` is the highest high of bars `t-20 .. t-1`. A breakout reference that included today's
own high could never be broken, which would make every `crosses_above` against it
silently dead. This is an **engine-facing contract** and is deliberately not enforceable
here — this package validates documents and never computes an indicator, so it has
nothing to check it against. It is exported as `EXCLUDES_CURRENT_BAR` in both `models.py`
and `strategy.ts` so an implementer has one authoritative place to read the rule.

**Units are enforced.** Comparing an RSI reading to a dollar price is a validation
error, not a silent nonsense result.

---

## 4. Comparisons

`above`/`below` and `greater_than`/`less_than` are **not synonyms**. The pair you choose
declares what you are comparing against, and the validator enforces it. The redundancy
in the vocabulary is spent on catching mistakes rather than on aliases.

| `op` | Right side must be | Reads as |
|---|---|---|
| `above` | a series | close above SMA(200) |
| `below` | a series | close below EMA(5) |
| `crosses_above` | a series | SMA(50) crosses above SMA(200) |
| `crosses_below` | a series | close crosses below the 10-bar lowest low |
| `greater_than` | a constant | RSI greater than 78 |
| `less_than` | a constant | the 5-bar % change less than −3 |
| `between` | a range | RSI between 20 and 30 (inclusive) |

The **left** side is always a series. A constant cannot cross anything, and `30 above
RSI` is a condition written backwards rather than a different one — the schema itself
rejects it.

A crossing requires both sides to be defined on the previous bar, so it is never true
on the first bar an indicator becomes available.

Groups are **flat** `all` / `any`, 1–8 conditions. Nesting is deliberately absent: a
nested boolean tree needs a nested editor to author, a nested renderer to explain, and a
reader who can hold precedence in their head.

---

## 5. Exits

An array of typed rules, not a bag of optional fields, so "this has an ATR stop and a 2R
target" is visible in the shape of the document. At least one exit is required; at most
one of each kind.

| `kind` | Fields | Priority |
|---|---|---|
| `atr_stop` | `atr_period`, `multiple`, `trail` | 0 |
| `percent_stop` | `percent`, `trail` | 0 |
| `r_multiple_target` | `multiple` | 1 |
| `percent_target` | `percent` | 1 |
| `time_exit` | `max_bars` | 2 |
| `signal_exit` | `logic`, `conditions` | 3 |
| `opposite_signal` | — | 3 |

**Lower priority number wins** when several fire on the same bar.

### Precedence, and the one place it is decided

Two rules govern which exit wins, and the second overrides the first where they meet:

1. The table above orders the exit **kinds** in general.
2. `execution.intrabar_priority` decides the **stop-versus-target pair** when a single
   daily bar's range covers both levels. `stop_first` (the default) keeps the base
   order. `target_first` moves targets ahead of stops — and only that pair; the time and
   signal bands are untouched.

Rule 2 wins where the two overlap, because a field that could never change an outcome
would be a lie.

**An engine must not read the priority table directly.** Call
`Strategy.effective_exit_priority()` (Python) or `effectiveExitPriority(strategy)`
(TypeScript), which fold both rules into one number per kind, or
`exits_in_priority_order()` / `exitsInPriorityOrder()`, which return the exact order to
test. Two conforming engines calling these get identical answers, so they cannot
disagree about which exit filled. That is the whole precedence contract: it is a
function, not a paragraph, so there is nothing left to interpret.

Stops before targets is the **conservative** assumption: when one bar's range covers
both, a daily bar contains no evidence of which came first, so the engine books the
outcome that hurts. `target_first` is the **aggressive** assumption — it books the
favourable outcome from a bar that contains no evidence for it, and because it improves
every affected trade it inflates win rate, profit factor and return together. It exists
because some strategies genuinely do fill the target first and refusing to model that
would be its own distortion, but a document that sets it is making a claim the data
cannot support, and the validator warns every time (`W_TARGET_FIRST`). Setting it on a
strategy that has no target at all is inert and warns separately
(`W_INERT_INTRABAR_PRIORITY`).

**Stops and targets are intrabar**, checked against the bar's high and low.
**Time and signal exits are end-of-bar**, filled under `execution.fill_timing`.

**1R is the distance from the entry fill to the initial stop** — not to a trailing stop,
and not to the sizing model's risk estimate. A strategy therefore cannot declare an
`r_multiple_target` without exactly one stop to measure R from.

**`opposite_signal`** is the entry group with every comparison replaced by its mirror
(`above↔below`, `crosses_above↔crosses_below`, `greater_than↔less_than`, `between` →
outside the range), keeping the same logic. It is deliberately *not* "the entry rule is
no longer true": the negation of `crosses_above` is true on almost every bar, so that
reading would close a position the day after it opened, every time.

**Maximum holding period** is `time_exit`. It is signalled on the close of the
`max_bars`-th bar and fills under `execution.fill_timing`, so a position lives one bar
longer than `max_bars` when filling at the next open. Stated explicitly because
off-by-one in holding period is the most common silent disagreement between two
backtesters.

---

## 6. Sizing

| `method` | Required params | Meaning |
|---|---|---|
| `equal_weight` | — | equity ÷ `max_open_positions` |
| `fixed_fraction` | `fraction` | a fixed share of equity per position |
| `fixed_notional` | `notional` | a fixed dollar amount per position |
| `atr_risk` | `risk_fraction`, `atr_period`, `atr_multiple` | shares such that a move to the stop loses `risk_fraction` of equity |

Exactly the parameters belonging to the chosen method may be present; any other is an
error, not an ignored field, so nothing can look like it has an effect that it does not.

`atr_risk` is the only method that ties size to volatility, and it is what makes
R-multiples comparable across symbols. `max_positions_per_symbol` is fixed at 1 — no
pyramiding in v2.

---

## 7. Universe, calendar, execution, costs

**Universe** is 1–50 tickers, uppercased, deduplicated and sorted on parse. The optional
`liquidity` screen filters per symbol per bar on *dollar* volume: a million shares of a
$3 stock and a million shares of a $300 stock are not the same market.

**Calendar** filters gate **entries only**. Exits are never calendar-gated — a filter
that could block an exit would be able to trap a position indefinitely, which is a way
to lose money that no backtest should be able to hide. On daily bars a session is one
trading day, so `days_of_week` is 1–5 (ISO, Mon–Fri).

**Execution** states the assumptions that change the result:

| Field | Values | Default |
|---|---|---|
| `signal_timing` | `close` | `close` |
| `fill_timing` | `next_open`, `next_close` | `next_open` |
| `intrabar_priority` | `stop_first`, `target_first` | `stop_first` |
| `gap_policy` | `fill_at_open`, `fill_at_level` | `fill_at_open` |
| `candidate_priority` | `alphabetical`, `highest_dollar_volume` | `alphabetical` |

`signal_timing` has one legal value. Any other would permit same-bar execution, which is
how backtests lie; the field exists so the assumption is stated rather than implied.

`intrabar_priority` is the aggressive/conservative switch described in §5. It is the one
execution field that changes which exit fills rather than only where it fills.

**Costs** carry commission (bps per leg, plus an optional per-order minimum) and one of
two slippage models: `fixed_bps`, or `atr_fraction` which scales with the symbol's own
volatility. `slippage_bps` has no plain default because it is model-specific — it is
filled with 5 only under `fixed_bps`, and must be absent otherwise.

Costs live *inside* the strategy so a result is reproducible from the document alone. A
runner may override them to sweep cost sensitivity, but the override has to be recorded
alongside the result: the document is the default, not a suggestion.

---

## 8. Validation rules

Validation runs in two passes and **reports everything it can at once**, so a caller
fixes the document once instead of rediscovering the next problem. Structural errors
short-circuit the semantic pass — semantic rules need a parsed document — but all
structural errors are reported together, and then all semantic ones.

### Structural (enforced by the schema)

| # | Rule |
|---|---|
| S1 | `dsl_version` is exactly `"2.0.0"` |
| S2 | No additional properties, anywhere |
| S3 | Every enum is closed (`kind`, `op`, `name`, `method`, `logic`, …) |
| S4 | Every number is bounded (periods 1–500, percents, multiples, fractions) |
| S5 | Lists are capped: 8 conditions, 6 exits, 50 symbols |
| S6 | Required fields present: `name`, `universe`, `entry`, `exits`, `sizing` |
| S7 | The left side of a comparison is a series — a constant there is a type error |
| S8 | Tickers match `^[A-Z][A-Z0-9.\-]{0,9}$` |
| S9 | Indicator periods meet their per-indicator minimum (2, or 1 for `pct_change`) — enforced by the schema's `if`/`then` rules as well as by the models |

### Semantic (enforced by `validate.py`)

| # | Rule | Code |
|---|---|---|
| V1 | `above`/`below`/`crosses_*` need a series on the right | `E_COMPARISON_NEEDS_SERIES` |
| V2 | `greater_than`/`less_than` need a constant on the right | `E_COMPARISON_NEEDS_CONSTANT` |
| V3 | `between` needs a range on the right | `E_COMPARISON_NEEDS_RANGE` |
| V4 | A range is only legal with `between` | `E_RANGE_NOT_ALLOWED` |
| V5 | `range.low < range.high` | `E_RANGE_BOUNDS` |
| V6 | Both sides of a series comparison share a unit | `E_UNIT_MISMATCH` |
| V7 | A series is not compared with itself | `E_SELF_COMPARISON` |
| V8 | Each exit kind appears at most once | `E_DUPLICATE_EXIT` |
| V9 | `signal_exit` and `opposite_signal` are mutually exclusive | `E_CONFLICTING_SIGNAL_EXITS` |
| V10 | `r_multiple_target` requires a stop | `E_R_MULTIPLE_NEEDS_STOP` |
| V11 | `r_multiple_target` requires **exactly one** stop | `E_R_MULTIPLE_AMBIGUOUS` |
| V12 | Sizing carries its method's parameters | `E_SIZING_MISSING_PARAM` |
| V13 | Sizing carries no other method's parameters | `E_SIZING_UNEXPECTED_PARAM` |
| V14 | `fraction × max_open_positions ≤ 1` — no implicit leverage | `E_SIZING_OVER_ALLOCATION` |
| V15 | Slippage carries its model's parameters, and no others | `E_SLIPPAGE_*_PARAM` |
| V16 | `calendar.date_range.start < end` | `E_CALENDAR_DATE_ORDER` |
| V17 | `days_of_week ⊆ 1..5` — daily bars have no weekend session | `E_CALENDAR_WEEKDAY` |
| V18 | `months ⊆ 1..12` | `E_CALENDAR_MONTH` |
| V19 | Indicator period meets its minimum | `E_PERIOD_TOO_SHORT` |

### Warnings — never block

| # | Trigger | Code |
|---|---|---|
| W1 | A price compared to a bare number (scale-dependent across symbols) | `W_PRICE_VS_CONSTANT` |
| W2 | No stop of any kind (per-trade loss is unbounded by the document) | `W_NO_STOP` |
| W3 | Target nearer than the stop (each win smaller than each loss) | `W_TARGET_INSIDE_STOP` |
| W4 | Zero commission or zero slippage | `W_ZERO_COSTS` |
| W5 | `intrabar_priority: target_first` — flatters every result | `W_TARGET_FIRST` |
| W6 | `gap_policy: fill_at_level` — assumes a fill at a price that never traded | `W_FILL_AT_LEVEL` |
| W7 | `atr_risk` sizing disagrees with the ATR stop, so risk ≠ 1R | `W_SIZING_STOP_MISMATCH` |
| W8 | Fewer than 3 symbols | `W_NARROW_UNIVERSE` |
| W9 | A condition duplicated inside one group | `W_DUPLICATE_CONDITION` |
| W10 | A fixed and a trailing stop together (the tighter binds) | `W_TRAILING_AND_FIXED` |
| W11 | `target_first` on a strategy with no target — the setting can never apply | `W_INERT_INTRABAR_PRIORITY` |

---

## 9. Error messages

Every issue carries four things, because a message that only says *what* is wrong makes
the reader guess at *where* and *what to do instead*:

```json
{
  "code": "E_COMPARISON_NEEDS_CONSTANT",
  "severity": "error",
  "path": "/entry/conditions/0/right",
  "message": "'greater_than' compares against a fixed number, but the right side is an indicator. Use 'above'/'below' to compare against another series, or replace the right side with {\"kind\": \"constant\", \"value\": ...}.",
  "found": {"kind": "indicator", "name": "sma", "period": 50}
}
```

`path` is a JSON Pointer, so an editor can highlight the node without re-deriving the
location from the message.

Model validators tag their message with `[CODE]` (see `models.coded`), which the error
mapper reads and strips. The alternative — inferring the code from the wording — breaks
the moment someone rewrites a message.

### Catalogue

| Code | Message shape |
|---|---|
| `E_VERSION` | This document declares dsl_version '1.0.0', but this validator implements 2.0.0. Versions are not interchangeable. |
| `E_UNKNOWN_FIELD` | Unknown field 'stop_loss'. The DSL rejects fields it does not recognise rather than ignoring them, so a typo cannot silently disable a rule. |
| `E_MISSING_FIELD` | Required field 'universe' is missing. |
| `E_NOT_IN_ENUM` | 'method' must be 'equal_weight', 'fixed_fraction', 'fixed_notional' or 'atr_risk'. The DSL uses closed vocabularies so that every strategy can be explained and executed the same way everywhere. |
| `E_OUT_OF_RANGE` | 'period' is out of range (less than equal 500). |
| `E_BAD_LENGTH` | 'symbols' must not be empty. |
| `E_BAD_TICKER` | Invalid ticker '../etc/passwd'. Tickers are 1-10 characters, starting with a letter, using A-Z, 0-9, '.' and '-' only. |
| `E_PERIOD_TOO_SHORT` | sma needs a period of at least 2, not 1. At 1 it is the close itself written the long way. Use {"kind": "price", "field": "close"} if that is what you meant. |
| `E_CALENDAR_WEEKDAY` | days_of_week must be 1-5 (Mon-Fri). Daily bars have no weekend session, so 6 and 7 could never match a bar. |
| `E_CALENDAR_MONTH` | months must be 1-12. |
| `E_NOT_AN_OBJECT` | The document is not valid JSON: Expecting property name at line 1, column 25. |
| `E_COMPARISON_NEEDS_SERIES` | 'above' compares one series against another, but the right side is a constant. Use 'greater_than'/'less_than' to compare against a fixed number, or put a price field or indicator on the right. |
| `E_COMPARISON_NEEDS_CONSTANT` | 'greater_than' compares against a fixed number, but the right side is an indicator. |
| `E_COMPARISON_NEEDS_RANGE` | 'between' needs a range on the right: {"kind": "range", "low": ..., "high": ...}. |
| `E_RANGE_NOT_ALLOWED` | A range is only meaningful with 'between'; 'less_than' takes a single value. |
| `E_RANGE_BOUNDS` | Range low (70) must be below high (30). |
| `E_UNIT_MISMATCH` | Cannot compare RSI(14) (oscillator) with SMA(50) (price). Comparing across units is almost always a mistake; to relate a price to a volume, compare each to its own average. |
| `E_SELF_COMPARISON` | Both sides of this comparison are the same series, so it is always true or always false. |
| `E_DUPLICATE_EXIT` | 2 exits of kind 'time_exit'. Each exit kind may appear once: two of the same kind would need a tie-break rule that the DSL deliberately does not have. |
| `E_CONFLICTING_SIGNAL_EXITS` | 'signal_exit' and 'opposite_signal' are two ways to spell the same slot and share an evaluation priority. |
| `E_R_MULTIPLE_NEEDS_STOP` | An R-multiple target measures profit in units of the initial risk, and this strategy has no stop, so 1R is undefined. Add an 'atr_stop' or a 'percent_stop', or use 'percent_target' instead. |
| `E_R_MULTIPLE_AMBIGUOUS` | An R-multiple target needs exactly one stop to measure 1R from; this strategy has 2 (percent_stop, atr_stop). |
| `E_SIZING_MISSING_PARAM` | Sizing method 'fixed_fraction' requires 'fraction'. |
| `E_SIZING_UNEXPECTED_PARAM` | 'notional' does not apply to sizing method 'fixed_fraction'. Leaving it in would suggest it has an effect that it does not. |
| `E_SIZING_OVER_ALLOCATION` | 50.0% per position across 5 slots commits 250% of equity. The DSL does not model leverage, so reduce the fraction to at most 20.0% or lower max_open_positions. |
| `E_SLIPPAGE_MISSING_PARAM` | Slippage model 'atr_fraction' requires 'slippage_atr_fraction'. |
| `E_SLIPPAGE_UNEXPECTED_PARAM` | 'slippage_bps' does not apply to slippage model 'atr_fraction'. |
| `E_CALENDAR_DATE_ORDER` | Start (2020-01-01) must be before end (2019-01-01). |

---

## 10. Examples

| File | Demonstrates |
|---|---|
| `01-rsi-pullback.json` | `between`, `above`, `less_than`, `percent_stop`, `r_multiple_target`, `time_exit`, equal-weight sizing |
| `02-donchian-breakout.json` | `crosses_above`/`crosses_below`, `highest_high`/`lowest_low`, volume filter, liquidity screen, `atr_risk` sizing, trailing `atr_stop`, `signal_exit`, ATR-fraction slippage |
| `03-golden-cross.json` | `opposite_signal`, trailing `percent_stop`, `fixed_fraction` sizing |
| `04-short-fade.json` | `direction: short`, `greater_than`, `below`, `ema`, `pct_change`, `percent_target`, calendar `date_range` and `months` |
| `05-monday-dip.json` | `days_of_week`, liquidity screen, `fixed_notional` sizing, `signal_exit`, `next_close` fills |

A test asserts these five between them exercise **every** comparison, indicator, exit
kind, sizing method, slippage model and calendar filter in the grammar. A feature no
example exercises is a feature nobody has checked.

---

## 11. Versioning

`dsl_version` is required and exact. A validator refuses a document it does not
implement rather than guessing, because a silently-misread strategy is worse than a
rejected one.

* **Patch** — clarified docs, better messages. No document changes meaning.
* **Minor** — new optional field with a documented default. Old documents still valid.
* **Major** — anything that changes what an existing document means, or removes a case.

### 2.0.0 hardening pass

No grammar change, so `dsl_version` stays at `2.0.0` — but two documents that used to
validate no longer do, which is worth knowing if any exist:

* An indicator with `period: 1` (other than `pct_change`) is now rejected. The minimum
  was always documented; it simply was not enforced.
* `execution.intrabar_priority` now demonstrably governs the stop/target pair through
  `effective_exit_priority()`. The intended reading is unchanged; it was previously only
  implied, which left two conforming engines free to disagree.

### Differences from v1.0.0

v1 is not a subset. The changes are deliberate:

| | v1.0.0 | v2.0.0 |
|---|---|---|
| Indicators | 24 | 8 |
| Comparisons | `<`, `<=`, `>`, `>=`, crossings | named, with operand-kind enforcement, plus `between` |
| Exits | flat `RiskRules` fields + one exit group | 6 typed exit rules with a fixed priority table |
| Sizing | equal weight only | 4 methods, including volatility-based |
| Calendar filters | none | date range, weekday, month |
| Execution & costs | separate run config | inside the document |
| R-multiples | none | with a stop-derived definition of 1R |

v1 chose indicator breadth; v2 spends that budget on precise exits and self-describing
execution instead. Eight indicators cover every example strategy anyone has written for
this system, and the sixteen that went were mostly variations nobody reached for.

---

## 12. Deliberately absent

Nested boolean logic · intraday bars · options, futures, FX · multi-leg positions ·
pyramiding and scaling in or out · cross-symbol and portfolio-level conditions
(pair trades, sector caps, correlation filters) · fundamental data · short-borrow costs
and financing · parameter optimisation or search · point-in-time universe construction.

Each is a real thing strategies do. Each would also make the grammar harder to explain
than it is to use, which is the trade this DSL refuses to make.
