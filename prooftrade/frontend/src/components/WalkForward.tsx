import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { Validation } from '../types'
import { fmtDate, fmtNum, fmtPct, fmtRatio, signClass } from '../format'

export interface WalkForwardProps {
  validation: Validation
}

/* -------------------------------------------------------------------------
   Tolerant readers. Any of these fields can be null on a degenerate run -
   too few bars for folds, a Sharpe too close to zero for a ratio to mean
   anything - and the panel still has to render.
------------------------------------------------------------------------- */

function num(value: number | null | undefined, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function maybeNum(value: number | null | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function text(value: string | null | undefined): string {
  return typeof value === 'string' ? value.trim() : ''
}

function list<T>(value: readonly T[] | null | undefined): readonly T[] {
  return value === null || value === undefined ? [] : value
}

/** Parameter values are integers (periods) or decimals (thresholds). */
function fmtParam(value: number): string {
  return Number.isInteger(value) ? fmtNum(value, 0) : fmtNum(value, 2)
}

/** 0.9 -> "-10%", 1.2 -> "+20%". Direction, not performance, so it stays muted. */
function fmtFactor(factor: number): string {
  const change = (factor - 1) * 100
  const rounded = Math.abs(change) < 0.05 ? 0 : change
  return `${rounded > 0 ? '+' : ''}${fmtPct(rounded, 0)}`
}

/* ------------------------------------------------------------------------- */

interface FoldRow {
  tick: string
  name: string
  range: string
  sharpe: number
  returnPct: number
  trades: number
}

interface CostRow {
  tick: string
  multiple: number
  bps: number
  cagr: number
  sharpe: number
  totalReturn: number
  trades: number
}

interface NeighbourRow {
  label: string
  path: string
  baseValue: number
  value: number
  factor: number
  sharpe: number
  cagr: number
  trades: number
}

interface SectionHeaderProps {
  title: string
  aside: string
}

function SectionHeader({ title, aside }: SectionHeaderProps) {
  return (
    <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-border px-4 py-2.5">
      <h3 className="text-xs font-semibold uppercase tracking-[0.08em] text-primary">{title}</h3>
      {aside === '' ? null : <span className="mono text-[11px] text-muted">{aside}</span>}
    </header>
  )
}

interface StatProps {
  label: string
  value: string
  tone: string
}

function Stat({ label, value, tone }: StatProps) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-border/60 py-1.5 last:border-b-0">
      <span className="text-[11px] uppercase tracking-[0.08em] text-muted">{label}</span>
      <span className={`mono text-sm ${tone}`}>{value}</span>
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

interface FoldTooltipProps {
  /** Injected by Recharts when it clones this element. */
  active?: boolean
  /** Injected by Recharts: the category value of the hovered bar. */
  label?: string | number
  rows: ReadonlyMap<string, FoldRow>
}

function FoldTooltip({ active, label, rows }: FoldTooltipProps) {
  if (active !== true || label === undefined) return null
  const row = rows.get(String(label))
  if (row === undefined) return null

  return (
    <div className="panel px-3 py-2 text-xs">
      <div className="mb-1 text-[11px] uppercase tracking-wider text-muted">{row.name}</div>
      <div className="mono mb-1.5 text-[11px] text-muted">{row.range}</div>
      <TooltipRow label="Sharpe" value={fmtRatio(row.sharpe)} valueClass={signClass(row.sharpe)} />
      <TooltipRow
        label="Return"
        value={fmtPct(row.returnPct, 2)}
        valueClass={signClass(row.returnPct)}
      />
      <TooltipRow label="Trades" value={fmtNum(row.trades, 0)} valueClass="text-primary" />
    </div>
  )
}

interface CostTooltipProps {
  active?: boolean
  label?: string | number
  rows: ReadonlyMap<string, CostRow>
}

function CostTooltip({ active, label, rows }: CostTooltipProps) {
  if (active !== true || label === undefined) return null
  const row = rows.get(String(label))
  if (row === undefined) return null

  return (
    <div className="panel px-3 py-2 text-xs">
      <div className="mb-1 text-[11px] uppercase tracking-wider text-muted">
        {row.tick} costs · {fmtNum(row.bps, 1)} bps round trip
      </div>
      <TooltipRow label="CAGR" value={fmtPct(row.cagr, 2)} valueClass={signClass(row.cagr)} />
      <TooltipRow label="Sharpe" value={fmtRatio(row.sharpe)} valueClass={signClass(row.sharpe)} />
      <TooltipRow
        label="Total return"
        value={fmtPct(row.totalReturn, 2)}
        valueClass={signClass(row.totalReturn)}
      />
      <TooltipRow label="Trades" value={fmtNum(row.trades, 0)} valueClass="text-primary" />
    </div>
  )
}

/* ------------------------------------------------------------------------- */

export function WalkForward({ validation }: WalkForwardProps) {
  const walkForward = validation.walk_forward
  const costs = validation.cost_sensitivity
  const robustness = validation.parameter_robustness

  /* --- 1. Walk-forward folds ------------------------------------------- */

  const foldRows: FoldRow[] = list(walkForward?.folds).map((fold, index) => {
    const position = num(fold.index, index + 1)
    const start = text(fold.start)
    const end = text(fold.end)
    return {
      tick: String(position),
      name: `Fold ${fmtNum(position, 0)}`,
      range: start === '' || end === '' ? '—' : `${fmtDate(start)} → ${fmtDate(end)}`,
      sharpe: num(fold.metrics?.sharpe),
      returnPct: num(fold.metrics?.total_return_pct),
      trades: num(fold.metrics?.trades),
    }
  })

  const foldsByTick = new Map<string, FoldRow>()
  for (const row of foldRows) foldsByTick.set(row.tick, row)

  // The two ends of the fold distribution: how far apart the eras really are.
  let weakestFold: FoldRow | null = null
  let strongestFold: FoldRow | null = null
  for (const row of foldRows) {
    if (weakestFold === null || row.sharpe < weakestFold.sharpe) weakestFold = row
    if (strongestFold === null || row.sharpe > strongestFold.sharpe) strongestFold = row
  }

  const positiveFolds = num(walkForward?.positive_folds)
  const totalFolds = num(walkForward?.total_folds, foldRows.length)
  const medianSharpe = num(walkForward?.median_sharpe)
  const worstFoldReturn = maybeNum(walkForward?.worst_fold_return_pct)
  const foldNote = text(walkForward?.note)

  /* --- 2. Cost sensitivity --------------------------------------------- */

  const costRows: CostRow[] = list(costs?.points).map((point) => {
    const multiple = num(point.multiple)
    return {
      tick: `${fmtParam(multiple)}x`,
      multiple,
      bps: num(point.round_trip_bps),
      cagr: num(point.cagr_pct),
      sharpe: num(point.sharpe),
      totalReturn: num(point.total_return_pct),
      trades: num(point.trades),
    }
  })

  const costsByTick = new Map<string, CostRow>()
  for (const row of costRows) costsByTick.set(row.tick, row)

  const baseSharpe = num(costs?.base_sharpe)
  const sharpeAt3x = num(costs?.sharpe_at_3x)
  const retainedAt3x = maybeNum(costs?.sharpe_retained_at_3x)
  const breakevenBps = maybeNum(costs?.breakeven_round_trip_bps)
  const survives5x = costs?.survives_5x
  const costNote = text(costs?.note)

  // 80% retained at triple costs is the evidence engine's full-marks line.
  const retainedTone =
    retainedAt3x === null
      ? 'text-muted'
      : retainedAt3x >= 0.8
        ? 'text-positive'
        : retainedAt3x >= 0.5
          ? 'text-warning'
          : 'text-negative'

  /* --- 3. Parameter neighbourhood -------------------------------------- */

  const neighbours: NeighbourRow[] = list(robustness?.neighbours).map((neighbour) => ({
    label: text(neighbour.label),
    path: text(neighbour.path),
    baseValue: num(neighbour.base_value),
    value: num(neighbour.value),
    factor: num(neighbour.factor, 1),
    sharpe: num(neighbour.sharpe),
    cagr: num(neighbour.cagr_pct),
    trades: num(neighbour.trades),
  }))

  const paramBaseSharpe = num(robustness?.base_sharpe)
  const paramMedianSharpe = num(robustness?.median_sharpe)
  const fragility = maybeNum(robustness?.fragility_ratio)
  const profitableShare = maybeNum(robustness?.profitable_share_pct)
  const probed = num(robustness?.parameters_probed)
  const parametersTotal = num(robustness?.parameters_total)
  const paramNote = text(robustness?.note)

  // Base far above its own neighbourhood is a spike, not a plateau.
  const fragilityTone =
    fragility === null
      ? 'text-muted'
      : fragility <= 1.25
        ? 'text-positive'
        : fragility <= 1.75
          ? 'text-warning'
          : 'text-negative'

  return (
    <div className="grid gap-4">
      {/* ---------------------------------------------------------------- */}
      <section className="panel min-w-0 overflow-hidden" aria-label="Walk-forward folds">
        <SectionHeader
          title="Walk-forward folds"
          aside={foldRows.length === 0 ? '' : `${fmtNum(foldRows.length, 0)} sequential windows`}
        />

        {foldNote !== '' ? (
          <p className="px-4 py-4 text-sm leading-6 text-muted">{foldNote}</p>
        ) : foldRows.length === 0 ? (
          <p className="px-4 py-4 text-sm leading-6 text-muted">
            The window was too short to cut into folds, so there is no cross-era evidence here.
          </p>
        ) : (
          <div className="grid gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_15rem]">
            <div className="h-[220px] w-full min-w-0">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={foldRows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                  <CartesianGrid
                    stroke="var(--color-border)"
                    strokeDasharray="2 4"
                    vertical={false}
                  />
                  <XAxis
                    dataKey="tick"
                    tick={{ fill: 'var(--color-muted)', fontSize: 11 }}
                    tickLine={false}
                    axisLine={{ stroke: 'var(--color-border)' }}
                  />
                  <YAxis
                    tickFormatter={(value: number) => value.toFixed(2)}
                    tick={{ fill: 'var(--color-muted)', fontSize: 11 }}
                    tickLine={false}
                    axisLine={false}
                    width={48}
                  />
                  <ReferenceLine y={0} stroke="var(--color-border)" strokeWidth={1} />
                  <Tooltip
                    isAnimationActive={false}
                    cursor={{ fill: 'var(--color-panel-hi)', fillOpacity: 0.5 }}
                    wrapperStyle={{ outline: 'none' }}
                    content={<FoldTooltip rows={foldsByTick} />}
                  />
                  <Bar dataKey="sharpe" name="Sharpe" isAnimationActive={false} maxBarSize={56}>
                    {foldRows.map((row) => (
                      <Cell
                        key={row.tick}
                        fill={
                          row.sharpe >= 0 ? 'var(--color-positive)' : 'var(--color-negative)'
                        }
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
              <p className="mt-1 text-center text-[11px] text-muted">
                Fold index · Sharpe within the fold
              </p>
            </div>

            <div className="min-w-0">
              <p className="text-sm leading-6 text-primary">
                <span className="mono">{fmtNum(positiveFolds, 0)}</span> of{' '}
                <span className="mono">{fmtNum(totalFolds, 0)}</span> folds positive, median Sharpe{' '}
                <span className={`mono ${signClass(medianSharpe)}`}>{fmtRatio(medianSharpe)}</span>
              </p>
              <div className="mt-2">
                {weakestFold === null ? null : (
                  <Stat
                    label="Weakest fold"
                    value={`${weakestFold.name} · ${fmtRatio(weakestFold.sharpe)}`}
                    tone={signClass(weakestFold.sharpe)}
                  />
                )}
                {strongestFold === null ? null : (
                  <Stat
                    label="Strongest fold"
                    value={`${strongestFold.name} · ${fmtRatio(strongestFold.sharpe)}`}
                    tone={signClass(strongestFold.sharpe)}
                  />
                )}
                {worstFoldReturn === null ? null : (
                  <Stat
                    label="Worst fold return"
                    value={fmtPct(worstFoldReturn, 2)}
                    tone={signClass(worstFoldReturn)}
                  />
                )}
                <Stat
                  label="Spread"
                  value={
                    weakestFold === null || strongestFold === null
                      ? '—'
                      : fmtRatio(strongestFold.sharpe - weakestFold.sharpe)
                  }
                  tone="text-muted"
                />
              </div>
            </div>
          </div>
        )}

        <p className="border-t border-border px-4 py-2.5 text-[11px] leading-4 text-muted">
          Each fold is a separate, non-overlapping slice of history backtested on its own, so this
          shows whether the same rules keep working as the market changes - it does not forecast the
          next fold, and because no parameters are fitted here it measures regime stability rather
          than classical overfitting.
        </p>
      </section>

      {/* ---------------------------------------------------------------- */}
      <section className="panel min-w-0 overflow-hidden" aria-label="Cost sensitivity">
        <SectionHeader
          title="Cost sensitivity"
          aside={costRows.length === 0 ? '' : `${fmtNum(costRows.length, 0)} cost levels re-run`}
        />

        {costRows.length === 0 ? (
          <p className="px-4 py-4 text-sm leading-6 text-muted">
            No cost sweep was run for this strategy.
          </p>
        ) : (
          <div className="grid gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_15rem]">
            <div className="min-w-0">
              <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1">
                <LegendChip color="var(--color-accent)">CAGR %</LegendChip>
                <LegendChip color="var(--color-warning)">Sharpe</LegendChip>
              </div>
              <div className="h-[220px] w-full min-w-0">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={costRows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                    <CartesianGrid
                      stroke="var(--color-border)"
                      strokeDasharray="2 4"
                      vertical={false}
                    />
                    <XAxis
                      dataKey="tick"
                      tick={{ fill: 'var(--color-muted)', fontSize: 11 }}
                      tickLine={false}
                      axisLine={{ stroke: 'var(--color-border)' }}
                    />
                    <YAxis
                      yAxisId="cagr"
                      orientation="left"
                      tickFormatter={(value: number) => `${value.toFixed(1)}%`}
                      tick={{ fill: 'var(--color-muted)', fontSize: 11 }}
                      tickLine={false}
                      axisLine={false}
                      width={52}
                    />
                    <YAxis
                      yAxisId="sharpe"
                      orientation="right"
                      tickFormatter={(value: number) => value.toFixed(2)}
                      tick={{ fill: 'var(--color-muted)', fontSize: 11 }}
                      tickLine={false}
                      axisLine={false}
                      width={44}
                    />
                    <ReferenceLine
                      yAxisId="cagr"
                      y={0}
                      stroke="var(--color-negative)"
                      strokeDasharray="3 3"
                      strokeWidth={1}
                    />
                    <Tooltip
                      isAnimationActive={false}
                      cursor={{ stroke: 'var(--color-border)', strokeWidth: 1 }}
                      wrapperStyle={{ outline: 'none' }}
                      content={<CostTooltip rows={costsByTick} />}
                    />
                    <Line
                      yAxisId="cagr"
                      type="linear"
                      dataKey="cagr"
                      name="CAGR %"
                      stroke="var(--color-accent)"
                      strokeWidth={1.5}
                      dot={{ r: 2, fill: 'var(--color-accent)', strokeWidth: 0 }}
                      activeDot={{
                        r: 3,
                        fill: 'var(--color-accent)',
                        stroke: 'var(--color-bg)',
                        strokeWidth: 1,
                      }}
                      isAnimationActive={false}
                    />
                    <Line
                      yAxisId="sharpe"
                      type="linear"
                      dataKey="sharpe"
                      name="Sharpe"
                      stroke="var(--color-warning)"
                      strokeWidth={1.5}
                      strokeDasharray="4 3"
                      dot={{ r: 2, fill: 'var(--color-warning)', strokeWidth: 0 }}
                      activeDot={{
                        r: 3,
                        fill: 'var(--color-warning)',
                        stroke: 'var(--color-bg)',
                        strokeWidth: 1,
                      }}
                      isAnimationActive={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
              <p className="mt-1 text-center text-[11px] text-muted">
                Multiple of the configured commission and slippage
              </p>
            </div>

            <div className="min-w-0">
              <p className="text-sm leading-6 text-primary">
                <span className={`mono ${retainedTone}`}>
                  {retainedAt3x === null ? '—' : fmtPct(retainedAt3x * 100, 0)}
                </span>{' '}
                of Sharpe retained at 3x costs.{' '}
                {breakevenBps === null
                  ? 'No break-even found in range.'
                  : `Break-even at ${fmtNum(breakevenBps, 1)} bps round trip.`}
              </p>
              <div className="mt-2">
                <Stat
                  label="Sharpe at 1x"
                  value={fmtRatio(baseSharpe)}
                  tone={signClass(baseSharpe)}
                />
                <Stat
                  label="Sharpe at 3x"
                  value={fmtRatio(sharpeAt3x)}
                  tone={signClass(sharpeAt3x)}
                />
                <Stat
                  label="Retained at 3x"
                  value={retainedAt3x === null ? '—' : fmtPct(retainedAt3x * 100, 0)}
                  tone={retainedTone}
                />
                <Stat
                  label="Break-even"
                  value={breakevenBps === null ? 'none in range' : `${fmtNum(breakevenBps, 1)} bps`}
                  tone={breakevenBps === null ? 'text-muted' : 'text-warning'}
                />
                {survives5x === true || survives5x === false ? (
                  <Stat
                    label="Survives 5x"
                    value={survives5x ? 'yes' : 'no'}
                    tone={survives5x ? 'text-positive' : 'text-negative'}
                  />
                ) : null}
              </div>
              {costNote === '' ? null : (
                <p className="mt-2 text-[11px] leading-4 text-muted">{costNote}</p>
              )}
            </div>
          </div>
        )}

        <p className="border-t border-border px-4 py-2.5 text-[11px] leading-4 text-muted">
          Re-running the identical trade list at multiples of the configured commission and
          slippage shows how much of the result is edge and how much is optimistic fills - it
          cannot model market impact, partial fills, or spreads that widen exactly when the signal
          fires.
        </p>
      </section>

      {/* ---------------------------------------------------------------- */}
      <section className="panel min-w-0 overflow-hidden" aria-label="Parameter neighbourhood">
        <SectionHeader
          title="Parameter neighbourhood"
          aside={
            neighbours.length === 0
              ? ''
              : `${fmtNum(neighbours.length, 0)} variants · ${fmtNum(probed, 0)}/${fmtNum(
                  parametersTotal,
                  0,
                )} parameters probed`
          }
        />

        <div className="px-4 py-3">
          {neighbours.length === 0 ? null : (
            <div className="mb-2 flex flex-wrap items-baseline gap-x-6 gap-y-1">
              <div className="flex items-baseline gap-2">
                <span className="text-[11px] uppercase tracking-[0.08em] text-muted">
                  Base Sharpe
                </span>
                <span className={`mono text-sm ${signClass(paramBaseSharpe)}`}>
                  {fmtRatio(paramBaseSharpe)}
                </span>
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-[11px] uppercase tracking-[0.08em] text-muted">
                  Neighbourhood median
                </span>
                <span className={`mono text-sm ${signClass(paramMedianSharpe)}`}>
                  {fmtRatio(paramMedianSharpe)}
                </span>
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-[11px] uppercase tracking-[0.08em] text-muted">
                  Fragility
                </span>
                <span className={`mono text-sm ${fragilityTone}`}>
                  {fragility === null ? '—' : `${fmtRatio(fragility)}x`}
                </span>
              </div>
              {profitableShare === null ? null : (
                <div className="flex items-baseline gap-2">
                  <span className="text-[11px] uppercase tracking-[0.08em] text-muted">
                    Variants profitable
                  </span>
                  <span className="mono text-sm text-primary">{fmtPct(profitableShare, 0)}</span>
                </div>
              )}
            </div>
          )}

          <p className="text-xs leading-5 text-muted">
            A real edge sits on a plateau: nudge a period or a threshold and the result barely
            moves. A spike - base Sharpe far above the median of its own neighbours - means the
            number was found, not earned.
          </p>
        </div>

        {neighbours.length === 0 ? (
          <p className="px-4 pb-4 text-sm leading-6 text-muted">
            {paramNote === ''
              ? 'This strategy has no numeric parameters to perturb.'
              : paramNote}
          </p>
        ) : (
          <div className="max-h-[22rem] overflow-auto border-t border-border">
            <table className="w-full min-w-[42rem] text-sm">
              <thead>
                <tr className="border-b border-border bg-panel text-[11px] uppercase tracking-[0.06em] text-muted">
                  <th
                    scope="col"
                    className="sticky top-0 z-10 bg-panel px-4 py-2 text-left font-medium"
                  >
                    Parameter
                  </th>
                  <th
                    scope="col"
                    className="sticky top-0 z-10 bg-panel px-4 py-2 text-right font-medium whitespace-nowrap"
                  >
                    Base → test
                  </th>
                  <th
                    scope="col"
                    className="sticky top-0 z-10 bg-panel px-4 py-2 text-right font-medium"
                  >
                    Change
                  </th>
                  <th
                    scope="col"
                    className="sticky top-0 z-10 bg-panel px-4 py-2 text-right font-medium"
                  >
                    Sharpe
                  </th>
                  <th
                    scope="col"
                    className="sticky top-0 z-10 bg-panel px-4 py-2 text-right font-medium"
                  >
                    CAGR
                  </th>
                  <th
                    scope="col"
                    className="sticky top-0 z-10 bg-panel px-4 py-2 text-right font-medium"
                  >
                    Trades
                  </th>
                </tr>
              </thead>
              <tbody>
                {neighbours.map((row, index) => (
                  <tr
                    key={`${index}-${row.path}-${row.factor}`}
                    className={`border-b border-border/60 last:border-b-0 hover:bg-panel-hi ${
                      row.sharpe < 0 ? 'bg-negative/[0.05]' : ''
                    }`}
                  >
                    <th scope="row" className="px-4 py-1.5 text-left font-normal text-primary">
                      {row.label}
                      <span className="mono ml-2 text-[11px] text-muted">{row.path}</span>
                    </th>
                    <td className="mono px-4 py-1.5 text-right whitespace-nowrap text-muted">
                      {fmtParam(row.baseValue)} → <span className="text-primary">{fmtParam(row.value)}</span>
                    </td>
                    <td className="mono px-4 py-1.5 text-right text-muted">
                      {fmtFactor(row.factor)}
                    </td>
                    <td className={`mono px-4 py-1.5 text-right ${signClass(row.sharpe)}`}>
                      {fmtRatio(row.sharpe)}
                    </td>
                    <td className={`mono px-4 py-1.5 text-right ${signClass(row.cagr)}`}>
                      {fmtPct(row.cagr, 2)}
                    </td>
                    <td className="mono px-4 py-1.5 text-right text-primary">
                      {fmtNum(row.trades, 0)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {neighbours.length > 0 && paramNote !== '' ? (
          <p className="border-t border-border px-4 py-2.5 text-xs leading-5 text-muted">
            {paramNote}
          </p>
        ) : null}

        <p className="border-t border-border px-4 py-2.5 text-[11px] leading-4 text-muted">
          Moving each numeric parameter off the value you chose shows whether the result survives
          being slightly wrong - it moves one parameter at a time, so it cannot rule out an
          interaction that only breaks when two of them move together.
        </p>
      </section>
    </div>
  )
}
