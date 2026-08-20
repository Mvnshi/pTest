"""Engine correctness: no look-ahead, exact fills, deterministic output."""

from __future__ import annotations

import numpy as np
import pytest

from app.dsl import (
    BacktestConfig,
    Condition,
    ConditionGroup,
    ConstantOperand,
    IndicatorOperand,
)
from app.engine import run_backtest
from tests.conftest import bar, business_days, make_strategy, write_bars


def test_signal_on_close_fills_at_next_open(toy_store, base_config):
    """The dip closes on day 20; the fill must be day 21's open, not day 20's."""
    strategy = make_strategy(["TOY"], max_holding_days=1)
    result = run_backtest(strategy, base_config, toy_store)

    assert len(result.trades) >= 1
    trade = result.trades[0]
    dates = business_days("2020-01-01", 60)
    assert trade.entry_date == dates[21], "entry must be the bar AFTER the signal"
    assert trade.entry_price == pytest.approx(95.0), "entry must be day 21's OPEN"


def test_no_lookahead_shifting_the_series_shifts_the_trades(tmp_path, base_config):
    """Insert a flat bar at the front; every trade date must move by exactly one bar."""
    from app.data import BarStore

    dates = business_days("2020-01-01", 80)
    rows = [bar(d, 100, 101, 99, 100) for d in dates]
    for i in (30, 31, 32):
        rows[i] = bar(dates[i], 94, 95, 93, 94)

    write_bars(tmp_path / "a", "TOY", rows)
    shifted = [bar(dates[0], 100, 101, 99, 100)] + [
        bar(d, r["open"], r["high"], r["low"], r["close"])
        for d, r in zip(dates[1:], rows[:-1])
    ]
    write_bars(tmp_path / "b", "TOY", shifted)

    strategy = make_strategy(["TOY"], max_holding_days=2)
    first = run_backtest(strategy, base_config, BarStore(tmp_path / "a"))
    second = run_backtest(strategy, base_config, BarStore(tmp_path / "b"))

    assert len(first.trades) == len(second.trades) >= 1
    index = {d: i for i, d in enumerate(dates)}
    for a, b in zip(first.trades, second.trades):
        assert index[b.entry_date] == index[a.entry_date] + 1
        assert b.entry_price == pytest.approx(a.entry_price)


def test_determinism_repeat_runs_are_identical(demo_store):
    strategy = make_strategy(
        demo_store.symbols,
        entry=[Condition(left=IndicatorOperand(name="rsi"), op="<",
                         right=ConstantOperand(value=35))],
        max_holding_days=10,
        stop_loss_pct=7,
    )
    config = BacktestConfig(run_robustness=False)
    a = run_backtest(strategy, config, demo_store)
    b = run_backtest(strategy, config, demo_store)
    assert np.array_equal(a.equity, b.equity)
    assert [t.as_dict() for t in a.trades] == [t.as_dict() for t in b.trades]


def test_costs_reduce_returns_monotonically_without_price_level_risk_rules(demo_store):
    """With no stop or target, cost is a pure tax and must never help."""
    strategy = make_strategy(
        demo_store.symbols,
        entry=[Condition(left=IndicatorOperand(name="rsi"), op="<",
                         right=ConstantOperand(value=35))],
        max_holding_days=10,
    )
    finals = []
    for multiple in (0.0, 1.0, 2.0, 5.0, 10.0):
        result = run_backtest(
            strategy, BacktestConfig(run_robustness=False), demo_store, cost_multiple=multiple
        )
        finals.append(result.equity[-1])
    assert finals == sorted(finals, reverse=True), "higher costs must never help"
    assert finals[0] > finals[-1]


def test_costs_can_be_non_monotonic_when_a_stop_is_anchored_to_the_fill(demo_store):
    """A documented consequence of a realistic risk model, pinned so it stays known.

    Stop and target levels are set from the actual (slipped) fill price. Changing the
    cost level therefore moves those levels, a different set of trades gets stopped
    out, and the resulting path can be better at higher cost. The engine reports this
    rather than smoothing it away, so the report cannot imply an invariant it does not
    have.
    """
    strategy = make_strategy(
        ["AAPL"], max_positions=1,
        entry=[Condition(left=IndicatorOperand(name="rsi"), op="<",
                         right=ConstantOperand(value=30))],
        stop_loss_pct=3, max_holding_days=30,
    )
    config = BacktestConfig(commission_bps=1, slippage_bps=5, run_robustness=False)
    finals = [
        run_backtest(strategy, config, demo_store, cost_multiple=m).equity[-1]
        for m in (0.0, 1.0, 2.0, 3.0, 5.0)
    ]
    assert finals != sorted(finals, reverse=True), (
        "this fixture is the known non-monotonic case; if it has become monotonic the "
        "risk-level anchoring changed and the docs need revisiting"
    )
    # The trend is still unmistakably downward across the full range.
    assert finals[-1] < finals[0]


def test_stop_loss_fills_at_the_stop_level(toy_store, base_config):
    """Entry at 95 on day 21 with a 5% stop -> stop at 90.25, and day 21 lows at 88."""
    strategy = make_strategy(["TOY"], stop_loss_pct=5)
    result = run_backtest(strategy, base_config, toy_store)
    trade = result.trades[0]
    assert trade.exit_reason == "stop_loss"
    assert trade.exit_price == pytest.approx(95.0 * 0.95)
    assert trade.exit_date == trade.entry_date, "the stop is reachable on the entry bar"


def test_gap_through_the_stop_fills_at_the_open(tmp_path, base_config):
    from app.data import BarStore

    dates = business_days("2020-01-01", 40)
    rows = [bar(d, 100, 101, 99, 100) for d in dates]
    rows[20] = bar(dates[20], 100, 100, 94, 94)      # signal bar
    rows[21] = bar(dates[21], 94, 94, 70, 72)        # opens at 94, then collapses
    rows[22] = bar(dates[22], 60, 61, 50, 55)        # gaps far below the stop
    write_bars(tmp_path / "bars", "TOY", rows)

    # Entry at 94 on day 21, 10% stop -> 84.6. Day 21 already trades down to 70, so the
    # stop is hit intrabar on the entry bar and fills at the stop level, not the open.
    strategy = make_strategy(["TOY"], stop_loss_pct=10)
    result = run_backtest(strategy, base_config, BarStore(tmp_path / "bars"))
    trade = result.trades[0]
    assert trade.exit_price == pytest.approx(94.0 * 0.9)

    # With a 1% stop the entry bar opens at 94 and the stop sits at 93.06, still inside
    # the bar's range, so it fills at the stop.
    tight = make_strategy(["TOY"], stop_loss_pct=1)
    tight_trade = run_backtest(tight, base_config, BarStore(tmp_path / "bars")).trades[0]
    assert tight_trade.exit_price == pytest.approx(94.0 * 0.99)


def test_stop_wins_when_stop_and_target_are_both_reachable(toy_store, base_config):
    """Day 22 ranges 91-130: both a 5% stop and a 20% target are inside it."""
    strategy = make_strategy(["TOY"], stop_loss_pct=5, take_profit_pct=20)
    result = run_backtest(strategy, base_config, toy_store)
    assert result.trades[0].exit_reason == "stop_loss"


def test_take_profit_fills_at_the_target(tmp_path, base_config):
    from app.data import BarStore

    dates = business_days("2020-01-01", 40)
    rows = [bar(d, 100, 101, 99, 100) for d in dates]
    rows[20] = bar(dates[20], 100, 100, 94, 94)
    rows[21] = bar(dates[21], 94, 96, 93.5, 95)
    rows[22] = bar(dates[22], 95, 120, 94.9, 118)
    write_bars(tmp_path / "bars", "TOY", rows)
    strategy = make_strategy(["TOY"], take_profit_pct=10)
    trade = run_backtest(strategy, base_config, BarStore(tmp_path / "bars")).trades[0]
    assert trade.exit_reason == "take_profit"
    assert trade.exit_price == pytest.approx(94.0 * 1.10)


def test_time_exit_closes_at_the_open_after_the_limit(toy_store, base_config):
    strategy = make_strategy(["TOY"], max_holding_days=3)
    trade = run_backtest(strategy, base_config, toy_store).trades[0]
    assert trade.exit_reason == "time_exit"
    # Signal at the close of holding day 3, filled at the next open: 4 bars held.
    assert trade.holding_days == 4


def test_slippage_moves_the_fill_price_against_the_trader(toy_store):
    config = BacktestConfig(commission_bps=0, slippage_bps=100, run_robustness=False)
    free = BacktestConfig(commission_bps=0, slippage_bps=0, run_robustness=False)
    strategy = make_strategy(["TOY"], max_holding_days=1)
    slipped = run_backtest(strategy, config, toy_store).trades[0]
    clean = run_backtest(strategy, free, toy_store).trades[0]
    assert slipped.entry_price == pytest.approx(clean.entry_price * 1.01)
    assert slipped.exit_price == pytest.approx(clean.exit_price * 0.99)
    # A worse fill buys fewer shares, so compare per-share P&L rather than totals.
    assert slipped.gross_pnl / slipped.shares == pytest.approx(clean.gross_pnl / clean.shares)
    assert slipped.net_pnl / slipped.shares < clean.net_pnl / clean.shares
    assert slipped.costs == pytest.approx(slipped.gross_pnl - slipped.net_pnl)


def test_commission_is_charged_on_both_legs(toy_store):
    config = BacktestConfig(commission_bps=50, slippage_bps=0, run_robustness=False)
    strategy = make_strategy(["TOY"], max_holding_days=1)
    trade = run_backtest(strategy, config, toy_store).trades[0]
    expected = trade.shares * trade.entry_price * 0.005 + trade.shares * trade.exit_price * 0.005
    assert trade.costs == pytest.approx(expected, rel=1e-9)


def test_position_cap_is_respected(demo_store):
    strategy = make_strategy(
        demo_store.symbols,
        entry=[Condition(left=IndicatorOperand(name="rsi"), op="<",
                         right=ConstantOperand(value=45))],
        max_positions=3,
        max_holding_days=15,
    )
    result = run_backtest(strategy, BacktestConfig(run_robustness=False), demo_store)
    assert result.position_count.max() <= 3
    assert result.skipped_signals > 0, "a tight cap on a loose rule must drop signals"


def test_short_direction_pnl_is_the_mirror_of_a_long(tmp_path, base_config):
    """A monotonic decline: check the short's arithmetic exactly, not just its sign."""
    from app.data import BarStore

    dates = business_days("2020-01-01", 40)
    rows = [bar(d, 100, 101, 99, 100) for d in dates]
    # Day 20 closes at 94 (the signal). Days 21+ step down 2 a day and stay down.
    rows[20] = bar(dates[20], 100, 100, 94, 94)
    price = 94.0
    for i in range(21, 40):
        price = max(price - 2, 60)
        rows[i] = bar(dates[i], round(price + 1, 4), round(price + 1.5, 4),
                      round(price - 0.5, 4), round(price, 4))
    write_bars(tmp_path / "bars", "TOY", rows)

    strategy = make_strategy(
        ["TOY"], direction="short",
        entry=[Condition(left=IndicatorOperand(name="close"), op="<",
                         right=ConstantOperand(value=95))],
        max_holding_days=5,
    )
    result = run_backtest(strategy, base_config, BarStore(tmp_path / "bars"))
    assert result.trades, "the short should have been entered"
    trade = result.trades[0]
    assert trade.direction == "short"
    assert trade.entry_date == dates[21]
    assert trade.entry_price == pytest.approx(93.0), "short fills at day 21's open"
    # The time exit is signalled at the close of the 5th holding bar (day 26) and,
    # like every other order, fills at the NEXT open - day 27.
    assert trade.exit_date == dates[27]
    assert trade.holding_days == 6
    assert trade.exit_price == pytest.approx(float(rows[27]["open"]))
    expected = (trade.entry_price - trade.exit_price) * trade.shares
    assert trade.net_pnl == pytest.approx(expected), "short P&L is entry minus exit"
    assert trade.net_pnl > 0
    assert result.equity[-1] > base_config.initial_capital


def test_short_loses_when_price_rises(tmp_path, base_config):
    from app.data import BarStore

    dates = business_days("2020-01-01", 40)
    rows = [bar(d, 100, 101, 99, 100) for d in dates]
    rows[20] = bar(dates[20], 100, 100, 94, 94)
    for i in range(21, 40):
        price = 94 + (i - 20) * 2
        rows[i] = bar(dates[i], price - 1, price + 1, price - 2, price)
    write_bars(tmp_path / "bars", "TOY", rows)
    strategy = make_strategy(
        ["TOY"], direction="short",
        entry=[Condition(left=IndicatorOperand(name="close"), op="<",
                         right=ConstantOperand(value=95))],
        max_holding_days=5,
    )
    result = run_backtest(strategy, base_config, BarStore(tmp_path / "bars"))
    assert result.trades[0].net_pnl < 0
    assert result.equity[-1] < base_config.initial_capital


def test_no_trades_produces_a_flat_curve(toy_store, base_config):
    impossible = make_strategy(
        ["TOY"],
        entry=[Condition(left=IndicatorOperand(name="close"), op="<",
                         right=ConstantOperand(value=1))],
        max_holding_days=5,
    )
    result = run_backtest(impossible, base_config, toy_store)
    assert result.trades == []
    assert result.equity[0] == result.equity[-1] == base_config.initial_capital


def test_warmup_suppresses_signals_before_indicators_are_defined(ramp_store, base_config):
    strategy = make_strategy(
        ["RAMP"],
        entry=[Condition(left=IndicatorOperand(name="close"), op=">",
                         right=IndicatorOperand(name="sma", period=200))],
        max_holding_days=5,
    )
    result = run_backtest(strategy, base_config, ramp_store)
    assert result.trades, "a permanent uptrend should eventually trade"
    first_index = result.dates.index(result.trades[0].entry_date)
    assert first_index >= 200, "no trade may happen before the SMA(200) window is full"


def test_open_position_is_liquidated_on_the_final_bar(ramp_store, base_config):
    strategy = make_strategy(
        ["RAMP"],
        entry=[Condition(left=IndicatorOperand(name="close"), op=">",
                         right=IndicatorOperand(name="sma", period=20))],
        # An exit rule that can never fire on a rising series: the DSL requires some
        # exit, but the position must still be open when the data runs out.
        exit_=[Condition(left=IndicatorOperand(name="close"), op="<",
                         right=ConstantOperand(value=0.01))],
    )
    result = run_backtest(strategy, base_config, ramp_store)
    assert result.trades[-1].exit_reason == "end_of_backtest"
    assert result.position_count[-1] == 0


def test_position_sizing_cannot_see_the_close_of_the_bar_it_fills_on(tmp_path, base_config):
    """Two worlds identical up to the entry bar's open, differing only in that bar's
    close. The size of an order filled at that open must be the same in both."""
    from app.data import BarStore

    dates = business_days("2020-01-01", 40)

    def build(entry_bar_close: float) -> BarStore:
        rows_a, rows_b = [], []
        for i, d in enumerate(dates):
            # Bar 20 is the first bar past the strategy's warm-up window, so nothing
            # can fire before it. AAA fires there and is held for the rest of the run;
            # its close on bar 26 - the bar BBB's order fills on - is the ONLY thing
            # that differs between the two worlds, and its wide range keeps 200 inside
            # the bar.
            close_a = 90.0 if i == 20 else 100.0
            if i == 26:
                close_a = entry_bar_close
            rows_a.append(bar(d, 100, 210, 90, close_a))
            # BBB fires on bar 25's close, so its order fills at bar 26's open.
            rows_b.append(bar(d, 100, 101, 89, 94.0 if i == 25 else 100.0))
        directory = tmp_path / f"w{int(entry_bar_close)}"
        write_bars(directory, "AAA", rows_a)
        write_bars(directory, "BBB", rows_b)
        return BarStore(directory)

    # Four slots, not two: with two the cash-affordability cap happens to bind and
    # would mask the leak, so the fixture would pass whether or not the bug is present.
    strategy = make_strategy(
        ["AAA", "BBB"], max_positions=4,
        entry=[Condition(left=IndicatorOperand(name="close"), op="<",
                         right=ConstantOperand(value=95))],
        exit_=[Condition(left=IndicatorOperand(name="close"), op="<",
                         right=ConstantOperand(value=0.01))],
    )
    low = run_backtest(strategy, base_config, build(100.0))
    high = run_backtest(strategy, base_config, build(200.0))

    def entries(result):
        return {(t.symbol, t.entry_date): t.shares for t in result.trades}

    assert any(sym == "BBB" for sym, _ in entries(low)), "fixture did not fill a second entry"
    assert entries(low) == entries(high), (
        "an order filled at the open changed size when a later close on that same bar "
        "changed - the sizing step is reading the future"
    )


def test_excursions_never_exceed_the_price_the_trade_actually_got(demo_store):
    """MAE/MFE must bracket the realised return: a trade cannot report a favourable
    excursion smaller than the gain it booked, or an adverse one shallower than its loss."""
    strategy = make_strategy(
        demo_store.symbols,
        entry=[Condition(left=IndicatorOperand(name="rsi"), op="<",
                         right=ConstantOperand(value=30))],
        stop_loss_pct=8, take_profit_pct=7, max_holding_days=20,
    )
    result = run_backtest(
        strategy, BacktestConfig(commission_bps=0, slippage_bps=0, run_robustness=False),
        demo_store,
    )
    assert result.trades
    for trade in result.trades:
        realised = (trade.exit_price - trade.entry_price) / trade.entry_price * 100.0
        assert trade.mfe_pct >= realised - 1e-6, f"{trade.symbol} {trade.entry_date}: MFE below its own gain"
        assert trade.mae_pct <= realised + 1e-6, f"{trade.symbol} {trade.entry_date}: MAE above its own loss"

    # On a level exit the excursion is exactly the price the trade got - the target or
    # stop level, or the open when the bar gapped through it - and never the rest of
    # that bar's range, which the position was assumed to have already left.
    level_exits = [t for t in result.trades if t.exit_reason in ("take_profit", "stop_loss")]
    assert level_exits, "fixture produced no level exits"
    for trade in level_exits:
        realised = (trade.exit_price - trade.entry_price) / trade.entry_price * 100.0
        booked = trade.mfe_pct if trade.exit_reason == "take_profit" else trade.mae_pct
        assert booked == pytest.approx(realised, abs=1e-6)


def test_a_symbol_running_past_the_window_is_not_treated_as_delisted(demo_store):
    """`data_end` must mean the snapshot ended, not that the window did."""
    import datetime as dt

    strategy = make_strategy(
        demo_store.symbols,
        entry=[Condition(left=IndicatorOperand(name="rsi"), op="<",
                         right=ConstantOperand(value=40))],
        max_holding_days=30,
    )
    windowed = run_backtest(
        strategy,
        BacktestConfig(start=dt.date(2010, 1, 1), end=dt.date(2015, 1, 1), run_robustness=False),
        demo_store,
    )
    reasons = {t.exit_reason for t in windowed.trades}
    assert "data_end" not in reasons, "the window edge must not look like a delisting"


def test_an_account_that_reaches_zero_stops_trading(tmp_path, base_config):
    """An unconstrained short into a vertical rally must not compound from a negative base."""
    from app.data import BarStore

    dates = business_days("2020-01-01", 60)
    rows = [bar(d, 100, 101, 99, 100) for d in dates]
    price = 100.0
    for i in range(20, 60):
        price *= 1.35  # a relentless squeeze
        rows[i] = bar(dates[i], round(price * 0.99, 4), round(price * 1.02, 4),
                      round(price * 0.98, 4), round(price, 4))
    write_bars(tmp_path / "bars", "SQZ", rows)

    strategy = make_strategy(
        ["SQZ"], direction="short", max_positions=1,
        entry=[Condition(left=IndicatorOperand(name="close"), op=">=",
                         right=ConstantOperand(value=100))],
        exit_=[Condition(left=IndicatorOperand(name="close"), op="<",
                         right=ConstantOperand(value=0.01))],
    )
    result = run_backtest(strategy, base_config, BarStore(tmp_path / "bars"))
    if result.ruined:
        assert result.ruin_date in result.dates
        index = result.dates.index(result.ruin_date)
        # Frozen from the moment of ruin: no further trading, no phantom recovery.
        assert result.position_count[index:].max() == 0
        assert len(set(result.equity[index:].tolist())) == 1
        assert result.equity[-1] <= 0
    else:
        assert result.equity.min() > 0, "equity went through zero without being flagged"
