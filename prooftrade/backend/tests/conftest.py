"""Shared fixtures.

Engine behaviour is tested against a tiny hand-built dataset rather than the demo
snapshot, so assertions can be exact: when a test says a stop filled at 92.00, that is
arithmetic, not an approximation of whatever the data happened to do.
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

import pytest

from app.data import BarStore
from app.dsl import (
    BacktestConfig,
    Condition,
    ConditionGroup,
    ConstantOperand,
    IndicatorOperand,
    PositionRules,
    RiskRules,
    Strategy,
)

COLUMNS = ["date", "open", "high", "low", "close", "adj_close", "volume"]


def write_bars(directory: Path, symbol: str, rows: list[dict]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f"{symbol}.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def bar(date: str, o: float, h: float, l: float, c: float, volume: int = 1_000_000) -> dict:
    return {"date": date, "open": o, "high": h, "low": l, "close": c,
            "adj_close": c, "volume": volume}


def business_days(start: str, count: int) -> list[str]:
    day = dt.date.fromisoformat(start)
    out: list[str] = []
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += dt.timedelta(days=1)
    return out


@pytest.fixture
def toy_store(tmp_path: Path) -> BarStore:
    """One symbol, a flat 100 with a single controlled excursion.

    TOY closes at 100 every day except a scripted window, so any trade the engine
    reports can be traced to an exact bar.
    """
    dates = business_days("2020-01-01", 60)
    rows = [bar(d, 100, 101, 99, 100) for d in dates]
    # Day 20 dips hard on the close (a signal day), day 21 opens lower still.
    rows[20] = bar(dates[20], 100, 100.5, 89, 90)
    rows[21] = bar(dates[21], 95, 96, 88, 92)
    rows[22] = bar(dates[22], 92, 130, 91, 128)
    write_bars(tmp_path / "bars", "TOY", rows)
    return BarStore(tmp_path / "bars")


@pytest.fixture
def ramp_store(tmp_path: Path) -> BarStore:
    """A symbol that rises 1% a day forever - a clean, cost-free-looking uptrend."""
    dates = business_days("2020-01-01", 400)
    rows = []
    price = 100.0
    for d in dates:
        rows.append(bar(d, round(price, 4), round(price * 1.02, 4),
                        round(price * 0.99, 4), round(price * 1.01, 4)))
        price *= 1.01
    write_bars(tmp_path / "bars", "RAMP", rows)
    return BarStore(tmp_path / "bars")


@pytest.fixture
def demo_store() -> BarStore:
    """The committed demo snapshot."""
    from app.data import get_store

    return get_store()


def make_strategy(
    universe: list[str],
    *,
    entry: list[Condition] | None = None,
    exit_: list[Condition] | None = None,
    logic: str = "all",
    direction: str = "long",
    max_positions: int = 5,
    **risk,
) -> Strategy:
    entry = entry or [
        Condition(left=IndicatorOperand(name="close"), op="<", right=ConstantOperand(value=95))
    ]
    return Strategy(
        name="test",
        universe=universe,
        position=PositionRules(direction=direction, max_positions=max_positions),
        entry=ConditionGroup(logic=logic, conditions=entry),
        exit=ConditionGroup(logic="any", conditions=exit_ or []),
        risk=RiskRules(**risk),
    )


@pytest.fixture
def base_config() -> BacktestConfig:
    return BacktestConfig(
        initial_capital=100_000, commission_bps=0, slippage_bps=0, run_robustness=False
    )
