import { useId, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import type { ExampleStrategy } from '../types'
import { fmtNum } from '../format'

/**
 * Step 1 of the flow: Describe.
 *
 * A single free-text box plus the seeded examples. The examples are not
 * decoration -- their `note` fields say out loud which ones are expected to
 * score badly, which is the demo's whole argument.
 */

const PLACEHOLDER =
  'Buy SPY and QQQ when RSI(14) drops below 30 and price is above the 200-day moving ' +
  'average. Sell when RSI goes above 55 or after 20 trading days. Use an 8% stop loss.'

/** Matches the server-side bound on ParseRequest.text. */
const MAX_CHARS = 4000

export interface StrategyInputProps {
  examples: ExampleStrategy[]
  onSubmit: (text: string) => void
  busy: boolean
}

export function StrategyInput({ examples, onSubmit, busy }: StrategyInputProps) {
  const [text, setText] = useState('')
  const areaRef = useRef<HTMLTextAreaElement>(null)
  const areaId = useId()
  const helpId = useId()

  const trimmed = text.trim()
  const canSubmit = !busy && trimmed.length > 0
  const nearLimit = text.length > MAX_CHARS - 200

  function submit() {
    if (!canSubmit) return
    onSubmit(trimmed)
  }

  /** Cmd+Enter on macOS, Ctrl+Enter elsewhere. */
  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault()
      submit()
    }
  }

  /** Fill the box and put the caret at the end. Deliberately does not submit. */
  function loadExample(example: ExampleStrategy) {
    setText(example.text)
    const area = areaRef.current
    if (area === null) return
    area.focus()
    area.setSelectionRange(example.text.length, example.text.length)
  }

  return (
    <section className="panel overflow-hidden" aria-label="Describe a strategy">
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-border px-4 py-2.5">
        <h2 className="text-xs font-semibold uppercase tracking-[0.08em] text-primary">
          Describe a strategy
        </h2>
        <span className="mono text-[11px] text-muted">
          Step 1 of 3 · plain English · daily bars
        </span>
      </header>

      <div className="px-4 py-3">
        <textarea
          id={areaId}
          ref={areaRef}
          rows={6}
          value={text}
          maxLength={MAX_CHARS}
          spellCheck={false}
          aria-busy={busy}
          aria-describedby={helpId}
          placeholder={PLACEHOLDER}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={handleKeyDown}
          className="w-full resize-y rounded-md border border-border bg-bg px-3 py-2.5 text-[13px] leading-relaxed text-primary placeholder:text-muted/60 focus:border-accent focus:outline-none"
        />

        <div className="mt-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <p id={helpId} className="text-[11px] leading-snug text-muted">
            Your text is converted into a typed strategy definition. No code is generated or
            executed.
          </p>

          <div className="flex items-center gap-3">
            <span
              className={`mono text-[11px] ${nearLimit ? 'text-warning' : 'text-muted'}`}
              aria-live="polite"
            >
              {fmtNum(text.length, 0)} / {fmtNum(MAX_CHARS, 0)}
            </span>
            <span className="hidden items-center gap-1 text-[11px] text-muted sm:flex">
              <kbd className="mono rounded border border-border bg-panel-hi px-1 py-px text-[10px] text-muted">
                Cmd
              </kbd>
              <span aria-hidden="true">+</span>
              <kbd className="mono rounded border border-border bg-panel-hi px-1 py-px text-[10px] text-muted">
                Enter
              </kbd>
            </span>
            <button
              type="button"
              onClick={submit}
              disabled={!canSubmit}
              className="rounded-md bg-accent px-4 py-2 text-[13px] font-semibold text-bg transition-colors hover:bg-accent/90 disabled:cursor-not-allowed disabled:bg-accent/30 disabled:text-bg/60"
            >
              {busy ? 'Parsing…' : 'Parse strategy'}
            </button>
          </div>
        </div>
      </div>

      {examples.length > 0 && (
        <div className="border-t border-border px-4 py-3">
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <h3 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
              Seeded examples
            </h3>
            <span className="text-[11px] text-muted">
              Deliberately mixed. Some of these are expected to score badly.
            </span>
          </div>

          <div className="mt-2 grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
            {examples.map((example) => {
              const loaded = trimmed === example.text.trim()
              return (
                <button
                  key={example.id}
                  type="button"
                  onClick={() => loadExample(example)}
                  disabled={busy}
                  title={example.text}
                  className={`flex min-w-0 flex-col gap-1 rounded-md border px-3 py-2 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                    loaded
                      ? 'border-accent/60 bg-accent/5'
                      : 'border-border bg-panel hover:border-accent/40 hover:bg-panel-hi'
                  }`}
                >
                  <span className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-[13px] font-semibold text-primary">
                      {example.name}
                    </span>
                    <span
                      className={`mono shrink-0 text-[10px] uppercase tracking-[0.08em] ${
                        loaded ? 'text-accent' : 'text-muted'
                      }`}
                    >
                      {loaded ? 'loaded' : 'load'}
                    </span>
                  </span>
                  <span className="line-clamp-2 text-[12px] leading-snug text-muted">
                    {example.text}
                  </span>
                  <span className="border-t border-border/70 pt-1 text-[11px] leading-snug text-muted/85">
                    {example.note}
                  </span>
                </button>
              )
            })}
          </div>
        </div>
      )}
    </section>
  )
}
