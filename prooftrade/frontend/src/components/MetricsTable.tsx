import type { Metrics } from '../types'
import { fmtDate, fmtMoney, fmtNum, fmtPct, fmtRatio, signClass } from '../format'

/**
 * Shape of `validation.split` in the POST /api/backtest response.
 *
 * Every field is optional here on purpose: when the window is too short the
 * backend returns `train` / `test` as empty objects and explains itself in
 * `note`, and `sharpe_retention` / `degradation_pct` are null whenever the
 * in-sample Sharpe is too close to zero for a ratio to mean anything.
 */
export interface SplitValidation {
  split_date?: string | null
  train?: Partial<Metrics> | null
  test?: Partial<Metrics> | null
  sharpe_retention?: number | null
  degradation_pct?: number | null
  note?: string | null
}

export interface MetricsTableProps {
  summary: Metrics
  benchmark: Metrics & { symbol: string }
  split: SplitValidation
}

const DASH = '—'

const pct = (v: number): string => fmtPct(v, 2)
const pct1 = (v: number): string => fmtPct(v, 1)
const int0 = (v: number): string => fmtNum(v, 0)

/** Plain-English definitions surfaced through the `?` affordance on every row. */
const DEF = {
  total_return:
    'Cumulative percentage change in account equity from the first bar to the last, after all costs.',
  cagr:
    'Compound annual growth rate - the constant yearly rate that turns the starting equity into the ending equity over this window.',
  volatility:
    'Annualised standard deviation of daily returns. It measures how rough the ride is, in both directions, not how bad the losses are.',
  sharpe:
    'Annualised return divided by annualised volatility, assuming a 0% risk-free rate.',
  sortino:
    'Like Sharpe, but the denominator counts only downside deviation, so upside swings are not penalised. Also assumes a 0% risk-free rate.',
  max_drawdown:
    'Largest peak-to-trough fall in equity, measured on daily closes. The worst loss you would have had to sit through.',
  calmar:
    'CAGR divided by the worst drawdown - return per unit of maximum pain.',
  exposure:
    'Share of trading days with at least one position open. The rest of the time the capital sat in cash earning nothing here.',
  trades:
    'Number of completed round trips - an entry paired with its exit - inside this window.',
  win_rate:
    'Share of closed trades with a positive net P&L. It says nothing about the size of the wins or losses.',
  profit_factor:
    'Gross profit divided by gross loss. Below 1.0 the losers outweigh the winners.',
  avg_win: 'Mean net return of the winning trades, measured on the entry notional.',
  avg_loss: 'Mean net return of the losing trades, measured on the entry notional.',
  expectancy:
    'Average net return per trade across winners and losers together - win rate and payoff size combined into one number.',
  avg_holding:
    'Mean number of trading days a position stayed open before it was closed.',
  total_costs:
    'Commission plus slippage charged on both legs of every trade, in dollars.',
  cost_drag:
    'Share of the gross profit that commission and slippage consumed. High drag means the edge is being paid to the broker.',
  best_day: 'Largest single-day gain in account equity over the window.',
  worst_day: 'Largest single-day loss in account equity over the window.',
  dd_days:
    'Longest stretch, in trading days, spent below a previous equity high before a new high was made.',
  split_date:
    'First bar of the out-of-sample half. Everything before it is in-sample; nothing after it influenced the rules.',
  degradation:
    'How much of the in-sample Sharpe was lost out of sample. Positive means the edge shrank once the data was unseen; negative means it grew.',
  retention:
    'Out-of-sample Sharpe divided by in-sample Sharpe. 1.0 means the edge held up exactly.',
  difference:
    'Strategy minus buy & hold. Coloured by whether the gap favours the strategy, which is not always the same as the sign.',
} as const

interface CompareRow {
  label: string
  definition: string
  pick: (m: Metrics) => number
  format: (v: number) => string
  /** Colour the two value cells by their own sign. */
  signed: boolean
  /** Colour the difference cell green when the strategy is higher. */
  higherIsBetter: boolean
}

const COMPARE_ROWS: readonly CompareRow[] = [
  {
    label: 'Total return',
    definition: DEF.total_return,
    pick: (m) => m.total_return_pct,
    format: pct,
    signed: true,
    higherIsBetter: true,
  },
  {
    label: 'CAGR',
    definition: DEF.cagr,
    pick: (m) => m.cagr_pct,
    format: pct,
    signed: true,
    higherIsBetter: true,
  },
  {
    label: 'Volatility (ann.)',
    definition: DEF.volatility,
    pick: (m) => m.volatility_pct,
    format: pct,
    signed: false,
    higherIsBetter: false,
  },
  {
    label: 'Sharpe',
    definition: DEF.sharpe,
    pick: (m) => m.sharpe,
    format: fmtRatio,
    signed: true,
    higherIsBetter: true,
  },
  {
    label: 'Sortino',
    definition: DEF.sortino,
    pick: (m) => m.sortino,
    format: fmtRatio,
    signed: true,
    higherIsBetter: true,
  },
  {
    label: 'Max drawdown',
    definition: DEF.max_drawdown,
    pick: (m) => m.max_drawdown_pct,
    format: pct,
    signed: true,
    higherIsBetter: true,
  },
  {
    label: 'Calmar',
    definition: DEF.calmar,
    pick: (m) => m.calmar,
    format: fmtRatio,
    signed: true,
    higherIsBetter: true,
  },
]

interface DetailItem {
  label: string
  definition: string
  text: string
  tone: string
}

function num(v: number | null | undefined): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

function isPopulated(m: Partial<Metrics> | null | undefined): boolean {
  return m != null && Object.keys(m).length > 0
}

function rangeLabel(m: Partial<Metrics> | null | undefined): string {
  const a = m?.start
  const b = m?.end
  if (typeof a !== 'string' || typeof b !== 'string' || a === '' || b === '') return ''
  return `${fmtDate(a)} – ${fmtDate(b)}`
}

function Term({ label, definition }: { label: string; definition: string }) {
  return (
    <span className="inline-flex items-baseline gap-1.5" title={definition}>
      <span className="cursor-help underline decoration-border decoration-dotted underline-offset-4">
        {label}
      </span>
      <span
        aria-hidden="true"
        className="inline-grid size-3.5 shrink-0 translate-y-px place-items-center rounded-full border border-border text-[9px] leading-none text-muted"
      >
        ?
      </span>
      <span className="sr-only">{definition}</span>
    </span>
  )
}

function Num({ text, tone }: { text: string; tone: string }) {
  return <span className={`mono ${tone}`}>{text}</span>
}

function SectionHead({ title, aside }: { title: string; aside: string }) {
  return (
    <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-border px-4 py-2.5">
      <h3 className="text-xs font-semibold uppercase tracking-[0.08em] text-primary">{title}</h3>
      {aside === '' ? null : <span className="mono text-[11px] text-muted">{aside}</span>}
    </header>
  )
}

export function MetricsTable({ summary, benchmark, split }: MetricsTableProps) {
  const train = split.train ?? null
  const test = split.test ?? null
  const hasSplit = isPopulated(train) && isPopulated(test)
  const degradation = num(split.degradation_pct)
  const retention = num(split.sharpe_retention)
  const note = typeof split.note === 'string' ? split.note.trim() : ''
  const splitDate = typeof split.split_date === 'string' ? split.split_date : ''

  const detail: readonly DetailItem[] = [
    {
      label: 'Exposure',
      definition: DEF.exposure,
      text: pct1(summary.exposure_pct),
      tone: 'text-primary',
    },
    { label: 'Trades', definition: DEF.trades, text: int0(summary.trades), tone: 'text-primary' },
    {
      label: 'Win rate',
      definition: DEF.win_rate,
      text: pct1(summary.win_rate_pct),
      tone: 'text-primary',
    },
    {
      label: 'Profit factor',
      definition: DEF.profit_factor,
      text: fmtRatio(summary.profit_factor),
      tone: signClass(summary.profit_factor - 1),
    },
    {
      label: 'Avg win',
      definition: DEF.avg_win,
      text: pct(summary.avg_win_pct),
      tone: signClass(summary.avg_win_pct),
    },
    {
      label: 'Avg loss',
      definition: DEF.avg_loss,
      text: pct(summary.avg_loss_pct),
      tone: signClass(summary.avg_loss_pct),
    },
    {
      label: 'Expectancy / trade',
      definition: DEF.expectancy,
      text: pct(summary.expectancy_pct),
      tone: signClass(summary.expectancy_pct),
    },
    {
      label: 'Avg holding days',
      definition: DEF.avg_holding,
      text: fmtNum(summary.avg_holding_days, 1),
      tone: 'text-primary',
    },
    {
      label: 'Total costs',
      definition: DEF.total_costs,
      text: fmtMoney(summary.total_costs),
      tone: 'text-primary',
    },
    {
      label: 'Cost drag',
      definition: DEF.cost_drag,
      text: pct1(summary.cost_drag_pct),
      tone: signClass(-summary.cost_drag_pct),
    },
    {
      label: 'Best day',
      definition: DEF.best_day,
      text: pct(summary.best_day_pct),
      tone: signClass(summary.best_day_pct),
    },
    {
      label: 'Worst day',
      definition: DEF.worst_day,
      text: pct(summary.worst_day_pct),
      tone: signClass(summary.worst_day_pct),
    },
    {
      label: 'Longest drawdown',
      definition: DEF.dd_days,
      text: `${int0(summary.max_drawdown_days)} d`,
      tone: 'text-primary',
    },
  ]

  const splitRows: readonly {
    label: string
    definition: string
    trainValue: number | null
    testValue: number | null
    format: (v: number) => string
    signed: boolean
  }[] = [
    {
      label: 'CAGR',
      definition: DEF.cagr,
      trainValue: num(train?.cagr_pct),
      testValue: num(test?.cagr_pct),
      format: pct,
      signed: true,
    },
    {
      label: 'Sharpe',
      definition: DEF.sharpe,
      trainValue: num(train?.sharpe),
      testValue: num(test?.sharpe),
      format: fmtRatio,
      signed: true,
    },
    {
      label: 'Max drawdown',
      definition: DEF.max_drawdown,
      trainValue: num(train?.max_drawdown_pct),
      testValue: num(test?.max_drawdown_pct),
      format: pct,
      signed: true,
    },
    {
      label: 'Trades',
      definition: DEF.trades,
      trainValue: num(train?.trades),
      testValue: num(test?.trades),
      format: int0,
      signed: false,
    },
  ]

  return (
    <section className="panel overflow-hidden" aria-label="Performance metrics">
      {/* ---------------------------------------------------------------- */}
      {/* Strategy vs buy & hold                                            */}
      {/* ---------------------------------------------------------------- */}
      <SectionHead
        title={`Strategy vs buy & hold ${benchmark.symbol}`}
        aside={`${fmtDate(summary.start)} – ${fmtDate(summary.end)} · ${fmtNum(summary.years, 1)}y · ${int0(summary.days)} bars`}
      />
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-[11px] uppercase tracking-[0.06em] text-muted">
              <th scope="col" className="px-4 py-2 text-left font-medium">
                Metric
              </th>
              <th scope="col" className="px-4 py-2 text-right font-medium">
                Strategy
              </th>
              <th scope="col" className="px-4 py-2 text-right font-medium whitespace-nowrap">
                {`Buy & hold ${benchmark.symbol}`}
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-right font-medium"
                title={DEF.difference}
              >
                Difference
              </th>
            </tr>
          </thead>
          <tbody>
            {COMPARE_ROWS.map((row) => {
              const s = num(row.pick(summary))
              const b = num(row.pick(benchmark))
              const diff = s !== null && b !== null ? s - b : null
              const diffTone =
                diff === null
                  ? 'text-muted'
                  : signClass(row.higherIsBetter ? diff : -diff)
              return (
                <tr
                  key={row.label}
                  className="border-b border-border/60 last:border-b-0 hover:bg-panel-hi"
                >
                  <th scope="row" className="px-4 py-1.5 text-left font-normal text-muted">
                    <Term label={row.label} definition={row.definition} />
                  </th>
                  <td className="px-4 py-1.5 text-right">
                    <Num
                      text={s === null ? DASH : row.format(s)}
                      tone={s === null ? 'text-muted' : row.signed ? signClass(s) : 'text-primary'}
                    />
                  </td>
                  <td className="px-4 py-1.5 text-right">
                    <Num
                      text={b === null ? DASH : row.format(b)}
                      tone={b === null ? 'text-muted' : row.signed ? signClass(b) : 'text-primary'}
                    />
                  </td>
                  <td className="px-4 py-1.5 text-right">
                    <Num
                      text={diff === null ? DASH : `${diff > 0 ? '+' : ''}${row.format(diff)}`}
                      tone={diffTone}
                    />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* ---------------------------------------------------------------- */}
      {/* Strategy-only detail                                              */}
      {/* ---------------------------------------------------------------- */}
      <SectionHead title="Trade and cost detail" aside="Strategy only" />
      <dl className="grid grid-cols-1 gap-x-8 px-4 py-1 sm:grid-cols-2 xl:grid-cols-3">
        {detail.map((item) => (
          <div
            key={item.label}
            className="flex items-baseline justify-between gap-4 border-b border-border/60 py-1.5"
          >
            <dt className="text-muted">
              <Term label={item.label} definition={item.definition} />
            </dt>
            <dd className="text-right">
              <Num text={item.text} tone={item.tone} />
            </dd>
          </div>
        ))}
      </dl>

      {/* ---------------------------------------------------------------- */}
      {/* In-sample vs out-of-sample                                        */}
      {/* ---------------------------------------------------------------- */}
      <SectionHead
        title="In-sample vs out-of-sample"
        aside={
          hasSplit && splitDate !== ''
            ? `Boundary ${fmtDate(splitDate)}`
            : ''
        }
      />
      {!hasSplit ? (
        <p className="px-4 py-3 text-sm text-muted">
          {note === ''
            ? 'No in-sample / out-of-sample split was produced for this run.'
            : note}
        </p>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-[11px] uppercase tracking-[0.06em] text-muted">
                  <th scope="col" className="px-4 py-2 text-left font-medium">
                    Metric
                  </th>
                  <th scope="col" className="px-4 py-2 text-right font-medium">
                    <span className="block">In-sample</span>
                    <span className="mono block text-[10px] normal-case tracking-normal text-muted">
                      {rangeLabel(train)}
                    </span>
                  </th>
                  <th scope="col" className="px-4 py-2 text-right font-medium">
                    <span className="block">Out-of-sample</span>
                    <span className="mono block text-[10px] normal-case tracking-normal text-muted">
                      {rangeLabel(test)}
                    </span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {splitRows.map((row) => (
                  <tr
                    key={row.label}
                    className="border-b border-border/60 last:border-b-0 hover:bg-panel-hi"
                  >
                    <th scope="row" className="px-4 py-1.5 text-left font-normal text-muted">
                      <Term label={row.label} definition={row.definition} />
                    </th>
                    <td className="px-4 py-1.5 text-right">
                      <Num
                        text={row.trainValue === null ? DASH : row.format(row.trainValue)}
                        tone={
                          row.trainValue === null
                            ? 'text-muted'
                            : row.signed
                              ? signClass(row.trainValue)
                              : 'text-primary'
                        }
                      />
                    </td>
                    <td className="px-4 py-1.5 text-right">
                      <Num
                        text={row.testValue === null ? DASH : row.format(row.testValue)}
                        tone={
                          row.testValue === null
                            ? 'text-muted'
                            : row.signed
                              ? signClass(row.testValue)
                              : 'text-primary'
                        }
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex flex-wrap items-baseline gap-x-8 gap-y-1 border-t border-border px-4 py-2.5">
            <div className="flex items-baseline gap-3">
              <span className="text-muted">
                <Term label="Split date" definition={DEF.split_date} />
              </span>
              <Num text={splitDate === '' ? DASH : fmtDate(splitDate)} tone="text-primary" />
            </div>
            <div className="flex items-baseline gap-3">
              <span className="text-muted">
                <Term label="Sharpe degradation" definition={DEF.degradation} />
              </span>
              <Num
                text={degradation === null ? DASH : pct1(degradation)}
                tone={degradation === null ? 'text-muted' : signClass(-degradation)}
              />
            </div>
            <div className="flex items-baseline gap-3">
              <span className="text-muted">
                <Term label="Sharpe retention" definition={DEF.retention} />
              </span>
              <Num
                text={retention === null ? DASH : fmtRatio(retention)}
                tone={retention === null ? 'text-muted' : signClass(retention - 1)}
              />
            </div>
          </div>
          {note === '' ? null : (
            <p className="border-t border-border px-4 py-2.5 text-xs text-muted">{note}</p>
          )}
        </>
      )}
    </section>
  )
}
