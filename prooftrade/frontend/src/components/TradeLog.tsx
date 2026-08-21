import { useMemo, useState } from 'react'
import type { Trade } from '../types'
import { fmtDate, fmtMoney, fmtNum, fmtPct, signClass } from '../format'

export interface TradeLogProps {
  trades: Trade[]
}

const PAGE_SIZE = 50

type SortKey =
  | 'symbol'
  | 'entry_date'
  | 'exit_date'
  | 'holding_days'
  | 'entry_price'
  | 'exit_price'
  | 'shares'
  | 'net_pnl'
  | 'return_pct'
  | 'costs'
  | 'exit_reason'
  | 'mae_pct'
  | 'mfe_pct'

type SortDir = 'asc' | 'desc'

interface Column {
  key: SortKey
  label: string
  definition: string
  numeric: boolean
}

const COLUMNS: readonly Column[] = [
  { key: 'symbol', label: 'Symbol', definition: 'Ticker the position was taken in.', numeric: false },
  {
    key: 'entry_date',
    label: 'Entry',
    definition: 'Date the position was opened, filled at that bar with slippage applied.',
    numeric: false,
  },
  { key: 'exit_date', label: 'Exit', definition: 'Date the position was closed.', numeric: false },
  {
    key: 'holding_days',
    label: 'Days',
    definition: 'Trading days the position stayed open.',
    numeric: true,
  },
  {
    key: 'entry_price',
    label: 'Entry $',
    definition: 'Fill price paid on entry, slippage included.',
    numeric: true,
  },
  {
    key: 'exit_price',
    label: 'Exit $',
    definition: 'Fill price received on exit, slippage included.',
    numeric: true,
  },
  {
    key: 'shares',
    label: 'Shares',
    definition: 'Position size in shares at entry.',
    numeric: true,
  },
  {
    key: 'net_pnl',
    label: 'Net P&L $',
    definition: 'Dollar profit or loss after commission and slippage on both legs.',
    numeric: true,
  },
  {
    key: 'return_pct',
    label: 'Return',
    definition: 'Net profit or loss as a percentage of the entry notional.',
    numeric: true,
  },
  {
    key: 'costs',
    label: 'Costs $',
    definition: 'Commission plus slippage charged on this round trip.',
    numeric: true,
  },
  {
    key: 'exit_reason',
    label: 'Exit reason',
    definition:
      'Which rule closed the position: an exit signal, a stop, a profit target, a time limit, or the end of the data.',
    numeric: false,
  },
  {
    key: 'mae_pct',
    label: 'MAE',
    definition:
      'Maximum adverse excursion - the worst unrealised loss the position reached while it was open.',
    numeric: true,
  },
  {
    key: 'mfe_pct',
    label: 'MFE',
    definition:
      'Maximum favourable excursion - the best unrealised gain the position reached while it was open.',
    numeric: true,
  },
]

function exitPillClass(reason: string): string {
  switch (reason) {
    case 'stop_loss':
    case 'trailing_stop':
      return 'border-negative/40 bg-negative/10 text-negative'
    case 'take_profit':
      return 'border-positive/40 bg-positive/10 text-positive'
    case 'signal':
      return 'border-accent/40 bg-accent/10 text-accent'
    case 'time_exit':
      return 'border-warning/40 bg-warning/10 text-warning'
    default:
      return 'border-border bg-panel-hi text-muted'
  }
}

function humanise(reason: string): string {
  return reason.replace(/_/g, ' ')
}

function shareText(v: number): string {
  return fmtNum(v, Number.isInteger(v) ? 0 : 2)
}

function compare(a: Trade, b: Trade, key: SortKey): number {
  const av = a[key]
  const bv = b[key]
  if (typeof av === 'number' && typeof bv === 'number') return av - bv
  return String(av).localeCompare(String(bv))
}

function Caret({ active, dir }: { active: boolean; dir: SortDir }) {
  return (
    <span
      aria-hidden="true"
      className={`text-[9px] leading-none ${active ? 'text-accent' : 'opacity-0'}`}
    >
      {active && dir === 'asc' ? '▲' : '▼'}
    </span>
  )
}

function Stat({ label, value, tone }: { label: string; value: string; tone: string }) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="text-[11px] uppercase tracking-[0.06em] text-muted">{label}</span>
      <span className={`mono ${tone}`}>{value}</span>
    </div>
  )
}

export function TradeLog({ trades }: TradeLogProps) {
  const [sortKey, setSortKey] = useState<SortKey>('entry_date')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [symbolQuery, setSymbolQuery] = useState('')
  const [exitFilter, setExitFilter] = useState('all')
  const [page, setPage] = useState(0)

  const exitReasons = useMemo(() => {
    const seen = new Set<string>()
    for (const t of trades) seen.add(t.exit_reason)
    return Array.from(seen).sort((a, b) => a.localeCompare(b))
  }, [trades])

  const filtered = useMemo(() => {
    const q = symbolQuery.trim().toUpperCase()
    return trades.filter(
      (t) =>
        (q === '' || t.symbol.toUpperCase().includes(q)) &&
        (exitFilter === 'all' || t.exit_reason === exitFilter),
    )
  }, [trades, symbolQuery, exitFilter])

  const sorted = useMemo(() => {
    const rows = filtered.slice()
    rows.sort((a, b) => (sortDir === 'asc' ? compare(a, b, sortKey) : -compare(a, b, sortKey)))
    return rows
  }, [filtered, sortKey, sortDir])

  const totals = useMemo(() => {
    let netPnl = 0
    let costs = 0
    let wins = 0
    for (const t of filtered) {
      netPnl += t.net_pnl
      costs += t.costs
      if (t.net_pnl > 0) wins += 1
    }
    return {
      count: filtered.length,
      netPnl,
      costs,
      winRate: filtered.length === 0 ? 0 : (wins / filtered.length) * 100,
    }
  }, [filtered])

  const total = sorted.length
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const current = Math.min(page, totalPages - 1)
  const startIndex = current * PAGE_SIZE
  const rows = sorted.slice(startIndex, startIndex + PAGE_SIZE)
  const firstShown = total === 0 ? 0 : startIndex + 1
  const lastShown = Math.min(startIndex + PAGE_SIZE, total)

  function toggleSort(key: SortKey, numeric: boolean): void {
    if (key === sortKey) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir(numeric || key.endsWith('_date') ? 'desc' : 'asc')
    }
    setPage(0)
  }

  if (trades.length === 0) {
    return (
      <section className="panel overflow-hidden" aria-label="Trade log">
        <header className="border-b border-border px-4 py-2.5">
          <h3 className="text-xs font-semibold uppercase tracking-[0.08em] text-primary">
            Trade log
          </h3>
        </header>
        <p className="px-4 py-6 text-sm text-muted">
          No trades. The entry conditions never fired on this universe and window.
        </p>
      </section>
    )
  }

  return (
    <section className="panel overflow-hidden" aria-label="Trade log">
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-border px-4 py-2.5">
        <h3 className="text-xs font-semibold uppercase tracking-[0.08em] text-primary">
          Trade log
        </h3>
        <span className="mono text-[11px] text-muted">
          {fmtNum(trades.length, 0)} round trips
        </span>
      </header>

      {/* Filters -------------------------------------------------------- */}
      <div className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-2.5">
        <label className="flex items-center gap-2 text-[11px] uppercase tracking-[0.06em] text-muted">
          Symbol
          <input
            type="text"
            value={symbolQuery}
            spellCheck={false}
            autoComplete="off"
            placeholder="e.g. SPY"
            onChange={(e) => {
              setSymbolQuery(e.target.value)
              setPage(0)
            }}
            className="mono w-32 rounded border border-border bg-bg px-2 py-1 text-sm normal-case tracking-normal text-primary placeholder:text-muted focus:border-accent focus:outline-none"
          />
        </label>
        <label className="flex items-center gap-2 text-[11px] uppercase tracking-[0.06em] text-muted">
          Exit reason
          <select
            value={exitFilter}
            onChange={(e) => {
              setExitFilter(e.target.value)
              setPage(0)
            }}
            className="rounded border border-border bg-bg px-2 py-1 text-sm normal-case tracking-normal text-primary focus:border-accent focus:outline-none"
          >
            <option value="all">all</option>
            {exitReasons.map((reason) => (
              <option key={reason} value={reason}>
                {humanise(reason)}
              </option>
            ))}
          </select>
        </label>
        {symbolQuery === '' && exitFilter === 'all' ? null : (
          <button
            type="button"
            onClick={() => {
              setSymbolQuery('')
              setExitFilter('all')
              setPage(0)
            }}
            className="rounded border border-border px-2 py-1 text-xs text-muted hover:bg-panel-hi hover:text-primary"
          >
            Clear filters
          </button>
        )}
      </div>

      {/* Filtered summary ----------------------------------------------- */}
      <div className="flex flex-wrap items-baseline gap-x-8 gap-y-1 border-b border-border px-4 py-2">
        <Stat label="Trades" value={fmtNum(totals.count, 0)} tone="text-primary" />
        <Stat label="Win rate" value={fmtPct(totals.winRate, 1)} tone="text-primary" />
        <Stat label="Net P&L" value={fmtMoney(totals.netPnl)} tone={signClass(totals.netPnl)} />
        <Stat label="Costs" value={fmtMoney(totals.costs)} tone="text-muted" />
      </div>

      {/* Table ----------------------------------------------------------- */}
      {total === 0 ? (
        <p className="px-4 py-6 text-sm text-muted">
          No trades match this filter.
        </p>
      ) : (
        <div className="max-h-[34rem] overflow-auto">
          <table className="w-full min-w-[68rem] text-sm">
            <thead>
              <tr>
                {COLUMNS.map((col) => {
                  const active = col.key === sortKey
                  return (
                    <th
                      key={col.key}
                      scope="col"
                      aria-sort={
                        active ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'
                      }
                      className="sticky top-0 z-10 bg-panel px-3 py-2 text-[11px] font-medium uppercase tracking-[0.06em] text-muted shadow-[inset_0_-1px_0_var(--color-border)]"
                    >
                      <button
                        type="button"
                        title={col.definition}
                        onClick={() => toggleSort(col.key, col.numeric)}
                        className={`inline-flex w-full items-center gap-1 whitespace-nowrap hover:text-primary ${
                          col.numeric ? 'justify-end' : 'justify-start'
                        } ${active ? 'text-primary' : ''}`}
                      >
                        <span>{col.label}</span>
                        <Caret active={active} dir={sortDir} />
                      </button>
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {rows.map((t) => (
                <tr
                  key={`${t.symbol}-${t.entry_date}-${t.exit_date}-${t.entry_price}`}
                  className="border-b border-border/60 last:border-b-0 hover:bg-panel-hi"
                >
                  <td className="mono px-3 py-1.5 font-medium text-primary">{t.symbol}</td>
                  <td className="mono whitespace-nowrap px-3 py-1.5 text-muted">
                    {fmtDate(t.entry_date)}
                  </td>
                  <td className="mono whitespace-nowrap px-3 py-1.5 text-muted">
                    {fmtDate(t.exit_date)}
                  </td>
                  <td className="mono px-3 py-1.5 text-right text-primary">
                    {fmtNum(t.holding_days, 0)}
                  </td>
                  <td className="mono px-3 py-1.5 text-right text-primary">
                    {fmtNum(t.entry_price, 2)}
                  </td>
                  <td className="mono px-3 py-1.5 text-right text-primary">
                    {fmtNum(t.exit_price, 2)}
                  </td>
                  <td className="mono px-3 py-1.5 text-right text-muted">{shareText(t.shares)}</td>
                  <td className={`mono px-3 py-1.5 text-right ${signClass(t.net_pnl)}`}>
                    {fmtNum(t.net_pnl, 2)}
                  </td>
                  <td className={`mono px-3 py-1.5 text-right ${signClass(t.return_pct)}`}>
                    {fmtPct(t.return_pct, 2)}
                  </td>
                  <td className="mono px-3 py-1.5 text-right text-muted">{fmtNum(t.costs, 2)}</td>
                  <td className="px-3 py-1.5">
                    <span
                      className={`inline-block whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px] leading-tight ${exitPillClass(
                        t.exit_reason,
                      )}`}
                    >
                      {humanise(t.exit_reason)}
                    </span>
                  </td>
                  <td className={`mono px-3 py-1.5 text-right ${signClass(t.mae_pct)}`}>
                    {fmtPct(t.mae_pct, 2)}
                  </td>
                  <td className={`mono px-3 py-1.5 text-right ${signClass(t.mfe_pct)}`}>
                    {fmtPct(t.mfe_pct, 2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination ------------------------------------------------------ */}
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t border-border px-4 py-2">
        <span className="mono text-xs text-muted">
          {`Showing ${fmtNum(firstShown, 0)}-${fmtNum(lastShown, 0)} of ${fmtNum(total, 0)}`}
          {total === trades.length ? '' : ` (filtered from ${fmtNum(trades.length, 0)})`}
        </span>
        <div className="flex items-center gap-2">
          <span className="mono text-xs text-muted">
            {`Page ${fmtNum(current + 1, 0)} / ${fmtNum(totalPages, 0)}`}
          </span>
          <button
            type="button"
            onClick={() => setPage(Math.max(0, current - 1))}
            disabled={current === 0}
            className="rounded border border-border px-2 py-1 text-xs text-primary hover:bg-panel-hi disabled:cursor-not-allowed disabled:text-muted disabled:opacity-50 disabled:hover:bg-transparent"
          >
            Prev
          </button>
          <button
            type="button"
            onClick={() => setPage(Math.min(totalPages - 1, current + 1))}
            disabled={current >= totalPages - 1}
            className="rounded border border-border px-2 py-1 text-xs text-primary hover:bg-panel-hi disabled:cursor-not-allowed disabled:text-muted disabled:opacity-50 disabled:hover:bg-transparent"
          >
            Next
          </button>
        </div>
      </div>
    </section>
  )
}
