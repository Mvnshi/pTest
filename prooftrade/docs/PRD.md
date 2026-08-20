# ProofTrade — Product Requirements (Investor-Demo MVP)

## 1. Thesis

Retail and semi-pro traders describe strategies in English and then either (a) never
test them, or (b) test them in a way that manufactures a beautiful equity curve which
does not survive contact with reality. Backtesting tools optimise for *making a curve
look good*. ProofTrade optimises for **telling you whether to believe the curve**.

> ProofTrade turns a plain-English trading strategy into a structured, typed strategy
> definition, backtests it across a universe of liquid US stocks and ETFs, shows where
> it works and where it fails, and produces an honest evidence report with
> anti-overfitting warnings.

The differentiator is not the backtest. Every product has a backtest. The
differentiator is the **Evidence Report**: a transparent, decomposable score plus
falsification probes that actively try to break the strategy the user just wrote.

## 2. Demo narrative (what an investor sees in 4 minutes)

1. Type: *"Buy SPY and QQQ when RSI(14) drops below 30 and price is above the 200-day
   moving average, sell when RSI goes above 55 or after 20 days."*
2. ProofTrade parses it into a typed DSL and **asks two clarification questions**
   ("No stop loss specified — add one?", "Universe not specified — use all 18 symbols?").
3. User confirms. The parsed strategy is shown as structured, human-readable rules —
   not code. Nothing executable was generated.
4. 4 seconds later: equity curve, drawdown, 180 trades, metrics, per-symbol heatmap,
   per-year and per-regime breakdown.
5. The **Evidence panel** says: *Score 54/100 — Grade C. 71% of net profit came from
   3 symbols. Sharpe falls from 0.81 to 0.19 at 3x transaction costs. Out-of-sample
   Sharpe is 41% below in-sample.*
6. The investor's reaction is the product: *"This is the first tool that argued with me."*

## 3. Users

| User | Job to be done |
|---|---|
| Self-directed retail trader | "Is the strategy I read on Reddit actually real?" |
| Aspiring quant | "Give me a rigorous harness without writing a backtester." |
| Content creator / educator | "Show my audience why this popular setup is fragile." |

MVP targets the first. The other two are adjacent and need no extra features.

## 4. Core user flow (MVP scope)

```
Describe → Parse (typed DSL) → Clarify → Confirm → Backtest → Evidence report
```

1. **Describe.** Free-text strategy input, with seeded examples.
2. **Parse.** Text is converted to a constrained, typed strategy DSL. The translator
   never emits code — only JSON validated against a Pydantic schema.
3. **Clarify.** If the strategy is ambiguous or incomplete (no exit, no stop, no
   universe, vague "moving average"), the system asks targeted questions with
   defaults pre-selected, and lists the assumptions it made.
4. **Confirm.** The user sees the structured strategy in plain language plus raw JSON
   and edits key knobs (universe, costs, date range, max positions).
5. **Backtest.** Deterministic daily-bar backtest across the universe.
6. **Report.** Equity curve, drawdown curve, trade log, metrics table, per-symbol
   heatmap, year/regime breakdown, evidence score and warnings.

## 5. Functional requirements

### FR-1 Strategy DSL
- Typed, whitelisted, non-executable. Indicators come from a fixed registry.
- Entry and exit are flat `all`/`any` condition groups (max 6 conditions each).
- Comparators: `<  <=  >  >=  crosses_above  crosses_below`.
- Operands: indicator (with typed params), price field, or numeric constant.
- Risk rules: stop loss %, take profit %, trailing stop %, max holding days.
- Position rules: equal-weight sizing, max concurrent positions.
- Anything outside the schema is rejected with a readable error. No `eval`, no
  generated Python, no user-supplied expressions.

### FR-2 Natural-language translation
- Default translator is **deterministic and offline**: a rule-based parser over a
  phrase grammar (indicators, comparators, numbers, durations, symbols, directions).
- Optional LLM translator behind `PROOFTRADE_LLM=anthropic`, used *only* to emit JSON
  conforming to the DSL schema. Output is schema-validated; on any validation failure
  the request is rejected and the deterministic parser's result is used instead.
- Both paths return: `strategy`, `questions[]`, `assumptions[]`, `confidence`,
  `unparsed_fragments[]`.

### FR-3 Clarification
Questions are generated when: no exit condition, no stop loss, no universe named,
ambiguous indicator period, ambiguous direction, or an unparsed fragment remains.
Each question carries typed options and a default, so the flow never blocks.

### FR-4 Backtest engine
- Daily bars, split/dividend adjusted, from a frozen local snapshot.
- **Signal on bar `t` close → fill on bar `t+1` open.** No same-bar fills, ever.
- Costs: commission (bps of notional) + slippage (bps applied to the fill price),
  charged on both entry and exit, both configurable.
- Intrabar risk exits on subsequent bars using that bar's high/low. If a stop and a
  target are both reachable in one bar, the **stop is assumed to fill first**.
- Equal-weight sizing with a hard cap on concurrent positions; candidate ordering is
  deterministic (alphabetical by symbol) and documented.
- Cash-constrained, long and short supported, no leverage, no pyramiding.

### FR-5 Determinism
- Same strategy + same config + same data snapshot ⇒ byte-identical results.
- Every run records `engine_version`, `data_snapshot_hash`, `config_hash`, and a
  `result_hash` over the output. The UI displays the result hash.
- No wall-clock, no RNG in the execution path. Bootstrap resampling (evidence only)
  uses an explicit, stored seed.

### FR-6 Validation
- **In-sample / out-of-sample split** at a configurable fraction (default 70/30),
  with metrics for both halves and a degradation figure.
- **Walk-forward**: N sequential, non-overlapping out-of-sample windows (default 5),
  each backtested independently; per-fold metrics and a consistency measure.
- Honest framing: the MVP does not fit parameters, so IS/OOS measures *regime
  stability*, not classical parameter overfitting. Parameter overfitting is probed
  separately (FR-7).

### FR-7 Robustness probes
- **Cost sensitivity**: re-run at 0×, 1×, 2×, 3×, 5× the configured costs; report the
  break-even cost level in bps at which CAGR turns negative.
- **Parameter neighbourhood**: perturb each numeric parameter in the DSL by ±10% and
  ±20% (deterministic grid, capped at 8 neighbours), re-run, and compare the base
  Sharpe against the neighbourhood median. A strategy that only works at its exact
  parameters is flagged as fragile.

### FR-8 Evidence score and warnings
A 0–100 score with a visible component breakdown (no black box):

| Component | Weight | Measures |
|---|---:|---|
| Sample size | 20 | Number of closed trades vs a 100-trade reference |
| Out-of-sample consistency | 20 | OOS Sharpe retained vs in-sample |
| Breadth | 15 | Share of universe symbols profitable; top-symbol PnL concentration |
| Time consistency | 15 | Share of calendar years and regimes profitable |
| Cost robustness | 15 | Sharpe retained at 3× costs |
| Parameter robustness | 15 | Base Sharpe vs perturbed-neighbourhood median |

Mandatory warnings (each with severity, evidence value, and threshold):
low trade count · concentrated returns (top symbol, top 5 trades) · cost sensitivity ·
out-of-sample degradation · short history · fragile parameters · low market exposure ·
severe drawdown · fixed-universe hindsight bias · single-snapshot data caveat.

### FR-9 Reporting UI
Equity curve (strategy vs buy-and-hold benchmark) · underwater drawdown curve ·
metrics table (strategy vs benchmark) · sortable trade log · per-symbol heatmap ·
per-year and per-regime tables · evidence panel with score breakdown and warnings.

### FR-10 Persistence
SQLite. Runs are immutable rows: strategy JSON, config, results JSON, hashes,
timestamp. Run history is listable and re-openable.

## 6. Non-functional requirements

- Backtest of 18 symbols × ~20 years completes in under 1 s; the full report
  (base run + 4 cost runs + 5 folds + up to 8 neighbours) in under 8 s.
- Backend runs offline with no API keys. Frontend is a static SPA.
- Cold start on a clean machine: `make setup && make dev`.

## 7. Hard constraints (enforced, not aspirational)

| Constraint | How it is enforced |
|---|---|
| No real-money trading | No broker SDK, no order-placement code path exists. |
| No paid APIs | Data is a vendored CSV snapshot; the optional refresh script uses a free public endpoint. |
| No live broker integration | Same as above; nothing in the codebase opens a trading connection. |
| No LLM code execution | The LLM adapter's only contract is `text → JSON`, validated by Pydantic before use. No `eval`/`exec`/`compile` anywhere in the backend (enforced by a test). |
| Deterministic and reproducible | Frozen data snapshot + hashes + no RNG in the execution path (enforced by a repeat-run equality test). |
| Costs and slippage | Required fields on every backtest config; a test asserts returns are monotonically non-increasing in cost. |
| Train/test or walk-forward | Every report includes both an IS/OOS split and walk-forward folds. |
| Required warnings | Low trade count, concentrated returns, and cost sensitivity are always evaluated and always reported. |

## 8. Explicitly out of scope for the MVP

Intraday or minute bars · options, futures, crypto, FX · portfolio optimisation ·
parameter optimisation or search (deliberately — it is the thing that causes
overfitting) · nested boolean logic in the DSL · point-in-time universe
reconstruction · short-borrow fees and financing · multi-user accounts and auth ·
live paper trading · strategy sharing or a marketplace · PDF export.

## 9. Success criteria for the demo

1. A non-technical viewer can go from sentence to evidence report without help.
2. At least one seeded example scores well and one scores badly, for contrast.
3. Every number on screen can be traced to a definition in the UI or docs.
4. Re-running the same strategy shows the same result hash, on screen.
5. No claim is made that the strategy will make money.
