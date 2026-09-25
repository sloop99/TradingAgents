"""Deterministic sector relative-return rotation snapshot.

This is a transparent relative-performance view, not proprietary RRG math,
capital-flow measurement, or a trading signal.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from contextlib import suppress
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SECTORS: dict[str, str] = {
    "XLB": "Materials",
    "XLC": "Communication Services",
    "XLE": "Energy",
    "XLF": "Financials",
    "XLI": "Industrials",
    "XLK": "Information Technology",
    "XLP": "Consumer Staples",
    "XLRE": "Real Estate",
    "XLU": "Utilities",
    "XLV": "Health Care",
    "XLY": "Consumer Discretionary",
}

_LOOKBACK_WEEKS = 24  # 13 weeks for relative return + 4 for its change + 7 trail points.
_TRAIL_WEEKS = 8
_METHOD = {
    "name": "13-week relative return and 4-week change",
    "prices": "Daily Close values supplied by the caller from auto_adjust=True data.",
    "weekly_sampling": "For each complete Friday-ending calendar week, use SPY's last session and require every ETF to have a close on that same session date.",
    "x": "Sector 13-week simple return minus SPY 13-week simple return, in percentage points.",
    "y": "x at this Friday-ending week minus x four calendar weeks earlier, in percentage points.",
    "trail": "Up to eight Friday-ending calendar weeks. Missing weeks are not compressed.",
    "limits": "Transparent custom relative-performance coordinates; not proprietary RRG math, flow estimates, or buy/sell signals.",
}


def _instant(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp with a UTC offset") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _session_date(value: Any) -> date | None:
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(stamp):
        return None
    # Keep the exchange-local session date for timezone-aware daily indices.
    return stamp.date()


def _clean_history(frame: pd.DataFrame, symbol: str, cutoff: date, warnings: list[str]) -> dict[date, float]:
    if not isinstance(frame, pd.DataFrame) or "Close" not in frame.columns:
        warnings.append(f"{symbol}: missing daily Close column")
        return {}

    closes: dict[date, float] = {}
    seen: set[date] = set()
    invalid = 0
    future = 0
    weekend = 0
    for index_value, raw_close in frame["Close"].items():
        session = _session_date(index_value)
        if session is None:
            invalid += 1
            continue
        if session.weekday() >= 5:
            weekend += 1
            continue
        if session in seen:
            warnings.append(f"{symbol}: duplicate daily date {session.isoformat()}; series withheld")
            return {}
        seen.add(session)
        if session > cutoff:
            future += 1
            continue
        try:
            if isinstance(raw_close, bool) or type(raw_close).__name__ == "bool_":
                invalid += 1
                continue
            close = float(raw_close)
        except (TypeError, ValueError, OverflowError):
            invalid += 1
            continue
        if not math.isfinite(close) or close <= 0:
            invalid += 1
            continue
        closes[session] = close
    if invalid:
        warnings.append(f"{symbol}: ignored {invalid} malformed or nonfinite/nonpositive daily value(s)")
    if future:
        warnings.append(f"{symbol}: ignored {future} future daily bar(s)")
    if weekend:
        warnings.append(f"{symbol}: ignored {weekend} weekend daily bar(s)")
    return closes


def _friday(session: date) -> date:
    return date.fromordinal(session.toordinal() + (4 - session.weekday()) % 7)


def _weekly_benchmark(closes: dict[date, float], as_of: date) -> dict[date, tuple[date, float]]:
    by_week: dict[date, tuple[date, float]] = {}
    for session, close in closes.items():
        week = _friday(session)
        # A Friday-ending week is complete only after its Friday has passed.
        if week >= as_of:
            continue
        if week not in by_week or session > by_week[week][0]:
            by_week[week] = (session, close)
    return by_week


def _relative_return(week: date, weekly_values: dict[date, float], benchmark_values: dict[date, float]) -> float:
    old_week = date.fromordinal(week.toordinal() - 13 * 7)
    sector_return = weekly_values[week] / weekly_values[old_week] - 1.0
    benchmark_return = benchmark_values[week] / benchmark_values[old_week] - 1.0
    return sector_return - benchmark_return


def build_sector_rotation(
    histories: dict[str, pd.DataFrame],
    *,
    as_of: str,
    retrieved_at: str,
    benchmark: str = "SPY",
    source: str = "Yahoo Finance",
) -> dict[str, Any]:
    """Build a JSON-safe sector snapshot from adjusted daily closes.

    The caller owns retrieval and must request yfinance ``auto_adjust=True``.
    ``as_of`` and ``retrieved_at`` must include a timezone offset; returned
    timestamps are canonical UTC strings.
    """
    as_of_dt = _instant(as_of, "as_of")
    retrieved_dt = _instant(retrieved_at, "retrieved_at")
    cutoff = as_of_dt.date()
    warnings: list[str] = []
    if retrieved_dt > as_of_dt:
        warnings.append(
            "Replay limitation: these prices were retrieved after the requested as_of cutoff; "
            "this is a historical replay, not a contemporaneous snapshot."
        )

    benchmark = str(benchmark).strip().upper()
    benchmark_frame = histories.get(benchmark)
    if benchmark_frame is None:
        raise ValueError(f"benchmark history {benchmark} is required")
    benchmark_daily = _clean_history(benchmark_frame, benchmark, cutoff, warnings)
    benchmark_weeks = _weekly_benchmark(benchmark_daily, cutoff)
    if len(benchmark_weeks) <= _LOOKBACK_WEEKS:
        raise ValueError(f"benchmark {benchmark} has insufficient complete weekly history")

    latest_week = max(benchmark_weeks)
    days_since_friday = (cutoff.weekday() - 4) % 7 or 7
    expected_latest_week = cutoff - timedelta(days=days_since_friday)
    if latest_week != expected_latest_week:
        raise ValueError(
            f"benchmark {benchmark} is stale: latest complete week {latest_week.isoformat()} "
            f"does not match expected {expected_latest_week.isoformat()}"
        )
    # Every coordinate in the latest eight-week trail depends on prices back to t-24w.
    required_weeks = [date.fromordinal(latest_week.toordinal() - 7 * n) for n in range(_LOOKBACK_WEEKS + 1)]
    missing_benchmark = [week for week in required_weeks if week not in benchmark_weeks]
    if missing_benchmark:
        raise ValueError(f"benchmark {benchmark} is missing required complete weekly observations")

    benchmark_values = {week: benchmark_weeks[week][1] for week in required_weeks}
    points: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for symbol, sector in SECTORS.items():
        frame = histories.get(symbol)
        if frame is None:
            excluded.append({"symbol": symbol, "reason": "missing_history"})
            continue
        daily = _clean_history(frame, symbol, cutoff, warnings)
        if not daily:
            excluded.append({"symbol": symbol, "reason": "invalid_or_duplicate_history"})
            continue

        weekly_values: dict[date, float] = {}
        missing: list[date] = []
        for week in required_weeks:
            benchmark_session = benchmark_weeks[week][0]
            close = daily.get(benchmark_session)
            if close is None:
                missing.append(week)
            else:
                weekly_values[week] = close
        if missing:
            if latest_week not in weekly_values:
                reason = "stale_latest_week"
            else:
                reason = "missing_required_weekly_observation"
            excluded.append({"symbol": symbol, "reason": reason})
            warnings.append(
                f"{symbol}: missing {len(missing)} benchmark-session observation(s) in the required weekly grid; withheld"
            )
            continue

        trail: list[dict[str, float | str]] = []
        coordinates_finite = True
        for weeks_ago in range(_TRAIL_WEEKS - 1, -1, -1):
            week = date.fromordinal(latest_week.toordinal() - weeks_ago * 7)
            x = _relative_return(week, weekly_values, benchmark_values) * 100.0
            prior_x = _relative_return(date.fromordinal(week.toordinal() - 4 * 7), weekly_values, benchmark_values) * 100.0
            y = x - prior_x
            if not math.isfinite(x) or not math.isfinite(y):
                coordinates_finite = False
                break
            trail.append({"date": week.isoformat(), "x": x, "y": y})

        if not coordinates_finite:
            excluded.append({"symbol": symbol, "reason": "nonfinite_return_calculation"})
            warnings.append(f"{symbol}: nonfinite return calculation; series withheld")
            continue

        latest_x = float(trail[-1]["x"])
        latest_y = float(trail[-1]["y"])
        sector_abs = (weekly_values[latest_week] / weekly_values[date.fromordinal(latest_week.toordinal() - 13 * 7)] - 1.0) * 100.0
        benchmark_abs = (benchmark_values[latest_week] / benchmark_values[date.fromordinal(latest_week.toordinal() - 13 * 7)] - 1.0) * 100.0
        if not math.isfinite(sector_abs) or not math.isfinite(benchmark_abs):
            excluded.append({"symbol": symbol, "reason": "nonfinite_return_calculation"})
            warnings.append(f"{symbol}: nonfinite absolute return calculation; series withheld")
            continue
        points.append(
            {
                "symbol": symbol,
                "sector": sector,
                "x": latest_x,
                "y": latest_y,
                "absolute_return_13w": float(sector_abs),
                "relative_return_13w": latest_x,
                "relative_change_4w": latest_y,
                "as_of": latest_week.isoformat(),
                "trail": trail,
            }
        )

    warnings.append(f"Latest complete benchmark week: {latest_week.isoformat()} ({benchmark} last session).")
    return {
        "as_of": _iso(as_of_dt),
        "retrieved_at": _iso(retrieved_dt),
        "source": str(source),
        "methodology": dict(_METHOD),
        "benchmark": benchmark,
        "data_freshness": {
            "latest_complete_week": latest_week.isoformat(),
            "benchmark_last_session": benchmark_weeks[latest_week][0].isoformat(),
            "requested_cutoff": _iso(as_of_dt),
            "replay": retrieved_dt > as_of_dt,
        },
        "points": points,
        "excluded": excluded,
        "warnings": warnings,
    }


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, allow_nan=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        with suppress(OSError):
            os.unlink(temporary_name)
        raise


def _download_histories(cache_dir: Path) -> dict[str, pd.DataFrame]:
    """Fetch all instruments in one bounded, no-paid-API Yahoo request."""
    import yfinance as yf  # lazy optional CLI dependency

    cache_dir.mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(cache_dir.resolve()))
    symbols = [*SECTORS, "SPY"]
    raw = yf.download(
        tickers=symbols,
        period="2y",
        interval="1d",
        auto_adjust=True,
        group_by="ticker",
        progress=False,
        threads=False,
        timeout=12,
        multi_level_index=True,
    )
    histories: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return histories
    for symbol in symbols:
        try:
            frame = raw[symbol]
        except (KeyError, TypeError):
            continue
        if isinstance(frame, pd.DataFrame):
            histories[symbol] = frame
    return histories


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", help="UTC-aware ISO-8601 cutoff; defaults to now")
    parser.add_argument("--output", default=".tradingagents/sector-rotation.json", help="atomic JSON output path")
    args = parser.parse_args(argv)
    output = Path(args.output)
    histories = _download_histories(output.parent / "cache" / "yfinance")
    retrieved_at = _iso(datetime.now(timezone.utc))
    as_of = args.as_of or retrieved_at
    payload = build_sector_rotation(
        histories,
        as_of=as_of,
        retrieved_at=retrieved_at,
    )
    if histories.get("SPY") is None or not payload["points"]:
        raise RuntimeError("Yahoo retrieval did not produce a valid benchmark and sector snapshot; output was preserved")
    _atomic_json(output, payload)
    print(f"Wrote {output} ({len(payload['points'])} sectors; {len(payload['excluded'])} withheld)")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a module CLI
    raise SystemExit(main())
