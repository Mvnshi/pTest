import { useMemo } from 'react'
import type { CSSProperties } from 'react'
import type { Concentration, SymbolStat } from '../types'
import { fmtMoney, fmtNum, fmtPct, signClass } from '../format'

export interface SymbolHeatmapProps {
  symbols: SymbolStat[]
  concentration: Concentration
}

/**
 * Raw channel values for the two semantic colours in index.css. They are
 * duplicated here as numbers because tile fills are interpolated per symbol and
 * have to be emitted as inline `rgba(...)` -- a Tailwind class name assembled at
 * runtime (`bg-positive/${n}`) is invisible to the compiler and gets purged.
 */
const POSITIVE_RGB = '46, 189, 133' /* --color-positive #2ebd85 */
const NEGATIVE_RGB = '246, 70, 93' /* --color-negative #f6465d */

/** Alpha at |net_pnl| == max. Kept below 1 so --color-primary text stays legible. */
const MAX_ALPHA = 0.56
/** Alpha floor for a symbol that traded and landed near, but not on, zero. */
const MIN_ALPHA = 0.07

/**
 * Diverging red-to-green fill, scaled against the largest absolute net P&L in
 * the set so the two extremes saturate. The square root keeps the long tail of
 * small contributors visible instead of collapsing them all into the panel
 * background -- the point of the grid is to show *breadth*, so a symbol that
 * made a little must not look identical to one that made nothing.
 */
function heatStyle(netPnl: number, maxAbs: number): CSSProperties {
  if (maxAbs <= 0 || netPnl === 0) {
    return { backgroundColor: 'var(--color-panel-hi)', borderColor: 'var(--color-border)' }
  }
  const magnitude = Math.min(1, Math.abs(netPnl) / maxAbs)
  const alpha = MIN_ALPHA + (MAX_ALPHA - MIN_ALPHA) * Math.sqrt(magnitude)
  const rgb = netPnl > 0 ? POSITIVE_RGB : NEGATIVE_RGB
  return {
    backgroundColor: `rgba(${rgb}, ${alpha.toFixed(3)})`,
    borderColor: `rgba(${rgb}, ${Math.min(0.85, alpha + 0.2).toFixed(3)})`,
  }
}

/** Symbols the strategy never touched: hatched, so "flat" never reads as "neutral". */
const NO_TRADE_STYLE: CSSProperties = {
  backgroundColor: 'var(--color-panel)',
  borderColor: 'var(--color-border)',
  backgroundImage:
    'repeating-linear-gradient(135deg, transparent 0 5px, rgba(125, 138, 156, 0.13) 5px 6px)',
}

/** Steps rendered in the scale legend, as a fraction of the largest |net_pnl|. */
const LEGEND_STEPS = [-1, -0.6, -0.3, -0.1, 0, 0.1, 0.3, 0.6, 1] as const

interface Column {
  key: keyof SymbolStat
  label: string
  definition: string
  align: 'left' | 'right'
}

const COLUMNS: readonly Column[] = [
  {
    key: 'symbol',
    label: 'Symbol',
    definition: 'Ticker from the strategy universe.',
    align: 'left',
  },
  {
    key: 'trades',
    label: 'Trades',
    definition: 'Completed round trips on this symbol - an entry paired with its exit.',
    align: 'right',
  },
  {
    key: 'net_pnl',
    label: 'Net P&L',
    definition: 'Dollar profit and loss on this symbol after commission and slippage.',
    align: 'right',
  },
  {
    key: 'pnl_share_pct',
    label: 'P&L share',
    definition:
      "This symbol's net P&L as a share of the total absolute P&L moved by every trade in the run. Signed, so a losing symbol shows a negative share.",
    align: 'right',
  },
  {
    key: 'win_rate_pct',
    label: 'Win rate',
    definition:
      'Share of this symbol’s closed trades with a positive net P&L. It says nothing about the size of those wins.',
    align: 'right',
  },
  {
    key: 'avg_return_pct',
    label: 'Avg return',
    definition: 'Mean net return per trade on this symbol, measured on the entry notional.',
    align: 'right',
  },
  {
    key: 'best_trade_pct',
    label: 'Best trade',
    definition: 'Return of the single best trade on this symbol.',
    align: 'right',
  },
  {
    key: 'worst_trade_pct',
    label: 'Worst trade',
    definition: 'Return of the single worst trade on this symbol.',
    align: 'right',
  },
]

const DASH = '—'

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

function Tile({ stat, maxAbs }: { stat: SymbolStat; maxAbs: number }) {
  const traded = stat.trades > 0
  const detail = traded
    ? `${stat.symbol}: ${fmtMoney(stat.net_pnl)} net over ${fmtNum(stat.trades, 0)} ${
        stat.trades === 1 ? 'trade' : 'trades'
      }, ${fmtPct(stat.win_rate_pct, 1)} win rate, ${fmtPct(stat.avg_return_pct, 2)} average return.`
    : `${stat.symbol}: the strategy never opened a position on this symbol.`

  return (
    <div
      className="flex min-w-0 flex-col gap-0.5 rounded-md border px-2 py-1.5"
      style={traded ? heatStyle(stat.net_pnl, maxAbs) : NO_TRADE_STYLE}
      title={detail}
    >
      <span
        className={`mono truncate text-[13px] font-semibold leading-tight ${
          traded ? 'text-primary' : 'text-muted'
        }`}
      >
        {stat.symbol}
      </span>
      {traded ? (
        <>
          <span className="mono truncate text-[12px] leading-tight text-primary">
            {fmtMoney(stat.net_pnl)}
          </span>
          <span className="mono truncate text-[10px] leading-tight text-primary/70">
            {fmtNum(stat.trades, 0)} {stat.trades === 1 ? 'trade' : 'trades'}
          </span>
        </>
      ) : (
        <>
          <span className="truncate text-[12px] leading-tight text-muted">no trades</span>
          <span className="mono truncate text-[10px] leading-tight text-muted/70">{DASH}</span>
        </>
      )}
      <span className="sr-only">{detail}</span>
    </div>
  )
}

export function SymbolHeatmap({ symbols, concentration }: SymbolHeatmapProps) {
  const maxAbs = useMemo(
    () => symbols.reduce((acc, s) => Math.max(acc, Math.abs(s.net_pnl)), 0),
    [symbols],
  )

  const sorted = useMemo(
    () => [...symbols].sort((a, b) => b.net_pnl - a.net_pnl || a.symbol.localeCompare(b.symbol)),
    [symbols],
  )

  const totals = useMemo(
    () =>
      symbols.reduce(
        (acc, s) => ({ trades: acc.trades + s.trades, netPnl: acc.netPnl + s.net_pnl }),
        { trades: 0, netPnl: 0 },
      ),
    [symbols],
  )

  const untraded = useMemo(() => symbols.filter((s) => s.trades === 0).length, [symbols])

  const share = concentration.top_symbol_pnl_share_pct
  const concentrated = share > 50
  const hasTopSymbol = concentration.top_symbol !== ''

  if (symbols.length === 0) {
    return (
      <section className="panel overflow-hidden" aria-label="Per-symbol results">
        <header className="border-b border-border px-4 py-2.5">
          <h3 className="text-xs font-semibold uppercase tracking-[0.08em] text-primary">
            Per-symbol results
          </h3>
        </header>
        <p className="px-4 py-3 text-sm text-muted">
          This run produced no per-symbol breakdown.
        </p>
      </section>
    )
  }

  return (
    <section className="panel overflow-hidden" aria-label="Per-symbol results">
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-border px-4 py-2.5">
        <h3 className="text-xs font-semibold uppercase tracking-[0.08em] text-primary">
          Per-symbol results
        </h3>
        <span className="mono text-[11px] text-muted">
          {fmtNum(symbols.length, 0)} symbols · {fmtNum(totals.trades, 0)} trades
          {untraded > 0 ? ` · ${fmtNum(untraded, 0)} never traded` : ''}
        </span>
      </header>

      {/* ------------------------------------------------------------------ */}
      {/* Heat grid                                                           */}
      {/* ------------------------------------------------------------------ */}
      <div className="grid grid-cols-3 gap-1.5 px-4 py-3 sm:grid-cols-4 md:grid-cols-6 xl:grid-cols-9">
        {symbols.map((stat) => (
          <Tile key={stat.symbol} stat={stat} maxAbs={maxAbs} />
        ))}
      </div>

      {/* Colour scale, anchored on the largest absolute net P&L in the set. */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 pb-3 text-[11px] text-muted">
        {maxAbs > 0 ? (
          <>
            <span className="mono text-negative">{fmtMoney(-maxAbs)}</span>
            <span className="flex overflow-hidden rounded-[2px] border border-border">
              {LEGEND_STEPS.map((step) => (
                <span
                  key={step}
                  className="block h-2.5 w-5"
                  style={heatStyle(step * maxAbs, maxAbs)}
                  aria-hidden="true"
                />
              ))}
            </span>
            <span className="mono text-positive">{fmtMoney(maxAbs)}</span>
            <span>Fill saturates at the largest absolute net P&amp;L in the universe.</span>
          </>
        ) : (
          <span>No symbol moved the account, so every tile is flat.</span>
        )}
        <span className="flex items-center gap-1.5">
          <span
            className="block size-2.5 rounded-[2px] border"
            style={NO_TRADE_STYLE}
            aria-hidden="true"
          />
          <span>hatched = no trades</span>
        </span>
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Concentration callout                                               */}
      {/* ------------------------------------------------------------------ */}
      <div className="px-4 pb-3">
        <div
          className={`rounded-md border px-3 py-2.5 ${
            concentrated ? 'border-warning/40 bg-warning/10' : 'border-border bg-panel-hi'
          }`}
        >
          <div className="flex items-baseline justify-between gap-3">
            <h4
              className={`text-[11px] font-semibold uppercase tracking-[0.08em] ${
                concentrated ? 'text-warning' : 'text-muted'
              }`}
            >
              Concentration
            </h4>
            {concentrated ? (
              <span className="mono text-[10px] uppercase tracking-[0.08em] text-warning">
                warning
              </span>
            ) : null}
          </div>
          <p className="mt-1.5 text-sm text-primary">
            {hasTopSymbol ? (
              <>
                <span className="mono">{concentration.top_symbol}</span> produced{' '}
                <span className={`mono ${concentrated ? 'text-warning' : 'text-primary'}`}>
                  {fmtPct(share, 1)}
                </span>{' '}
                of gross profit.{' '}
              </>
            ) : (
              <>No symbol produced a profit in this run. </>
            )}
            <span className="mono">{fmtNum(concentration.profitable_symbols, 0)}</span> of{' '}
            <span className="mono">{fmtNum(concentration.total_symbols, 0)}</span> symbols were
            profitable. The best 5 trades produced{' '}
            <span className="mono">{fmtPct(concentration.top5_trades_pnl_share_pct, 1)}</span> of
            gross profit. The best 10 days produced{' '}
            <span className="mono">{fmtPct(concentration.top10_days_return_share_pct, 1)}</span> of
            everything the up days returned.{' '}
            <span className="mono">{fmtNum(concentration.positive_years, 0)}</span> of{' '}
            <span className="mono">{fmtNum(concentration.total_years, 0)}</span> calendar years were
            positive.
          </p>
          <p className="mt-1.5 text-xs text-muted">
            {concentrated && hasTopSymbol
              ? `Over half the gross profit rests on ${concentration.top_symbol} alone. Drop that one ticker and most of the edge goes with it, so read this run as a claim about ${concentration.top_symbol} rather than about the rule.`
              : 'No single symbol carries the result on its own, but symbol-level P&L is still the first place a rule fitted to one ticker shows itself.'}
          </p>
        </div>
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Sorted table                                                        */}
      {/* ------------------------------------------------------------------ */}
      <div className="max-h-[30rem] overflow-auto border-t border-border">
        <table className="w-full text-sm">
          <caption className="sr-only">
            Per-symbol trade statistics, sorted by net profit and loss, highest first.
          </caption>
          <thead>
            <tr>
              {COLUMNS.map((col) => (
                <th
                  key={col.key}
                  scope="col"
                  className={`sticky top-0 z-10 whitespace-nowrap bg-panel px-3 py-2 text-[11px] font-medium uppercase tracking-[0.06em] text-muted shadow-[inset_0_-1px_0_var(--color-border)] ${
                    col.align === 'left' ? 'text-left' : 'text-right'
                  }`}
                >
                  <Term label={col.label} definition={col.definition} />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((s) => {
              const traded = s.trades > 0
              return (
                <tr
                  key={s.symbol}
                  className="border-b border-border/60 last:border-b-0 hover:bg-panel-hi"
                >
                  <th
                    scope="row"
                    className={`whitespace-nowrap px-3 py-1.5 text-left font-normal ${
                      traded ? 'text-primary' : 'text-muted'
                    }`}
                  >
                    <span className="mono">{s.symbol}</span>
                  </th>
                  <td className="whitespace-nowrap px-3 py-1.5 text-right">
                    <span className={`mono ${traded ? 'text-primary' : 'text-muted'}`}>
                      {fmtNum(s.trades, 0)}
                    </span>
                  </td>
                  {traded ? (
                    <>
                      <td className="whitespace-nowrap px-3 py-1.5 text-right">
                        <span className={`mono ${signClass(s.net_pnl)}`}>
                          {fmtMoney(s.net_pnl)}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-3 py-1.5 text-right">
                        <span className={`mono ${signClass(s.pnl_share_pct)}`}>
                          {fmtPct(s.pnl_share_pct, 2)}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-3 py-1.5 text-right">
                        <span className="mono text-primary">{fmtPct(s.win_rate_pct, 1)}</span>
                      </td>
                      <td className="whitespace-nowrap px-3 py-1.5 text-right">
                        <span className={`mono ${signClass(s.avg_return_pct)}`}>
                          {fmtPct(s.avg_return_pct, 2)}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-3 py-1.5 text-right">
                        <span className={`mono ${signClass(s.best_trade_pct)}`}>
                          {fmtPct(s.best_trade_pct, 2)}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-3 py-1.5 text-right">
                        <span className={`mono ${signClass(s.worst_trade_pct)}`}>
                          {fmtPct(s.worst_trade_pct, 2)}
                        </span>
                      </td>
                    </>
                  ) : (
                    <td
                      colSpan={COLUMNS.length - 2}
                      className="whitespace-nowrap px-3 py-1.5 text-right text-muted"
                    >
                      no trades
                    </td>
                  )}
                </tr>
              )
            })}
          </tbody>
          <tfoot>
            <tr>
              <th
                scope="row"
                className="sticky bottom-0 whitespace-nowrap bg-panel px-3 py-2 text-left text-[11px] font-medium uppercase tracking-[0.06em] text-muted shadow-[inset_0_1px_0_var(--color-border)]"
              >
                Total
              </th>
              <td className="sticky bottom-0 whitespace-nowrap bg-panel px-3 py-2 text-right shadow-[inset_0_1px_0_var(--color-border)]">
                <span className="mono text-primary">{fmtNum(totals.trades, 0)}</span>
              </td>
              <td className="sticky bottom-0 whitespace-nowrap bg-panel px-3 py-2 text-right shadow-[inset_0_1px_0_var(--color-border)]">
                <span className={`mono ${signClass(totals.netPnl)}`}>
                  {fmtMoney(totals.netPnl)}
                </span>
              </td>
              <td
                colSpan={COLUMNS.length - 3}
                className="sticky bottom-0 whitespace-nowrap bg-panel px-3 py-2 text-right text-[11px] text-muted shadow-[inset_0_1px_0_var(--color-border)]"
              >
                {fmtNum(concentration.profitable_symbols, 0)} of{' '}
                {fmtNum(concentration.total_symbols, 0)} profitable
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  )
}
