import type { Strategy } from '../types'

/**
 * Step 3 of the flow: Confirm.
 *
 * The parsed strategy, in the plain-language form the server rendered it in,
 * with the typed definition itself one click away. Nothing here is executable
 * and nothing is paraphrased on the client -- the sentences below come straight
 * from `strategy_render` in the API response.
 */

/**
 * The `strategy_render` block of the parse and backtest responses.
 *
 * Declared structurally here, in the same spirit as the report panels, so the
 * card compiles against the payload exactly as it arrives.
 */
export interface StrategyRender {
  direction: string
  universe: string[]
  entry_logic: string
  entry: string[]
  exit_logic: string
  exit: string[]
  sizing: string
}

export interface StrategyCardProps {
  strategy: Strategy
  render: StrategyRender
}

/** Reads a possibly-absent string field without ever rendering "null". */
function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function RuleList({ rules, emptyLabel }: { rules: string[]; emptyLabel: string }) {
  if (rules.length === 0) {
    return <p className="mt-1.5 text-[12px] text-muted">{emptyLabel}</p>
  }
  return (
    <ol className="mt-1.5 flex flex-col gap-1">
      {rules.map((rule, index) => (
        <li key={`${index}-${rule}`} className="flex items-start gap-2">
          <span className="mono mt-px shrink-0 rounded border border-border bg-panel-hi px-1.5 py-px text-[10px] text-muted">
            {index + 1}
          </span>
          <span className="text-[13px] leading-snug text-primary">{rule}</span>
        </li>
      ))}
    </ol>
  )
}

export function StrategyCard({ strategy, render }: StrategyCardProps) {
  const direction = text(render.direction) || 'long'
  const isShort = direction.toLowerCase() === 'short'
  const name = text(strategy.name)
  const sourceText = text(strategy.source_text)
  const json = JSON.stringify(strategy, null, 2)

  return (
    <section className="panel overflow-hidden" aria-label="Parsed strategy">
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-border px-4 py-2.5">
        <h2 className="text-xs font-semibold uppercase tracking-[0.08em] text-primary">
          {name === '' ? 'Parsed strategy' : name}
        </h2>
        <span className="mono text-[11px] text-muted">
          Step 3 of 3 · {render.universe.length} symbols
        </span>
      </header>

      {/* ------------------------------------------------------------------ */}
      {/* Direction and sizing                                                */}
      {/* ------------------------------------------------------------------ */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-border px-4 py-3">
        <span
          className={`mono rounded border px-1.5 py-px text-[11px] font-semibold uppercase tracking-[0.08em] ${
            isShort
              ? 'border-negative/50 bg-negative/10 text-negative'
              : 'border-positive/50 bg-positive/10 text-positive'
          }`}
        >
          {direction}
        </span>
        <span className="text-[13px] leading-snug text-primary">{render.sizing}</span>
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Universe                                                            */}
      {/* ------------------------------------------------------------------ */}
      <div className="border-b border-border px-4 py-3">
        <h3 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
          Universe
        </h3>
        {render.universe.length === 0 ? (
          <p className="mt-1.5 text-[12px] text-muted">No symbols selected.</p>
        ) : (
          <ul className="mt-1.5 flex flex-wrap gap-1">
            {render.universe.map((symbol) => (
              <li
                key={symbol}
                className="mono rounded border border-border bg-panel-hi px-1.5 py-px text-[11px] text-primary"
              >
                {symbol}
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Entry and exit rules                                                */}
      {/* ------------------------------------------------------------------ */}
      <div className="grid grid-cols-1 divide-y divide-border border-b border-border md:grid-cols-2 md:divide-x md:divide-y-0">
        <div className="min-w-0 px-4 py-3">
          <h3 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
            Entry - {render.entry_logic}:
          </h3>
          <RuleList rules={render.entry} emptyLabel="No entry conditions." />
        </div>
        <div className="min-w-0 px-4 py-3">
          <h3 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
            Exit - {render.exit_logic}:
          </h3>
          <RuleList rules={render.exit} emptyLabel="No exit conditions." />
        </div>
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* What the user actually typed, for side-by-side comparison           */}
      {/* ------------------------------------------------------------------ */}
      {sourceText !== '' && (
        <div className="border-b border-border px-4 py-3">
          <h3 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
            Your description
          </h3>
          <p className="mt-1.5 border-l-2 border-border pl-2.5 text-[12px] leading-snug text-muted">
            {sourceText}
          </p>
        </div>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* The typed definition itself                                         */}
      {/* ------------------------------------------------------------------ */}
      <details className="group">
        <summary className="flex cursor-pointer select-none list-none items-center gap-2 px-4 py-2.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted transition-colors hover:text-primary [&::-webkit-details-marker]:hidden">
          <span
            className="mono inline-block text-accent transition-transform group-open:rotate-90"
            aria-hidden="true"
          >
            &#9656;
          </span>
          Typed strategy definition (JSON)
        </summary>
        <div className="max-h-80 overflow-auto border-t border-border bg-bg px-4 py-3">
          <pre className="mono text-[11px] leading-relaxed text-primary/90">{json}</pre>
        </div>
      </details>

      <p className="border-t border-border px-4 py-2.5 text-[11px] leading-snug text-muted">
        This is the complete strategy. The backtester reads only these fields.
      </p>
    </section>
  )
}
