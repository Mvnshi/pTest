/**
 * Wire types for the ProofTrade API.
 *
 * Every interface here mirrors a JSON shape the FastAPI backend actually emits
 * (see docs/samples/backtest_response.json). Field names are the server's, not
 * the UI's: nothing is renamed on the way in, so a field on screen can always
 * be traced back to a key in the payload. Where the backend can send `null` the
 * type says so, because a degenerate run - no trades, a window too short for
 * folds - is a normal outcome that still has to render.
 */

/* ---------------------------------------------------------------------------
   Strategy DSL
--------------------------------------------------------------------------- */

/** Fixed indicator registry. The DSL is a whitelist; nothing else parses. */
export type IndicatorName =
  | 'close'
  | 'open'
  | 'high'
  | 'low'
  | 'volume'
  | 'sma'
  | 'ema'
  | 'macd'
  | 'macd_signal'
  | 'macd_hist'
  | 'donchian_high'
  | 'donchian_low'
  | 'dist_from_sma_pct'
  | 'rsi'
  | 'roc'
  | 'atr'
  | 'atr_pct'
  | 'stdev_pct'
  | 'bb_upper'
  | 'bb_lower'
  | 'bb_pctb'
  | 'drawdown_pct'
  | 'volume_sma'
  | 'rel_volume'

export type Comparator = '<' | '<=' | '>' | '>=' | 'crosses_above' | 'crosses_below'

export type GroupLogic = 'all' | 'any'

export type Direction = 'long' | 'short'

/** One side of a comparison: a named indicator with its typed parameters. */
export interface IndicatorOperand {
  kind: 'indicator'
  name: IndicatorName
  period: number | null
  k: number | null
  fast: number | null
  slow: number | null
  signal: number | null
}

/** One side of a comparison: a bare number. */
export interface ConstantOperand {
  kind: 'constant'
  value: number
}

export type Operand = IndicatorOperand | ConstantOperand

export interface Condition {
  left: Operand
  op: Comparator
  right: Operand
}

export interface ConditionGroup {
  logic: GroupLogic
  conditions: Condition[]
}

export interface RiskRules {
  stop_loss_pct: number | null
  take_profit_pct: number | null
  trailing_stop_pct: number | null
  max_holding_days: number | null
}

export interface PositionRules {
  direction: Direction
  sizing: 'equal_weight'
  max_positions: number
  /** Always 1 in the MVP - no pyramiding. */
  max_positions_per_symbol: number
}

export interface Strategy {
  dsl_version: string
  name: string
  source_text: string
  universe: string[]
  position: PositionRules
  entry: ConditionGroup
  exit: ConditionGroup
  risk: RiskRules
}

/**
 * Server-rendered plain-English form of the strategy. Rendered on the backend
 * so the prose and the executed rules can never drift apart.
 */
export interface StrategyRender {
  direction: string
  universe: string[]
  entry_logic: string
  entry: string[]
  exit_logic: string
  exit: string[]
  sizing: string
}

/* ---------------------------------------------------------------------------
   Run configuration
--------------------------------------------------------------------------- */

export interface BacktestConfig {
  /** ISO date, or null for "the whole snapshot". */
  start: string | null
  end: string | null
  initial_capital: number
  commission_bps: number
  slippage_bps: number
  benchmark: string
  train_fraction: number
  walk_forward_folds: number
  run_robustness: boolean
  seed: number
}

/* ---------------------------------------------------------------------------
   Metrics and series
--------------------------------------------------------------------------- */

export interface Metrics {
  start: string
  end: string
  days: number
  years: number
  initial_equity: number
  final_equity: number
  total_return_pct: number
  cagr_pct: number
  volatility_pct: number
  sharpe: number
  sortino: number
  max_drawdown_pct: number
  max_drawdown_days: number
  calmar: number
  exposure_pct: number
  trades: number
  win_rate_pct: number
  profit_factor: number
  avg_win_pct: number
  avg_loss_pct: number
  expectancy_pct: number
  avg_holding_days: number
  total_costs: number
  cost_drag_pct: number
  best_day_pct: number
  worst_day_pct: number
}

/** The benchmark block is a Metrics plus the ticker it was measured on. */
export interface BenchmarkMetrics extends Metrics {
  symbol: string
}

/** One trading day of the equity curve. ~5,000 of these per report. */
export interface SeriesPoint {
  date: string
  equity: number
  benchmark: number
  /** Percent below the running peak, so always <= 0. */
  drawdown: number
  benchmark_drawdown: number
  /** Percent of equity deployed on this bar. */
  exposure: number
}

export type ExitReason = string

export interface Trade {
  symbol: string
  direction: Direction
  entry_date: string
  exit_date: string
  entry_price: number
  exit_price: number
  shares: number
  notional: number
  gross_pnl: number
  costs: number
  net_pnl: number
  return_pct: number
  holding_days: number
  /** "signal" | "stop_loss" | "take_profit" | "trailing_stop" | "time_exit" | "end_of_data" */
  exit_reason: ExitReason
  /** Maximum adverse excursion while the position was open, in percent. */
  mae_pct: number
  /** Maximum favourable excursion while the position was open, in percent. */
  mfe_pct: number
}

/* ---------------------------------------------------------------------------
   Breakdowns
--------------------------------------------------------------------------- */

export interface SymbolStat {
  symbol: string
  trades: number
  net_pnl: number
  pnl_share_pct: number
  win_rate_pct: number
  avg_return_pct: number
  total_return_pct: number
  best_trade_pct: number
  worst_trade_pct: number
}

/** One calendar year of the run. */
export interface PeriodStat {
  label: string
  days: number
  return_pct: number
  benchmark_return_pct: number
  max_drawdown_pct: number
  trades: number
  sharpe: number
}

/** One market regime, classified on the benchmark, not on the strategy. */
export interface RegimeStat {
  regime: string
  days: number
  share_of_time_pct: number
  return_pct: number
  benchmark_return_pct: number
  hit_rate_pct: number
  trades: number
}

export interface ConcentrationDetail {
  symbol_pnl: Record<string, number>
}

export interface Concentration {
  top_symbol: string
  top_symbol_pnl_share_pct: number
  top5_trades_pnl_share_pct: number
  top10_days_return_share_pct: number
  profitable_symbols: number
  total_symbols: number
  positive_years: number
  total_years: number
  detail: ConcentrationDetail
}

/* ---------------------------------------------------------------------------
   Validation: in-sample / out-of-sample, walk-forward, robustness probes
--------------------------------------------------------------------------- */

export interface SplitValidation {
  split_date: string | null
  train: Metrics
  test: Metrics
  /** Out-of-sample Sharpe / in-sample Sharpe. Null when in-sample Sharpe ~ 0. */
  sharpe_retention: number | null
  degradation_pct: number | null
  note: string
}

export interface Fold {
  index: number
  start: string
  end: string
  metrics: Metrics
}

export interface WalkForwardResult {
  folds: Fold[]
  positive_folds: number
  total_folds: number
  median_sharpe: number
  worst_fold_return_pct: number | null
  note: string
}

export interface CostPoint {
  /** Multiple of the configured commission and slippage: 0, 1, 2, 3, 5. */
  multiple: number
  round_trip_bps: number
  cagr_pct: number
  sharpe: number
  total_return_pct: number
  trades: number
}

export interface CostSensitivity {
  points: CostPoint[]
  base_sharpe: number
  sharpe_at_3x: number
  sharpe_retained_at_3x: number | null
  /** Round-trip cost at which CAGR turns negative. Null when none in range. */
  breakeven_round_trip_bps: number | null
  survives_5x: boolean
  /**
   * False when a higher cost level produced a HIGHER return somewhere in the sweep.
   * Stops and targets are anchored to the slipped fill, so changing the cost level
   * moves those levels and a different set of trades is stopped out. When this is
   * false, `note` explains it and the curve should be read as five separate
   * backtests rather than one strategy being taxed.
   */
  monotonic: boolean
  note: string
}

/** One perturbed variant of a numeric DSL parameter. */
export interface Neighbour {
  label: string
  path: string
  base_value: number
  value: number
  /** 0.8, 0.9, 1.1, 1.2 - the perturbation applied to base_value. */
  factor: number
  sharpe: number
  cagr_pct: number
  trades: number
}

export interface ParameterRobustness {
  neighbours: Neighbour[]
  base_sharpe: number
  median_sharpe: number
  worst_sharpe: number
  profitable_share_pct: number | null
  /** base Sharpe / neighbourhood median. Above ~1.5 the base run is a spike. */
  fragility_ratio: number | null
  parameters_probed: number
  parameters_total: number
  note: string
}

export interface Validation {
  split: SplitValidation
  walk_forward: WalkForwardResult
  cost_sensitivity: CostSensitivity
  parameter_robustness: ParameterRobustness
}

/* ---------------------------------------------------------------------------
   Evidence
--------------------------------------------------------------------------- */

export interface ScoreComponent {
  key: string
  label: string
  weight: number
  /** 0..1 before weighting. */
  score: number
  /** score * weight - the points this component contributed. */
  points: number
  measurement: string
  detail: string
}

export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info'

export interface EvidenceWarning {
  id: string
  severity: Severity
  title: string
  message: string
  measurement: string
  threshold: string
}

export interface Evidence {
  score: number
  grade: string
  headline: string
  components: ScoreComponent[]
  warnings: EvidenceWarning[]
}

/* ---------------------------------------------------------------------------
   Report envelope
--------------------------------------------------------------------------- */

export interface Diagnostics {
  /** Entry signals dropped because every position slot was already full. */
  skipped_entry_signals: number
  /** Entry signals dropped because the slot could not buy one whole share. */
  unaffordable_entry_signals: number
  /** True when equity reached zero and the run stopped there. */
  account_ruined: boolean
  /** Date equity reached zero; empty string when the account survived. */
  ruin_date: string
  warmup_bars: number
  universe_size: number
  bars_evaluated: number
  backtests_run: number
}

export interface Timings {
  backtest_ms: number
  validation_ms: number
  robustness_ms: number
}

export interface Reproducibility {
  deterministic: boolean
  note: string
  canonical_strategy: string
  /** Not sent by the backend inside this block; the UI folds it in. */
  result_hash?: string
}

export interface BacktestReport {
  engine_version: string
  dsl_version: string
  data_snapshot_hash: string
  data_is_synthetic: boolean
  config_hash: string
  strategy: Strategy
  strategy_render: StrategyRender
  config: BacktestConfig
  summary: Metrics
  benchmark: BenchmarkMetrics
  series: SeriesPoint[]
  trades: Trade[]
  per_symbol: SymbolStat[]
  per_year: PeriodStat[]
  per_regime: RegimeStat[]
  concentration: Concentration
  validation: Validation
  evidence: Evidence
  diagnostics: Diagnostics
  result_hash: string
  run_id: string
  created_at: string
  timings_ms: Timings
  reproducibility: Reproducibility
  /** Only present when something non-fatal happened, e.g. persistence failed. */
  notes?: string[]
}

/* ---------------------------------------------------------------------------
   Parse (natural language -> DSL)
--------------------------------------------------------------------------- */

export interface ClarifyOption {
  label: string
  value: string
  description: string
}

export interface ClarifyQuestion {
  id: string
  question: string
  why: string
  options: ClarifyOption[]
  default_value: string
}

export interface Assumption {
  /** Dotted path into the strategy this assumption filled in. */
  field: string
  text: string
}

export interface ParseResponse {
  strategy: Strategy
  strategy_render: StrategyRender
  questions: ClarifyQuestion[]
  assumptions: Assumption[]
  /** 0..1 - the parser's own estimate of how much of the sentence it read. */
  confidence: number
  unparsed: string[]
  translator: string
  notes: string[]
}

/* ---------------------------------------------------------------------------
   Metadata endpoints
--------------------------------------------------------------------------- */

export interface HealthInfo {
  status: string
  engine_version: string
  dsl_version: string
  data_snapshot_hash: string
  data_is_synthetic: boolean
  /** Count of symbols in the snapshot, not the symbols themselves. */
  symbols: number
  start: string
  end: string
  /** The translator actually in use: "rules" or "anthropic". */
  translator: string
  translator_configured: string
}

export interface SymbolCoverage {
  symbol: string
  bars: number
  start: string
  end: string
}

export interface UniverseInfo {
  symbols: SymbolCoverage[]
  start: string
  end: string
  snapshot_hash: string
  is_synthetic: boolean
}

export interface ExampleStrategy {
  id: string
  name: string
  text: string
  /** The honest one-liner about how this example is expected to score. */
  note: string
}

/** Row shape of GET /api/runs - enough to list history, not to render a report. */
export interface RunSummary {
  run_id: string
  created_at: string
  name: string
  score: number
  grade: string
  trades: number
  cagr_pct: number
  sharpe: number
  max_drawdown_pct: number
  result_hash: string
}
