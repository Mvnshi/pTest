# ProofTrade — Architecture

## 1. Shape of the system

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Browser — React + TypeScript + Tailwind + Recharts (Vite SPA)           │
│  Describe → Clarify → Confirm → Report                                   │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │ JSON over HTTP (typed client, generated types)
┌───────────────────────────────▼──────────────────────────────────────────┐
│  FastAPI                                                                  │
│  /api/parse   /api/backtest   /api/runs   /api/universe   /api/examples   │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │
        ┌───────────────────────┼────────────────────────┬─────────────────┐
        ▼                       ▼                        ▼                 ▼
┌───────────────┐   ┌───────────────────────┐   ┌────────────────┐  ┌────────────┐
│ nl/           │   │ engine/               │   │ evidence/      │  │ storage/   │
│ translator    │   │ indicators → signals  │   │ score+warnings │  │ SQLite     │
│ (rules | LLM) │   │ → event loop → trades │   │                │  │ runs table │
│      ↓        │   │ → equity → metrics    │   └────────────────┘  └────────────┘
│  dsl.py  ◄────┼───┤ validation: IS/OOS,   │
│  (Pydantic)   │   │ walk-forward, probes  │
└───────────────┘   └───────────┬───────────┘
                                ▼
                    ┌───────────────────────┐
                    │ data/bars/*.csv       │
                    │ frozen snapshot       │
                    │ + SHA-256 manifest    │
                    └───────────────────────┘
```

`dsl.py` is the contract everything else is written against: the translator's only
output, the engine's only input, and the source of the JSON Schema the frontend
renders. Nothing downstream of the translator ever sees free text.

## 2. Key design decisions

### 2.1 The LLM is a parser, not an author
The translator's entire contract is `str → StrategyDSL`. The default implementation is
a **deterministic rule-based parser** with no network and no model, so the demo is
reproducible and free. An optional Anthropic adapter can be enabled with an env var;
its output is parsed as JSON and validated by Pydantic before anything else touches
it. A validation failure is a hard reject, not a repair attempt. There is no code
generation path, and a test asserts the backend contains no `eval`/`exec`/`compile`.

### 2.2 Execution model: signal on close, fill on next open
The single most common source of fake backtest returns is same-bar execution. The
engine physically cannot do it: signals are computed from data up to and including bar
`t`'s close, and the resulting orders sit in a pending queue that is only drained at
bar `t+1`'s open. A dedicated test shifts an entire price series forward and asserts
the trade list shifts with it.

### 2.3 Costs are on the fill price, not bolted on afterwards
Slippage moves the fill price against the trader (`open × (1 + slip)` on a buy,
`open × (1 − slip)` on a sell); commission is charged separately as bps of notional.
Both appear per-trade in the trade log, so a user can see the cost drag on any single
trade rather than only in aggregate.

### 2.4 Determinism is a property, not a hope
No wall-clock, no dict-ordering dependence, no unseeded RNG in the execution path.
Candidate ordering when more entry signals fire than there are open slots is
alphabetical by symbol — arbitrary, but fixed and documented. Every run stores
`engine_version`, `data_snapshot_hash`, `config_hash` and a `result_hash`; the UI
displays the result hash so a viewer can watch it stay identical across re-runs.

### 2.5 The report is adversarial by construction
The base backtest is one of ~18 runs. The others exist to attack it: four cost levels,
five walk-forward folds, and up to eight parameter-neighbourhood perturbations. The
evidence score is a weighted, itemised function of those attacks — every component
shows its raw measurement, its threshold, and its contribution.

### 2.6 Data is a frozen snapshot
`data/bars/*.csv` holds adjusted daily OHLCV for an 18-symbol universe. It is
committed, hashed, and never fetched at runtime. Two offline scripts can regenerate
it: `fetch_data.py` (free public endpoint, one-time vendoring) and
`generate_sample_data.py` (seeded synthetic bars with regime switching, so the demo
runs on a machine with no network at all).

## 3. Module responsibilities

| Module | Responsibility | Depends on |
|---|---|---|
| `app/dsl.py` | Typed strategy schema, indicator registry spec, validation | pydantic |
| `app/data.py` | Load/adjust/cache bars, trading calendar, snapshot hash | pandas |
| `app/indicators.py` | Vectorised indicator computation from the registry | pandas, numpy |
| `app/signals.py` | DSL condition groups → boolean entry/exit arrays | dsl, indicators |
| `app/engine.py` | Event loop, fills, costs, risk exits, trades, equity | signals, data |
| `app/metrics.py` | Return/risk statistics, per-symbol, per-year, per-regime | numpy |
| `app/validation.py` | IS/OOS split, walk-forward folds, cost + parameter probes | engine, metrics |
| `app/evidence.py` | Score components and warning generation | metrics, validation |
| `app/nl/rules.py` | Deterministic English → DSL parser, questions, assumptions | dsl |
| `app/nl/llm.py` | Optional constrained LLM adapter (schema-validated) | dsl |
| `app/storage.py` | SQLite run persistence | sqlite3 |
| `app/api.py` | FastAPI routes, request/response models | all of the above |

Dependencies point one way: `api → {nl, engine, validation, evidence, storage} → {signals, metrics} → {indicators, data} → dsl`.

## 4. Core data flow for one report

```
text
 └─▶ translate()        → StrategyDSL + questions + assumptions
      └─▶ (user confirms / edits)
           └─▶ run_report(strategy, config)
                ├─ run_backtest(full period)          → equity, trades, metrics
                ├─ split IS/OOS                       → two metric sets + degradation
                ├─ 5 × run_backtest(fold window)      → per-fold metrics
                ├─ 4 × run_backtest(cost multiple)    → cost curve + break-even bps
                ├─ ≤8 × run_backtest(perturbed DSL)   → neighbourhood Sharpe distribution
                ├─ per-symbol / per-year / per-regime aggregation
                └─ score() + warnings()               → evidence report
                     └─▶ persist to SQLite, return JSON
```

## 5. API surface

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Liveness, engine version, data snapshot hash |
| `GET` | `/api/universe` | Symbols, date coverage, snapshot metadata |
| `GET` | `/api/examples` | Seeded demo strategies (one strong, one weak) |
| `GET` | `/api/schema` | JSON Schema for the DSL + indicator registry |
| `POST` | `/api/parse` | `{text}` → strategy, questions, assumptions, confidence |
| `POST` | `/api/backtest` | `{strategy, config}` → full report, persisted |
| `GET` | `/api/runs` | Recent runs (id, name, score, created_at) |
| `GET` | `/api/runs/{id}` | Full stored report |

## 6. File tree

```
prooftrade/
├── README.md
├── Makefile                        # setup / dev / test / data
├── docs/
│   ├── PRD.md
│   ├── ARCHITECTURE.md             # this file
│   └── METHODOLOGY.md              # metric + score definitions, honest caveats
├── data/
│   └── bars/
│       ├── manifest.json           # snapshot range, per-symbol coverage
│       └── {SPY,QQQ,...}.csv       # date,open,high,low,close,adj_close,volume
├── scripts/
│   ├── fetch_data.py               # one-time vendoring from a free public endpoint
│   └── generate_sample_data.py     # seeded offline synthetic fallback
├── backend/
│   ├── pyproject.toml
│   ├── requirements.txt
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                 # uvicorn entrypoint, static SPA mount
│   │   ├── api.py                  # routes
│   │   ├── config.py               # paths, engine version, defaults
│   │   ├── dsl.py                  # ★ typed strategy schema
│   │   ├── data.py                 # bar loading, calendar, snapshot hash
│   │   ├── indicators.py           # indicator registry implementations
│   │   ├── signals.py              # DSL → boolean signal arrays
│   │   ├── engine.py               # ★ deterministic backtest loop
│   │   ├── metrics.py              # statistics and breakdowns
│   │   ├── validation.py           # IS/OOS, walk-forward, robustness probes
│   │   ├── evidence.py             # ★ score + warnings
│   │   ├── report.py               # orchestrates one full report
│   │   ├── storage.py              # SQLite
│   │   ├── examples.py             # seeded demo strategies
│   │   └── nl/
│   │       ├── __init__.py
│   │       ├── rules.py            # ★ deterministic English → DSL
│   │       └── llm.py              # optional constrained LLM adapter
│   └── tests/
│       ├── test_dsl.py
│       ├── test_indicators.py
│       ├── test_engine.py          # no-lookahead, costs, stops, determinism
│       ├── test_metrics.py
│       ├── test_validation.py
│       ├── test_evidence.py
│       ├── test_nl.py
│       ├── test_api.py
│       └── test_constraints.py     # no eval/exec, no network at runtime
└── frontend/
    ├── package.json
    ├── vite.config.ts
    ├── tsconfig.json
    ├── index.html
    └── src/
        ├── main.tsx
        ├── App.tsx                 # step machine: describe → clarify → report
        ├── api.ts                  # typed fetch client
        ├── types.ts                # mirrors backend response models
        ├── format.ts               # number/percent/date formatting
        ├── index.css               # Tailwind entry + design tokens
        └── components/
            ├── StrategyInput.tsx   # free text + seeded examples
            ├── ClarifyPanel.tsx    # questions, assumptions, confidence
            ├── StrategyCard.tsx    # parsed rules in plain language + JSON
            ├── ConfigPanel.tsx     # universe, costs, dates, positions
            ├── EquityChart.tsx     # strategy vs benchmark
            ├── DrawdownChart.tsx   # underwater curve
            ├── MetricsTable.tsx    # strategy vs benchmark, IS vs OOS
            ├── TradeLog.tsx        # sortable, paginated
            ├── SymbolHeatmap.tsx   # per-symbol contribution grid
            ├── RegimeBreakdown.tsx # per-year + per-regime tables
            ├── EvidencePanel.tsx   # score, components, warnings
            ├── WalkForward.tsx     # per-fold bars + cost-sensitivity curve
            └── ui.tsx              # small shared primitives
```

★ = correctness-critical; these carry the heaviest test coverage.

## 7. Implementation plan

| Phase | Deliverable | Definition of done |
|---|---|---|
| 0 | Frozen data snapshot + loader | 18 symbols, ~5 000 bars each, manifest hashed; loader returns adjusted OHLCV |
| 1 | DSL (`dsl.py`) | Round-trips JSON; rejects unknown indicators, bad periods, empty entries |
| 2 | Indicators + signals | Each indicator matches a hand-computed reference; conditions produce boolean arrays |
| 3 | Engine | Trades, equity, costs, stops; no-lookahead and determinism tests pass |
| 4 | Metrics + breakdowns | Sharpe/CAGR/MaxDD verified against closed-form cases; per-symbol/year/regime tables |
| 5 | Validation + probes | IS/OOS, 5 folds, cost curve, parameter neighbourhood |
| 6 | Evidence score + warnings | All three required warnings fire on constructed inputs |
| 7 | NL translator | Seeded examples parse correctly; ambiguity produces questions |
| 8 | API + SQLite | Endpoints return typed JSON; runs persist and reload |
| 9 | Frontend | Full flow renders; charts, heatmap, evidence panel |
| 10 | Hardening | Test suite green, README, end-to-end run against a live server |

Phases 1–6 are strictly sequential (each is the next one's input). Phase 7 depends
only on phase 1, and phase 9 only on the phase-8 contract, so both can be built in
parallel with the engine work.

## 8. Known limitations (stated in the product, not hidden)

1. **Fixed universe chosen with hindsight.** All 18 symbols exist and are liquid today.
   A real system needs point-in-time index membership; this one over-states results,
   and the report says so.
2. **No parameter fitting.** IS/OOS therefore measures regime stability, not classical
   overfitting. Parameter fragility is probed separately.
3. **Daily bars only.** Intrabar stop fills are approximated from the bar's high/low.
4. **Flat boolean logic.** `all` or `any`, not nested expressions.
5. **No borrow costs, financing, or capacity modelling** on short positions.
6. **One data vendor, one snapshot.** No cross-vendor reconciliation.
