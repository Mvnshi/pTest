"""The DSL is the security boundary; these tests are about what it refuses."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.dsl import (
    BacktestConfig,
    Condition,
    ConditionGroup,
    ConstantOperand,
    IndicatorOperand,
    Strategy,
    numeric_parameters,
    perturb,
    stable_hash,
)


def rsi_below(value: float) -> Condition:
    return Condition(left=IndicatorOperand(name="rsi"), op="<",
                     right=ConstantOperand(value=value))


def test_unknown_indicator_is_rejected():
    with pytest.raises(ValidationError):
        IndicatorOperand(name="secret_sauce")


def test_parameters_the_indicator_does_not_take_are_rejected():
    with pytest.raises(ValidationError):
        IndicatorOperand(name="rsi", k=2.0)


def test_defaults_are_filled_from_the_registry():
    assert IndicatorOperand(name="rsi").period == 14
    macd = IndicatorOperand(name="macd_signal")
    assert (macd.fast, macd.slow, macd.signal) == (12, 26, 9)


def test_period_bounds_are_enforced():
    with pytest.raises(ValidationError):
        IndicatorOperand(name="sma", period=1)
    with pytest.raises(ValidationError):
        IndicatorOperand(name="sma", period=5000)


def test_macd_fast_must_be_shorter_than_slow():
    with pytest.raises(ValidationError):
        IndicatorOperand(name="macd", fast=30, slow=10)


def test_comparing_two_constants_is_rejected():
    with pytest.raises(ValidationError):
        Condition(left=ConstantOperand(value=1), op="<", right=ConstantOperand(value=2))


def test_a_strategy_needs_an_entry():
    with pytest.raises(ValidationError):
        Strategy(universe=["SPY"], entry=ConditionGroup(conditions=[]))


def test_a_strategy_needs_some_way_out():
    with pytest.raises(ValidationError):
        Strategy(universe=["SPY"], entry=ConditionGroup(conditions=[rsi_below(30)]))
    # Any one of these is enough.
    Strategy(universe=["SPY"], entry=ConditionGroup(conditions=[rsi_below(30)]),
             risk={"max_holding_days": 5})
    Strategy(universe=["SPY"], entry=ConditionGroup(conditions=[rsi_below(30)]),
             risk={"stop_loss_pct": 5})


def test_unknown_fields_are_rejected_not_ignored():
    with pytest.raises(ValidationError):
        Strategy(
            universe=["SPY"],
            entry=ConditionGroup(conditions=[rsi_below(30)]),
            risk={"max_holding_days": 5},
            shell_command="rm -rf /",
        )


def test_universe_is_normalised_and_deduplicated():
    s = Strategy(universe=["spy", "SPY", " qqq "],
                 entry=ConditionGroup(conditions=[rsi_below(30)]),
                 risk={"max_holding_days": 5})
    assert s.universe == ["QQQ", "SPY"]


def test_invalid_tickers_are_rejected():
    with pytest.raises(ValidationError):
        Strategy(universe=["../../etc/passwd"],
                 entry=ConditionGroup(conditions=[rsi_below(30)]),
                 risk={"max_holding_days": 5})


def test_condition_count_is_capped():
    with pytest.raises(ValidationError):
        ConditionGroup(conditions=[rsi_below(i) for i in range(10)])


def test_round_trips_through_json():
    original = Strategy(
        universe=["SPY", "QQQ"],
        entry=ConditionGroup(logic="any", conditions=[rsi_below(30)]),
        risk={"stop_loss_pct": 8, "max_holding_days": 20},
    )
    restored = Strategy.model_validate(original.model_dump(mode="json"))
    assert restored == original
    assert stable_hash(restored) == stable_hash(original)


def test_hash_changes_when_anything_changes():
    a = Strategy(universe=["SPY"], entry=ConditionGroup(conditions=[rsi_below(30)]),
                 risk={"max_holding_days": 5})
    b = Strategy(universe=["SPY"], entry=ConditionGroup(conditions=[rsi_below(31)]),
                 risk={"max_holding_days": 5})
    assert stable_hash(a) != stable_hash(b)


def test_numeric_parameters_are_enumerated_in_a_stable_order():
    s = Strategy(
        universe=["SPY"],
        entry=ConditionGroup(conditions=[
            rsi_below(30),
            Condition(left=IndicatorOperand(name="close"), op=">",
                      right=IndicatorOperand(name="sma", period=200)),
        ]),
        risk={"stop_loss_pct": 8, "max_holding_days": 20},
    )
    paths = [p.path for p in numeric_parameters(s)]
    assert paths == [
        "entry[0].left.period", "entry[0].right.value", "entry[1].right.period",
        "risk.stop_loss_pct", "risk.max_holding_days",
    ]
    assert numeric_parameters(s) == numeric_parameters(s)


def test_perturbation_produces_a_valid_neighbour():
    s = Strategy(universe=["SPY"], entry=ConditionGroup(conditions=[rsi_below(30)]),
                 risk={"max_holding_days": 20})
    ref = next(p for p in numeric_parameters(s) if p.path == "entry[0].right.value")
    neighbour = perturb(s, ref, 1.2)
    assert neighbour is not None
    assert neighbour.entry.conditions[0].right.value == pytest.approx(36.0)
    assert s.entry.conditions[0].right.value == 30.0, "the original must not be mutated"


def test_perturbation_that_rounds_back_onto_itself_is_skipped():
    s = Strategy(universe=["SPY"], entry=ConditionGroup(conditions=[rsi_below(30)]),
                 risk={"max_holding_days": 2})
    ref = next(p for p in numeric_parameters(s) if p.path == "risk.max_holding_days")
    assert perturb(s, ref, 1.1) is None  # 2 * 1.1 -> 2


def test_config_rejects_an_inverted_window():
    import datetime as dt

    with pytest.raises(ValidationError):
        BacktestConfig(start=dt.date(2020, 1, 1), end=dt.date(2019, 1, 1))
