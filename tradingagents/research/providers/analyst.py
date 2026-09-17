"""Optional Yahoo Finance aggregate analyst-target snapshot adapter.

This feed deliberately remains metadata.  Yahoo returns an aggregate vendor
observation, without constituent firms, publication times, or a stated target
horizon; it must never become an ``EvidenceFact`` or an implied valuation.
"""

from __future__ import annotations

import inspect
import math
from collections.abc import Callable, Mapping
from datetime import date, datetime, time, timezone
from typing import Any

_CACHE_SCHEMA_VERSION = "v1"
_CACHE_TTL_SECONDS = 3600.0
_REQUEST_TIMEOUT_SECONDS = 10
_SOURCE_ROOT = "https://finance.yahoo.com/quote/"
_REQUIRED_VALUES = ("mean", "median", "low", "high")


def _cutoff(value: date | datetime | str) -> datetime:
    """Return an ISO date/timestamp cutoff as a UTC instant."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, time.max, timezone.utc)
    text = str(value).strip()
    if "T" not in text and " " not in text:
        return datetime.combine(date.fromisoformat(text), time.max, timezone.utc)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp as_of must include a timezone")
    return parsed.astimezone(timezone.utc)


def _instant(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _iso_currency(value: Any) -> str | None:
    currency = str(value or "").strip()
    return currency if len(currency) == 3 and currency.isalpha() and currency.isupper() else None


def validate_snapshot(raw: Any, ticker: str, as_of: date | datetime | str) -> dict[str, Any] | None:
    """Validate an aggregate snapshot without converting it to financial evidence.

    ``None`` means quarantine: callers must not use or replace an invalid cache
    record as though it were a newly observed value.
    """
    if not isinstance(raw, Mapping):
        return None
    # Accept a provider packet for cache reads as well as the public inner object
    # used by the engine for fixture/provider metadata validation.
    candidate = raw.get("analyst_targets") if isinstance(raw.get("analyst_targets"), Mapping) else raw
    if not isinstance(candidate, Mapping):
        return None
    symbol = str(ticker).strip().upper()
    if not symbol or str(candidate.get("ticker") or "").strip().upper() != symbol:
        return None
    if candidate.get("provider") != "yahoo_finance" or candidate.get("feed_kind") != "aggregate_feed_observation":
        return None
    if candidate.get("published_at") is not None or candidate.get("horizon") != "unspecified":
        return None
    currency = _iso_currency(candidate.get("currency"))
    retrieved_at = _instant(candidate.get("retrieved_at"))
    try:
        cutoff = _cutoff(as_of)
    except (TypeError, ValueError):
        return None
    if currency is None or retrieved_at is None or retrieved_at > cutoff:
        return None
    source_url = candidate.get("source_url")
    if not isinstance(source_url, str) or not source_url.strip():
        return None
    values = candidate.get("values")
    if not isinstance(values, Mapping) or set(values) != set(_REQUIRED_VALUES):
        return None
    cleaned = {key: _number(values.get(key)) for key in _REQUIRED_VALUES}
    if any(value is None or value < 0 for value in cleaned.values()):
        return None
    low, high = cleaned["low"], cleaned["high"]
    assert low is not None and high is not None
    if low > high or not (low <= cleaned["mean"] <= high) or not (low <= cleaned["median"] <= high):
        return None
    count = candidate.get("analyst_count")
    if count is not None:
        cleaned_count = _number(count)
        if cleaned_count is None or isinstance(count, bool) or not isinstance(cleaned_count, int) or cleaned_count < 0:
            return None
        count = cleaned_count
    limitations = candidate.get("limitations")
    if not isinstance(limitations, list) or not all(isinstance(item, str) and item.strip() for item in limitations):
        return None
    # Produce a plain JSON-compatible normalized copy so engine/fixture callers
    # cannot retain a mapping subclass or untrusted extra fields as values.
    return {
        "provider": "yahoo_finance",
        "feed_kind": "aggregate_feed_observation",
        "ticker": symbol,
        "currency": currency,
        "retrieved_at": retrieved_at.isoformat().replace("+00:00", "Z"),
        "published_at": None,
        "horizon": "unspecified",
        "values": cleaned,
        "analyst_count": count,
        "source_url": source_url.strip(),
        "limitations": list(limitations),
    }


class YahooAnalystTargetsProvider:
    """Fetch Yahoo's current aggregate analyst-target snapshot, if eligible."""

    def __init__(self, cache: Any = None, ticker_factory: Callable[[str], Any] | None = None):
        self.cache = cache
        self._ticker_factory = ticker_factory

    @staticmethod
    def _retrieved_at() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _factory(self) -> Callable[[str], Any]:
        if self._ticker_factory is not None:
            return self._ticker_factory
        import yfinance as yf

        return yf.Ticker

    @staticmethod
    def _call(method: Callable[..., Any]) -> Any:
        """Use yfinance's timeout parameter only on versions that expose it."""
        try:
            parameters = inspect.signature(method).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "timeout" in parameters:
            return method(timeout=_REQUEST_TIMEOUT_SECONDS)
        return method()

    @staticmethod
    def _packet(symbol: str, as_of: date | datetime | str) -> dict[str, Any]:
        return {
            "identity": {"ticker": symbol, "provider": "yahoo_finance"},
            "facts": [],
            "documents": [],
            "issues": [],
            "coverage": {"analyst_targets": "unsupported", "point_in_time": "partial"},
            "metadata": {"provider": "yahoo_finance", "as_of": _cutoff(as_of).isoformat().replace("+00:00", "Z")},
        }

    @staticmethod
    def _cache_snapshot(payload: Any) -> Any:
        if not isinstance(payload, Mapping):
            return None
        metadata = payload.get("metadata")
        if isinstance(metadata, Mapping):
            return metadata.get("analyst_targets")
        return payload.get("analyst_targets")

    @staticmethod
    def _issue(packet: dict[str, Any], code: str, severity: str, message: str) -> None:
        packet["issues"].append({"code": code, "severity": severity, "message": message})

    def fetch(self, ticker: str, as_of: date | datetime | str) -> dict[str, Any]:
        symbol = str(ticker).strip().upper()
        cutoff = _cutoff(as_of)
        packet = self._packet(symbol, as_of)
        cache_key = f"analyst_targets:yahoo:{_CACHE_SCHEMA_VERSION}:{symbol}"
        historical = cutoff < datetime.now(timezone.utc)
        if self.cache is not None:
            cached = self.cache.get_json(cache_key, max_age_seconds=None if historical else _CACHE_TTL_SECONDS)
            if cached is not None:
                snapshot = validate_snapshot(self._cache_snapshot(cached), symbol, cutoff)
                if snapshot is not None:
                    packet["metadata"]["analyst_targets"] = snapshot
                    packet["coverage"]["analyst_targets"] = "partial"
                    packet["identity"]["currency"] = snapshot["currency"]
                    return packet
                self._issue(packet, "ANALYST_TARGETS_CACHE_INVALID", "warning", "Cached Yahoo analyst-target snapshot failed validation and was quarantined.")
                return packet
        if historical:
            self._issue(packet, "ANALYST_TARGETS_HISTORICAL_UNAVAILABLE", "warning", "Yahoo aggregate analyst targets are a current snapshot; this adapter does not fetch them for a past cutoff.")
            return packet
        try:
            ticker_obj = self._factory()(symbol)
        except Exception as exc:
            self._issue(packet, "ANALYST_TARGETS_PROVIDER_ERROR", "error", f"Yahoo analyst-target provider unavailable: {type(exc).__name__}: {exc}")
            return packet
        try:
            targets = self._call(ticker_obj.get_analyst_price_targets)
        except Exception as exc:
            self._issue(packet, "ANALYST_TARGETS_PROVIDER_ERROR", "error", f"Yahoo analyst targets unavailable: {type(exc).__name__}: {exc}")
            return packet
        try:
            info = self._call(ticker_obj.get_info)
        except Exception as exc:
            self._issue(packet, "ANALYST_TARGETS_METADATA_ERROR", "warning", f"Yahoo analyst-target identity metadata unavailable: {type(exc).__name__}: {exc}")
            return packet
        if not isinstance(info, Mapping) or str(info.get("symbol") or "").strip().upper() != symbol:
            self._issue(packet, "ANALYST_TARGETS_IDENTITY_UNVERIFIED", "warning", "Yahoo metadata symbol did not match the requested ticker; aggregate targets were withheld.")
            return packet
        currency = _iso_currency(info.get("currency"))
        if currency is None:
            self._issue(packet, "ANALYST_TARGETS_CURRENCY_UNVERIFIED", "warning", "Yahoo metadata did not provide an explicit ISO currency; aggregate targets were withheld.")
            return packet
        retrieved_at = self._retrieved_at()
        raw_snapshot = {
            "provider": "yahoo_finance",
            "feed_kind": "aggregate_feed_observation",
            "ticker": symbol,
            "currency": currency,
            "retrieved_at": retrieved_at,
            "published_at": None,
            "horizon": "unspecified",
            "values": {key: targets.get(key) if isinstance(targets, Mapping) else None for key in _REQUIRED_VALUES},
            "analyst_count": info.get("numberOfAnalystOpinions"),
            "source_url": f"{_SOURCE_ROOT}{symbol}/analysis/",
            "limitations": [
                "Yahoo supplies an aggregate vendor observation, not named analyst or firm estimates.",
                "No publication timestamp, target horizon, methodology, or point-in-time historical vintage is established by this feed.",
                "This observation is not verified against a current share price and must not be used to compute return or upside.",
            ],
        }
        snapshot = validate_snapshot(raw_snapshot, symbol, cutoff)
        if snapshot is None:
            self._issue(packet, "ANALYST_TARGETS_INVALID", "warning", "Yahoo aggregate analyst targets were missing or internally inconsistent and were withheld.")
            return packet
        packet["metadata"]["analyst_targets"] = snapshot
        packet["coverage"]["analyst_targets"] = "partial"
        packet["identity"]["currency"] = currency
        if self.cache is not None:
            self.cache.put_json(cache_key, packet)
        return packet
