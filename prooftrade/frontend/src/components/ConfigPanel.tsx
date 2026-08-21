import { useId } from 'react'
import type { ReactNode } from 'react'
import type { BacktestConfig, UniverseInfo } from '../types'
import { fmtDate, fmtMoney, fmtNum, fmtPct } from '../format'

/**
 * The knobs for a run, sitting between Confirm and Backtest.
 *
 * This component owns no state: every edit is pushed up as a partial patch, so
 * the config the user sees is always the config that will be sent. Costs are
 * given the same visual weight as capital on purpose -- they are the setting
 * that decides whether most of these strategies survive.
 */

export interface ConfigPanelProps {
  config: BacktestConfig
  universe: UniverseInfo
  onChange: (patch: Partial<BacktestConfig>) => void
}

const CAPITAL = { min: 1_000, max: 1_000_000_000, step: 1_000 } as const
const BPS = { min: 0, max: 200, step: 0.5 } as const
const FOLDS = { min: 2, max: 10, step: 1 } as const
const TRAIN = { min: 0.3, max: 0.9, step: 0.05 } as const

const INPUT_CLASS =
  'mono w-full rounded-md border border-border bg-bg px-2 py-1.5 text-[13px] text-primary transition-colors focus:border-accent focus:outline-none'

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

function Field({
  id,
  label,
  hint,
  basis,
  aside,
  children,
}: {
  id: string
  label: string
  hint: string
  basis: string
  aside: string
  children: ReactNode
}) {
  return (
    <div className={`flex min-w-0 grow flex-col gap-1 ${basis}`}>
      <div className="flex items-baseline justify-between gap-2">
        <label
          htmlFor={id}
          className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted"
        >
          {label}
        </label>
        {aside !== '' && <span className="mono text-[11px] text-primary">{aside}</span>}
      </div>
      {children}
      <p className="text-[11px] leading-snug text-muted">{hint}</p>
    </div>
  )
}

function NumberField({
  id,
  label,
  hint,
  basis,
  aside,
  value,
  min,
  max,
  step,
  onCommit,
}: {
  id: string
  label: string
  hint: string
  basis: string
  aside: string
  value: number
  min: number
  max: number
  step: number
  onCommit: (value: number) => void
}) {
  return (
    <Field id={id} label={label} hint={hint} basis={basis} aside={aside}>
      <input
        id={id}
        type="number"
        className={INPUT_CLASS}
        value={value}
        min={min}
        max={max}
        step={step}
        /* Typed digits flow straight up; the clamp waits for blur so that
           partially typed numbers are not rewritten mid-keystroke. */
        onChange={(event) => {
          const parsed = Number.parseFloat(event.target.value)
          if (Number.isFinite(parsed) && parsed !== value) onCommit(parsed)
        }}
        onBlur={(event) => {
          const parsed = Number.parseFloat(event.target.value)
          const next = clamp(Number.isFinite(parsed) ? parsed : value, min, max)
          if (next !== value) onCommit(next)
        }}
      />
    </Field>
  )
}

export function ConfigPanel({ config, universe, onChange }: ConfigPanelProps) {
  const uid = useId()
  const id = (name: string) => `${uid}-${name}`

  const perLegBps = config.commission_bps + config.slippage_bps
  const roundTripBps = perLegBps * 2
  const trainPct = config.train_fraction * 100

  return (
    <section className="panel overflow-hidden" aria-label="Run configuration">
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-border px-4 py-2.5">
        <h2 className="text-xs font-semibold uppercase tracking-[0.08em] text-primary">
          Run configuration
        </h2>
        <span className="mono text-[11px] text-muted">
          {fmtNum(universe.symbols.length, 0)} symbols · {fmtDate(universe.start)} to{' '}
          {fmtDate(universe.end)}
        </span>
      </header>

      <div className="flex flex-wrap gap-x-4 gap-y-3 px-4 py-3">
        <NumberField
          id={id('capital')}
          label="Initial capital"
          hint="Starting cash. Positions are sized from equity; no leverage, no pyramiding."
          basis="basis-[11rem]"
          aside={fmtMoney(config.initial_capital)}
          value={config.initial_capital}
          min={CAPITAL.min}
          max={CAPITAL.max}
          step={CAPITAL.step}
          onCommit={(initial_capital) => onChange({ initial_capital })}
        />

        <NumberField
          id={id('commission')}
          label="Commission (bps)"
          hint="1 bp = 0.01% of notional, charged on both legs."
          basis="basis-[10rem]"
          aside=""
          value={config.commission_bps}
          min={BPS.min}
          max={BPS.max}
          step={BPS.step}
          onCommit={(commission_bps) => onChange({ commission_bps })}
        />

        <NumberField
          id={id('slippage')}
          label="Slippage (bps)"
          hint="Applied to the fill price, against you."
          basis="basis-[10rem]"
          aside=""
          value={config.slippage_bps}
          min={BPS.min}
          max={BPS.max}
          step={BPS.step}
          onCommit={(slippage_bps) => onChange({ slippage_bps })}
        />

        <Field
          id={id('start')}
          label="Start"
          hint="Blank uses the first bar in the snapshot."
          basis="basis-[10rem]"
          aside=""
        >
          <input
            id={id('start')}
            type="date"
            className={INPUT_CLASS}
            value={config.start ?? ''}
            min={universe.start}
            max={universe.end}
            onChange={(event) => {
              const raw = event.target.value
              onChange({ start: raw === '' ? null : raw })
            }}
          />
        </Field>

        <Field
          id={id('end')}
          label="End"
          hint="Blank uses the last bar in the snapshot."
          basis="basis-[10rem]"
          aside=""
        >
          <input
            id={id('end')}
            type="date"
            className={INPUT_CLASS}
            value={config.end ?? ''}
            min={universe.start}
            max={universe.end}
            onChange={(event) => {
              const raw = event.target.value
              onChange({ end: raw === '' ? null : raw })
            }}
          />
        </Field>

        <Field
          id={id('train')}
          label="Train fraction"
          hint="In-sample share. The remainder is held out and never used to pick anything."
          basis="basis-[13rem]"
          aside={`${fmtPct(trainPct, 0)} / ${fmtPct(100 - trainPct, 0)}`}
        >
          <input
            id={id('train')}
            type="range"
            className="w-full"
            style={{ accentColor: 'var(--color-accent)' }}
            value={config.train_fraction}
            min={TRAIN.min}
            max={TRAIN.max}
            step={TRAIN.step}
            onChange={(event) => {
              const parsed = Number.parseFloat(event.target.value)
              if (Number.isFinite(parsed)) {
                onChange({ train_fraction: clamp(parsed, TRAIN.min, TRAIN.max) })
              }
            }}
          />
        </Field>

        <NumberField
          id={id('folds')}
          label="Walk-forward folds"
          hint="Sequential, non-overlapping out-of-sample windows, each tested on its own."
          basis="basis-[10rem]"
          aside=""
          value={config.walk_forward_folds}
          min={FOLDS.min}
          max={FOLDS.max}
          step={FOLDS.step}
          onCommit={(folds) =>
            onChange({ walk_forward_folds: Math.round(clamp(folds, FOLDS.min, FOLDS.max)) })
          }
        />

        <div className="flex min-w-0 grow basis-[15rem] flex-col gap-1">
          <label
            htmlFor={id('robustness')}
            className="flex cursor-pointer items-center gap-2 text-[13px] text-primary"
          >
            <input
              id={id('robustness')}
              type="checkbox"
              className="shrink-0"
              style={{ accentColor: 'var(--color-accent)' }}
              checked={config.run_robustness}
              onChange={(event) => onChange({ run_robustness: event.target.checked })}
            />
            Run robustness probes (adds ~2s)
          </label>
          <p className="text-[11px] leading-snug text-muted">
            Re-runs at 0x to 5x costs and perturbs every numeric parameter by ±10% and ±20%.
          </p>
        </div>
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Derived consequences of the settings above                          */}
      {/* ------------------------------------------------------------------ */}
      <div className="flex flex-wrap gap-x-5 gap-y-1 border-t border-border px-4 py-2.5 text-[11px] text-muted">
        <span>
          Round trip cost{' '}
          <span className="mono text-primary">
            {fmtNum(roundTripBps, 1)} bps ({fmtPct(roundTripBps / 100, 2)})
          </span>{' '}
          of notional — {fmtNum(perLegBps, 1)} bps in, the same out.
        </span>
        <span>
          Split{' '}
          <span className="mono text-primary">
            {fmtPct(trainPct, 0)} in-sample / {fmtPct(100 - trainPct, 0)} out-of-sample
          </span>
          , then <span className="mono text-primary">{fmtNum(config.walk_forward_folds, 0)}</span>{' '}
          walk-forward folds.
        </span>
        <span>
          Benchmark <span className="mono text-primary">{config.benchmark}</span> · seed{' '}
          <span className="mono text-primary">{fmtNum(config.seed, 0)}</span> (fixed, so a repeat
          run returns the same result hash).
        </span>
      </div>
    </section>
  )
}
