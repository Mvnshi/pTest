/**
 * Typed client for the ProofTrade API.
 *
 * Every endpoint lives under `/api`, which Vite proxies to the FastAPI server
 * in dev and which is same-origin in the built SPA. There is no base URL to
 * configure and no auth: the backend runs locally, offline, with no keys.
 */

import type {
  BacktestConfig,
  BacktestReport,
  ExampleStrategy,
  HealthInfo,
  ParseResponse,
  RunSummary,
  Strategy,
  UniverseInfo,
} from './types'

const BASE = '/api'

/**
 * A failed request, carrying enough to say something useful on screen.
 *
 * FastAPI answers validation failures with `{"detail": ...}` where `detail` is
 * either a string we wrote or a list of Pydantic error objects. Both are
 * flattened into `detail` here so callers never have to inspect the shape.
 */
export class ApiError extends Error {
  readonly status: number
  readonly detail: string
  readonly url: string

  constructor(status: number, detail: string, url: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.url = url
  }
}

interface PydanticError {
  loc?: unknown
  msg?: unknown
  type?: unknown
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** "body -> strategy -> universe: field required" from one Pydantic error. */
function renderPydanticError(entry: PydanticError): string {
  const path = Array.isArray(entry.loc)
    ? entry.loc.filter((p) => typeof p === 'string' || typeof p === 'number').join(' -> ')
    : ''
  const message = typeof entry.msg === 'string' ? entry.msg : 'invalid value'
  return path === '' ? message : `${path}: ${message}`
}

/** Flattens any shape FastAPI puts in `detail` into one readable line. */
function readDetail(body: unknown, status: number): string {
  if (typeof body === 'string' && body.trim() !== '') return body.trim()

  if (isRecord(body)) {
    const detail = body['detail']
    if (typeof detail === 'string' && detail.trim() !== '') return detail.trim()
    if (Array.isArray(detail) && detail.length > 0) {
      const lines = detail
        .filter(isRecord)
        .map((entry) => renderPydanticError(entry as PydanticError))
        .filter((line) => line !== '')
      if (lines.length > 0) return lines.join('; ')
    }
    const message = body['message']
    if (typeof message === 'string' && message.trim() !== '') return message.trim()
  }

  return `Request failed with HTTP ${status}.`
}

/** Body may be JSON, may be an HTML error page, may be empty. Never throws. */
async function readBody(response: Response): Promise<unknown> {
  const text = await response.text().catch(() => '')
  if (text === '') return null
  try {
    return JSON.parse(text) as unknown
  } catch {
    return text
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${BASE}${path}`

  let response: Response
  try {
    response = await fetch(url, init)
  } catch (cause) {
    const reason = cause instanceof Error ? cause.message : 'network error'
    throw new ApiError(0, `Could not reach the ProofTrade backend (${reason}).`, url)
  }

  const body = await readBody(response)

  if (!response.ok) {
    throw new ApiError(response.status, readDetail(body, response.status), url)
  }
  if (body === null) {
    throw new ApiError(response.status, 'The server returned an empty response.', url)
  }
  return body as T
}

function postJson<T>(path: string, payload: unknown, signal?: AbortSignal): Promise<T> {
  const init: RequestInit = {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }
  if (signal !== undefined) init.signal = signal
  return request<T>(path, init)
}

function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const init: RequestInit = { method: 'GET' }
  if (signal !== undefined) init.signal = signal
  return request<T>(path, init)
}

/* ------------------------------------------------------------------------- */

/** Engine versions, the data snapshot hash, and whether the bars are real. */
export function getHealth(signal?: AbortSignal): Promise<HealthInfo> {
  return get<HealthInfo>('/health', signal)
}

/** Per-symbol coverage of the frozen snapshot. */
export function getUniverse(signal?: AbortSignal): Promise<UniverseInfo> {
  return get<UniverseInfo>('/universe', signal)
}

/** Seeded demo strategies, deliberately mixed between good and bad evidence. */
export function getExamples(signal?: AbortSignal): Promise<ExampleStrategy[]> {
  return get<ExampleStrategy[]>('/examples', signal)
}

/**
 * Plain English -> typed DSL. `answers` carries the clarification choices made
 * so far, keyed by question id; re-posting with more answers re-derives the
 * whole strategy rather than patching it.
 */
export function parseStrategy(
  text: string,
  answers?: Record<string, string>,
  signal?: AbortSignal,
): Promise<ParseResponse> {
  const payload: { text: string; answers?: Record<string, string> } = { text }
  if (answers !== undefined && Object.keys(answers).length > 0) payload.answers = answers
  return postJson<ParseResponse>('/parse', payload, signal)
}

/** The full report: base run, IS/OOS split, walk-forward, robustness probes. */
export function runBacktest(
  strategy: Strategy,
  config: BacktestConfig,
  signal?: AbortSignal,
): Promise<BacktestReport> {
  return postJson<BacktestReport>('/backtest', { strategy, config }, signal)
}

/** Stored run history, newest first. */
export function listRuns(signal?: AbortSignal): Promise<RunSummary[]> {
  return get<RunSummary[]>('/runs', signal)
}

/** One stored run, re-opened byte-for-byte as it was returned originally. */
export function getRun(runId: string, signal?: AbortSignal): Promise<BacktestReport> {
  return get<BacktestReport>(`/runs/${encodeURIComponent(runId)}`, signal)
}
