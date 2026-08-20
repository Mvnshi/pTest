# ProofTrade

**Turn a plain-English trading strategy into a typed strategy definition, backtest it
across a universe of liquid US stocks and ETFs, and get an honest evidence report that
tells you where it works, where it fails, and how much of it is probably luck.**

Every backtesting tool gives you an equity curve. ProofTrade's job is to tell you
whether to believe it.

```
"Buy SPY and QQQ when RSI(14) drops below 30 and price is above the 200-day
 moving average. Sell when RSI goes above 55 or after 20 days. 8% stop loss."
                                  |
                                  v
     typed DSL  ->  clarification questions  ->  backtest  ->  evidence report
```

---

## Quick start

```bash
make setup     # python venv + npm install
make data      # generate the deterministic offline snapshot (already committed)
make dev       # API on :8000, UI on :5173
```

Open http://127.0.0.1:5173, pick one of the seeded examples, and run it.

To serve everything from a single process instead: `make serve` (builds the SPA and
serves it from FastAPI on :8000).

Run the tests with `make test`.

---

## What is actually in the box

| | |
|---|---|
| **Translation** | A deterministic English -> DSL parser (no model, no network). An optional Anthropic adapter exists behind an env var; its output is validated against the same schema before anything touches it. |
| **Strategy DSL** | Typed Pydantic models. 24 whitelisted indicators, six comparators, flat `all`/`any` logic, risk rules, position rules. Nothing executable, ever. |
| **Engine** | Daily bars, signal on close, fill at the next open, commission and slippage on every leg, intrabar stop and target fills, position caps, long and short. ~140 ms for 18 symbols x 20 years. |
| **Validation** | 70/30 in-sample/out-of-sample split, five independent walk-forward folds, a five-point cost-sensitivity sweep, and up to eight parameter-neighbourhood perturbations. 18 backtests per report, ~3 s total. |
| **Evidence report** | A 0-100 score from six itemised components, plus warnings for low trade count, concentrated returns, cost sensitivity, out-of-sample degradation, fragile parameters, narrow universe, low exposure, severe drawdown, and benchmark underperformance. |
| **Reproducibility** | Every run records the engine version, the data snapshot hash, a config hash and a result hash. Re-run the same strategy and the hash is identical; the UI shows it. |

### The evidence score is not a prediction

It scores **how much the backtest can be trusted**, not how much money the strategy will
make. A strategy that reliably loses money can score well: that just means the evidence
it loses money is solid. The two are reported side by side and never conflated.

---

## The data

`data/bars/` holds 18 symbols of adjusted daily OHLCV from 2006-01-03 to 2026-06-30,
about 5 159 bars each:

```
SPY QQQ IWM DIA        broad market
TLT GLD                bonds and gold
XLE XLF XLK XLV        sectors
AAPL MSFT NVDA AMZN JPM XOM KO JNJ    mega caps
```

**The committed snapshot is synthetic.** It is produced by
`scripts/generate_sample_data.py` - a seeded, regime-switching, GARCH-style generator
calibrated so each symbol's annualised return and volatility land near its real
2006-2026 figures, with three crisis drawdowns of 30-50% in the index series. It exists
so the demo runs offline, reproducibly, on any machine, with no vendor terms attached.

It is realistic in structure. It is **not real market history**, and the product says so
in three places: the generator's own output, a persistent banner in the UI, and a
`critical` warning in every evidence report. No number produced from it is a statement
about real markets.

To swap in real bars: `make data-real` vendors a snapshot from a free public endpoint
(one time, offline; the engine still never touches the network). The data hash changes,
the synthetic warning disappears, and everything else works identically.

Regenerating is deterministic - `make data` twice produces byte-identical files, and the
committed CSVs are exactly what the committed script produces.

---

## Honest limitations

Stated here rather than buried, because a tool about intellectual honesty that hides its
own caveats is a bad joke.

1. **The universe was chosen with hindsight.** All 18 symbols exist and are liquid today.
   A strategy tested only on survivors looks better than the same strategy run on the
   full historical cross-section with delistings included.
2. **No parameter fitting, so IS/OOS is not classical overfitting detection.** It measures
   regime stability. Parameter overfitting is probed separately by perturbing every
   numeric knob.
3. **Daily bars hide the intrabar path.** A stop and a target inside the same bar are
   resolved by assuming the stop filled first. That is conservative, not correct.
4. **Flat boolean logic only** - `all` or `any`, no nesting.
5. **No borrow costs, financing, market impact or capacity modelling** on shorts.
6. **One data source, one snapshot.** Nothing is reconciled against a second vendor.
7. **Cost is not a pure tax.** Stops and targets are set from the actual (slipped) fill
   price, so raising the cost level moves those levels and a different set of trades gets
   stopped out. Return is therefore *not* guaranteed to be monotonic in cost — the report
   detects this and says so on the cost curve rather than smoothing it away.
8. **Shorts have no margin model.** There is no maintenance requirement and no broker to
   close you out, so a squeeze is unbounded. The engine stops the run if equity reaches
   zero and raises a critical warning, which a real account would have hit sooner.
9. **A backtest is a lower bound on how wrong you can be**, not an estimate of future
   return.

---

## Hard constraints, and how they are enforced

| Constraint | Enforcement |
|---|---|
| No real-money trading | No broker SDK and no order-placement path exists. `test_constraints.py` greps for them. |
| No paid APIs | Data is vendored CSV; the optional refresh script uses a free public endpoint. |
| No live broker integration | Same. |
| No LLM code execution | The adapter's only contract is `text -> JSON`, validated by Pydantic before use. A test asserts the backend contains no `eval`, `exec`, `compile`, `__import__`, `os.system`, or `subprocess`. |
| Deterministic and reproducible | No wall clock or RNG in the execution path; a test asserts repeat runs are byte-identical, and every report carries a result hash. |
| Transaction costs and slippage | Required config fields with non-zero defaults; a test asserts returns are monotonically non-increasing in cost **for strategies without price-level risk rules**, and a second test pins the documented case where they are not (see below). |
| Train/test or walk-forward | Every report contains both, and a test asserts the folds tile the window without overlap. |
| Required warnings | Low trade count, concentrated returns and cost sensitivity are always evaluated; tests assert each fires on constructed inputs. |

---

## Layout

```
prooftrade/
├── docs/          PRD.md · ARCHITECTURE.md · METHODOLOGY.md · samples/
├── data/bars/     the frozen snapshot + manifest
├── scripts/       generate_sample_data.py (offline) · fetch_data.py (vendoring)
├── backend/app/   dsl · data · indicators · signals · engine · metrics
│                  validation · evidence · report · nl/ · storage · api
├── backend/tests/ 112 tests
└── frontend/src/  App + components (charts, tables, heatmap, evidence panel)
```

`docs/ARCHITECTURE.md` has the module map and the design decisions.
`docs/METHODOLOGY.md` has every formula, threshold and caveat.

---

## API

| Method | Path | |
|---|---|---|
| `GET` | `/api/health` | versions, data hash, translator in use |
| `GET` | `/api/universe` | symbols and coverage |
| `GET` | `/api/examples` | seeded demo strategies |
| `GET` | `/api/schema` | DSL JSON Schema + indicator registry |
| `POST` | `/api/parse` | `{text, answers?}` -> strategy, questions, assumptions, confidence |
| `POST` | `/api/backtest` | `{strategy, config}` -> full report, persisted to SQLite |
| `GET` | `/api/runs` · `/api/runs/{id}` | run history |

A complete example response is in `docs/samples/backtest_response.json`.

### Optional LLM translation

```bash
export PROOFTRADE_LLM=anthropic
export ANTHROPIC_API_KEY=...
pip install anthropic
```

The model is given the indicator registry and asked for a JSON object. Its output is
parsed into the same Pydantic models as everything else; anything that fails validation
is rejected outright and the deterministic parser's result is used instead, with a note
saying so. The model never writes code and never touches the engine.

---

**Not investment advice. Not a trading system. No part of this places an order.**
