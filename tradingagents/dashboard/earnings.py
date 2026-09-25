"""Read a stored earnings calendar and turn it into the agenda and rerun alerts.

The file comes from ``tradingagents.research.earnings_calendar``; nothing here
makes a network call. Alerts are recomputed against the current time on every
request, so they advance while the page stays open.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tradingagents.research.rerun_policy import MARKET_TZ, TickerState, evaluate

from .indexer import ResearchIndex, ResearchRun, _ticker_summaries
from .sectors import _timestamp

MAX_BYTES = 2_000_000
STALE_AFTER = timedelta(days=3)   # dates move, especially estimated ones
AGENDA_DAYS = 60
_TIMINGS = {"before_open", "after_close", "unknown"}
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.^=-]{0,19}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_KIND_ORDER = {"earnings": 0, "ex_dividend": 1, "dividend_paid": 2}


def earnings_payload(path: Path | None, index: ResearchIndex, *, now: datetime | None = None) -> dict[str, Any]:
    instant = now or datetime.now(timezone.utc)
    return build_earnings_view(load_earnings_snapshot(path, now=instant), index, instant)


def load_earnings_snapshot(path: Path | None, *, now: datetime | None = None) -> dict[str, Any]:
    """A validated snapshot. A missing or invalid file is a visible gap, never partial data."""
    empty = {"status": "unavailable", "retrieved_at": None, "source": None, "tickers": [], "warnings": []}
    if path is None or not path.is_file():
        return {**empty, "warnings": ["No earnings calendar collected yet."]}
    try:
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("file exceeds 2 MB")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("snapshot must be an object")
        retrieved = _timestamp(raw.get("retrieved_at"))
        instant = now or datetime.now(timezone.utc)
        if retrieved > instant + timedelta(minutes=5):
            raise ValueError("retrieval time is in the future")
        entries = raw.get("tickers")
        if not isinstance(entries, list) or len(entries) > 500:
            raise ValueError("invalid ticker list")
        tickers: list[dict[str, Any]] = []
        for entry in entries:
            clean = _entry(entry)
            if any(existing["ticker"] == clean["ticker"] for existing in tickers):
                raise ValueError(f"duplicate ticker {clean['ticker']}")
            tickers.append(clean)
        warnings = raw.get("warnings") if isinstance(raw.get("warnings"), list) else []
        return {
            "status": "stale" if instant - retrieved > STALE_AFTER else "available",
            "retrieved_at": raw["retrieved_at"],
            "source": str(raw.get("source") or "Yahoo Finance")[:200],
            "tickers": tickers,
            "warnings": [str(warning)[:1000] for warning in warnings][:30],
        }
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        return {**empty, "warnings": [f"Earnings calendar withheld: {type(exc).__name__}: {exc}"]}


def build_earnings_view(snapshot: dict[str, Any], index: ResearchIndex, now: datetime) -> dict[str, Any]:
    """The agenda, next report per ticker and rerun alerts for the archived tickers."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    today = now.astimezone(MARKET_TZ).date()
    horizon = today + timedelta(days=AGENDA_DAYS)
    summaries = _ticker_summaries(index.runs)
    groups = {summary["ticker"]: summary["group"] for summary in summaries}
    research = _research_dates(index.runs)
    calendar = {entry["ticker"]: entry for entry in snapshot["tickers"] if entry["ticker"] in groups}

    events: list[dict[str, Any]] = []
    next_earnings: dict[str, dict[str, Any]] = {}
    undated: list[str] = []
    for ticker, entry in calendar.items():
        group = groups[ticker]
        upcoming = entry["next_earnings"]
        if upcoming is None:
            undated.append(ticker)
        elif date.fromisoformat(upcoming["date"]) >= today:
            next_earnings[ticker] = {key: upcoming[key] for key in ("date", "timing", "estimated")}
            if date.fromisoformat(upcoming["date"]) <= horizon:
                events.append({"date": upcoming["date"], "ticker": ticker, "group": group, "kind": "earnings",
                               **{key: upcoming[key] for key in ("timing", "estimated", "window_end", "eps", "revenue")}})
        dividend = entry["dividend"]
        if group == "holding" and dividend:
            for kind, key in (("ex_dividend", "ex_date"), ("dividend_paid", "pay_date")):
                day = dividend[key]
                if day and today <= date.fromisoformat(day) <= horizon:
                    events.append({"date": day, "ticker": ticker, "group": group, "kind": kind})
    events.sort(key=lambda event: (
        event["date"], _KIND_ORDER[event["kind"]], event["group"] != "holding", event["ticker"],
    ))

    states = [
        TickerState(summary["ticker"], summary["group"], research.get(summary["ticker"]), summary["row_run_id"])
        for summary in summaries
    ]
    available = snapshot["status"] != "unavailable"
    return {
        "status": snapshot["status"],
        "retrieved_at": snapshot["retrieved_at"],
        "source": snapshot["source"],
        "today": today.isoformat(),
        "events": events,
        "next_earnings": next_earnings,
        "alerts": evaluate(states, calendar, now),
        "undated": sorted(undated),
        "missing": sorted(ticker for ticker in groups if ticker not in calendar) if available else [],
        "warnings": [*snapshot["warnings"],
                     *(f"{ticker}: {warning}" for ticker, entry in sorted(calendar.items())
                       for warning in entry["warnings"])],
    }


def _research_dates(runs: list[ResearchRun]) -> dict[str, date]:
    """Each ticker's newest full report. Evidence packets carry no verdict, so they don't count."""
    newest: dict[str, date] = {}
    for run in runs:
        if run.is_smoke or run.status != "completed" or run.is_evidence_only:
            continue
        try:
            day = date.fromisoformat(run.analysis_date)
        except ValueError:
            continue
        if run.ticker not in newest or day > newest[run.ticker]:
            newest[run.ticker] = day
    return newest


# ---------- validation ----------

def _entry(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("ticker entry must be an object")
    ticker = raw.get("ticker")
    if not isinstance(ticker, str) or not _TICKER_RE.match(ticker):
        raise ValueError("invalid ticker symbol")
    quote_type = raw.get("quote_type")
    last = _object_or_none(raw.get("last_reported"))
    dividend = _object_or_none(raw.get("dividend"))
    warnings = raw.get("warnings") if isinstance(raw.get("warnings"), list) else []
    return {
        "ticker": ticker,
        "quote_type": str(quote_type)[:40] if quote_type else None,
        "next_earnings": _next_earnings(raw.get("next_earnings")),
        "last_reported": None if last is None else {"date": _date(last.get("date")), "timing": _timing(last.get("timing"))},
        "dividend": None if dividend is None else {
            "ex_date": _optional_date(dividend.get("ex_date")), "pay_date": _optional_date(dividend.get("pay_date")),
        },
        "warnings": [str(warning)[:300] for warning in warnings][:10],
    }


def _next_earnings(raw: Any) -> dict[str, Any] | None:
    value = _object_or_none(raw)
    if value is None:
        return None
    if not isinstance(value.get("estimated"), bool):
        raise ValueError("estimated must be true or false")
    return {
        "date": _date(value.get("date")),
        "window_end": _optional_date(value.get("window_end")),
        "timing": _timing(value.get("timing")),
        "estimated": value["estimated"],
        "eps": _range(value.get("eps")),
        "revenue": _range(value.get("revenue")),
    }


def _range(raw: Any) -> dict[str, float | int | None]:
    value = _object_or_none(raw) or {}
    result = {}
    for key in ("avg", "low", "high"):
        number = value.get(key)
        if number is not None and (isinstance(number, bool) or not isinstance(number, (int, float))
                                   or not math.isfinite(number)):
            raise ValueError(f"estimate {key} must be a finite number or null")
        result[key] = number
    return result


def _object_or_none(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("expected an object or null")
    return raw


def _date(raw: Any) -> str:
    if not isinstance(raw, str) or not _DATE_RE.match(raw):
        raise ValueError(f"invalid date {raw!r}")
    return date.fromisoformat(raw).isoformat()


def _optional_date(raw: Any) -> str | None:
    return None if raw is None else _date(raw)


def _timing(raw: Any) -> str:
    if raw not in _TIMINGS:
        raise ValueError(f"invalid timing {raw!r}")
    return raw
