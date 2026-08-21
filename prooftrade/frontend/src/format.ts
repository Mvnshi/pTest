/**
 * Number and date formatting for the whole UI.
 *
 * One rule underpins every function here: a value that cannot be rendered
 * honestly is rendered as a dash, never as "NaN", "Infinity" or "null". A
 * quant tool that prints NaN has already lost the reader's trust.
 *
 * The colour helpers return literal Tailwind class strings so the v4 scanner
 * can see every class that will ever be emitted.
 */

/** What every formatter falls back to when the value is not a real number. */
const DASH = '-'

const LOCALE = 'en-US'

const MONTHS = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
] as const

function fixed(value: number, digits: number): string {
  return value.toLocaleString(LOCALE, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

/**
 * A percentage that already arrives in percent units: 12.34 -> "12.34%".
 *
 * Negatives keep their sign; -0 is normalised so a rounded-away loss never
 * shows as "-0.00%". Infinity and NaN - which turn up in ratios computed over
 * an empty denominator - render as a dash.
 */
export function fmtPct(v: number, digits = 2): string {
  if (typeof v !== 'number' || !Number.isFinite(v)) return DASH
  const rounded = Number(v.toFixed(digits))
  const safe = rounded === 0 ? 0 : rounded
  return `${fixed(safe, digits)}%`
}

/** A plain number with thousands separators: 5159 -> "5,159". */
export function fmtNum(v: number, digits = 2): string {
  if (typeof v !== 'number' || !Number.isFinite(v)) return DASH
  const rounded = Number(v.toFixed(digits))
  const safe = rounded === 0 ? 0 : rounded
  return fixed(safe, digits)
}

/**
 * Dollars, compacted once the magnitude makes full precision noise:
 * 1_240_000 -> "$1.2M", 340_000 -> "$340k", 2_759.33 -> "$2,759",
 * -180.84 -> "-$181".
 */
export function fmtMoney(v: number): string {
  if (typeof v !== 'number' || !Number.isFinite(v)) return DASH
  const sign = v < 0 ? '-' : ''
  const abs = Math.abs(v)

  if (abs >= 1e9) return `${sign}$${compact(abs / 1e9)}B`
  if (abs >= 1e6) return `${sign}$${compact(abs / 1e6)}M`
  if (abs >= 1e5) return `${sign}$${compact(abs / 1e3)}k`
  return `${sign}$${fixed(Math.round(abs), 0)}`
}

/** One decimal below 100, none above, so compacted money stays 3-4 glyphs. */
function compact(value: number): string {
  return value < 100 ? fixed(value, 1) : fixed(Math.round(value), 0)
}

/**
 * An ISO date - "2019-03-12" or a full timestamp - as "12 Mar 2019".
 *
 * Parsed by hand rather than through `Date`, because `new Date('2019-03-12')`
 * is UTC midnight and renders as the 11th for anyone west of Greenwich.
 */
export function fmtDate(iso: string): string {
  if (typeof iso !== 'string') return DASH
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso)
  if (match === null) return iso.trim() === '' ? DASH : iso
  const year = match[1]
  const month = MONTHS[Number(match[2]) - 1]
  const day = Number(match[3])
  if (month === undefined || !Number.isFinite(day)) return iso
  return `${day} ${month} ${year}`
}

/** A unitless ratio - Sharpe, profit factor, fragility: 1.4215 -> "1.42". */
export function fmtRatio(v: number): string {
  if (typeof v !== 'number' || !Number.isFinite(v)) return DASH
  const rounded = Number(v.toFixed(2))
  return fixed(rounded === 0 ? 0 : rounded, 2)
}

/**
 * Colour for a signed value. Zero and unreadable values stay muted: grey is
 * the honest colour for "this number does not lean either way".
 */
export function signClass(v: number): string {
  if (typeof v !== 'number' || !Number.isFinite(v) || v === 0) return 'text-muted'
  return v > 0 ? 'text-positive' : 'text-negative'
}

/**
 * Badge colours for warning severity. Critical and high read as losses because
 * that is what they are; info stays quiet so it cannot be mistaken for alarm.
 */
export function severityClass(s: string): string {
  switch (s) {
    case 'critical':
      return 'bg-negative/15 text-negative'
    case 'high':
      return 'bg-negative/10 text-negative'
    case 'medium':
      return 'bg-warning/15 text-warning'
    case 'low':
      return 'bg-accent/10 text-accent'
    case 'info':
      return 'bg-panel-hi text-muted'
    default:
      return 'bg-panel-hi text-muted'
  }
}
