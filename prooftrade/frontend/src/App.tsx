import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import {
  ApiError,
  getExamples,
  getHealth,
  getUniverse,
  parseStrategy,
  runBacktest,
} from './api'
import type {
  BacktestConfig,
  BacktestReport,
  ExampleStrategy,
  HealthInfo,
  ParseResponse,
  UniverseInfo,
} from './types'
import { fmtNum, fmtPct, fmtRatio, signClass } from './format'

import { ClarifyPanel } from './components/ClarifyPanel'
import { ConfigPanel } from './components/ConfigPanel'
import { DrawdownChart } from './components/DrawdownChart'
import { EquityChart } from './components/EquityChart'
import { EvidencePanel } from './components/EvidencePanel'
import { MetricsTable } from './components/MetricsTable'
import { RegimeBreakdown } from './components/RegimeBreakdown'
import { StrategyCard } from './components/StrategyCard'
import { StrategyInput } from './components/StrategyInput'
import { SymbolHeatmap } from './components/SymbolHeatmap'
import { TradeLog } from './components/TradeLog'
import { WalkForward } from './components/WalkForward'

/* ---------------------------------------------------------------------------
   Constants
--------------------------------------------------------------------------- */

type Step = 'describe' | 'clarify' | 'report'

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'trades', label: 'Trades' },
  { id: 'symbols', label: 'Symbols' },
  { id: 'regimes', label: 'Regimes' },
  { id: 'evidence', label: 'Evidence' },
  { id: 'robustness', label: 'Robustness' },
] as const

type TabId = (typeof TABS)[number]['id']

/** Mirrors the server-side defaults on BacktestConfig, field for field. */
const DEFAULT_CONFIG: BacktestConfig = {
  start: null,
  end: null,
  initial_capital: 100_000,
  commission_bps: 1,
  slippage_bps: 5,
  benchmark: 'SPY',
  train_fraction: 0.7,
  walk_forward_folds: 5,
  run_robustness: true,
  seed: 7,
}

/**
 * What the backend is actually doing while the request is in flight, in the
 * order it does it. Named honestly: this is a description of the pipeline, not
 * a progress bar reading real telemetry.
 */
const RUN_STAGES = [
  'running base backtest',
  'in-sample / out-of-sample split',
  'walk-forward folds',
  'cost sensitivity',
  'parameter neighbourhood',
  'scoring the evidence',
] as const

const STAGE_MS = 520

/* ---------------------------------------------------------------------------
   Helpers
--------------------------------------------------------------------------- */

function errorMessage(cause: unknown): string {
  if (cause instanceof ApiError) return cause.detail
  if (cause instanceof Error) return cause.message
  return 'Something went wrong.'
}

function wasAborted(cause: unknown): boolean {
  return cause instanceof DOMException && cause.name === 'AbortError'
}

/** Grade colour. A is not celebrated, it is simply not red. */
function gradeClass(grade: string): string {
  switch (grade.trim().charAt(0).toUpperCase()) {
    case 'A':
      return 'border-positive/40 bg-positive/10 text-positive'
    case 'B':
      return 'border-accent/40 bg-accent/10 text-accent'
    case 'C':
      return 'border-warning/40 bg-warning/10 text-warning'
    default:
      return 'border-negative/40 bg-negative/10 text-negative'
  }
}

/* ---------------------------------------------------------------------------
   Small presentational pieces
--------------------------------------------------------------------------- */

function HeaderStat({ label, value, tone }: { label: string; value: string; tone: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-muted">
        {label}
      </span>
      <span className={`mono truncate text-[13px] leading-4 ${tone}`}>{value}</span>
    </div>
  )
}

function ErrorBanner({ message, onDismiss }: { message: string; onDismiss: () => void }) {
  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-[var(--radius-panel)] border border-negative/40 bg-negative/10 px-4 py-2.5"
    >
      <span className="mono mt-px shrink-0 rounded-sm bg-negative/15 px-1.5 py-px text-[9px] font-medium uppercase tracking-[0.14em] text-negative">
        error
      </span>
      <p className="min-w-0 grow text-[13px] leading-snug text-primary">{message}</p>
      <button
        type="button"
        onClick={onDismiss}
        className="shrink-0 rounded border border-border px-2 py-0.5 text-[11px] text-muted transition-colors hover:border-negative/50 hover:text-primary"
      >
        Dismiss
      </button>
    </div>
  )
}

function Notice({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="panel px-5 py-4">
      <h2 className="text-xs font-semibold uppercase tracking-[0.08em] text-primary">{title}</h2>
      <div className="mt-1.5 text-[13px] leading-snug text-muted">{children}</div>
    </div>
  )
}

/** The determinate-looking note shown while the 2-4 second report is built. */
function RunProgress({ stage }: { stage: number }) {
  const pct = Math.round(((stage + 1) / RUN_STAGES.length) * 100)
  return (
    <section className="panel overflow-hidden" aria-live="polite" aria-busy="true">
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-border px-4 py-2.5">
        <h2 className="text-xs font-semibold uppercase tracking-[0.08em] text-primary">
          Building the report
        </h2>
        <span className="mono text-[11px] text-muted">
          {fmtNum(Math.min(stage + 1, RUN_STAGES.length), 0)} of{' '}
          {fmtNum(RUN_STAGES.length, 0)}
        </span>
      </header>

      <div className="h-px w-full bg-border">
        <div
          className="h-px bg-accent transition-[width] duration-300 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>

      <ol className="flex flex-col">
        {RUN_STAGES.map((name, index) => {
          const done = index < stage
          const active = index === stage
          return (
            <li
              key={name}
              className="flex items-baseline gap-3 border-b border-border/60 px-4 py-1.5 last:border-b-0"
            >
              <span
                className={`mono shrink-0 text-[11px] ${
                  done ? 'text-positive' : active ? 'text-accent' : 'text-muted/60'
                }`}
              >
                {done ? 'done' : active ? '····' : '    '}
              </span>
              <span
                className={`text-[13px] ${
                  done ? 'text-muted' : active ? 'text-primary' : 'text-muted/60'
                }`}
              >
                {name}
              </span>
            </li>
          )
        })}
      </ol>

      <p className="border-t border-border px-4 py-2 text-[11px] leading-snug text-muted">
        Eighteen symbols over twenty years, re-run at five cost levels, across five
        walk-forward folds and up to eight parameter neighbours. Two to four seconds.
      </p>
    </section>
  )
}

/* ---------------------------------------------------------------------------
   App
--------------------------------------------------------------------------- */

export default function App() {
  /* --- metadata -------------------------------------------------------- */
  const [health, setHealth] = useState<HealthInfo | null>(null)
  const [universe, setUniverse] = useState<UniverseInfo | null>(null)
  const [examples, setExamples] = useState<ExampleStrategy[]>([])
  const [booting, setBooting] = useState(true)
  const [bootError, setBootError] = useState<string | null>(null)
  const [bootAttempt, setBootAttempt] = useState(0)

  /* --- flow ------------------------------------------------------------ */
  const [step, setStep] = useState<Step>('describe')
  const [sourceText, setSourceText] = useState('')
  const [parse, setParse] = useState<ParseResponse | null>(null)
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [parsing, setParsing] = useState(false)

  const [config, setConfig] = useState<BacktestConfig>(DEFAULT_CONFIG)
  const [report, setReport] = useState<BacktestReport | null>(null)
  const [running, setRunning] = useState(false)
  const [stage, setStage] = useState(0)
  const [tab, setTab] = useState<TabId>('overview')

  const [error, setError] = useState<string | null>(null)

  /** Guards against an older /api/parse answering after a newer one. */
  const parseSeq = useRef(0)

  /* --- load health, universe and examples once -------------------------- */
  useEffect(() => {
    const controller = new AbortController()
    const { signal } = controller

    async function load(): Promise<void> {
      setBooting(true)
      try {
        const [nextHealth, nextUniverse, nextExamples] = await Promise.all([
          getHealth(signal),
          getUniverse(signal),
          getExamples(signal),
        ])
        if (signal.aborted) return
        setHealth(nextHealth)
        setUniverse(nextUniverse)
        setExamples(nextExamples)
        setBootError(null)
      } catch (cause) {
        if (signal.aborted || wasAborted(cause)) return
        setBootError(errorMessage(cause))
      } finally {
        if (!signal.aborted) setBooting(false)
      }
    }

    void load()
    return () => controller.abort()
  }, [bootAttempt])

  /* --- fake-but-honest staging of the backtest request ------------------ */
  useEffect(() => {
    if (!running) {
      setStage(0)
      return
    }
    setStage(0)
    const id = window.setInterval(() => {
      setStage((current) => Math.min(current + 1, RUN_STAGES.length - 1))
    }, STAGE_MS)
    return () => window.clearInterval(id)
  }, [running])

  /* --- parse ------------------------------------------------------------ */
  const reparse = useCallback(
    async (text: string, nextAnswers: Record<string, string>): Promise<void> => {
      const seq = parseSeq.current + 1
      parseSeq.current = seq
      setParsing(true)
      setError(null)
      try {
        const result = await parseStrategy(text, nextAnswers)
        if (parseSeq.current !== seq) return
        setParse(result)
        setStep('clarify')
      } catch (cause) {
        if (parseSeq.current !== seq || wasAborted(cause)) return
        setError(errorMessage(cause))
      } finally {
        if (parseSeq.current === seq) setParsing(false)
      }
    },
    [],
  )

  const handleDescribe = useCallback(
    (text: string): void => {
      setSourceText(text)
      setAnswers({})
      setReport(null)
      void reparse(text, {})
    },
    [reparse],
  )

  const handleAnswer = useCallback(
    (id: string, value: string): void => {
      // Computed outside the updater on purpose: a state updater has to stay
      // pure, and StrictMode would otherwise fire two /api/parse requests.
      const next = { ...answers, [id]: value }
      setAnswers(next)
      void reparse(sourceText, next)
    },
    [answers, reparse, sourceText],
  )

  /* --- backtest --------------------------------------------------------- */
  const handleRun = useCallback((): void => {
    if (parse === null || running) return
    const strategy = parse.strategy

    async function execute(): Promise<void> {
      setRunning(true)
      setError(null)
      try {
        const result = await runBacktest(strategy, config)
        setReport(result)
        setTab('overview')
        setStep('report')
      } catch (cause) {
        if (wasAborted(cause)) return
        setError(errorMessage(cause))
      } finally {
        setRunning(false)
      }
    }

    void execute()
  }, [config, parse, running])

  const handleConfigChange = useCallback((patch: Partial<BacktestConfig>): void => {
    setConfig((current) => ({ ...current, ...patch }))
  }, [])

  const handleNewStrategy = useCallback((): void => {
    setStep('describe')
    setParse(null)
    setReport(null)
    setAnswers({})
    setSourceText('')
    setError(null)
  }, [])

  const dismissError = useCallback((): void => setError(null), [])

  /* --- derived ---------------------------------------------------------- */
  const reproducibility = useMemo(() => {
    if (report === null) return null
    return { ...report.reproducibility, result_hash: report.result_hash }
  }, [report])

  const synthetic = health?.data_is_synthetic === true

  /* --- render ----------------------------------------------------------- */
  return (
    <div className="flex min-h-screen flex-col bg-bg">
      <div className="sticky top-0 z-30 border-b border-border bg-bg/95 backdrop-blur">
        {synthetic && (
          <div className="border-b border-warning/25 bg-warning/10 px-4 py-1 text-center text-[11px] font-medium tracking-[0.02em] text-warning">
            Demo data is simulated, not real market history.
          </div>
        )}

        <header className="mx-auto flex w-full max-w-[1480px] flex-wrap items-center justify-between gap-x-8 gap-y-2 px-4 py-2.5">
          <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-0.5">
            <span className="text-[17px] font-semibold tracking-tight text-primary">
              Proof<span className="text-accent">Trade</span>
            </span>
            <p className="text-[12px] leading-snug text-muted">
              Turns a sentence into a testable strategy, then argues with the result.
            </p>
          </div>

          <div className="flex shrink-0 flex-wrap items-center gap-x-5 gap-y-1">
            {health === null ? (
              <span className="mono text-[11px] text-muted">
                {booting ? 'reading snapshot…' : 'backend unreachable'}
              </span>
            ) : (
              <>
                <HeaderStat
                  label="engine"
                  value={`v${health.engine_version} · dsl ${health.dsl_version}`}
                  tone="text-muted"
                />
                <HeaderStat
                  label="snapshot"
                  value={health.data_snapshot_hash}
                  tone="text-primary"
                />
                <HeaderStat
                  label="universe"
                  value={`${fmtNum(health.symbols, 0)} symbols`}
                  tone="text-muted"
                />
              </>
            )}
          </div>
        </header>
      </div>

      <main className="mx-auto flex w-full max-w-[1480px] grow flex-col gap-4 px-4 py-5">
        {error !== null && <ErrorBanner message={error} onDismiss={dismissError} />}

        {bootError !== null && (
          <Notice title="Backend not reachable">
            <p>{bootError}</p>
            <p className="mt-1">
              The API is expected at <span className="mono text-primary">/api</span>. Start it with{' '}
              <span className="mono text-primary">make dev</span> and try again.
            </p>
            <button
              type="button"
              onClick={() => setBootAttempt((n) => n + 1)}
              className="mt-2 rounded border border-border bg-panel-hi px-3 py-1 text-[12px] text-primary transition-colors hover:border-accent hover:text-accent"
            >
              Retry
            </button>
          </Notice>
        )}

        {/* ---------------------------------------------------------------- */}
        {/* Step 1 - Describe                                                 */}
        {/* ---------------------------------------------------------------- */}
        {step === 'describe' && (
          <>
            {booting && bootError === null && (
              <Notice title="Loading">Reading the frozen data snapshot…</Notice>
            )}
            <StrategyInput examples={examples} onSubmit={handleDescribe} busy={parsing} />
          </>
        )}

        {/* ---------------------------------------------------------------- */}
        {/* Step 2 - Clarify and confirm                                      */}
        {/* ---------------------------------------------------------------- */}
        {step === 'clarify' && parse !== null && (
          <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <div className="flex min-w-0 flex-col gap-4">
              <StrategyCard strategy={parse.strategy} render={parse.strategy_render} />
            </div>

            <div className="flex min-w-0 flex-col gap-4">
              <ClarifyPanel
                parse={parse}
                answers={answers}
                onAnswer={handleAnswer}
                onConfirm={handleRun}
                onBack={handleNewStrategy}
                busy={parsing || running}
              />

              {universe === null ? (
                <Notice title="Run configuration">
                  Waiting for the universe manifest before the run knobs can be shown.
                </Notice>
              ) : (
                <ConfigPanel config={config} universe={universe} onChange={handleConfigChange} />
              )}

              {running && <RunProgress stage={stage} />}
            </div>
          </div>
        )}

        {step === 'clarify' && parse === null && (
          <Notice title="Nothing to clarify">
            The strategy was cleared. Start again from a description.
          </Notice>
        )}

        {/* ---------------------------------------------------------------- */}
        {/* Step 3 - Report                                                   */}
        {/* ---------------------------------------------------------------- */}
        {step === 'report' && report !== null && reproducibility !== null && (
          <>
            {/* Compact result header */}
            <section className="panel px-4 py-3">
              <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
                <div className="flex min-w-0 items-center gap-3">
                  <span
                    className={`mono shrink-0 rounded border px-2 py-0.5 text-[13px] font-semibold ${gradeClass(
                      report.evidence.grade,
                    )}`}
                    title={`Evidence score ${fmtNum(report.evidence.score, 1)} of 100`}
                  >
                    {report.evidence.grade}
                  </span>
                  <div className="min-w-0">
                    <h1 className="truncate text-[15px] font-medium leading-5 text-primary">
                      {report.strategy.name}
                    </h1>
                    <p className="mono text-[11px] text-muted">
                      evidence {fmtNum(report.evidence.score, 1)} / 100 ·{' '}
                      {report.data_is_synthetic ? 'simulated snapshot' : 'vendored snapshot'}{' '}
                      {report.data_snapshot_hash}
                    </p>
                  </div>
                </div>

                <div className="flex flex-wrap items-start gap-x-7 gap-y-2">
                  <HeaderStat
                    label="CAGR"
                    value={fmtPct(report.summary.cagr_pct)}
                    tone={signClass(report.summary.cagr_pct)}
                  />
                  <HeaderStat
                    label="Sharpe"
                    value={fmtRatio(report.summary.sharpe)}
                    tone={signClass(report.summary.sharpe)}
                  />
                  <HeaderStat
                    label="Max drawdown"
                    value={fmtPct(report.summary.max_drawdown_pct)}
                    tone={signClass(report.summary.max_drawdown_pct)}
                  />
                  <HeaderStat
                    label="Trades"
                    value={fmtNum(report.summary.trades, 0)}
                    tone="text-primary"
                  />
                  <HeaderStat
                    label="deterministic run id"
                    value={report.result_hash}
                    tone="text-primary"
                  />
                </div>

                <div className="flex shrink-0 items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setStep('clarify')}
                    disabled={parse === null}
                    className="rounded border border-border bg-panel-hi px-3 py-1 text-[12px] text-primary transition-colors hover:border-accent hover:text-accent disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    Edit strategy
                  </button>
                  <button
                    type="button"
                    onClick={handleNewStrategy}
                    className="rounded border border-border px-3 py-1 text-[12px] text-muted transition-colors hover:border-accent hover:text-accent"
                  >
                    New strategy
                  </button>
                </div>
              </div>
            </section>

            {/* Tabs */}
            <div
              role="tablist"
              aria-label="Report sections"
              className="flex flex-wrap items-center gap-1 border-b border-border"
            >
              {TABS.map((entry) => {
                const active = entry.id === tab
                return (
                  <button
                    key={entry.id}
                    type="button"
                    role="tab"
                    aria-selected={active}
                    onClick={() => setTab(entry.id)}
                    className={`-mb-px border-b-2 px-3 py-1.5 text-[13px] transition-colors ${
                      active
                        ? 'border-accent text-primary'
                        : 'border-transparent text-muted hover:text-primary'
                    }`}
                  >
                    {entry.label}
                  </button>
                )
              })}
            </div>

            <div className="flex flex-col gap-4">
              {tab === 'overview' && (
                <>
                  <EquityChart
                    series={report.series}
                    benchmarkSymbol={report.benchmark.symbol}
                    initialCapital={report.config.initial_capital}
                  />
                  <DrawdownChart series={report.series} />
                  <MetricsTable
                    summary={report.summary}
                    benchmark={report.benchmark}
                    split={report.validation.split}
                  />
                </>
              )}

              {tab === 'trades' && <TradeLog trades={report.trades} />}

              {tab === 'symbols' && (
                <SymbolHeatmap
                  symbols={report.per_symbol}
                  concentration={report.concentration}
                />
              )}

              {tab === 'regimes' && (
                <RegimeBreakdown years={report.per_year} regimes={report.per_regime} />
              )}

              {tab === 'evidence' && (
                <EvidencePanel
                  evidence={report.evidence}
                  reproducibility={reproducibility}
                  dataIsSynthetic={report.data_is_synthetic}
                />
              )}

              {tab === 'robustness' && <WalkForward validation={report.validation} />}
            </div>
          </>
        )}

        {step === 'report' && report === null && (
          <Notice title="No report">
            The run was cleared before it could be shown. Describe a strategy to start again.
          </Notice>
        )}
      </main>

      <footer className="mx-auto w-full max-w-[1480px] px-4 pb-6 pt-2">
        <p className="border-t border-border pt-3 text-[11px] leading-snug text-muted">
          ProofTrade scores how much a backtest can be trusted, not how much money a
          strategy will make. Nothing here is investment advice, and no result on this
          page is a claim about future returns.
        </p>
      </footer>
    </div>
  )
}
