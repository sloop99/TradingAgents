"""Conservative Yahoo daily market evidence adapter."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

_CACHE_TTL_SECONDS = 3600.0
_CACHE_SCHEMA_VERSION = "v2"
_LOOKBACK_DAYS = 400
_MAX_DAILY_ROWS = 30
_HISTORY_TIMEOUT_SECONDS = 10
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
                "corporate_action_window": "up to 400 calendar days before the cutoff",
            },
        }

    def fetch(self, ticker: str, as_of: date | datetime | str) -> dict[str, Any]:
        symbol = str(ticker).strip().upper()
        cutoff = self._as_of_date(as_of)
        cache_key = f"market:yahoo:{_CACHE_SCHEMA_VERSION}:{symbol}:{cutoff.isoformat()}"
        if self.cache is not None:
            cached = self.cache.get_json(cache_key, max_age_seconds=_CACHE_TTL_SECONDS)
            if isinstance(cached, dict):
                return cached

        packet = self._packet(symbol, cutoff, "")
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
        except Exception as exc:  # constructing a provider handle can also fail
            packet["issues"].append(
                {
                    "code": "MARKET_PROVIDER_ERROR",
                    "severity": "error",
                    "message": f"Yahoo market provider unavailable: {type(exc).__name__}: {exc}",
                }
            )
            retrieved_at = self._retrieved_at()
            packet["metadata"]["retrieved_at"] = retrieved_at
            if self.cache is not None:
                self.cache.put_json(cache_key, packet)
            return packet

        history: Any = None
        try:
            start = cutoff - timedelta(days=_LOOKBACK_DAYS)
            history = ticker_obj.history(
                start=start.isoformat(),
                end=cutoff.isoformat(),
                auto_adjust=False,
                actions=True,
                timeout=_HISTORY_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # provider failures are evidence gaps, not crashes
            packet["issues"].append(
                {
                    "code": "MARKET_PROVIDER_ERROR",
                    "severity": "error",
                    "message": f"Yahoo market history unavailable: {type(exc).__name__}: {exc}",
                }
            )

        info: dict[str, Any] | None = None
        try:
            info = self._info(ticker_obj)
        except Exception as exc:  # metadata must not discard otherwise usable prices
            packet["issues"].append(
                {
                    "code": "MARKET_METADATA_PROVIDER_ERROR",
                    "severity": "warning",
                    "message": f"Yahoo market metadata unavailable: {type(exc).__name__}: {exc}",
                }
            )

        # This is the earliest availability time this adapter can establish: both
        # bounded Yahoo responses have completed.  It is intentionally not an
        # exchange publication time or a claim about historical availability.
        retrieved_at = self._retrieved_at()
        packet["metadata"]["retrieved_at"] = retrieved_at
        verified_metadata = self._verified_metadata(symbol, info)
        if info and verified_metadata is None:
            packet["issues"].append(
                {
                    "code": "MARKET_METADATA_IDENTITY_UNVERIFIED",
                    "severity": "warning",
                    "message": (
                        "Yahoo metadata symbol did not match the requested symbol; metadata values "
                        "were withheld."
                    ),
                }
            )
        if verified_metadata is not None:
            self._apply_identity_metadata(packet, verified_metadata)

        currency = self._currency(verified_metadata)
        facts = self._metadata_facts(symbol, verified_metadata, retrieved_at, currency)
        if history is not None:
            facts = self._facts(symbol, history, cutoff, retrieved_at, currency) + facts
            packet["facts"] = facts
            packet["metadata"]["daily_rows"] = len(
                {fact["period_end"] for fact in facts if fact["metric"] in {"close", "close_adjusted"}}
            )
            packet["coverage"]["facts"] = "sufficient" if facts else "partial"
            packet["issues"].append(
                {
                    "code": "MARKET_CORPORATE_ACTION_WINDOW_PARTIAL",
                    "severity": "warning",
                    "message": (
                        "Corporate actions are retained from Yahoo's returned 400-day request window; "
                        "this is not complete corporate-action history or a security master."
                    ),
                }
            )
        elif facts:
            packet["facts"] = facts
            packet["coverage"]["facts"] = "partial"

        if self.cache is not None:
            self.cache.put_json(cache_key, packet)
        return packet

    @classmethod
    def _facts(
        cls,
        symbol: str,
        history: Any,
        cutoff: date,
        retrieved_at: str,
        currency: str | None,
    ) -> list[dict[str, Any]]:
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
        price_rows = rows[-_MAX_DAILY_ROWS:]
        facts: list[dict[str, Any]] = []
        price_unit = f"{currency}/share" if currency else "native_currency/share"
        for day, row in price_rows:
            for column, metric, unit, definition in (
                ("Close", "close", price_unit, "Yahoo daily Close; historical split basis follows the vendor."),
                ("Adj Close", "close_adjusted", price_unit, "Yahoo split/dividend-adjusted daily close."),
            ):
                value = cls._clean_number(cls._column(row, column))
                if value is not None:
                    facts.append(cls._fact(symbol, metric, value, unit, day, retrieved_at, definition))
        for day, row in rows:
            split = cls._clean_number(cls._column(row, "Stock Splits"))
            if split not in (None, 0):
                facts.append(cls._fact(symbol, "split_ratio", split, "ratio", day, retrieved_at, "Yahoo stock split ratio from the returned vendor history window; absence of a record does not establish that no split occurred."))
            dividend = cls._clean_number(cls._column(row, "Dividends"))
            if dividend not in (None, 0):
                facts.append(cls._fact(symbol, "dividend_per_share", dividend, price_unit, day, retrieved_at, "Yahoo cash dividend per share from the returned vendor history window."))
        return facts

    @staticmethod
    def _info(ticker_obj: Any) -> dict[str, Any]:
        getter = getattr(ticker_obj, "get_info", None)
        info = getter() if callable(getter) else ticker_obj.info
        if not isinstance(info, dict):
            raise TypeError("Yahoo metadata was not a mapping")
        return info

    @staticmethod
    def _verified_metadata(symbol: str, info: dict[str, Any] | None) -> dict[str, Any] | None:
        if not info:
            return None
        returned_symbol = str(info.get("symbol") or "").strip().upper()
        return info if returned_symbol == symbol else None

    @staticmethod
    def _currency(info: dict[str, Any] | None) -> str | None:
        if not info:
            return None
        currency = str(info.get("currency") or "").strip()
        # Do not turn Yahoo quotation labels such as ``GBp`` into an ISO code
        # (``GBP`` would be a different per-share unit).
        return currency if len(currency) == 3 and currency.isalpha() and currency.isupper() else None

    @classmethod
    def _apply_identity_metadata(cls, packet: dict[str, Any], info: dict[str, Any]) -> None:
        currency = cls._currency(info)
        if currency:
            packet["identity"]["currency"] = currency
        exchange = str(info.get("fullExchangeName") or info.get("exchange") or "").strip()
        if exchange:
            packet["identity"]["exchange"] = exchange
        quote_type = str(info.get("quoteType") or "").strip()
        if quote_type:
            packet["metadata"]["quote_type"] = quote_type

    @classmethod
    def _metadata_facts(
        cls,
        symbol: str,
        info: dict[str, Any] | None,
        retrieved_at: str,
        currency: str | None,
    ) -> list[dict[str, Any]]:
        if not info:
            return []
        try:
            retrieval_day = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00")).astimezone(timezone.utc).date()
        except ValueError:
            return []
        facts: list[dict[str, Any]] = []
        for key, metric, unit, definition in (
            (
                "marketCap",
                "market_cap_reported",
                currency or "native_currency",
                "Yahoo Finance market-cap vendor snapshot; its methodology, timestamp, and point-in-time availability are not independently verified.",
            ),
            (
                "sharesOutstanding",
                "shares_outstanding_market",
                "shares",
                "Yahoo Finance shares-outstanding vendor snapshot; share classes, split basis, filing reconciliation, and point-in-time availability are unresolved.",
            ),
        ):
            value = cls._clean_number(info.get(key))
            if value is not None:
                facts.append(cls._fact(symbol, metric, value, unit, retrieval_day, retrieved_at, definition))
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
