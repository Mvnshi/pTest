import { useMemo } from 'react'
import type { PeriodStat, RegimeStat } from '../types'
import { fmtNum, fmtPct, fmtRatio, signClass } from '../format'

export interface RegimeBreakdownProps {
  years: PeriodStat[]
  regimes: RegimeStat[]
}

/**
 * Human-readable names for the four regime keys the backend emits, each carrying
 * the rule that produced it. The qualifier is part of the label, not decoration:
 * "bull" means nothing to a reader who cannot see how it was decided.
 */
const REGIME_LABELS: Record<string, { name: string; qualifier: string } | undefined> = {
  crisis: { name: 'Crisis', qualifier: '(benchmark >20% below peak)' },
  high_vol: { name: 'High volatility', qualifier: '(top-quintile 20d vol)' },
  bull: { name: 'Bull', qualifier: '(200d average rising)' },
  sideways: { name: 'Sideways', qualifier: '(everything else)' },
}

function regimeLabel(key: string): { name: string; qualifier: string } {
  const known = REGIME_LABELS[key]
  if (known !== undefined) return known
  const name = key.replace(/_/g, ' ')
  return { name: name.charAt(0).toUpperCase() + name.slice(1), qualifier: '' }
}

const DEF = {
  year: 'Calendar year of the equity curve. A year is measured from the prior year’s final close, so no trading day is dropped at the boundary.',
  regime:
    'Market state for each day, decided from the benchmark alone. Precedence runs crisis, then high volatility, then bull, then sideways.',
  days: 'Trading days the window contains.',
  share: 'Share of all trading days in the backtest that fell into this regime.',
  return:
    'Strategy return over the window, compounded from daily equity changes, after all costs.',
  benchmark: 'Buy & hold return of the benchmark over the same days.',
  drawdown: 'Largest peak-to-trough fall in equity inside the window.',
  hit: 'Share of days inside this regime on which the strategy made money. It counts days, not trades, and says nothing about how big those days were.',
  trades: 'Round trips whose exit fell inside this window.',
  sharpe:
    'Annualised return divided by annualised volatility for this window alone, assuming a 0% risk-free rate. One year is a short sample; treat it as a texture, not a verdict.',
  lagging:
    'The strategy lost money in this window while buy & hold gained. Not fatal on its own, but it is where the cost of sitting in cash shows up.',
} as const

function Term({ label, definition }: { label: string; definition: string }) {
  return (
    <span
      className="cursor-help underline decoration-border decoration-dotted underline-offset-4"
      title={definition}
    >
      {label}
    </span>
  )
}

/**
 * Diverging bar drawn from a fixed centre line, so the eye can scan a column of
 * returns without reading any digits. Widths are inline styles because they are
 * continuous values; the sign colours come from the CSS custom properties in
 * index.css rather than runtime-built Tailwind class names.
 */
function DivergingBar({ value, maxAbs }: { value: number; maxAbs: number }) {
  const magnitude = maxAbs > 0 ? Math.min(1, Math.abs(value) / maxAbs) : 0
  const half = magnitude * 50
  const positive = value >= 0
  return (
    <span
      aria-hidden="true"
      className="relative block h-1.5 w-14 shrink-0 rounded-[1px] bg-border/50"
    >
      <span className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-border" />
      <span
        className="absolute inset-y-0 rounded-[1px]"
        style={{
          left: positive ? '50%' : `${50 - half}%`,
          width: `${half}%`,
          backgroundColor: positive ? 'var(--color-positive)' : 'var(--color-negative)',
        }}
      />
    </span>
  )
}

function ReturnCell({ value, maxAbs }: { value: number; maxAbs: number }) {
  return (
    <td className="whitespace-nowrap px-3 py-1.5">
      <span className="flex items-center justify-end gap-2">
        <DivergingBar value={value} maxAbs={maxAbs} />
        <span className={`mono w-[4.5rem] text-right ${signClass(value)}`}>
          {fmtPct(value, 2)}
        </span>
      </span>
    </td>
  )
}

function SectionHead({ title, aside }: { title: string; aside: string }) {
  return (
    <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-border px-4 py-2.5">
      <h3 className="text-xs font-semibold uppercase tracking-[0.08em] text-primary">{title}</h3>
      {aside === '' ? null : <span className="mono text-[11px] text-muted">{aside}</span>}
    </header>
  )
}

function HeadCell({
  label,
  definition,
  align = 'right',
}: {
  label: string
  definition: string
  align?: 'left' | 'right'
}) {
  return (
    <th
      scope="col"
      className={`sticky top-0 z-10 whitespace-nowrap bg-panel px-3 py-2 text-[11px] font-medium uppercase tracking-[0.06em] text-muted shadow-[inset_0_-1px_0_var(--color-border)] ${
        align === 'left' ? 'text-left' : 'text-right'
      }`}
    >
      <Term label={label} definition={definition} />
    </th>
  )
}

/** A window where the strategy lost money while buy & hold made money. */
function isLagging(returnPct: number, benchmarkPct: number): boolean {
  return returnPct < 0 && benchmarkPct > 0
}

const LAGGING_ROW = 'bg-warning/[0.07]'

function laggingCellBorder(lagging: boolean): string {
  return lagging ? 'border-l-2 border-warning' : 'border-l-2 border-transparent'
}

function EmptyPanel({ title, message }: { title: string; message: string }) {
  return (
    <section className="panel flex h-full flex-col overflow-hidden">
      <SectionHead title={title} aside="" />
      <p className="px-4 py-3 text-sm text-muted">{message}</p>
    </section>
  )
}

export function RegimeBreakdown({ years, regimes }: RegimeBreakdownProps) {
  const maxAbsYear = useMemo(
    () => years.reduce((acc, y) => Math.max(acc, Math.abs(y.return_pct)), 0),
    [years],
  )

  const maxAbsRegime = useMemo(
    () => regimes.reduce((acc, r) => Math.max(acc, Math.abs(r.return_pct)), 0),
    [regimes],
  )

  const positiveYears = useMemo(
    () => years.filter((y) => y.return_pct > 0).length,
    [years],
  )

  const laggingYears = useMemo(
    () => years.filter((y) => isLagging(y.return_pct, y.benchmark_return_pct)).length,
    [years],
  )

  const laggingRegimes = useMemo(
    () => regimes.filter((r) => isLagging(r.return_pct, r.benchmark_return_pct)).length,
    [regimes],
  )

  const regimeDays = useMemo(
    () => regimes.reduce((acc, r) => acc + r.days, 0),
    [regimes],
  )

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2" aria-label="Period breakdown">
      {/* ------------------------------------------------------------------ */}
      {/* By calendar year                                                    */}
      {/* ------------------------------------------------------------------ */}
      {years.length === 0 ? (
        <EmptyPanel
          title="By calendar year"
          message="This run is too short to break down by calendar year."
        />
      ) : (
        <section
          className="panel flex h-full flex-col overflow-hidden"
          aria-label="Results by calendar year"
        >
          <SectionHead
            title="By calendar year"
            aside={`${fmtNum(positiveYears, 0)} of ${fmtNum(years.length, 0)} years positive`}
          />
          <div className="max-h-[32rem] min-h-0 flex-1 overflow-auto">
            <table className="w-full text-sm">
              <caption className="sr-only">
                Strategy results for each calendar year against the benchmark.
              </caption>
              <thead>
                <tr>
                  <HeadCell label="Year" definition={DEF.year} align="left" />
                  <HeadCell label="Return" definition={DEF.return} />
                  <HeadCell label={'Buy & hold'} definition={DEF.benchmark} />
                  <HeadCell label="Max DD" definition={DEF.drawdown} />
                  <HeadCell label="Trades" definition={DEF.trades} />
                  <HeadCell label="Sharpe" definition={DEF.sharpe} />
                </tr>
              </thead>
              <tbody>
                {years.map((y) => {
                  const lagging = isLagging(y.return_pct, y.benchmark_return_pct)
                  return (
                    <tr
                      key={y.label}
                      className={`border-b border-border/60 last:border-b-0 hover:bg-panel-hi ${
                        lagging ? LAGGING_ROW : ''
                      }`}
                      title={lagging ? DEF.lagging : undefined}
                    >
                      <th
                        scope="row"
                        className={`whitespace-nowrap py-1.5 pr-3 pl-2.5 text-left font-normal ${laggingCellBorder(
                          lagging,
                        )}`}
                      >
                        <span className="mono text-primary">{y.label}</span>
                        <span className="mono ml-2 text-[10px] text-muted">
                          {fmtNum(y.days, 0)}d
                        </span>
                        {lagging ? <span className="sr-only"> — {DEF.lagging}</span> : null}
                      </th>
                      <ReturnCell value={y.return_pct} maxAbs={maxAbsYear} />
                      <td className="whitespace-nowrap px-3 py-1.5 text-right">
                        <span className={`mono ${signClass(y.benchmark_return_pct)}`}>
                          {fmtPct(y.benchmark_return_pct, 2)}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-3 py-1.5 text-right">
                        <span className={`mono ${signClass(y.max_drawdown_pct)}`}>
                          {fmtPct(y.max_drawdown_pct, 2)}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-3 py-1.5 text-right">
                        <span className={`mono ${y.trades === 0 ? 'text-muted' : 'text-primary'}`}>
                          {fmtNum(y.trades, 0)}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-3 py-1.5 text-right">
                        <span className={`mono ${signClass(y.sharpe)}`}>{fmtRatio(y.sharpe)}</span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <p className="border-t border-border px-4 py-2.5 text-xs text-muted">
            {laggingYears === 0
              ? 'No calendar year lost money while buy & hold gained.'
              : `${fmtNum(laggingYears, 0)} of ${fmtNum(years.length, 0)} years are highlighted: the strategy lost money while buy & hold gained.`}
          </p>
        </section>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* By market regime                                                    */}
      {/* ------------------------------------------------------------------ */}
      {regimes.length === 0 ? (
        <EmptyPanel
          title="By market regime"
          message="This run is too short to classify into market regimes."
        />
      ) : (
        <section
          className="panel flex h-full flex-col overflow-hidden"
          aria-label="Results by market regime"
        >
          <SectionHead
            title="By market regime"
            aside={`${fmtNum(regimeDays, 0)} trading days classified`}
          />
          <div className="min-h-0 flex-1 overflow-auto">
            <table className="w-full text-sm">
              <caption className="sr-only">
                Strategy results in each market regime against the benchmark.
              </caption>
              <thead>
                <tr>
                  <HeadCell label="Regime" definition={DEF.regime} align="left" />
                  <HeadCell label="Days" definition={DEF.days} />
                  <HeadCell label="Time" definition={DEF.share} />
                  <HeadCell label="Return" definition={DEF.return} />
                  <HeadCell label={'Buy & hold'} definition={DEF.benchmark} />
                  <HeadCell label="Hit rate" definition={DEF.hit} />
                  <HeadCell label="Trades" definition={DEF.trades} />
                </tr>
              </thead>
              <tbody>
                {regimes.map((r) => {
                  const lagging = isLagging(r.return_pct, r.benchmark_return_pct)
                  const label = regimeLabel(r.regime)
                  return (
                    <tr
                      key={r.regime}
                      className={`border-b border-border/60 last:border-b-0 hover:bg-panel-hi ${
                        lagging ? LAGGING_ROW : ''
                      }`}
                      title={lagging ? DEF.lagging : undefined}
                    >
                      <th
                        scope="row"
                        className={`py-1.5 pr-3 pl-2.5 text-left font-normal ${laggingCellBorder(
                          lagging,
                        )}`}
                      >
                        <span className="block text-primary">{label.name}</span>
                        {label.qualifier === '' ? null : (
                          <span className="block text-[10px] leading-tight text-muted">
                            {label.qualifier}
                          </span>
                        )}
                        {lagging ? <span className="sr-only">{DEF.lagging}</span> : null}
                      </th>
                      <td className="whitespace-nowrap px-3 py-1.5 text-right">
                        <span className="mono text-primary">{fmtNum(r.days, 0)}</span>
                      </td>
                      <td className="whitespace-nowrap px-3 py-1.5 text-right">
                        <span className="mono text-primary">{fmtPct(r.share_of_time_pct, 1)}</span>
                      </td>
                      <ReturnCell value={r.return_pct} maxAbs={maxAbsRegime} />
                      <td className="whitespace-nowrap px-3 py-1.5 text-right">
                        <span className={`mono ${signClass(r.benchmark_return_pct)}`}>
                          {fmtPct(r.benchmark_return_pct, 2)}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-3 py-1.5 text-right">
                        <span className="mono text-primary">{fmtPct(r.hit_rate_pct, 1)}</span>
                      </td>
                      <td className="whitespace-nowrap px-3 py-1.5 text-right">
                        <span className={`mono ${r.trades === 0 ? 'text-muted' : 'text-primary'}`}>
                          {fmtNum(r.trades, 0)}
                        </span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <div className="border-t border-border px-4 py-2.5">
            <p className="text-xs text-muted">
              Regime labels are computed from the benchmark with full-sample knowledge. They
              describe the past; they are not a signal you could have traded.
            </p>
            {laggingRegimes > 0 ? (
              <p className="mt-1 text-xs text-muted">
                {fmtNum(laggingRegimes, 0)} of {fmtNum(regimes.length, 0)} regimes are highlighted:
                the strategy lost money while buy &amp; hold gained.
              </p>
            ) : null}
          </div>
        </section>
      )}
    </div>
  )
}
