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
| CAGR | `(E_T / E_0)^(252/N) - 1` |
| Annualised volatility | `stdev(r) * sqrt(252)` |
| Sharpe | `mean(r) / stdev(r) * sqrt(252)` |
| Sortino | `mean(r) / DD * sqrt(252)` with `DD = sqrt(mean(min(r_t, 0)^2))` |
| Max drawdown | `max_t (1 - E_t / max(E_0..E_t))`, reported positive |
| Calmar | `CAGR / MaxDD` |
| Time in market | `days_with_a_position / N` |
| Average gross exposure | `mean(gross_position_value_t / E_t)` |
| Turnover (annual) | `sum_trades(entry_notional + exit_notional) / (2 * mean(E) * N/252)` |
| Win rate | `wins / trade_count`, a win being `net_pnl > 0` |
| Profit factor | `sum(net_pnl of wins) / abs(sum(net_pnl of losses))` |
| Average win | `mean(net_pnl of wins)` |
| Average loss | `mean(abs(net_pnl) of losses)` |
| Expectancy | `win_rate * avg_win - (1 - win_rate) * avg_loss` |
| Average holding days | `mean(exit_index - entry_index)`, in trading days |
| Trade count | number of closed trades |

Conventions with more than one accepted definition: **the risk-free rate is 0%**, so Sharpe and Sortino
are excess over zero — a non-zero rf lowers every Sharpe here, most sharply after 2022, so these figures
are not comparable to published Sharpes computed over T-bills. **Downside deviation** divides by all `N`
days, not only the negative ones; the other convention yields a larger Sortino. **Profit factor** with no
losing trades is `null`, not infinity. **Expectancy** is identically the mean net P&L per trade, shown
beside a percentage form (mean per-trade `return_pct`) since dollar expectancy grows with equity. All
trades are closed by construction, so trade statistics contain no open-position P&L.

### 4.1 Sharpe is inflated by low exposure

Take a strategy invested on a fraction `p` of days with per-invested-day mean `mu` and standard
deviation `sigma`, in costless cash otherwise. Across the full series `mean(r) = p*mu` and
`stdev(r) ~= sqrt(p)*sigma`, so:

```
Sharpe  ~  sqrt(p) * (mu/sigma) * sqrt(252)     # scales with sqrt(exposure)
CAGR    ~  p * 252 * mu                         # scales with exposure
```

Return falls linearly with exposure; Sharpe falls only with its square root. At 20% time in market,
Sharpe is flattered by `1/sqrt(0.20) ~= 2.2x` relative to the return actually delivered, because idle days
add zero to the numerator *and* zero to the denominator, and the ratio rewards the zeros. Separately, only
about `p*N` observations are informative, so the estimator's standard error is inflated by `1/sqrt(p)`.
ProofTrade prints time in market beside Sharpe and fires `low_exposure` below 10%.

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

`score = 100 * sum(weight_i * subscore_i) / sum(weight_i)`, with every component's raw measurement,
sub-score and contribution shown in the UI. `ramp(x, lo, hi) = clamp((x - lo)/(hi - lo), 0, 1)`; a
ramp written with `lo > hi` descends.

| # | Component | Weight | Measurement -> sub-score |
|---|---|---:|---|
| 1 | Sample size | 20 | `ramp(trade_count, 0, 100)` — 0 trades -> 0, 100+ -> 1 |
| 2 | OOS consistency | 20 | `0.7 * ramp(OOS_sharpe / IS_sharpe, 0, 1) + 0.3 * folds_profitable_fraction` |
| 3 | Breadth | 15 | `0.5 * ramp(symbols_profitable_fraction, 0.2, 0.6) + 0.5 * ramp(top_symbol_share, 0.7, 0.3)` |
| 4 | Time consistency | 15 | `0.6 * ramp(years_profitable_fraction, 0.3, 0.7) + 0.4 * regimes_profitable_fraction` |
| 5 | Cost robustness | 15 | `0.7 * ramp(sharpe_3x / sharpe_1x, 0, 1) + 0.3 * ramp(breakeven_bps, 0, 100)` |
| 6 | Parameter robustness | 15 | `0.6 * ramp(neighbourhood_median_sharpe / base_sharpe, 0.3, 0.9) + 0.4 * neighbours_profitable_fraction` |

- `top_symbol_share = max_symbol_net_pnl / sum(positive symbol net_pnl)`, which stays well-defined
  when total net P&L is negative. 70% or more of profit in one symbol scores 0; 30% or less scores 1.
- Components 2, 5 and 6 are ratios against a base Sharpe. If the base (or in-sample) Sharpe is
  `<= 0.05` the ratio carries no information and the sub-score is **0**: a strategy with no
  measurable edge earns no credit for retaining it.
- Component 4 counts only calendar years with `>= 60` trading days in the window and regimes with
  `>= 20` days, so a stub period at the window edge cannot swing a fraction.
- If the neighbourhood is empty, component 6 scores 1.0 and the report says so. The DSL requires an
  exit, so every valid strategy has a numeric parameter and this is near-unreachable.

**Grades:** A `>= 80` · B `65-79` · C `50-64` · D `35-49` · F `< 35`.

**What the score is not.** It is a **confidence-in-the-evidence** score: how much to trust what this
backtest is telling you, not whether the strategy will make money. The two come apart in both directions.
A strategy that loses money across 400 trades, 18 symbols, 20 years, every regime and 5x costs can score
highly — it is saying *the evidence that this is bad is trustworthy*. A spectacular equity curve built on
11 trades in one symbol during one bull market scores F, and should. Read the score with the metrics,
never instead of them.

## 10. Warnings

Every warning is evaluated on every run and reported with its trigger value beside its threshold.
`info` warnings are unconditional facts about the method; `high` warnings mean the headline numbers
probably do not measure what they appear to.

| ID | Trigger | Severity | What to do |
|---|---|---|---|
| `low_trade_count` | `trade_count < 30` | high | Widen the universe or lengthen the window. Below 30 trades the Sharpe standard error exceeds most Sharpes worth having. |
| `concentrated_returns` | top symbol `> 50%` of net profit, or top 5 trades `> 50%` | high | Re-read the per-symbol heatmap. You may have one lucky position dressed as a system. Drop the top symbol and re-run. |
| `cost_sensitive` | Sharpe at 3x costs `< 50%` of base, or CAGR negative below 50 bps per leg | high | The edge is inside the spread. Assume real fills are worse than modelled and treat it as untradeable at retail costs. |
| `oos_degradation` | degradation `> 40%` | high | The rule did not survive the regime change. Check whether the in-sample half rests on one dominant episode. |
| `short_history` | trades span `< 3 years`, or `< 2` regimes covered | medium | You have tested one market, not markets. Extend the window before concluding anything. |
| `fragile_parameters` | base Sharpe `> 1.5x` neighbourhood median | high | The result depends on the exact numbers you typed. Prefer a neighbour that is merely good over a peak that is exceptional. |
| `low_exposure` | time in market `< 10%` | medium | See section 4.1: Sharpe is flattered by about `1/sqrt(exposure)`. Compare CAGR instead. |
| `severe_drawdown` | max drawdown `> 35%` | medium | Sizing, not the signal, is the problem. Most people abandon a system well before a 35% drawdown ends. |
| `universe_hindsight` | always | info | The 18 symbols were picked in 2026 knowing who survived. Results are biased upward by an unknown amount. |
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
