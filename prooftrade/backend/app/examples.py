"""Seeded demo strategies.

Deliberately mixed: at least one that holds up reasonably under scrutiny and at least
one that falls apart, because a demo where everything scores well proves nothing.
"""

from __future__ import annotations

EXAMPLES: list[dict] = [
    {
        "id": "rsi_dip_uptrend",
        "name": "RSI dip in an uptrend",
        "text": (
            "Buy when RSI(14) drops below 30 and the price is above the 200-day moving "
            "average. Sell when RSI goes above 55 or after 20 trading days. "
            "Use an 8% stop loss."
        ),
        "note": "The classic mean-reversion setup. Clean evidence, but it barely trades and loses to buy-and-hold.",
    },
    {
        "id": "golden_cross",
        "name": "Golden cross trend following",
        "text": (
            "Go long when the 50-day moving average crosses above the 200-day moving "
            "average. Exit when it crosses back below. At most 6 positions."
        ),
        "note": "Slow trend following. Beats the benchmark, then hands you a 55% drawdown to sit through.",
    },
    {
        "id": "breakout_20d",
        "name": "20-day breakout with a trailing stop",
        "text": (
            "Buy when the price breaks above the 20-day high and volume is above average "
            "volume. Use a 10% trailing stop and exit after 60 trading days at most. "
            "At most 5 positions."
        ),
        "note": "Thousands of trades, so the statistics are solid - and they show the costs eat the edge.",
    },
    {
        "id": "overfit_bait",
        "name": "Tuned to the point of absurdity",
        "text": (
            "Buy AAPL and NVDA when RSI(9) is below 33 and the price is above the "
            "150-day moving average. Sell after 5 days. Take profit at 7%."
        ),
        "note": "Oddly specific numbers on two stocks everyone knows went up. Breadth and concentration flags fire.",
    },
    {
        "id": "bollinger_reversion",
        "name": "Bollinger band reversion",
        "text": (
            "Buy ETFs when the close is below the lower bollinger band. Sell when the "
            "close crosses above the 20-day moving average or after 15 days. 6% stop loss."
        ),
        "note": "Catching falling knives. Loses money and takes a 52% drawdown doing it.",
    },
]
