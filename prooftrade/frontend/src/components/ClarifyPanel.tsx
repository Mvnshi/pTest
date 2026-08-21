import { useId } from 'react'
import type { ClarifyQuestion, ParseResponse } from '../types'
import { fmtNum, fmtPct } from '../format'

/**
 * Step 2 of the flow: Clarify.
 *
 * Every ambiguity the translator hit is surfaced with a pre-selected default, so
 * the flow never blocks but the user always sees what was decided for them. The
 * two sections that matter most for honesty -- the assumptions and the fragments
 * the parser could not read -- are always on screen, never behind a disclosure.
 */

/** The option shape is derived from the question so this file owns no duplicate type. */
type ClarifyOption = ClarifyQuestion['options'][number]

export interface ClarifyPanelProps {
  parse: ParseResponse
  answers: Record<string, string>
  onAnswer: (id: string, value: string) => void
  onConfirm: () => void
  onBack: () => void
  busy: boolean
}

interface ConfidenceBand {
  label: string
  color: string
}

/**
 * Bands for the parse-confidence bar. This is the parser's own estimate of how
 * much of the sentence it understood -- deliberately not dressed up as anything
 * predictive.
 */
function band(confidence: number): ConfidenceBand {
  if (confidence >= 0.85) return { label: 'high', color: 'var(--color-positive)' }
  if (confidence >= 0.6) return { label: 'moderate', color: 'var(--color-accent)' }
  return { label: 'low', color: 'var(--color-warning)' }
}

function Question({
  question,
  index,
  selected,
  disabled,
  groupName,
  onAnswer,
}: {
  question: ClarifyQuestion
  index: number
  selected: string
  disabled: boolean
  groupName: string
  onAnswer: (id: string, value: string) => void
}) {
  return (
    <li className="border-t border-border px-4 py-3 first:border-t-0">
      <div className="flex items-start gap-2">
        <span className="mono mt-0.5 shrink-0 rounded border border-border bg-panel-hi px-1.5 py-px text-[10px] text-muted">
          {index + 1}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-semibold leading-snug text-primary">
            {question.question}
          </p>
          {question.why !== '' && (
            <p className="mt-0.5 text-[11px] leading-snug text-muted">{question.why}</p>
          )}

          <div
            role="radiogroup"
            aria-label={question.question}
            className="mt-2 grid grid-cols-1 gap-1.5 sm:grid-cols-2"
          >
            {question.options.map((option: ClarifyOption) => {
              const active = option.value === selected
              const isDefault = option.value === question.default_value
              return (
                <label
                  key={option.value}
                  className={`flex cursor-pointer items-start gap-2 rounded-md border px-2.5 py-2 transition-colors ${
                    active
                      ? 'border-accent/60 bg-accent/5'
                      : 'border-border bg-panel hover:border-accent/40 hover:bg-panel-hi'
                  } ${disabled ? 'cursor-not-allowed opacity-50' : ''}`}
                >
                  <input
                    type="radio"
                    name={groupName}
                    value={option.value}
                    checked={active}
                    disabled={disabled}
                    onChange={() => onAnswer(question.id, option.value)}
                    className="mt-0.5 shrink-0"
                    style={{ accentColor: 'var(--color-accent)' }}
                  />
                  <span className="min-w-0">
                    <span className="flex flex-wrap items-baseline gap-x-2">
                      <span
                        className={`text-[13px] leading-snug ${
                          active ? 'text-primary' : 'text-primary/85'
                        }`}
                      >
                        {option.label}
                      </span>
                      {isDefault && (
                        <span className="mono shrink-0 text-[10px] uppercase tracking-[0.08em] text-muted">
                          default
                        </span>
                      )}
                    </span>
                    {option.description !== '' && (
                      <span className="mt-0.5 block text-[11px] leading-snug text-muted">
                        {option.description}
                      </span>
                    )}
                  </span>
                </label>
              )
            })}
          </div>
        </div>
      </div>
    </li>
  )
}

export function ClarifyPanel({
  parse,
  answers,
  onAnswer,
  onConfirm,
  onBack,
  busy,
}: ClarifyPanelProps) {
  const groupPrefix = useId()

  const confidence = Math.max(0, Math.min(1, parse.confidence))
  const { label: confidenceLabel, color: confidenceColor } = band(confidence)

  const changed = parse.questions.reduce((count, question) => {
    const stored: string | undefined = answers[question.id]
    return stored !== undefined && stored !== question.default_value ? count + 1 : count
  }, 0)

  return (
    <section className="panel overflow-hidden" aria-label="Clarify the strategy">
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-border px-4 py-2.5">
        <h2 className="text-xs font-semibold uppercase tracking-[0.08em] text-primary">
          Clarify
        </h2>
        <span className="mono text-[11px] text-muted">
          Step 2 of 3 · {fmtNum(parse.questions.length, 0)}{' '}
          {parse.questions.length === 1 ? 'question' : 'questions'}
          {changed > 0 ? ` · ${fmtNum(changed, 0)} changed from default` : ''}
        </span>
      </header>

      {/* ------------------------------------------------------------------ */}
      {/* Parse confidence                                                    */}
      {/* ------------------------------------------------------------------ */}
      <div className="border-b border-border px-4 py-3">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
            Parse confidence
          </span>
          <span className="mono text-[12px] text-primary">
            {fmtPct(confidence * 100, 0)}{' '}
            <span className="text-muted">· {confidenceLabel}</span>
          </span>
        </div>
        <div
          role="meter"
          aria-valuemin={0}
          aria-valuemax={1}
          aria-valuenow={confidence}
          aria-valuetext={`${fmtPct(confidence * 100, 0)} — ${confidenceLabel}`}
          aria-label="Parse confidence"
          className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-panel-hi"
        >
          <div
            className="h-full rounded-full"
            style={{ width: `${(confidence * 100).toFixed(1)}%`, backgroundColor: confidenceColor }}
          />
        </div>
        <p className="mt-1.5 text-[11px] leading-snug text-muted">
          How much of your sentence the translator understood. It says nothing about whether
          the strategy works.
        </p>
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Questions                                                           */}
      {/* ------------------------------------------------------------------ */}
      {parse.questions.length === 0 ? (
        <p className="border-b border-border px-4 py-3 text-[13px] text-primary">
          Nothing ambiguous - ready to run.
        </p>
      ) : (
        <ol className="border-b border-border">
          {parse.questions.map((question, index) => {
            const stored: string | undefined = answers[question.id]
            return (
              <Question
                key={question.id}
                question={question}
                index={index}
                selected={stored ?? question.default_value}
                disabled={busy}
                groupName={`${groupPrefix}-${question.id}`}
                onAnswer={onAnswer}
              />
            )
          })}
        </ol>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* Assumptions                                                         */}
      {/* ------------------------------------------------------------------ */}
      <div className="border-b border-border px-4 py-3">
        <h3 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
          Assumptions I made
        </h3>
        {parse.assumptions.length === 0 ? (
          <p className="mt-1.5 text-[12px] text-muted">
            None. Everything the backtester needs was stated explicitly.
          </p>
        ) : (
          <ul className="mt-1.5 flex flex-col gap-1.5">
            {parse.assumptions.map((assumption) => (
              <li key={`${assumption.field}-${assumption.text}`} className="flex items-start gap-2">
                <span className="mono mt-px shrink-0 rounded border border-border bg-panel-hi px-1.5 py-px text-[10px] text-muted">
                  {assumption.field}
                </span>
                <span className="text-[12px] leading-snug text-primary/90">{assumption.text}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Unparsed fragments -- never hidden, this is the honest part          */}
      {/* ------------------------------------------------------------------ */}
      {parse.unparsed.length > 0 && (
        <div className="border-b border-warning/40 bg-warning/5 px-4 py-3">
          <h3 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-warning">
            I could not interpret these parts
          </h3>
          <ul className="mt-1.5 flex flex-col gap-1">
            {parse.unparsed.map((fragment, index) => (
              <li key={`${index}-${fragment}`} className="flex items-start gap-2">
                <span className="mono mt-px shrink-0 text-[11px] text-warning" aria-hidden="true">
                  ×
                </span>
                <span className="mono text-[12px] leading-snug text-primary/90">{fragment}</span>
              </li>
            ))}
          </ul>
          <p className="mt-1.5 text-[11px] leading-snug text-muted">
            These fragments were dropped. They are not part of the strategy that will be tested.
          </p>
        </div>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* Actions                                                             */}
      {/* ------------------------------------------------------------------ */}
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-3">
        <p className="text-[11px] leading-snug text-muted">
          Defaults are pre-selected, so nothing here blocks the run.
        </p>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onBack}
            disabled={busy}
            className="rounded-md border border-border bg-panel px-3 py-2 text-[13px] text-primary transition-colors hover:bg-panel-hi disabled:cursor-not-allowed disabled:opacity-40"
          >
            Back
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className="rounded-md bg-accent px-4 py-2 text-[13px] font-semibold text-bg transition-colors hover:bg-accent/90 disabled:cursor-not-allowed disabled:bg-accent/30 disabled:text-bg/60"
          >
            {busy ? 'Running…' : 'Run backtest'}
          </button>
        </div>
      </div>
    </section>
  )
}
