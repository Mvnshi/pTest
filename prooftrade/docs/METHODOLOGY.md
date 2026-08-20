# ProofTrade — Methodology

This document defines every number ProofTrade puts on screen: where the data comes from, how a fill is
priced, how each statistic is computed, how the evidence score is assembled. It is written to be checked,
not believed: where a choice is arbitrary it is called arbitrary, and where a result is flattered by an
assumption, the assumption is named. Scope lives in `PRD.md`, structure in `ARCHITECTURE.md`.

## 1. Data

One frozen snapshot of adjusted daily OHLCV bars, committed under `data/bars/*.csv` and hashed with
SHA-256. Nothing is fetched at runtime, and every stored run records the snapshot hash, so a result
is always attributable to an exact dataset. Coverage is 18 liquid US equities and ETFs,
2006-01-01 to 2026-06-30:

| Group | Symbols |
|---|---|
| Broad market ETFs | SPY, QQQ, IWM, DIA |
| Cross-asset ETFs | TLT, GLD |
| Sector ETFs | XLE, XLF, XLK, XLV |
| Mega-cap single names | AAPL, MSFT, NVDA, AMZN, JPM, XOM, KO, JNJ |

**Adjustment.** Vendors adjust `close` for dividends and splits but often publish raw `open`, `high`
and `low`. Mixing the two creates phantom gaps that a backtest will happily trade. ProofTrade puts
the whole bar on a total-return basis with one per-bar ratio:

```
ratio_t = adj_close_t / close_t
open_t, high_t, low_t  <-  open_t * ratio_t, high_t * ratio_t, low_t * ratio_t
close_t                <-  adj_close_t
```

Volume is unscaled. Bar ordering is then re-asserted (`high >= max(open, close)`,
`low <= min(open, close)`) to absorb vendor rounding; a non-positive price is a hard load error, not
a repair. Dividends are effectively reinvested at the ex-date close: these are total returns, with
no withholding tax modelled.

**Survivorship and hindsight selection.** The 18 symbols were chosen in 2026 because they exist, are
liquid, and are recognisable; every one survived 2008, 2020 and 2022. A strategy tested here is tested on
a set of known winners. There is no point-in-time index membership, no delisted or acquired names, no
reconstruction of what an investor could actually have picked in 2006. This biases results upward by an
unknown amount — not a small correction that can be waved off. `universe_hindsight` fires on every run.

## 2. Execution model

**Signal on close, fill at next open.** Conditions are evaluated on data up to and including bar
`t`'s close; the resulting orders sit in a pending queue drained at bar `t+1`'s open. Same-bar
execution is structurally impossible, not merely avoided. If a symbol has no bar on `t+1`, a queued
exit stays queued and a queued entry is dropped.

**Fill price with slippage**, where `s = slippage_bps / 1e4`:

```
buy_fill  = open * (1 + s)
sell_fill = open * (1 - s)
```

Slippage always moves the price against the trader, on both legs, long and short, and is applied to
the price rather than netted off P&L afterwards so it compounds correctly with sizing. Commission is
charged separately on both legs as `|shares| * fill * commission_bps / 1e4`. Defaults are
`commission_bps = 1.0` and `slippage_bps = 5.0` — 6 bps per leg, 12 bps round trip — and both are
per-run configuration, not constants.

**Intrabar risk exits** are checked on every bar after entry against that bar's `low` and `high`
(reversed for shorts):

1. If a stop and a target are both reachable in one bar, **the stop is assumed to fill first**.
   Daily bars do not reveal the intrabar path, so the pessimistic ordering is chosen — a deliberate
   downward bias.
2. **Gaps.** If the bar opens beyond the stop, the fill is the open, not the stop level:
   `fill = open if open < stop (long) else stop`. A stop is not a guaranteed price. The same rule
   applies to targets that gap through favourably.
3. A trailing stop is measured from the extreme set on bars *strictly before* the current one, so a
   new high made later in the same bar cannot retroactively protect the position.
4. Time exits (`max_holding_days`) and signal exits are queued at the close and filled at the next
   open like any other order — they are not intrabar.

**Forced exits.** A position whose symbol runs out of data mid-window is closed at that symbol's last
close (`data_end`); anything open on the final bar is closed at that close (`end_of_backtest`). Both are
labelled in the trade log, so the strategy's exits stay distinguishable from the harness's.

## 3. Position sizing

Equal weight, long or short, no leverage, no pyramiding.

```
slot_notional = equity_at_open / max_positions
shares        = floor(slot_notional / fill)          # whole shares only
```

`equity_at_open` is cash plus marked-to-market holdings when the queue is drained, so sizing compounds.

- **Cash-constrained.** A long entry is reduced, and if necessary skipped, when
  `notional + commission` exceeds available cash. Equity is never levered.
- **Hard cap** of `max_positions` concurrent positions (default 5), at most one per symbol.
- **Deterministic tie-breaking.** When more entries fire than there are free slots, candidates are taken
  **alphabetically by symbol** and the rest counted as `skipped_signals`. Alphabetical order is arbitrary
  — not a ranking, not claimed neutral — but fixed and documented, so runs reproduce. Slots freed by an
  exit queued at the same close count as available.

## 4. Metrics

Let `E_0 .. E_T` be the daily equity curve, `N = T` the number of daily returns, and
`r_t = E_t / E_{t-1} - 1`. Annualisation uses **252 trading days per year**; standard deviations are
sample (`ddof = 1`).

| Metric | Formula |
|---|---|
| Total return | `E_T / E_0 - 1` |
| CAGR | `(E_T / E_0)^(252/N) - 1`, with `N` in years = `days / 252` |
| Annualised volatility | `stdev(r) * sqrt(252)` |
| Sharpe | `mean(r) / stdev(r) * sqrt(252)` |
| Sortino | `mean(r) / DD * sqrt(252)` with `DD = sqrt(mean(min(r_t, 0)^2))` |
| Max drawdown | `min_t (E_t / max(E_0..E_t) - 1)`, reported **negative** |
| Longest drawdown | consecutive days spent below the previous equity peak |
| Calmar | `CAGR / abs(MaxDD)` |
| Average exposure | `mean(gross_position_value_t / E_t)`, a value of 1.0 meaning fully invested |
| Win rate | `wins / trade_count`, a win being `net_pnl > 0` |
| Profit factor | `sum(net_pnl of wins) / abs(sum(net_pnl of losses))` |
| Average win | `mean(return_pct of wins)`, per trade |
| Average loss | `mean(return_pct of losses)`, per trade, reported **negative** |
| Expectancy | `mean(return_pct)` across all trades — the average trade, sign included |
| Average holding days | `mean(exit_index - entry_index)`, in trading days |
| Total costs | `sum(gross_pnl - net_pnl)` across trades: commission and slippage, both legs |
| Cost drag | `total_costs / abs(sum(gross_pnl))`, the share of gross profit paid away |
| Best / worst day | `max(r)` and `min(r)` |
| Trade count | number of closed trades |

Conventions with more than one accepted definition, stated because the choice moves the number:

- **The risk-free rate is 0%**, so Sharpe and Sortino are excess over zero. A non-zero rate lowers
  every Sharpe here, most sharply after 2022, so these figures are not comparable to published
  Sharpes computed over T-bills.
- **Downside deviation divides by all `N` days**, not only the negative ones. The other convention
  yields a larger Sortino.
- **Zero-variance guard.** If `stdev(r) <= 1e-9` the dispersion is floating-point noise rather than
  risk, and Sharpe and Sortino are reported as `0` rather than as the astronomical number the
  division would produce.
- **Profit factor** is `0` when there are no winning trades and `inf` when there are no losing ones;
  the UI renders the latter as `-`.
- **Average loss and max drawdown are reported with their sign**, so a losing average shows as
  negative rather than as a positive magnitude.
- **Exposure is average gross exposure, not the fraction of days invested.** A strategy holding one
  of five slots reads 0.2, the same as one fully invested on a fifth of days. They are different
  things and the difference matters for the Sharpe argument below.
- **Per-symbol P&L share** uses gross profit as its denominator — the sum of the positive
  per-symbol net P&L totals — which is the same denominator the concentration block uses, so the
  two figures agree on screen. Positive shares sum to 100%; a losing symbol reports a negative
  share, meaning it gave back that fraction of what the winners made.
- All trades are closed by construction — anything still open on the final bar is liquidated at that
  close — so trade statistics contain no open-position P&L.
- **Metrics for a sub-period** (a year, a regime, an IS/OOS half) are computed on the equity slice for
  that window, with trades attributed to the window containing their **exit** date.

### 4.1 Sharpe is inflated by low exposure

Take a strategy invested on a fraction `p` of days with per-invested-day mean `mu` and standard
deviation `sigma`, in costless cash otherwise. Across the full series `mean(r) = p*mu` and
`stdev(r) ~= sqrt(p)*sigma`, so:

```
Sharpe  ~  sqrt(p) * (mu/sigma) * sqrt(252)     # scales with sqrt(exposure)
CAGR    ~  p * 252 * mu                         # scales with exposure
```

Return falls linearly with exposure; Sharpe falls only with its square root. At 20% average exposure,
Sharpe is flattered by `1/sqrt(0.20) ~= 2.2x` relative to the return actually delivered, because idle days
add zero to the numerator *and* zero to the denominator, and the ratio rewards the zeros. Separately, only
about `p*N` observations are informative, so the estimator's standard error is inflated by `1/sqrt(p)`.
ProofTrade prints average exposure beside Sharpe and fires `low_exposure` below 10%.

## 5. Benchmark

Buy-and-hold **SPY** over the identical window with the identical starting capital, entered at the
first available bar and never traded again:

```
benchmark_t = capital * (1 - c) * close_t / close_start,   c = (commission_bps + slippage_bps)/1e4
```

One entry cost is charged so the comparison is not free; there is no exit cost because the position is
never exited. Cost-sensitivity re-runs charge the benchmark the same multiple.

**Why comparing Sharpes is not apples to apples.** SPY is invested 100% of the time. A strategy
invested 20% of the time is scored on a statistic that, per section 4.1, credits it roughly 2.2x for
the 80% of days it holds cash — cash earning the assumed 0% risk-free rate with no opportunity cost
charged. The risk differs in kind too: SPY's drawdown is the market's, while a sparse strategy's is
whatever it happened to hold. A fair comparison would lever the strategy to SPY's volatility, which
needs financing costs and capacity assumptions the MVP does not model. Compare **CAGR** and **max
drawdown** first; read Sharpe only next to time in market.

## 6. Regime classification

Computed from the benchmark alone, deterministically, in priority order — first match wins.

| Label | Condition |
|---|---|
| `crisis` | benchmark drawdown from its running peak `> 20%` |
| `high_vol` | 20-day realised vol in the top quintile of the full-sample distribution, and not `crisis` |
| `bull` | 200-day SMA slope positive, and neither of the above |
| `sideways` | everything else |

```
dd_t    = 1 - B_t / max(B_0..B_t)
vol_t   = stdev(b_{t-19..t}) * sqrt(252)      # b = daily benchmark returns
slope_t = SMA200_t - SMA200_{t-21}            # one month of trading days
```

The quintile cut is the 80th percentile of `vol_t` over the whole sample. **These labels use full-sample
knowledge and are not tradeable**: the threshold is a percentile of a distribution that includes the
future, and a drawdown is measured from a peak only known once it has passed. They answer "where did this
P&L come from", not "what should the strategy do next". Nothing in the engine reads a regime label.

## 7. Validation

**IS/OOS.** Split by trading days at **70/30** (configurable 0.3-0.9), with full metrics for both halves
plus `degradation = clamp(1 - OOS_sharpe / IS_sharpe, 0, 1)` for `IS_sharpe > 0`. Clamping means an OOS
Sharpe above in-sample scores 0 rather than negative, and a sign flip scores 1 rather than something
unbounded. If in-sample Sharpe is `<= 0` the ratio is meaningless and degradation is `null`.

**Walk-forward.** Five sequential, non-overlapping out-of-sample windows spanning the full period
(configurable 2-10). Each fold is backtested **independently and from flat**: fresh capital, no open
positions carried in, no equity carried across a boundary. No position state leaks between folds.
Per fold: return, Sharpe, max drawdown, trade count, plus the fraction of folds with positive net
P&L. Indicator warm-up history is read from bars before the fold start — indicator state may cross a
boundary, position state may not.

**What this actually measures.** The MVP fits no parameters: no optimiser, no grid search, nothing
selected by looking at results, because parameter search is the mechanism that manufactures
overfitting. So IS/OOS here is **not** a test for classical parameter overfitting — there is no
fitting step for it to catch. It measures **regime stability**: whether a fixed rule that worked in
2006-2020 still worked in 2020-2026. That is a real question, and a different one. Parameter
fragility is probed separately in section 8.2. A production system would add, and this one does not
have:

- **Nested cross-validation** over a real parameter search, holding out the selection step itself.
- **Deflated Sharpe ratio**, adjusting the observed Sharpe for the number of configurations tried
  and the non-normality of returns.
- **White's Reality Check / Hansen's SPA test**, testing a best-of-N strategy against the null that
  none of them beats the benchmark.
- **Multiple-testing correction** across a session. ProofTrade scores each run in isolation; a user
  who runs 40 variants and keeps the best has performed a search no single report can see.

## 8. Robustness probes

**Cost sensitivity.** The full backtest re-runs at **0x, 1x, 2x, 3x and 5x** configured commission
and slippage, reporting CAGR, Sharpe and trade count at each level. Break-even cost is **a scan, not
a solve**: CAGR is evaluated at those five discrete levels, the adjacent pair bracketing a sign
change is found, and the crossing is **linearly interpolated** between them in bps of per-leg cost
(`(commission_bps + slippage_bps) * multiple`). CAGR is not linear in cost — trade selection is
unchanged but compounding is not — so the figure is an approximation limited by the grid's
coarseness. Still positive at 5x is reported as `> 5x configured` rather than extrapolated; negative
at 0x means break-even is 0 and the strategy loses money before any costs.

**Parameter neighbourhood.** Every numeric parameter reachable in the DSL — indicator periods, MACD
fast/slow/signal, Bollinger `k`, every constant threshold, and every risk rule (stop, target, trailing
stop, max holding days) — is enumerated in a fixed order and perturbed by **-20%, -10%, +10%, +20%**, one
at a time. Integer parameters are rounded, and a perturbation that rounds back onto the original value is
skipped rather than counted as a neighbour, as is one that fails DSL validation (period out of range, MACD
fast `>=` slow). The neighbourhood is **capped at 8 neighbours** in enumeration order, so runtime is
bounded and the set is identical on every re-run. Reported: base Sharpe, neighbourhood **median** Sharpe,
min and max, and the fraction of neighbours with positive net P&L. A strategy whose Sharpe collapses one
step from the exact numbers typed is fragile whether or not anyone optimised deliberately — a number read
off a forum post has already been selected by someone.

## 9. Evidence score

`score = sum(weight_i * subscore_i)` over six components whose weights sum to 100. Every component
reports its raw measurement, its sub-score and its point contribution, so the total can be rebuilt by
hand. `ramp(x, lo, hi) = clamp((x - lo)/(hi - lo), 0, 1)`; a ramp written with `lo > hi` descends.

| # | Component | Weight | Measurement -> sub-score |
|---|---|---:|---|
| 1 | Sample size | 20 | `0` at `trades <= 10`, otherwise `min(1, log10(trades/10) / log10(40))` |
| 2 | OOS consistency | 20 | `0.67 * base + 0.33 * folds_positive_fraction` |
| 3 | Breadth | 15 | `size_factor * (0.6 * spread + 0.4 * focus)` |
| 4 | Time consistency | 15 | `0.6 * ramp(years_positive_fraction, 0.25, 0.7) + 0.4 * regimes_positive_fraction` |
| 5 | Cost robustness | 15 | `min(1, ramp(sharpe_3x / sharpe_1x, 0, 0.8) + 0.1 * survives_5x)` |
| 6 | Parameter robustness | 15 | `0.5 * neighbours_profitable_fraction + 0.5 * stability` |

**Component 1 is log-scaled** because the tenth trade tells you far more than the hundredth. Ten
trades or fewer scores zero; 400 trades scores one.

**Component 2.** `base` is `ramp(OOS_sharpe / IS_sharpe, 0, 1)`, with two special cases:
- In-sample Sharpe `<= 0.05`: the ratio carries no information, so `base = 0.35` and the component is
  labelled *not measurable* — scored as inconclusive, not as a pass.
- Retention `>= 2.0`: out-of-sample beating in-sample several times over is **not** a bonus. It means
  the halves do not describe the same process, so `base = 0.6`. Whichever half is unrepresentative,
  one of them is.

**Component 3.** `spread = ramp(symbols_profitable_fraction, 0.2, 0.6)`,
`focus = ramp(top_symbol_profit_share_pct, 70, 25)`, and
`size_factor = ramp(universe_size, 1, 8)`. The size factor is the anti-cherry-picking term: "both my
two hand-picked stocks were profitable" is not breadth evidence however good the ratio looks, so a
2-symbol universe caps this component at about 14% of its weight and a 1-symbol universe at zero.
`top_symbol_profit_share` is measured against **gross** profit (the sum of positive symbol P&L), so a
single huge winner cannot be masked by netting it against losers.

**Component 5.** Retention is `sharpe_at_3x / sharpe_at_1x`; full marks at 80% retained, with a 0.1
bonus for still being profitable at 5x costs. If the base Sharpe is `<= 0.05` the ratio is meaningless
and the component scores 0.2.

**Component 6.** `stability = ramp(base_sharpe / neighbourhood_median_sharpe, 2.5, 1.0)` — a ratio near
1.0 is a plateau, above 2.5 the chosen parameters are suspiciously special. When the neighbourhood
median Sharpe is `<= 0.05` but the base works, the fragility ratio is capped at 9.99 rather than
dividing by near-zero. A strategy with no numeric parameters to perturb scores a neutral 0.5 and says so.

**Grades:** A `>= 80` · B `65-79` · C `50-64` · D `35-49` · F `< 35`.

**What the score is not.** It is a **confidence-in-the-evidence** score: how much to trust what this
backtest is telling you, not whether the strategy will make money. The two come apart in both
directions. A strategy that loses money across 400 trades, 18 symbols, 20 years, every regime and 5x
costs can score highly — that is the report saying *the evidence that this is bad is trustworthy*. A
spectacular equity curve built on 11 trades in one symbol during one bull market scores F, and should.
Read the score with the metrics, never instead of them. The seeded demo strategies are deliberately
spread across the grade range and none of them was tuned to land on a particular grade.

## 10. Warnings

Every warning is evaluated on every run and reported with its measured value beside its threshold.
`info` warnings are unconditional facts about the method; `critical` and `high` mean the headline
numbers probably do not measure what they appear to. Warnings sort by severity, then by id.

| ID | Trigger | Severity | What to do |
|---|---|---|---|
| `synthetic_data` | the snapshot is generated, not vendored | critical | Nothing here is a statement about real markets. Run `make data-real` before drawing any conclusion. |
| `low_trade_count` | `trades < 30` (critical below 10) | high | Widen the universe or lengthen the window. Below 30 trades the Sharpe standard error exceeds most Sharpes worth having. |
| `concentrated_returns_symbol` | top symbol `> 50%` of gross profit | high | One lucky position dressed as a system. Drop that symbol and re-run. |
| `concentrated_returns_trades` | top 5 trades `> 50%` of gross profit, with `>= 10` trades | high | Remove the five best trades and look again. |
| `concentrated_returns_days` | top 10 up-days `> 60%` of all up-day return, over `> 250` days | medium | Check those days are not data artefacts. |
| `cost_sensitive` | Sharpe at 3x costs `< 50%` of base | high | The edge is inside the spread. Assume real fills are worse than modelled. |
| `thin_cost_margin` | break-even `< 50 bps` round trip (high below 25) | medium/high | Real fills on real size routinely exceed this. |
| `oos_degradation` | degradation `> 40%` (high above 70%) | medium/high | The rule did not survive the regime change. |
| `fragile_parameters` | base Sharpe `> 1.5x` neighbourhood median (high above 2.5x) | medium/high | The result depends on the exact numbers you typed. Prefer a neighbour that is merely good over a peak that is exceptional. |
| `narrow_universe` | `< 5` symbols (high at `<= 2`) | medium/high | A rule validated on a couple of hand-picked names is a statement about those names. |
| `short_history` | `< 3` calendar years contain trades | high | You have tested one market, not markets. |
| `single_regime` | `< 2` regimes contain trades | medium | Untested is not the same as safe. |
| `low_exposure` | average exposure `< 10%` | medium | See section 4.1: Sharpe is flattered by roughly `1/sqrt(exposure)`. Compare CAGR instead. |
| `severe_drawdown` | max drawdown worse than `-35%` | high | Most people abandon a system well before a 35% drawdown ends. |
| `thin_profit_factor` | profit factor `< 1.10` with `>= 20` trades | medium | A small worsening in fills flips this negative. |
| `underperforms_benchmark` | strategy CAGR below buy-and-hold CAGR (high if `> 3` points behind) | medium/high | Whatever the risk-adjusted figures say, the simplest alternative made more money. That is the bar. |
| `position_cap_binding` | dropped entry signals `> 20%` of trades taken | medium | The engine kept the alphabetically first symbols and dropped the rest. That tie-break is arbitrary; a different one gives a different result. |
| `universe_hindsight` | always | info | The 18 symbols were picked knowing who survived. Results are biased upward by an unknown amount. |
| `single_data_source` | always | info | One vendor, one snapshot, no cross-vendor reconciliation. Bad ticks and adjustment errors are inherited silently. |

## 11. What this does not prove

**No transaction-cost model survives contact with real liquidity.** Slippage here is a fixed number of
basis points on an open. Real slippage depends on order size against displayed liquidity, time of day, the
spread at the moment of the fill, and whether anyone else is trading the same signal — it is not constant,
not symmetric, and worst exactly when the strategy most wants to trade. Market impact and capacity are not
modelled at all: results are identical for a $100k account and a $100m one, which cannot be true.

**Daily bars hide the intrabar path.** When a bar's low touches the stop and its high touches the
target, the engine assumes the stop filled first. That is a guess — the pessimistic one,
deliberately — but a strategy whose results turn on how that guess resolves has not been tested,
only approximated.

**One snapshot is one sample.** Twenty years across 18 symbols feels like a lot of data. It is one
realisation of one market history, containing one 2008, one 2020 and one 2022. Statistics computed on
overlapping, autocorrelated, regime-switching returns from a single path carry far less information than
their sample sizes imply.

**A backtest is a lower bound on how wrong you can be, not an estimate of future return.** Everything
above measures how a strategy would have behaved under assumptions chosen to be conservative but which
remain assumptions, on a universe chosen with hindsight, along a path that will not repeat. A good
ProofTrade report means the strategy failed to break under the specific attacks in sections 7 and 8. It
says nothing about the attacks nobody ran.
