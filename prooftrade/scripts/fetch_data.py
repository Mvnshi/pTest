#!/usr/bin/env python3
"""Fetch a frozen daily-bar snapshot for the ProofTrade demo universe.

This script is an *offline, one-time* data vendoring step. The backtest engine
never touches the network: it reads the CSVs written here. Re-running this
script with the same SNAPSHOT_END will reproduce the same files.

Source: Yahoo Finance public chart endpoint (no API key, no paid tier).
Usage:  python scripts/fetch_data.py [--out data/bars]
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request

# Frozen snapshot boundaries. Changing these changes the data hash, which is
# recorded with every backtest run so results stay attributable to a dataset.
SNAPSHOT_START = "2006-01-01"
SNAPSHOT_END = "2026-06-30"

UNIVERSE = [
    # Broad market / index ETFs
    "SPY", "QQQ", "IWM", "DIA",
    # Cross-asset ETFs (regime diversity: bonds, gold)
    "TLT", "GLD",
    # Sector ETFs
    "XLE", "XLF", "XLK", "XLV",
    # Mega-cap single names
    "AAPL", "MSFT", "NVDA", "AMZN", "JPM", "XOM", "KO", "JNJ",
]

CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    "?period1={p1}&period2={p2}&interval=1d&events=div%2Csplit"
    "&includeAdjustedClose=true"
)
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def _epoch(date_str: str) -> int:
    d = dt.datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp())


def fetch_symbol(symbol: str, retries: int = 10) -> list[dict]:
    url = CHART_URL.format(symbol=symbol, p1=_epoch(SNAPSHOT_START), p2=_epoch(SNAPSHOT_END))
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            break
        except Exception as exc:  # noqa: BLE001 - network retry loop
            last_err = exc
            time.sleep(min(20 + 15 * attempt, 90))
    else:
        raise RuntimeError(f"failed to fetch {symbol}: {last_err}")

    result = payload["chart"]["result"][0]
    stamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    adj = result["indicators"]["adjclose"][0]["adjclose"]

    rows: list[dict] = []
    for i, ts in enumerate(stamps):
        o, h, l, c, v, a = (
            quote["open"][i], quote["high"][i], quote["low"][i],
            quote["close"][i], quote["volume"][i], adj[i],
        )
        if None in (o, h, l, c, a):
            continue  # halted / incomplete bar
        # Yahoo timestamps are exchange-open instants; convert in exchange tz.
        day = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
        # Bars are stamped at 13:30/14:30 UTC for US equities, so the UTC date
        # is always the correct trading date.
        rows.append({
            "date": day.strftime("%Y-%m-%d"),
            "open": round(float(o), 6),
            "high": round(float(h), 6),
            "low": round(float(l), 6),
            "close": round(float(c), 6),
            "adj_close": round(float(a), 6),
            "volume": int(v or 0),
        })

    # Deduplicate and sort defensively; determinism starts at the data layer.
    seen: dict[str, dict] = {}
    for row in rows:
        seen[row["date"]] = row
    return [seen[d] for d in sorted(seen)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "data", "bars"))
    args = ap.parse_args()
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    manifest = []
    for symbol in UNIVERSE:
        rows = fetch_symbol(symbol)
        if len(rows) < 2000:
            print(f"  !! {symbol}: only {len(rows)} bars, skipping", file=sys.stderr)
            continue
        path = os.path.join(out_dir, f"{symbol}.csv")
        with open(path, "w", newline="") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=["date", "open", "high", "low", "close", "adj_close", "volume"]
            )
            writer.writeheader()
            writer.writerows(rows)
        manifest.append({
            "symbol": symbol,
            "bars": len(rows),
            "start": rows[0]["date"],
            "end": rows[-1]["date"],
        })
        print(f"  {symbol:5s} {len(rows):5d} bars  {rows[0]['date']} -> {rows[-1]['date']}", flush=True)
        time.sleep(8)

    with open(os.path.join(out_dir, "manifest.json"), "w") as fh:
        json.dump(
            {
                "snapshot_start": SNAPSHOT_START,
                "snapshot_end": SNAPSHOT_END,
                "source": "Yahoo Finance daily bars (public chart endpoint)",
                "symbols": manifest,
            },
            fh,
            indent=2,
        )
        fh.write("\n")
    print(f"\nWrote {len(manifest)} symbols to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
