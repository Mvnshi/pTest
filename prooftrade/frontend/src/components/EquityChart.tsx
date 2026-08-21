import { useMemo, useState } from 'react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { SeriesPoint } from '../types'
import { fmtDate, fmtMoney, fmtPct, signClass } from '../format'

type YScale = 'log' | 'linear'

interface AxisPlan {
  domain: [number, number]
  ticks: number[]
}

/** "$120k", "$1.2M" -- axis labels must stay narrow or they eat the plot. */
function compactMoney(value: number): string {
  if (!Number.isFinite(value)) return '--'
  const sign = value < 0 ? '-' : ''
  const abs = Math.abs(value)
  if (abs >= 1e9) return `${sign}$${trimUnit(abs / 1e9)}B`
  if (abs >= 1e6) return `${sign}$${trimUnit(abs / 1e6)}M`
  if (abs >= 1e3) return `${sign}$${trimUnit(abs / 1e3)}k`
  return `${sign}$${Math.round(abs)}`
}

function trimUnit(n: number): string {
  const s = n >= 10 ? n.toFixed(0) : n.toFixed(1)
  return s.endsWith('.0') ? s.slice(0, -2) : s
}

function round12(n: number): number {
  return Number(n.toPrecision(12))
}

/** Min/max across both curves plus the starting capital line. */
function extent(series: SeriesPoint[], seed: number): { min: number; max: number } {
  let min = Number.isFinite(seed) ? seed : Number.POSITIVE_INFINITY
  let max = Number.isFinite(seed) ? seed : Number.NEGATIVE_INFINITY
  for (const point of series) {
    if (Number.isFinite(point.equity)) {
      if (point.equity < min) min = point.equity
      if (point.equity > max) max = point.equity
    }
    if (Number.isFinite(point.benchmark)) {
      if (point.benchmark < min) min = point.benchmark
      if (point.benchmark > max) max = point.benchmark
    }
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { min: 0, max: 1 }
  return { min, max }
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

/** 1 / 1.5 / 2 / 3 / 5 / 7 x 10^n gridlines -- readable across a 20-year curve. */
function logAxis(min: number, max: number): AxisPlan {
  const lowExp = Math.floor(Math.log10(min))
  const highExp = Math.ceil(Math.log10(max))
  const decades = Math.max(1, highExp - lowExp)
  const mantissas = decades <= 1 ? [1, 1.5, 2, 3, 5, 7] : decades <= 3 ? [1, 2, 5] : [1]
  const candidates: number[] = []
  for (let exp = lowExp - 1; exp <= highExp + 1; exp += 1) {
    for (const mantissa of mantissas) candidates.push(round12(mantissa * 10 ** exp))
  }
  candidates.sort((a, b) => a - b)

  let lo = candidates[0]
  for (const value of candidates) {
    if (value <= min) lo = value
  }
  let hi = candidates[candidates.length - 1]
  for (let i = candidates.length - 1; i >= 0; i -= 1) {
    if (candidates[i] >= max) hi = candidates[i]
  }
  if (!(hi > lo)) hi = round12(lo * 10)

  return { domain: [lo, hi], ticks: candidates.filter((v) => v >= lo && v <= hi) }
}

function linearAxis(min: number, max: number, targetTicks: number): AxisPlan {
  if (!(max > min)) {
    const pad = Math.abs(max) * 0.05 || 1
    return { domain: [min - pad, max + pad], ticks: [min - pad, max, max + pad] }
  }
  const rawStep = (max - min) / targetTicks
  const magnitude = 10 ** Math.floor(Math.log10(rawStep))
  const norm = rawStep / magnitude
  const stepFactor = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10
  const step = stepFactor * magnitude
  const lo = Math.floor(min / step) * step
  const hi = Math.ceil(max / step) * step
  const ticks: number[] = []
  for (let value = lo; value <= hi + step / 2; value += step) ticks.push(round12(value))
  if (ticks.length < 2) return { domain: [lo, hi], ticks: [lo, hi] }
  return { domain: [ticks[0], ticks[ticks.length - 1]], ticks }
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

interface EquityTooltipProps {
  /** Injected by Recharts when it clones this element. */
  active?: boolean
  /** Injected by Recharts: the category value of the hovered x position. */
  label?: string | number
  points: ReadonlyMap<string, SeriesPoint>
  benchmarkSymbol: string
}

function EquityTooltip({ active, label, points, benchmarkSymbol }: EquityTooltipProps) {
  if (active !== true || label === undefined) return null
  const point = points.get(String(label))
  if (point === undefined) return null

  const gapPct = point.benchmark !== 0 ? (point.equity / point.benchmark - 1) * 100 : 0

  return (
    <div className="panel px-3 py-2 text-xs">
      <div className="mb-1 text-[11px] uppercase tracking-wider text-muted">{fmtDate(point.date)}</div>
      <TooltipRow label="Strategy" value={fmtMoney(point.equity)} valueClass="text-accent" />
      <TooltipRow
        label={`Buy & hold ${benchmarkSymbol}`}
        value={fmtMoney(point.benchmark)}
        valueClass="text-primary"
      />
      <div className="mt-1 border-t border-border pt-1">
        <TooltipRow label="Gap" value={fmtPct(gapPct)} valueClass={signClass(gapPct)} />
      </div>
    </div>
  )
}

interface ScaleButtonProps {
  value: YScale
  current: YScale
  disabled: boolean
  onSelect: (scale: YScale) => void
}

function ScaleButton({ value, current, disabled, onSelect }: ScaleButtonProps) {
  const isActive = current === value
  return (
    <button
      type="button"
      disabled={disabled}
      aria-pressed={isActive}
      onClick={() => onSelect(value)}
      className={[
        'px-2 py-0.5 text-[11px] uppercase tracking-wider transition-colors',
        isActive ? 'bg-panel-hi text-accent' : 'text-muted hover:text-primary',
        disabled ? 'cursor-not-allowed opacity-40 hover:text-muted' : '',
      ].join(' ')}
    >
      {value}
    </button>
  )
}

interface LegendChipProps {
  color: string
  dashed?: boolean
  children: string
}

function LegendChip({ color, dashed = false, children }: LegendChipProps) {
  return (
    <span className="flex items-center gap-1.5 text-[11px] text-muted">
      <span
        aria-hidden="true"
        className="inline-block h-0 w-4"
        style={{ borderTop: `2px ${dashed ? 'dashed' : 'solid'} ${color}` }}
      />
      {children}
    </span>
  )
}

export interface EquityChartProps {
  series: SeriesPoint[]
  benchmarkSymbol: string
  initialCapital: number
}

export function EquityChart({ series, benchmarkSymbol, initialCapital }: EquityChartProps) {
  // Log is the honest default: a 20-year curve on a linear axis hides the
  // early years entirely and flatters whatever compounded last.
  const [scale, setScale] = useState<YScale>('log')

  const bounds = useMemo(() => extent(series, initialCapital), [series, initialCapital])
  const canUseLog = bounds.min > 0
  const effectiveScale: YScale = canUseLog ? scale : 'linear'

  const xTicks = useMemo(() => yearTicks(series, 2), [series])
  const yAxis = useMemo(
    () =>
      effectiveScale === 'log'
        ? logAxis(bounds.min, bounds.max)
        : linearAxis(bounds.min, bounds.max, 6),
    [effectiveScale, bounds],
  )
  const points = useMemo(() => {
    const map = new Map<string, SeriesPoint>()
    for (const point of series) map.set(point.date, point)
    return map
  }, [series])

  return (
    <section className="panel min-w-0 p-4">
      <header className="mb-3 flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <h3 className="text-sm font-semibold tracking-tight text-primary">Equity curve</h3>
          <LegendChip color="var(--color-accent)">Strategy</LegendChip>
          <LegendChip color="var(--color-muted)">{`Buy & hold ${benchmarkSymbol}`}</LegendChip>
          <LegendChip color="var(--color-border)" dashed>
            {`Start ${compactMoney(initialCapital)}`}
          </LegendChip>
        </div>
        <div
          className="flex items-center overflow-hidden rounded border border-border"
          role="group"
          aria-label="Y axis scale"
        >
          <ScaleButton value="log" current={effectiveScale} disabled={!canUseLog} onSelect={setScale} />
          <ScaleButton value="linear" current={effectiveScale} disabled={false} onSelect={setScale} />
        </div>
      </header>

      {series.length === 0 ? (
        <div className="flex h-[340px] items-center justify-center rounded border border-dashed border-border text-xs text-muted">
          No equity series in this run.
        </div>
      ) : (
        <div className="h-[340px] w-full min-w-0">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart
              data={series}
              syncId="prooftrade-timeline"
              margin={{ top: 8, right: 12, bottom: 0, left: 0 }}
            >
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
                scale={effectiveScale}
                domain={yAxis.domain}
                ticks={yAxis.ticks}
                allowDataOverflow={false}
                tickFormatter={compactMoney}
                tick={{ fill: 'var(--color-muted)', fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                width={58}
              />
              <ReferenceLine
                y={initialCapital}
                stroke="var(--color-border)"
                strokeDasharray="4 4"
                strokeWidth={1}
              />
              <Tooltip
                isAnimationActive={false}
                cursor={{ stroke: 'var(--color-border)', strokeWidth: 1 }}
                wrapperStyle={{ outline: 'none' }}
                content={<EquityTooltip points={points} benchmarkSymbol={benchmarkSymbol} />}
              />
              <Line
                type="linear"
                dataKey="benchmark"
                name={`Buy & hold ${benchmarkSymbol}`}
                stroke="var(--color-muted)"
                strokeWidth={1}
                dot={false}
                activeDot={{ r: 2.5, fill: 'var(--color-muted)', stroke: 'var(--color-bg)', strokeWidth: 1 }}
                isAnimationActive={false}
                connectNulls={false}
              />
              <Line
                type="linear"
                dataKey="equity"
                name="Strategy"
                stroke="var(--color-accent)"
                strokeWidth={1.5}
                dot={false}
                activeDot={{ r: 2.5, fill: 'var(--color-accent)', stroke: 'var(--color-bg)', strokeWidth: 1 }}
                isAnimationActive={false}
                connectNulls={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      <p className="mt-2 text-[11px] leading-4 text-muted">
        Strategy is net of commission and slippage; the benchmark pays one entry cost.
      </p>
    </section>
  )
}
