/**
 * ProofTrade Strategy DSL v2.0.0 — TypeScript types.
 *
 * Mirrors `models.py`, which is the source of truth; `strategy.schema.json` is
 * generated from the same models and `tests/test_dsl_v2.py` asserts all three agree
 * field-for-field, so this file cannot silently drift.
 *
 * These types describe the wire format only. They are deliberately structural: every
 * union is discriminated by a literal `kind`, so `switch (operand.kind)` narrows
 * exhaustively and adding a variant becomes a compile error at every call site rather
 * than a runtime surprise.
 *
 * A strategy is data. There is no expression string, formula field, or callback in this
 * grammar — nothing here needs an evaluator to interpret.
 */

export const DSL_VERSION = '2.0.0' as const

export const LIMITS = {
  MIN_PERIOD: 1,
  MAX_PERIOD: 500,
  MAX_CONDITIONS: 8,
  MAX_SYMBOLS: 50,
  MAX_EXITS: 6,
} as const

/* ------------------------------------------------------------------------------------
   Operands
------------------------------------------------------------------------------------ */

/**
 * Comparing an RSI reading to a dollar price is meaningless, so every series operand
 * carries a unit and the validator refuses to compare across incompatible ones.
 */
export type Unit = 'price' | 'volume' | 'oscillator' | 'percent'

export type PriceField = 'open' | 'high' | 'low' | 'close' | 'volume'

export type IndicatorName =
  | 'sma'            // simple moving average of close
  | 'ema'            // exponential moving average of close
  | 'rsi'            // Wilder's relative strength index, 0-100
  | 'atr'            // Wilder's average true range, in price units
  | 'highest_high'   // highest high of the PRIOR `period` bars
  | 'lowest_low'     // lowest low of the PRIOR `period` bars
  | 'pct_change'     // percent change of close over `period` bars
  | 'volume_sma'     // simple moving average of volume

/**
 * Minimum period per indicator, enforced by the Python models and mirrored into the
 * generated JSON Schema.
 *
 * Two is the floor for anything that averages, smooths or spans a window: SMA(1) is
 * just the close, RSI(1) is a constant 0 or 100, ATR(1) is the bar's own true range.
 * Those are ways of writing something else by accident. `pct_change` is the exception —
 * a one-bar percent change is the daily return.
 */
export const MIN_PERIOD_BY_INDICATOR: Record<IndicatorName, number> = {
  sma: 2,
  ema: 2,
  rsi: 2,
  atr: 2,
  highest_high: 2,
  lowest_low: 2,
  pct_change: 1,
  volume_sma: 2,
}

/**
 * `highest_high(20)` on bar t is the highest high of bars t-20..t-1, EXCLUDING bar t.
 *
 * Engine-facing contract, intentionally not enforceable by a validator: this package
 * checks documents and never computes an indicator. A reference that included today's
 * own high could never be broken, which would make every `crosses_above` against it
 * silently dead.
 */
export const EXCLUDES_CURRENT_BAR: readonly IndicatorName[] = ['highest_high', 'lowest_low']

export const INDICATOR_UNITS: Record<IndicatorName, Unit> = {
  sma: 'price',
  ema: 'price',
  rsi: 'oscillator',
  atr: 'price',
  highest_high: 'price',
  lowest_low: 'price',
  pct_change: 'percent',
  volume_sma: 'volume',
}

/** A raw field of the current bar. */
export interface PriceOperand {
  kind: 'price'
  field: PriceField
}

/** One indicator reading on the current bar. */
export interface IndicatorOperand {
  kind: 'indicator'
  name: IndicatorName
  /** 1–500. `pct_change` accepts 1; every other indicator needs at least 2. */
  period: number
}

/** A fixed number: the 30 in "RSI less than 30". */
export interface ConstantOperand {
  kind: 'constant'
  value: number
}

/** An inclusive numeric interval. Only `between` accepts one. */
export interface RangeOperand {
  kind: 'range'
  low: number
  high: number
}

/**
 * The left side of a comparison is always a series. A constant cannot cross anything,
 * and "30 above RSI" is a condition written backwards rather than a different one.
 */
export type SeriesOperand = PriceOperand | IndicatorOperand
export type RightOperand = SeriesOperand | ConstantOperand | RangeOperand

/* ------------------------------------------------------------------------------------
   Comparisons
------------------------------------------------------------------------------------ */

/**
 * `above`/`below` and `greater_than`/`less_than` are NOT synonyms — the pair you use
 * declares what you are comparing against, and the validator enforces it:
 *
 *   above | below                  → right side must be another SERIES
 *   crosses_above | crosses_below  → right side must be a SERIES; both sides must be
 *                                    defined on the previous bar
 *   greater_than | less_than       → right side must be a CONSTANT
 *   between                        → right side must be a RANGE
 *
 * The redundancy in the vocabulary is spent on catching mistakes, not on aliases.
 */
export type Comparison =
  | 'above'
  | 'below'
  | 'crosses_above'
  | 'crosses_below'
  | 'greater_than'
  | 'less_than'
  | 'between'

export const SERIES_COMPARISONS: readonly Comparison[] = [
  'above', 'below', 'crosses_above', 'crosses_below',
]
export const CONSTANT_COMPARISONS: readonly Comparison[] = ['greater_than', 'less_than']
export const RANGE_COMPARISONS: readonly Comparison[] = ['between']

/** Used by the `opposite_signal` exit. See `OppositeSignal`. */
export const MIRROR: Record<Comparison, Comparison> = {
  above: 'below',
  below: 'above',
  crosses_above: 'crosses_below',
  crosses_below: 'crosses_above',
  greater_than: 'less_than',
  less_than: 'greater_than',
  between: 'between',
}

/** `left <op> right`, evaluated on the close of each bar, per symbol. */
export interface Condition {
  left: SeriesOperand
  op: Comparison
  right: RightOperand
}

/**
 * A flat conjunction or disjunction.
 *
 * Flat on purpose: a nested boolean tree needs a nested editor to author, a nested
 * renderer to explain, and a reader who can hold precedence in their head.
 */
export interface ConditionGroup {
  logic: 'all' | 'any'
  /** 1–8 conditions. */
  conditions: Condition[]
}

/* ------------------------------------------------------------------------------------
   Exits
------------------------------------------------------------------------------------ */

export type ExitKind =
  | 'atr_stop'
  | 'percent_stop'
  | 'r_multiple_target'
  | 'percent_target'
  | 'time_exit'
  | 'signal_exit'
  | 'opposite_signal'

/**
 * Base precedence between exit KINDS. Lower number wins.
 *
 * This table is the default only. The stop-versus-target pair is additionally governed
 * by `execution.intrabar_priority`. Do not read this table directly when implementing
 * an engine — call `effectiveExitPriority(strategy)`, which folds both rules into one
 * answer.
 */
export const EXIT_PRIORITY: Record<ExitKind, number> = {
  atr_stop: 0,
  percent_stop: 0,
  r_multiple_target: 1,
  percent_target: 1,
  time_exit: 2,
  signal_exit: 3,
  opposite_signal: 3,
}

/**
 * Stop placed `multiple` × ATR(atr_period) from entry. With `trail: true` it follows
 * the best price reached since entry and never moves against the position.
 */
export interface AtrStop {
  kind: 'atr_stop'
  atr_period: number
  multiple: number
  trail: boolean
}

/** Stop `percent` from entry, or from the best price when trailing. */
export interface PercentStop {
  kind: 'percent_stop'
  percent: number
  trail: boolean
}

/**
 * Target at `multiple` × the initial risk.
 *
 * 1R is the distance from the entry fill to the INITIAL stop level — not to a trailing
 * stop, and not to the sizing model's risk estimate. A strategy therefore cannot
 * declare an R-multiple target without declaring exactly one stop to measure R from.
 */
export interface RMultipleTarget {
  kind: 'r_multiple_target'
  multiple: number
}

export interface PercentTarget {
  kind: 'percent_target'
  percent: number
}

/**
 * Maximum holding period, in bars held.
 *
 * Signalled on the close of the `max_bars`-th bar and filled under
 * `execution.fill_timing`, so a position lives one bar longer than `max_bars` when
 * filling at the next open.
 */
export interface TimeExit {
  kind: 'time_exit'
  max_bars: number
}

/** An explicit exit rule set, evaluated like the entry group. */
export interface SignalExit {
  kind: 'signal_exit'
  logic: 'all' | 'any'
  conditions: Condition[]
}

/**
 * Exit when the mirror of the entry rule fires: the entry group with every comparison
 * replaced by its `MIRROR`, keeping the same logic.
 *
 * Deliberately not "the entry rule is no longer true" — the negation of `crosses_above`
 * is true on almost every bar, so that reading would close a position the day after it
 * opened, every time.
 */
export interface OppositeSignal {
  kind: 'opposite_signal'
}

export type ExitRule =
  | AtrStop
  | PercentStop
  | RMultipleTarget
  | PercentTarget
  | TimeExit
  | SignalExit
  | OppositeSignal

/* ------------------------------------------------------------------------------------
   Sizing, universe, calendar
------------------------------------------------------------------------------------ */

export type SizingMethod =
  | 'equal_weight'
  | 'fixed_fraction'
  | 'fixed_notional'
  | 'atr_risk'

/**
 * How much to buy, and how many positions may be open at once.
 *
 * Exactly the parameters belonging to `method` may be present; any other is a
 * validation error rather than an ignored field, so nothing can look like it has an
 * effect that it does not.
 */
export interface Sizing {
  method: SizingMethod
  max_open_positions: number
  /** Always 1 — no pyramiding in v2. */
  max_positions_per_symbol: 1
  allow_fractional_shares: boolean

  /** `fixed_fraction` only. Fraction × max_open_positions must not exceed 1. */
  fraction?: number | null
  /** `fixed_notional` only. */
  notional?: number | null
  /** `atr_risk` only. */
  risk_fraction?: number | null
  atr_period?: number | null
  atr_multiple?: number | null
}

/**
 * A structural screen applied per symbol per bar, before any entry is considered.
 * Dollar volume rather than share volume: a million shares of a $3 stock and a million
 * shares of a $300 stock are not the same market.
 */
export interface Liquidity {
  min_avg_dollar_volume: number
  lookback: number
}

export interface Universe {
  /** 1–50 tickers, uppercased, deduplicated and sorted on parse. */
  symbols: string[]
  liquidity?: Liquidity | null
}

export interface DateRange {
  /** ISO date, YYYY-MM-DD. */
  start: string
  end: string
}

/**
 * Date and session filters. These gate ENTRIES ONLY.
 *
 * Exits are never calendar-gated: a filter that could block an exit would be able to
 * trap a position indefinitely, which is a way to lose money no backtest should hide.
 *
 * On daily bars a session is one trading day, so `days_of_week` is 1–5 (ISO, Mon–Fri).
 */
export interface Calendar {
  date_range?: DateRange | null
  days_of_week?: number[] | null
  months?: number[] | null
}

/* ------------------------------------------------------------------------------------
   Execution and costs
------------------------------------------------------------------------------------ */

/**
 * Execution assumptions, stated in the document rather than assumed by the engine.
 * Every field here changes the reported result; making them explicit is what lets two
 * people compare two backtests and know they are comparing the same thing.
 */
export interface Execution {
  /**
   * Always 'close'. Any other value would permit same-bar execution, which is how
   * backtests lie; the field exists so the assumption is stated rather than implied.
   */
  signal_timing: 'close'
  fill_timing: 'next_open' | 'next_close'
  /**
   * Which fills when one bar's range covers both a stop and a target. A daily bar
   * records no intrabar path, so this is an assumption either way.
   *
   * `stop_first` (default) is the CONSERVATIVE assumption: it books the outcome that
   * hurts and cannot flatter a result.
   *
   * `target_first` is the AGGRESSIVE assumption. It books the favourable outcome from a
   * bar containing no evidence for it, inflating win rate, profit factor and return
   * together. The validator warns on it every time. See `effectiveExitPriority`.
   */
  intrabar_priority: 'stop_first' | 'target_first'
  /** Where an order fills when the bar opens beyond the level. */
  gap_policy: 'fill_at_open' | 'fill_at_level'
  /** Tie-break when more candidates fire than there are free slots. */
  candidate_priority: 'alphabetical' | 'highest_dollar_volume'
}

/**
 * Transaction costs and slippage. Slippage moves the fill price against the position;
 * commission is charged on the notional of both legs.
 *
 * `slippage_bps` has no plain default because it is model-specific: it is filled in
 * with 5 only when `slippage_model` is `fixed_bps`, and must be absent otherwise.
 */
export interface Costs {
  commission_bps: number
  commission_min_usd: number
  slippage_model: 'fixed_bps' | 'atr_fraction'
  slippage_bps?: number | null
  slippage_atr_fraction?: number | null
  slippage_atr_period?: number | null
}

/* ------------------------------------------------------------------------------------
   Strategy
------------------------------------------------------------------------------------ */

/**
 * A complete, self-describing strategy: the document carries its own execution
 * assumptions and cost model, so a result can be reproduced from the strategy alone.
 *
 * A runner may override costs to sweep them for sensitivity analysis, but the override
 * has to be recorded alongside the result — the document is the default, not a
 * suggestion.
 */
export interface Strategy {
  dsl_version: typeof DSL_VERSION
  name: string
  description: string
  /** The original English the strategy was translated from, when there was any. */
  source_text: string

  universe: Universe
  direction: 'long' | 'short'
  entry: ConditionGroup
  /** 1–6 rules, at most one of each kind. */
  exits: ExitRule[]
  sizing: Sizing
  calendar: Calendar
  execution: Execution
  costs: Costs
}

/* ------------------------------------------------------------------------------------
   Validation results
------------------------------------------------------------------------------------ */

export type Severity = 'error' | 'warning'

/**
 * One problem with a document. `path` is a JSON Pointer to the offending node, so a
 * UI can highlight it without re-deriving the location from the message.
 */
export interface Issue {
  code: string
  severity: Severity
  path: string
  message: string
  found: unknown
}

export interface ValidationResult {
  ok: boolean
  errors: Issue[]
  warnings: Issue[]
}

/** Narrowing helpers, so call sites never test `kind` with a bare string. */
export const STOP_KINDS: readonly ExitKind[] = ['atr_stop', 'percent_stop']
export const TARGET_KINDS: readonly ExitKind[] = ['r_multiple_target', 'percent_target']

/**
 * Exit precedence for one document, with the intrabar rule already applied.
 *
 * Two rules govern which exit wins, and this folds them into one number per kind so an
 * engine never has to reconcile them itself:
 *
 * 1. `EXIT_PRIORITY` orders the kinds in general: stops, targets, time, signals.
 * 2. `execution.intrabar_priority` decides the stop-versus-target pair when a single
 *    daily bar's range covers both levels. `stop_first` (the default) keeps the base
 *    order; `target_first` moves targets ahead of stops, and only that pair.
 *
 * Rule 2 wins where they overlap, because a field that could never change an outcome
 * would be a lie. Two conforming engines calling this get identical numbers.
 *
 * Mirrors `Strategy.effective_exit_priority()` in models.py.
 */
export function effectiveExitPriority(strategy: Strategy): Record<ExitKind, number> {
  const priority: Record<ExitKind, number> = { ...EXIT_PRIORITY }
  if (strategy.execution.intrabar_priority === 'target_first') {
    const stopBand = Math.min(...STOP_KINDS.map((k) => EXIT_PRIORITY[k]))
    for (const kind of TARGET_KINDS) priority[kind] = stopBand - 1
  }
  return priority
}

/**
 * This document's exits in the exact order an engine must test them.
 *
 * Authoritative — not the `exits` array, and not `EXIT_PRIORITY` alone. Mirrors
 * `Strategy.exits_in_priority_order()`.
 */
export function exitsInPriorityOrder(strategy: Strategy): ExitRule[] {
  const priority = effectiveExitPriority(strategy)
  return [...strategy.exits].sort(
    (a, b) => priority[a.kind] - priority[b.kind] || a.kind.localeCompare(b.kind),
  )
}

export const isSeriesOperand = (o: RightOperand): o is SeriesOperand =>
  o.kind === 'price' || o.kind === 'indicator'

export const operandUnit = (o: SeriesOperand): Unit =>
  o.kind === 'price' ? (o.field === 'volume' ? 'volume' : 'price') : INDICATOR_UNITS[o.name]
