"""Conservative Yahoo daily market evidence adapter."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

_CACHE_TTL_SECONDS = 3600.0
_LOOKBACK_DAYS = 400
_MAX_DAILY_ROWS = 30
_SOURCE_URL = "https://finance.yahoo.com/"
_PUBLICATION_DEFINITION = (
    "Yahoo daily-bar observation; published_at is the first retrieval timestamp "
    "available to this adapter, not a verified exchange publication timestamp."
)


class YahooMarketProvider:
    """Fetch a bounded, explicitly caveated set of Yahoo daily market facts."""

    def __init__(self, cache: Any = None, ticker_factory: Callable[[str], Any] | None = None):
        self.cache = cache
        self._ticker_factory = ticker_factory

    @staticmethod
    def _as_of_date(as_of: date | datetime | str) -> date:
        if isinstance(as_of, datetime):
            return as_of.date()
        if isinstance(as_of, date):
            return as_of
        return date.fromisoformat(str(as_of)[:10])

    @staticmethod
    def _retrieved_at() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _clean_number(value: Any) -> float | int | None:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(number):
            return None
        return int(number) if number.is_integer() else number

    @staticmethod
    def _row_date(index_value: Any) -> date | None:
        if isinstance(index_value, datetime):
            return index_value.date()
        if isinstance(index_value, date):
            return index_value
        try:
            parsed = datetime.fromisoformat(str(index_value).replace("Z", "+00:00"))
            return parsed.date()
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _column(row: Any, name: str) -> Any:
        try:
            return row[name]
        except (KeyError, IndexError, TypeError):
            return None

    def _factory(self) -> Callable[[str], Any]:
        if self._ticker_factory is not None:
            return self._ticker_factory
        import yfinance as yf

        return yf.Ticker

    @staticmethod
    def _packet(ticker: str, as_of: date, retrieved_at: str) -> dict[str, Any]:
        return {
            "identity": {
                "ticker": ticker,
                "provider": "yahoo_finance",
                "source_url": f"{_SOURCE_URL}quote/{ticker}",
            },
            "facts": [],
            "documents": [],
            "issues": [],
            "coverage": {
                "identity": "partial",
                "documents": "unsupported",
                "facts": "partial",
                "point_in_time": "partial",
            },
            "metadata": {
                "provider": "yahoo_finance",
                "as_of": as_of.isoformat(),
                "retrieved_at": retrieved_at,
                "lookback_days": _LOOKBACK_DAYS,
                "daily_rows": 0,
            },
        }

    def fetch(self, ticker: str, as_of: date | datetime | str) -> dict[str, Any]:
        symbol = str(ticker).strip().upper()
        cutoff = self._as_of_date(as_of)
        cache_key = f"market:yahoo:{symbol}:{cutoff.isoformat()}"
        if self.cache is not None:
            cached = self.cache.get_json(cache_key, max_age_seconds=_CACHE_TTL_SECONDS)
            if isinstance(cached, dict):
                return cached

        retrieved_at = self._retrieved_at()
        packet = self._packet(symbol, cutoff, retrieved_at)
        packet["issues"].append(
            {
                "code": "MARKET_PUBLICATION_PROXY",
                "severity": "warning",
                "message": _PUBLICATION_DEFINITION,
            }
        )
        if cutoff < datetime.now(timezone.utc).date():
            packet["issues"].append(
                {
                    "code": "MARKET_HISTORICAL_VINTAGE_UNVERIFIED",
                    "severity": "warning",
                    "message": (
                        "Yahoo history is a later downloaded/revised series; this packet does "
                        "not establish point-in-time availability as of the requested date."
                    ),
                }
            )

        try:
            ticker_obj = self._factory()(symbol)
            start = cutoff - timedelta(days=_LOOKBACK_DAYS)
            history = ticker_obj.history(
                start=start.isoformat(),
                end=cutoff.isoformat(),
                auto_adjust=False,
                actions=True,
            )
            facts = self._facts(symbol, history, cutoff, retrieved_at)
            packet["facts"] = facts
            packet["metadata"]["daily_rows"] = len(
                {fact["period_end"] for fact in facts if fact["kind"] == "reported"}
            )
            packet["coverage"]["facts"] = "sufficient" if facts else "partial"
        except Exception as exc:  # provider failures are evidence gaps, not crashes
            packet["issues"].append(
                {
                    "code": "MARKET_PROVIDER_ERROR",
                    "severity": "error",
                    "message": f"Yahoo market history unavailable: {type(exc).__name__}: {exc}",
                }
            )

        if self.cache is not None:
            self.cache.put_json(cache_key, packet)
        return packet

    @classmethod
    def _facts(cls, symbol: str, history: Any, cutoff: date, retrieved_at: str) -> list[dict[str, Any]]:
        rows: list[tuple[date, Any]] = []
        try:
            iterator = history.iterrows()
        except AttributeError:
            return []
        for index_value, row in iterator:
            day = cls._row_date(index_value)
            if day is not None and day < cutoff:
                rows.append((day, row))
        rows.sort(key=lambda item: item[0])
        rows = rows[-_MAX_DAILY_ROWS:]
        facts: list[dict[str, Any]] = []
        for day, row in rows:
            for column, metric, unit, definition in (
                ("Close", "close", "native_currency/share", "Yahoo daily Close; historical split basis follows the vendor and quote currency is not resolved by this adapter."),
                ("Adj Close", "close_adjusted", "native_currency/share", "Yahoo split/dividend-adjusted daily close; quote currency is not resolved by this adapter."),
            ):
                value = cls._clean_number(cls._column(row, column))
                if value is not None:
                    facts.append(cls._fact(symbol, metric, value, unit, day, retrieved_at, definition))
            split = cls._clean_number(cls._column(row, "Stock Splits"))
            if split not in (None, 0):
                facts.append(cls._fact(symbol, "split_ratio", split, "ratio", day, retrieved_at, "Yahoo stock split ratio."))
            dividend = cls._clean_number(cls._column(row, "Dividends"))
            if dividend not in (None, 0):
                facts.append(cls._fact(symbol, "dividend_per_share", dividend, "native_currency/share", day, retrieved_at, "Yahoo cash dividend per share; quote currency is not resolved by this adapter."))
        return facts

    @staticmethod
    def _fact(symbol: str, metric: str, value: Any, unit: str, day: date, retrieved_at: str, definition: str) -> dict[str, Any]:
        return {
            "fact_id": f"yahoo:{symbol}:{day.isoformat()}:{metric}",
            "metric": metric,
            "value": value,
            "unit": unit,
            "period_end": day.isoformat(),
            "published_at": retrieved_at,
            "retrieved_at": retrieved_at,
            "source_url": f"{_SOURCE_URL}quote/{symbol}/history",
            "definition": f"{definition} {_PUBLICATION_DEFINITION}",
            "adjustment_basis": "vendor_split_adjusted" if metric == "close" else "split_dividend_adjusted" if metric == "close_adjusted" else "reported",
            "kind": "reported",
        }
