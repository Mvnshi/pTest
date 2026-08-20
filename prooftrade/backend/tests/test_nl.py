"""The translator: what it understands, what it admits it does not."""

from __future__ import annotations

import pytest

from app.nl.rules import TranslationError, translate

SYMBOLS = ["AAPL", "AMZN", "DIA", "GLD", "IWM", "JNJ", "JPM", "KO", "MSFT", "NVDA",
           "QQQ", "SPY", "TLT", "XLE", "XLF", "XLK", "XLV", "XOM"]


def parse(text: str, **answers):
    return translate(text, SYMBOLS, answers or None)


def test_the_headline_demo_sentence():
    r = parse("Buy SPY and QQQ when RSI(14) drops below 30 and price is above the "
              "200-day moving average, sell when RSI goes above 55 or after 20 days. "
              "Use an 8% stop loss.")
    s = r.strategy
    assert s.universe == ["QQQ", "SPY"]
    assert s.position.direction == "long"
    assert len(s.entry.conditions) == 2
    assert s.entry.logic == "all"
    assert s.entry.conditions[0].left.name == "rsi"
    assert s.entry.conditions[0].right.value == 30
    assert s.entry.conditions[1].right.name == "sma"
    assert s.entry.conditions[1].right.period == 200
    assert s.exit.conditions[0].right.value == 55
    assert s.risk.stop_loss_pct == 8
    assert s.risk.max_holding_days == 20


def test_crossovers_and_pronoun_resolution():
    r = parse("Go long when the 50-day moving average crosses above the 200-day moving "
              "average. Exit when it crosses back below.")
    entry, exit_ = r.strategy.entry.conditions[0], r.strategy.exit.conditions[0]
    assert entry.op == "crosses_above"
    assert (entry.left.period, entry.right.period) == (50, 200)
    assert exit_.op == "crosses_below"
    assert (exit_.left.period, exit_.right.period) == (50, 200), "\"it\" must resolve"


def test_short_direction_and_cover_as_the_exit_verb():
    r = parse("Short tech stocks when RSI is above 75, cover when RSI drops below 50")
    assert r.strategy.position.direction == "short"
    assert r.strategy.entry.conditions[0].op == ">"
    assert r.strategy.exit.conditions[0].op == "<"
    assert len(r.strategy.entry.conditions) == 1, "the exit clause must not leak into entry"


def test_a_bare_close_is_not_mistaken_for_the_verb_close():
    r = parse("Buy ETFs when the close is below the lower bollinger band. Sell when the "
              "close crosses above the 20-day moving average or after 15 days. 6% stop loss.")
    assert r.strategy.entry.conditions[0].right.name == "bb_lower"
    assert r.strategy.exit.conditions[0].op == "crosses_above"
    assert r.strategy.risk.stop_loss_pct == 6
    assert r.strategy.risk.max_holding_days == 15


def test_risk_phrases_are_not_turned_into_conditions():
    r = parse("Buy when RSI is below 30. 8% stop loss, take profit at 12%, "
              "10% trailing stop, hold for 30 days.")
    assert r.strategy.risk.stop_loss_pct == 8
    assert r.strategy.risk.take_profit_pct == 12
    assert r.strategy.risk.trailing_stop_pct == 10
    assert r.strategy.risk.max_holding_days == 30
    assert len(r.strategy.entry.conditions) == 1, "risk numbers must not become rules"


def test_a_holding_limit_does_not_swallow_a_moving_average_period():
    r = parse("Buy when the price is above the 200-day moving average, sell after 20 days")
    assert r.strategy.risk.max_holding_days == 20
    assert r.strategy.entry.conditions[0].right.period == 200


def test_or_produces_a_disjunctive_entry():
    r = parse("Buy when RSI is below 30 or the close is below the lower bollinger band, "
              "sell after 10 days")
    assert r.strategy.entry.logic == "any"
    assert len(r.strategy.entry.conditions) == 2


def test_units_are_converted_to_trading_bars():
    r = parse("Buy when the 3 month return is above 20 percent, sell after 10 days")
    assert r.strategy.entry.conditions[0].left.name == "roc"
    assert r.strategy.entry.conditions[0].left.period == 63


def test_position_cap_is_read():
    r = parse("Buy when RSI is below 30, at most 3 positions, sell after 5 days")
    assert r.strategy.position.max_positions == 3


def test_nonsense_is_rejected_rather_than_guessed():
    with pytest.raises(TranslationError):
        parse("buy the dip")
    with pytest.raises(TranslationError):
        parse("buy when the moon is in the seventh house")


def test_a_missing_exit_asks_a_question_instead_of_inventing_one_silently():
    r = parse("Buy when RSI is below 30")
    assert any(q.id == "exit_rule" for q in r.questions)
    assert r.confidence < 0.9
    # A default is still applied so the flow never blocks, and it is disclosed.
    assert r.strategy.risk.max_holding_days is not None
    assert any("exit" in a.field for a in r.assumptions)


def test_answering_a_question_changes_the_strategy_deterministically():
    a = parse("Buy when RSI is below 30", exit_rule="stop_8")
    assert a.strategy.risk.stop_loss_pct == 8
    assert a.strategy.risk.max_holding_days is None
    b = parse("Buy when RSI is below 30", exit_rule="time_20")
    assert b.strategy.risk.max_holding_days == 20
    assert b.strategy.risk.stop_loss_pct is None
    assert parse("Buy when RSI is below 30", exit_rule="stop_8").strategy == a.strategy


def test_the_universe_question_is_asked_and_answered():
    r = parse("Buy when RSI is below 30, sell after 10 days")
    assert any(q.id == "universe" for q in r.questions)
    assert r.strategy.universe == sorted(SYMBOLS)
    answered = parse("Buy when RSI is below 30, sell after 10 days", universe="index_etfs")
    assert answered.strategy.universe == ["DIA", "IWM", "QQQ", "SPY"]


def test_the_moving_average_ambiguity_is_surfaced_and_resolvable():
    r = parse("Buy when the price is above the 50-day moving average, sell after 10 days")
    assert any(q.id == "ma_type" for q in r.questions)
    assert r.strategy.entry.conditions[0].right.name == "sma"
    ema = parse("Buy when the price is above the 50-day moving average, sell after 10 days",
                ma_type="ema")
    assert ema.strategy.entry.conditions[0].right.name == "ema"


def test_unparsed_fragments_are_reported_not_hidden():
    r = parse("Buy when RSI is below 30 and the CEO seems trustworthy on television, "
              "sell after 10 days")
    assert r.unparsed, "an uninterpretable clause must be surfaced"
    assert any("ceo" in u or "television" in u for u in r.unparsed)


def test_translation_is_deterministic():
    text = ("Buy SPY when RSI(14) drops below 30 and price is above the 200-day moving "
            "average, sell when RSI goes above 55. 8% stop loss.")
    assert parse(text).strategy == parse(text).strategy
    assert parse(text).as_dict() == parse(text).as_dict()
