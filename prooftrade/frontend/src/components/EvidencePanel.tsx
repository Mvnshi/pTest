import type { Evidence } from '../types'
import { fmtNum, fmtPct, severityClass } from '../format'

/**
 * The `reproducibility` block of the POST /api/backtest response.
 *
 * Declared structurally here rather than imported so the panel compiles
 * against the payload as it actually arrives. Every field is optional: a run
 * that failed validation still renders, it just has less to say.
 */
export interface Reproducibility {
  deterministic?: boolean | null | undefined
  note?: string | null | undefined
  canonical_strategy?: string | null | undefined
  /** Some payloads carry it here; otherwise the hash is read out of `note`. */
  result_hash?: string | null | undefined
}

export interface EvidencePanelProps {
  evidence: Evidence
  reproducibility: Reproducibility
  dataIsSynthetic: boolean
}

/* -------------------------------------------------------------------------
   Tolerant readers.

   The report is generated server-side and every one of these fields can be
   an empty string or null on a degenerate run (no trades, window too short).
   Reading through these keeps the panel from rendering "NaN" or "null".
------------------------------------------------------------------------- */

function num(value: number | null | undefined, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function text(value: string | null | undefined): string {
  return typeof value === 'string' ? value.trim() : ''
}

function list<T>(value: readonly T[] | null | undefined): readonly T[] {
  return value === null || value === undefined ? [] : value
}

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0
  return Math.min(1, Math.max(0, value))
}

/* ------------------------------------------------------------------------- */

type Tone = 'positive' | 'accent' | 'warning' | 'negative' | 'muted'

/** Static strings so Tailwind's scanner sees every class that can be emitted. */
const TONE_TEXT: Record<Tone, string> = {
  positive: 'text-positive',
  accent: 'text-accent',
  warning: 'text-warning',
  negative: 'text-negative',
  muted: 'text-muted',
}

const TONE_BORDER: Record<Tone, string> = {
  positive: 'border-positive',
  accent: 'border-accent',
  warning: 'border-warning',
  negative: 'border-negative',
  muted: 'border-border',
}

const TONE_FILL: Record<Tone, string> = {
  positive: 'var(--color-positive)',
  accent: 'var(--color-accent)',
  warning: 'var(--color-warning)',
  negative: 'var(--color-negative)',
  muted: 'var(--color-muted)',
}

/** A and B pass, C is a caution, D and F fail. Anything else stays neutral. */
function gradeTone(grade: string): Tone {
  switch (grade.trim().charAt(0).toUpperCase()) {
    case 'A':
    case 'B':
      return 'positive'
    case 'C':
      return 'warning'
    case 'D':
    case 'E':
    case 'F':
      return 'negative'
    default:
      return 'accent'
  }
}

/** Component bars are graded on the share of their own weight they earned. */
function ratioTone(ratio: number): Tone {
  if (ratio >= 0.8) return 'positive'
  if (ratio >= 0.6) return 'accent'
  if (ratio >= 0.4) return 'warning'
  return 'negative'
}

/** Critical and high warnings get a coloured edge; the rest sit on a hairline. */
function warningBorder(severity: string): string {
  switch (severity.trim().toLowerCase()) {
    case 'critical':
      return 'border-negative/55'
    case 'high':
      return 'border-warning/55'
    default:
      return 'border-border'
  }
}

const LABELLED_HASH = /result hash\s+([0-9a-f]{6,64})\b/i
const BARE_HASH = /\b[0-9a-f]{8,64}\b/

/**
 * The engine states the result hash inside `reproducibility.note`; some
 * payloads also carry it as its own field. Prefer the field, fall back to
 * lifting it out of the sentence so the footer always has something to show.
 */
function readResultHash(reproducibility: Reproducibility): string {
  const explicit = text(reproducibility.result_hash)
  if (explicit !== '') return explicit

  const note = text(reproducibility.note)
  const labelled = LABELLED_HASH.exec(note)
  if (labelled !== null) {
    const captured = labelled[1]
    if (typeof captured === 'string') return captured
  }
  const bare = BARE_HASH.exec(note)
  if (bare !== null) {
    const captured = bare[0]
    if (typeof captured === 'string') return captured
  }
  return ''
}

interface ComponentRow {
  label: string
  points: number
  weight: number
  ratio: number
  measurement: string
  detail: string
}

interface WarningRow {
  severity: string
  title: string
  message: string
  measurement: string
  threshold: string
}

interface ComponentRowItemProps {
  row: ComponentRow
  weakest: boolean
}

function ComponentRowItem({ row, weakest }: ComponentRowItemProps) {
  const tone = weakest ? 'warning' : ratioTone(row.ratio)
  const pointsLabel = `${fmtNum(row.points, 1)} / ${fmtNum(row.weight, 0)} pts`

  return (
    <li
      className={`border-l-2 px-4 py-3 ${
        weakest ? 'border-l-warning bg-warning/[0.045]' : 'border-l-transparent'
      }`}
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="flex min-w-[11rem] flex-[1.1] items-center gap-2">
          <span className="text-sm text-primary">{row.label}</span>
          {weakest ? (
            <span className="rounded-sm border border-warning/50 px-1.5 py-px text-[9px] uppercase tracking-[0.12em] text-warning">
              Weak link
            </span>
          ) : null}
        </div>

        <div
          className="h-1.5 min-w-[7rem] flex-[1.6] overflow-hidden rounded-sm bg-panel-hi"
          role="progressbar"
          aria-label={`${row.label}: ${pointsLabel}`}
          aria-valuenow={Number(row.points.toFixed(1))}
          aria-valuemin={0}
          aria-valuemax={Number(row.weight.toFixed(0))}
        >
          <div
            className="h-full rounded-sm"
            style={{ width: `${(row.ratio * 100).toFixed(2)}%`, backgroundColor: TONE_FILL[tone] }}
          />
        </div>

        <div className="flex shrink-0 items-baseline justify-end gap-3">
          <span className={`mono w-[6.5rem] text-right text-sm ${TONE_TEXT[tone]}`}>
            {pointsLabel}
          </span>
          <span className="mono w-10 text-right text-[11px] text-muted">
            {fmtPct(row.ratio * 100, 0)}
          </span>
        </div>
      </div>

      {row.measurement === '' ? null : (
        <p className="mt-2 text-xs leading-5 text-primary">{row.measurement}</p>
      )}
      {row.detail === '' ? null : (
        <p className="mt-0.5 text-[11px] leading-4 text-muted">{row.detail}</p>
      )}
    </li>
  )
}

function WarningCard({ row }: { row: WarningRow }) {
  const footer: string[] = []
  if (row.measurement !== '') footer.push(`Measured: ${row.measurement}`)
  if (row.threshold !== '') footer.push(`Threshold: ${row.threshold}`)

  return (
    <article className={`rounded border bg-bg/40 px-3 py-2.5 ${warningBorder(row.severity)}`}>
      <header className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
        <span
          className={`rounded-sm px-1.5 py-px text-[9px] font-medium uppercase tracking-[0.14em] ${severityClass(
            row.severity,
          )}`}
        >
          {row.severity}
        </span>
        <h4 className="text-sm font-medium leading-5 text-primary">{row.title}</h4>
      </header>
      {row.message === '' ? null : (
        <p className="mt-1.5 text-xs leading-5 text-muted">{row.message}</p>
      )}
      {footer.length === 0 ? null : (
        <p className="mono mt-2 border-t border-border pt-1.5 text-[11px] leading-4 text-muted">
          {footer.join(' · ')}
        </p>
      )}
    </article>
  )
}

export function EvidencePanel({ evidence, reproducibility, dataIsSynthetic }: EvidencePanelProps) {
  const score = num(evidence.score)
  const grade = text(evidence.grade)
  const headline = text(evidence.headline)
  const tone = gradeTone(grade)

  const rows: ComponentRow[] = list(evidence.components).map((component) => {
    const weight = num(component.weight)
    const points = num(component.points)
    return {
      label: text(component.label),
      points,
      weight,
      // Weights differ, so the fair comparison is the share of weight earned.
      ratio: weight > 0 ? clamp01(points / weight) : clamp01(num(component.score)),
      measurement: text(component.measurement),
      detail: text(component.detail),
    }
  })

  // The weak link is the component that kept the least of its own weight.
  let weakestIndex = -1
  for (let i = 0; i < rows.length; i += 1) {
    const candidate = rows[i]
    if (weakestIndex === -1 || candidate.ratio < rows[weakestIndex].ratio) weakestIndex = i
  }

  // Order is the engine's (severity, then id). Deliberately not re-sorted.
  const warnings: WarningRow[] = list(evidence.warnings).map((warning) => ({
    severity: text(warning.severity),
    title: text(warning.title),
    message: text(warning.message),
    measurement: text(warning.measurement),
    threshold: text(warning.threshold),
  }))

  const resultHash = readResultHash(reproducibility)
  const note = text(reproducibility.note)
  const deterministic = reproducibility.deterministic

  return (
    <section className="panel overflow-hidden" aria-label="Evidence report">
      {dataIsSynthetic ? (
        <div className="border-b border-negative/40 bg-negative/[0.07] px-4 py-3">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="rounded-sm border border-negative/60 px-1.5 py-px text-[9px] font-medium uppercase tracking-[0.14em] text-negative">
              Synthetic data
            </span>
            <span className="text-sm font-medium text-primary">
              These bars were generated, not observed.
            </span>
          </div>
          <p className="mt-1.5 text-xs leading-5 text-muted">
            The snapshot comes from a seeded regime-switching model so the demo runs offline and
            reproducibly. The structure is realistic; the prices are not real. Nothing on this page
            is a statement about how this strategy would have behaved in an actual market.
          </p>
        </div>
      ) : null}

      <header className="border-b border-border px-4 py-4">
        <div className="flex flex-wrap items-start justify-between gap-x-8 gap-y-4">
          <div className="flex items-end gap-4">
            <div className="flex items-baseline gap-1.5">
              <span className={`mono text-6xl font-semibold leading-none tracking-tight ${TONE_TEXT[tone]}`}>
                {fmtNum(score, 1)}
              </span>
              <span className="mono text-lg leading-none text-muted">/100</span>
            </div>
            {grade === '' ? null : (
              <div
                className={`grid size-11 shrink-0 place-items-center rounded border ${TONE_BORDER[tone]} ${TONE_TEXT[tone]}`}
                aria-label={`Grade ${grade}`}
              >
                <span className="mono text-2xl font-semibold leading-none">{grade}</span>
              </div>
            )}
          </div>

          <div className="min-w-[16rem] max-w-2xl flex-1">
            <div className="text-[11px] uppercase tracking-[0.14em] text-muted">Evidence score</div>
            {headline === '' ? null : (
              <p className="mt-1 text-sm leading-6 text-primary">{headline}</p>
            )}
          </div>
        </div>

        <div className="mt-4 h-1 w-full overflow-hidden rounded-sm bg-panel-hi">
          <div
            className="h-full rounded-sm"
            style={{
              width: `${(clamp01(score / 100) * 100).toFixed(2)}%`,
              backgroundColor: TONE_FILL[tone],
            }}
          />
        </div>

        <p className="mt-2 text-[11px] leading-4 text-muted">
          This scores how much the backtest can be trusted - not how much money the strategy will
          make. A strategy that reliably loses money can score well.
        </p>
      </header>

      <div className="border-b border-border">
        <div className="flex items-baseline justify-between gap-4 px-4 py-2.5">
          <h3 className="text-xs font-semibold uppercase tracking-[0.08em] text-primary">
            Score components
          </h3>
          <span className="mono text-[11px] text-muted">
            {fmtNum(rows.length, 0)} components · 100 pts total
          </span>
        </div>
        {rows.length === 0 ? (
          <p className="px-4 pb-3 text-xs text-muted">
            No component breakdown was produced for this run.
          </p>
        ) : (
          <ul className="divide-y divide-border/60 border-t border-border">
            {rows.map((row, index) => (
              <ComponentRowItem
                key={`${index}-${row.label}`}
                row={row}
                weakest={index === weakestIndex}
              />
            ))}
          </ul>
        )}
      </div>

      <div className="border-b border-border">
        <div className="flex items-baseline justify-between gap-4 px-4 py-2.5">
          <h3 className="text-xs font-semibold uppercase tracking-[0.08em] text-primary">
            Warnings
          </h3>
          <span className="mono text-[11px] text-muted">
            {fmtNum(warnings.length, 0)} raised · most severe first
          </span>
        </div>
        {warnings.length === 0 ? (
          <p className="px-4 pb-3 text-xs text-muted">
            No warnings were raised. That is not the same as an endorsement.
          </p>
        ) : (
          <div className="grid gap-2 px-4 pb-4">
            {warnings.map((row, index) => (
              <WarningCard key={`${index}-${row.title}`} row={row} />
            ))}
          </div>
        )}
      </div>

      <footer className="px-4 py-3">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="text-[11px] uppercase tracking-[0.14em] text-muted">Result hash</span>
          <span className="mono text-xs text-accent">
            {resultHash === '' ? 'unavailable' : resultHash}
          </span>
          {deterministic === true ? (
            <span className="mono text-[11px] text-positive">deterministic</span>
          ) : null}
          {deterministic === false ? (
            <span className="mono text-[11px] text-warning">determinism not verified</span>
          ) : null}
        </div>
        {note === '' ? null : (
          <p className="mono mt-1.5 text-[11px] leading-4 text-muted">{note}</p>
        )}
      </footer>
    </section>
  )
}
