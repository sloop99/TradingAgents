"""Earnings and dividend calendar snapshot for every researched ticker.

Yahoo's calendar is live only: a snapshot records what was published when it
was retrieved and cannot be replayed to an earlier date. The dashboard reads
the stored file and never fetches it itself.
"""

from __future__ import annotations

import argparse
import logging
import math
import numbers
from collections.abc import Mapping
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from tradingagents.research.sector_rotation import _atomic_json, _instant, _iso

SOURCE = "Yahoo Finance"

# Yahoo stores report times as fixed UTC instants all year (about 12:00 before
# the open, 20:00 after the close), so November's after-close reports read
# 15:00 EST. Classifying on the UTC clock keeps them after the close.
_BEFORE_OPEN = (time(9, 0), time(14, 30))
_AFTER_CLOSE = time(20, 0)


def build_earnings_calendar(raw_by_ticker: Mapping[str, Mapping[str, Any]], *, retrieved_at: str) -> dict[str, Any]:
    """Build a JSON-safe snapshot from raw yfinance responses.

    Each value holds ``info``, ``calendar`` and ``earnings_dates`` (any may be
    None) and ``errors``, one message per failed call. The caller owns retrieval.
    """
    retrieved = _instant(retrieved_at, "retrieved_at")
    return {
        "retrieved_at": _iso(retrieved),
        "source": SOURCE,
        "tickers": [_entry(ticker, raw_by_ticker[ticker], retrieved) for ticker in sorted(raw_by_ticker)],
        "warnings": [],
    }


def _entry(ticker: str, raw: Mapping[str, Any], retrieved: datetime) -> dict[str, Any]:
    info = raw.get("info") if isinstance(raw.get("info"), Mapping) else {}
    calendar = raw.get("calendar") if isinstance(raw.get("calendar"), Mapping) else {}
    quote_type = info.get("quoteType")
    return {
        "ticker": ticker,
        "quote_type": str(quote_type)[:40] if quote_type else None,
        "next_earnings": _next_earnings(info, calendar, retrieved.date()),
        "last_reported": _last_reported(raw.get("earnings_dates"), retrieved),
        "dividend": _dividend(calendar),
        "warnings": [str(error)[:300] for error in raw.get("errors", [])],
    }


def _next_earnings(info: Mapping[str, Any], calendar: Mapping[str, Any], today: date) -> dict[str, Any] | None:
    start = _epoch(info.get("earningsTimestampStart"))
    # A quote can keep a window that has already passed (DXYZ still carried September 2025).
    if start is not None and start.date() >= today:
        end = _epoch(info.get("earningsTimestampEnd"))
        window_end = end.date() if end is not None and end.date() > start.date() else None
        event = {
            "date": start.date().isoformat(),
            "window_end": window_end.isoformat() if window_end else None,
            "timing": _timing(start),
            "estimated": bool(info.get("isEarningsDateEstimate")) or window_end is not None,
        }
    else:
        listed = calendar.get("Earnings Date") or []
        days = sorted({day for day in map(_as_date, listed if isinstance(listed, (list, tuple)) else [listed])
                       if day is not None and day >= today})
        if not days:
            return None
        event = {
            "date": days[0].isoformat(),
            "window_end": days[-1].isoformat() if len(days) > 1 else None,
            "timing": "unknown",
            "estimated": len(days) > 1,
        }
    event["eps"] = _range(calendar, "Earnings")
    event["revenue"] = _range(calendar, "Revenue")
    return event


def _last_reported(frame: Any, retrieved: datetime) -> dict[str, str] | None:
    """The newest row with a reported EPS, at or before retrieval."""
    if not isinstance(frame, pd.DataFrame) or "Reported EPS" not in frame.columns:
        return None
    latest: pd.Timestamp | None = None
    for stamp, reported in frame["Reported EPS"].items():
        if _number(reported) is None:
            continue
        moment = pd.Timestamp(stamp)
        if moment.tzinfo is None:
            moment = moment.tz_localize("America/New_York")
        if moment.tz_convert("UTC").to_pydatetime() > retrieved:
            continue
        if latest is None or moment > latest:
            latest = moment
    if latest is None:
        return None
    return {
        "date": latest.tz_convert("America/New_York").date().isoformat(),
        "timing": _timing(latest.tz_convert("UTC").to_pydatetime()),
    }


def _dividend(calendar: Mapping[str, Any]) -> dict[str, str | None] | None:
    ex_date = _as_date(calendar.get("Ex-Dividend Date"))
    pay_date = _as_date(calendar.get("Dividend Date"))
    if ex_date is None and pay_date is None:
        return None
    return {
        "ex_date": ex_date.isoformat() if ex_date else None,
        "pay_date": pay_date.isoformat() if pay_date else None,
    }


def _range(calendar: Mapping[str, Any], prefix: str) -> dict[str, float | int | None]:
    return {
        "avg": _number(calendar.get(f"{prefix} Average")),
        "low": _number(calendar.get(f"{prefix} Low")),
        "high": _number(calendar.get(f"{prefix} High")),
    }


def _timing(instant: datetime) -> str:
    clock = instant.astimezone(timezone.utc).time()
    if _BEFORE_OPEN[0] <= clock < _BEFORE_OPEN[1]:
        return "before_open"
    if clock >= _AFTER_CLOSE:
        return "after_close"
    return "unknown"


def _epoch(value: Any) -> datetime | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    return datetime.fromtimestamp(number, tz=timezone.utc)


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return None
    if not math.isfinite(float(value)):
        return None
    return int(value) if isinstance(value, numbers.Integral) else float(value)


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


# ---------- retrieval ----------

def _fetch(ticker: str) -> dict[str, Any]:
    """The three calendar responses for one ticker; a failed call is recorded, not raised."""
    import yfinance as yf  # lazy optional CLI dependency

    handle = yf.Ticker(ticker)
    result: dict[str, Any] = {"info": None, "calendar": None, "earnings_dates": None, "errors": []}
    calls = (
        ("info", lambda: handle.info),
        ("calendar", lambda: handle.calendar),
        ("earnings_dates", lambda: handle.get_earnings_dates(limit=8)),
    )
    for key, call in calls:
        try:
            result[key] = call()
        except Exception as exc:  # yfinance surfaces network, parsing and HTTP failures as varied types
            result["errors"].append(f"{key}: {type(exc).__name__}: {exc}")
    return result


def _fetch_all(tickers: list[str], cache_dir: Path) -> dict[str, dict[str, Any]]:
    import yfinance as yf

    cache_dir.mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(cache_dir.resolve()))
    # Funds and crypto log "no earnings dates" 404s that are expected here.
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    return {ticker: _fetch(ticker) for ticker in tickers}


def _failed(raw: Mapping[str, Any]) -> bool:
    return bool(raw.get("errors")) and all(raw.get(key) is None for key in ("info", "calendar", "earnings_dates"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-root", action="append", type=Path, default=[], help="Research archive root; repeatable")
    parser.add_argument("--output", default=".tradingagents/earnings-calendar.json", help="atomic JSON output path")
    args = parser.parse_args(argv)

    from tradingagents.dashboard.indexer import build_index, default_scan_roots

    roots = args.scan_root or default_scan_roots()
    index = build_index(roots)
    tickers = sorted({run.ticker for run in index.runs
                      if not run.is_smoke and run.status == "completed" and run.ticker != "UNKNOWN"})
    if not tickers:
        raise RuntimeError(f"No researched tickers found under {', '.join(map(str, roots))}")

    output = Path(args.output)
    raw = _fetch_all(tickers, output.parent / "cache" / "yfinance")
    failed = [ticker for ticker in tickers if _failed(raw[ticker])]
    if len(failed) == len(tickers):
        raise RuntimeError("Yahoo returned nothing for any ticker; the existing calendar was preserved")
    snapshot = build_earnings_calendar(raw, retrieved_at=_iso(datetime.now(timezone.utc)))
    _atomic_json(output, snapshot)
    dated = sum(1 for entry in snapshot["tickers"] if entry["next_earnings"])
    undated = len(tickers) - dated - len(failed)
    print(f"Wrote {output} ({dated} dated, {undated} undated, {len(failed)} failed)")
    for ticker in failed:
        print(f"  {ticker}: {'; '.join(raw[ticker]['errors'])}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a module CLI
    raise SystemExit(main())
