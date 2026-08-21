import { useMemo } from 'react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { SeriesPoint } from '../types'
import { fmtDate, fmtPct, signClass } from '../format'

interface WorstPoint {
  index: number
  date: string
  value: number
}

/**
 * One tick every `stepYears` calendar years, anchored on the first bar, using
 * real dates from the data so a category x-axis can match them.
 */
function yearTicks(series: SeriesPoint[], stepYears: number): string[] {
  const ticks: string[] = []
  let nextYear = Number.NEGATIVE_INFINITY
  for (const point of series) {
    const year = Number.parseInt(point.date.slice(0, 4), 10)
    if (!Number.isFinite(year)) continue
    if (year >= nextYear) {
      ticks.push(point.date)
      nextYear = year + stepYears
    }
  }
  if (ticks.length < 2 && series.length > 0) {
    const lastDate = series[series.length - 1].date
    if (ticks[0] !== lastDate) ticks.push(lastDate)
  }
  return ticks
}

/** Deepest point of either curve; drives the axis floor. */
function lowest(series: SeriesPoint[]): number {
  let min = 0
  for (const point of series) {
    if (Number.isFinite(point.drawdown) && point.drawdown < min) min = point.drawdown
    if (Number.isFinite(point.benchmark_drawdown) && point.benchmark_drawdown < min) {
      min = point.benchmark_drawdown
    }
  }
  return min
}

function worstDrawdown(series: SeriesPoint[]): WorstPoint | null {
  let worst: WorstPoint | null = null
  for (let i = 0; i < series.length; i += 1) {
    const point = series[i]
    if (!Number.isFinite(point.drawdown)) continue
    if (worst === null || point.drawdown < worst.value) {
      worst = { index: i, date: point.date, value: point.drawdown }
    }
  }
  return worst
}

function axisTicks(floor: number): number[] {
  const depth = Math.abs(floor)
  const step = depth <= 20 ? 5 : depth <= 60 ? 10 : 20
  const ticks: number[] = []
  for (let value = 0; value >= floor - step / 2; value -= step) ticks.push(Number(value.toFixed(4)))
  if (ticks[ticks.length - 1] > floor) ticks.push(floor)
  return ticks.reverse()
}

interface TooltipRowProps {
  label: string
  value: string
  valueClass: string
}

function TooltipRow({ label, value, valueClass }: TooltipRowProps) {
  return (
    <div className="flex items-baseline justify-between gap-6 leading-5">
      <span className="text-muted">{label}</span>
      <span className={`mono ${valueClass}`}>{value}</span>
    </div>
  )
}

interface DrawdownTooltipProps {
  /** Injected by Recharts when it clones this element. */
  active?: boolean
  /** Injected by Recharts: the category value of the hovered x position. */
  label?: string | number
  points: ReadonlyMap<string, SeriesPoint>
}

function DrawdownTooltip({ active, label, points }: DrawdownTooltipProps) {
  if (active !== true || label === undefined) return null
  const point = points.get(String(label))
  if (point === undefined) return null

  return (
    <div className="panel px-3 py-2 text-xs">
      <div className="mb-1 text-[11px] uppercase tracking-wider text-muted">{fmtDate(point.date)}</div>
      <TooltipRow
        label="Strategy"
        value={fmtPct(point.drawdown)}
        valueClass={signClass(point.drawdown)}
      />
      <TooltipRow
        label="Benchmark"
        value={fmtPct(point.benchmark_drawdown)}
        valueClass={signClass(point.benchmark_drawdown)}
      />
    </div>
  )
}

interface LegendChipProps {
  color: string
  children: string
}

function LegendChip({ color, children }: LegendChipProps) {
  return (
    <span className="flex items-center gap-1.5 text-[11px] text-muted">
      <span
        aria-hidden="true"
        className="inline-block h-0 w-4"
        style={{ borderTop: `2px solid ${color}` }}
      />
      {children}
    </span>
  )
}

export interface DrawdownChartProps {
  series: SeriesPoint[]
}

export function DrawdownChart({ series }: DrawdownChartProps) {
  // Data minimum rounded down to a multiple of 10; never a zero-height axis.
  const floor = useMemo(() => Math.min(-10, Math.floor(lowest(series) / 10) * 10), [series])
  const yTicks = useMemo(() => axisTicks(floor), [floor])
  const xTicks = useMemo(() => yearTicks(series, 2), [series])
  const worst = useMemo(() => worstDrawdown(series), [series])
  const points = useMemo(() => {
    const map = new Map<string, SeriesPoint>()
    for (const point of series) map.set(point.date, point)
    return map
  }, [series])

  // Keep the callout inside the plot: anchor it away from the nearest edge.
  const labelPosition: 'left' | 'right' =
    worst !== null && series.length > 1 && worst.index > series.length / 2 ? 'left' : 'right'

  return (
    <section className="panel min-w-0 p-4">
      <header className="mb-3 flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <h3 className="text-sm font-semibold tracking-tight text-primary">Drawdown</h3>
          <LegendChip color="var(--color-negative)">Strategy</LegendChip>
          <LegendChip color="var(--color-muted)">Benchmark</LegendChip>
        </div>
        {worst !== null ? (
          <div className="text-[11px] text-muted">
            Worst{' '}
            <span className={`mono ${signClass(worst.value)}`}>{fmtPct(worst.value)}</span>
            <span className="text-muted"> on {fmtDate(worst.date)}</span>
          </div>
        ) : null}
      </header>

      {series.length === 0 ? (
        <div className="flex h-[220px] items-center justify-center rounded border border-dashed border-border text-xs text-muted">
          No drawdown series in this run.
        </div>
      ) : (
        <div className="h-[220px] w-full min-w-0">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart
              data={series}
              syncId="prooftrade-timeline"
              margin={{ top: 8, right: 12, bottom: 0, left: 0 }}
            >
              <defs>
                <linearGradient id="prooftrade-dd-fill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--color-negative)" stopOpacity={0.05} />
                  <stop offset="100%" stopColor="var(--color-negative)" stopOpacity={0.38} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="var(--color-border)" strokeDasharray="2 4" vertical={false} />
              <XAxis
                dataKey="date"
                ticks={xTicks}
                interval="preserveStartEnd"
                minTickGap={16}
                tickFormatter={(value: string) => value.slice(0, 4)}
                tick={{ fill: 'var(--color-muted)', fontSize: 11 }}
                tickLine={false}
                axisLine={{ stroke: 'var(--color-border)' }}
              />
              <YAxis
                domain={[floor, 0]}
                ticks={yTicks}
                allowDataOverflow={false}
                tickFormatter={(value: number) => `${value.toFixed(0)}%`}
                tick={{ fill: 'var(--color-muted)', fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                width={48}
              />
              <ReferenceLine y={0} stroke="var(--color-border)" strokeWidth={1} />
              <Tooltip
                isAnimationActive={false}
                cursor={{ stroke: 'var(--color-border)', strokeWidth: 1 }}
                wrapperStyle={{ outline: 'none' }}
                content={<DrawdownTooltip points={points} />}
              />
              <Area
                type="linear"
                dataKey="benchmark_drawdown"
                name="Benchmark"
                stroke="var(--color-muted)"
                strokeWidth={1}
                fill="none"
                fillOpacity={0}
                dot={false}
                activeDot={{ r: 2.5, fill: 'var(--color-muted)', stroke: 'var(--color-bg)', strokeWidth: 1 }}
                isAnimationActive={false}
                connectNulls={false}
              />
              <Area
                type="linear"
                dataKey="drawdown"
                name="Strategy"
                stroke="var(--color-negative)"
                strokeWidth={1.25}
                fill="url(#prooftrade-dd-fill)"
                fillOpacity={1}
                dot={false}
                activeDot={{
                  r: 2.5,
                  fill: 'var(--color-negative)',
                  stroke: 'var(--color-bg)',
                  strokeWidth: 1,
                }}
                isAnimationActive={false}
                connectNulls={false}
              />
              {worst !== null && worst.value < 0 ? (
                <ReferenceDot
                  x={worst.date}
                  y={worst.value}
                  r={3}
                  fill="var(--color-negative)"
                  stroke="var(--color-bg)"
                  strokeWidth={1}
                  ifOverflow="extendDomain"
                  label={{
                    value: `${fmtPct(worst.value)} · ${fmtDate(worst.date)}`,
                    position: labelPosition,
                    offset: 8,
                    fill: 'var(--color-negative)',
                    fontSize: 11,
                  }}
                />
              ) : null}
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}

      <p className="mt-2 text-[11px] leading-4 text-muted">
        Underwater curve: percent below the previous equity peak, every day.
      </p>
    </section>
  )
}
