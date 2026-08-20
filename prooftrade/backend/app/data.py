"""Loading the frozen daily-bar snapshot.

The engine never touches the network. It reads CSVs from `DATA_DIR`, which are
committed to the repository, and hashes them so every result can be attributed to an
exact dataset.

Bars are returned on a **total-return basis**: the whole OHLC bar is scaled by
`adj_close / close`, so dividends and splits do not create phantom gaps that a naive
backtest would happily trade.
"""

from __future__ import annotations

import functools
import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .config import DATA_DIR

REQUIRED_COLUMNS = ("date", "open", "high", "low", "close", "adj_close", "volume")


class DataError(RuntimeError):
    """Raised when the snapshot is missing or malformed."""


@dataclass(frozen=True)
class SymbolCoverage:
    symbol: str
    bars: int
    start: str
    end: str


class BarStore:
    """In-memory, read-only view of the snapshot."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = Path(directory or DATA_DIR)
        self._frames: dict[str, pd.DataFrame] = {}
        self._load()

    # -- loading -----------------------------------------------------------------

    def _load(self) -> None:
        if not self.directory.exists():
            raise DataError(
                f"No market data at {self.directory}. Run `make data` to generate a snapshot."
            )
        files = sorted(self.directory.glob("*.csv"))
        if not files:
            raise DataError(
                f"No CSV bars in {self.directory}. Run `make data` to generate a snapshot."
            )
        for path in files:
            symbol = path.stem.upper()
            frame = pd.read_csv(path)
            missing = set(REQUIRED_COLUMNS) - set(frame.columns)
            if missing:
                raise DataError(f"{path.name} is missing columns: {sorted(missing)}")
            frame["date"] = pd.to_datetime(frame["date"], format="%Y-%m-%d")
            frame = frame.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
            self._frames[symbol] = self._adjust(frame)
        if not self._frames:
            raise DataError(f"No usable symbols in {self.directory}")

    @staticmethod
    def _adjust(frame: pd.DataFrame) -> pd.DataFrame:
        """Put the whole bar on a total-return basis."""
        close = frame["close"].to_numpy(dtype=float)
        adj = frame["adj_close"].to_numpy(dtype=float)
        if np.any(close <= 0) or np.any(adj <= 0):
            raise DataError("non-positive prices in snapshot")
        ratio = adj / close
        out = pd.DataFrame({
            "date": frame["date"],
            "open": frame["open"].to_numpy(dtype=float) * ratio,
            "high": frame["high"].to_numpy(dtype=float) * ratio,
            "low": frame["low"].to_numpy(dtype=float) * ratio,
            "close": adj,
            "volume": frame["volume"].to_numpy(dtype=float),
            "raw_close": close,
        })
        # Adjustment must not break the bar's internal ordering.
        bad = (
            (out["high"] < out[["open", "close"]].max(axis=1) - 1e-9)
            | (out["low"] > out[["open", "close"]].min(axis=1) + 1e-9)
        )
        if bool(bad.any()):
            out.loc[bad, "high"] = out.loc[bad, ["open", "close", "high"]].max(axis=1)
            out.loc[bad, "low"] = out.loc[bad, ["open", "close", "low"]].min(axis=1)
        return out.set_index("date")

    # -- access ------------------------------------------------------------------

    @property
    def symbols(self) -> list[str]:
        return sorted(self._frames)

    def has(self, symbol: str) -> bool:
        return symbol.upper() in self._frames

    def bars(self, symbol: str) -> pd.DataFrame:
        try:
            return self._frames[symbol.upper()]
        except KeyError:
            raise DataError(f"unknown symbol {symbol!r}") from None

    def coverage(self) -> list[SymbolCoverage]:
        out = []
        for symbol in self.symbols:
            frame = self._frames[symbol]
            out.append(SymbolCoverage(
                symbol=symbol,
                bars=len(frame),
                start=frame.index[0].strftime("%Y-%m-%d"),
                end=frame.index[-1].strftime("%Y-%m-%d"),
            ))
        return out

    def calendar(self, symbols: list[str] | None = None) -> pd.DatetimeIndex:
        """Union of trading days across the given symbols, ascending."""
        names = [s.upper() for s in (symbols or self.symbols)]
        index = pd.DatetimeIndex([])
        for name in names:
            index = index.union(self._frames[name].index)
        return index.sort_values()

    @functools.cached_property
    def snapshot_hash(self) -> str:
        """SHA-256 over the raw files, so a result can be tied to an exact dataset."""
        digest = hashlib.sha256()
        for path in sorted(self.directory.glob("*.csv")):
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
        return digest.hexdigest()[:16]

    @functools.cached_property
    def date_range(self) -> tuple[str, str]:
        starts = [f.index[0] for f in self._frames.values()]
        ends = [f.index[-1] for f in self._frames.values()]
        return min(starts).strftime("%Y-%m-%d"), max(ends).strftime("%Y-%m-%d")


_STORE: BarStore | None = None


def get_store(directory: Path | None = None, *, reload: bool = False) -> BarStore:
    """Process-wide singleton; the snapshot is immutable so sharing it is safe."""
    global _STORE
    if _STORE is None or reload or (directory is not None and Path(directory) != _STORE.directory):
        _STORE = BarStore(directory)
    return _STORE
