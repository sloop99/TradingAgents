import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from tradingagents.research import earnings_calendar
from tradingagents.research.earnings_calendar import build_earnings_calendar

RETRIEVED = "2026-09-25T20:05:00Z"


def ts(year, month, day, hour=20, minute=0):
    """A Yahoo-style epoch: report times are fixed UTC instants."""
    return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp())


def raw(info=None, calendar=None, earnings_dates=None, errors=()):
    return {"info": info, "calendar": calendar, "earnings_dates": earnings_dates, "errors": list(errors)}


def aapl_calendar(**overrides):
    value = {
        "Dividend Date": date(2026, 8, 12), "Ex-Dividend Date": date(2026, 8, 9),
        "Earnings Date": [date(2026, 10, 29)],
        "Earnings High": 2.07, "Earnings Low": 1.93, "Earnings Average": 1.98124,
        "Revenue High": 117219700000, "Revenue Low": 112248100000, "Revenue Average": 113624521680,
    }
    value.update(overrides)
    return value


def quote(start, end=None, estimate=False, quote_type="EQUITY"):
    return {"quoteType": quote_type, "isEarningsDateEstimate": estimate,
            "earningsTimestampStart": start, "earningsTimestampEnd": end if end is not None else start}


def history(*rows):
    """Rows of (New York timestamp, EPS estimate, reported EPS), newest first like Yahoo."""
    index = pd.DatetimeIndex([pd.Timestamp(stamp, tz="America/New_York") for stamp, _, _ in rows])
    return pd.DataFrame(
        {"EPS Estimate": [row[1] for row in rows], "Reported EPS": [row[2] for row in rows],
         "Surprise(%)": [float("nan")] * len(rows)},
        index=index,
    )


def only(snapshot):
    (entry,) = snapshot["tickers"]
    return entry


def test_next_report_comes_from_the_quote_window_with_consensus_estimates():
    snapshot = build_earnings_calendar(
        {"AAPL": raw(quote(ts(2026, 10, 29)), aapl_calendar())}, retrieved_at=RETRIEVED)
    assert snapshot["retrieved_at"] == RETRIEVED
    assert snapshot["source"] == "Yahoo Finance"
    entry = only(snapshot)
    assert entry["ticker"] == "AAPL"
    assert entry["quote_type"] == "EQUITY"
    assert entry["next_earnings"] == {
        "date": "2026-10-29", "window_end": None, "timing": "after_close", "estimated": False,
        "eps": {"avg": 1.98124, "low": 1.93, "high": 2.07},
        "revenue": {"avg": 113624521680, "low": 112248100000, "high": 117219700000},
    }
    assert entry["dividend"] == {"ex_date": "2026-08-09", "pay_date": "2026-08-12"}
    assert entry["warnings"] == []


@pytest.mark.parametrize(("stamp", "timing"), [
    (ts(2026, 11, 10, 12, 30), "before_open"),   # LUNR: 07:30 EST
    (ts(2026, 10, 29, 12, 0), "before_open"),    # CAT: 08:00 EDT
    (ts(2026, 11, 17, 20, 0), "after_close"),    # NVDA: reads 15:00 EST, still after the close
    (ts(2026, 10, 29, 0, 0), "unknown"),         # midnight placeholder
    (ts(2026, 10, 29, 17, 0), "unknown"),        # mid-session
])
def test_report_timing_is_classified_from_the_utc_clock(stamp, timing):
    entry = only(build_earnings_calendar({"X": raw(quote(stamp), {})}, retrieved_at=RETRIEVED))
    assert entry["next_earnings"]["timing"] == timing


def test_estimated_dates_are_flagged():
    entry = only(build_earnings_calendar(
        {"LUNR": raw(quote(ts(2026, 11, 10, 12, 30), estimate=True), {})}, retrieved_at=RETRIEVED))
    assert entry["next_earnings"]["estimated"] is True


def test_a_multi_day_window_is_estimated_and_keeps_its_end():
    entry = only(build_earnings_calendar(
        {"X": raw(quote(ts(2026, 11, 3), ts(2026, 11, 6)), {})}, retrieved_at=RETRIEVED))
    assert entry["next_earnings"]["date"] == "2026-11-03"
    assert entry["next_earnings"]["window_end"] == "2026-11-06"
    assert entry["next_earnings"]["estimated"] is True


def test_a_quote_window_in_the_past_is_ignored():
    # DXYZ's quote still carries its September 2025 window.
    dxyz = raw(quote(1756983540, 1757332800, estimate=True), {"Earnings Date": [], "Earnings Average": None})
    entry = only(build_earnings_calendar({"DXYZ": dxyz}, retrieved_at=RETRIEVED))
    assert entry["next_earnings"] is None


def test_calendar_dates_are_the_fallback_without_a_quote_window():
    calendar = aapl_calendar(**{"Earnings Date": [date(2026, 11, 3), date(2026, 11, 7)]})
    entry = only(build_earnings_calendar({"X": raw({"quoteType": "EQUITY"}, calendar)}, retrieved_at=RETRIEVED))
    assert entry["next_earnings"]["date"] == "2026-11-03"
    assert entry["next_earnings"]["window_end"] == "2026-11-07"
    assert entry["next_earnings"]["estimated"] is True
    assert entry["next_earnings"]["timing"] == "unknown"


def test_missing_estimates_become_null():
    calendar = aapl_calendar(**{"Earnings Average": float("nan"), "Earnings Low": None, "Revenue High": float("inf")})
    entry = only(build_earnings_calendar({"X": raw(quote(ts(2026, 10, 29)), calendar)}, retrieved_at=RETRIEVED))
    assert entry["next_earnings"]["eps"] == {"avg": None, "low": None, "high": 2.07}
    assert entry["next_earnings"]["revenue"]["high"] is None


def test_last_reported_comes_from_the_earnings_history():
    dates = history(
        ("2026-10-29 16:00", 1.98, float("nan")),   # upcoming: not reported yet
        ("2026-07-30 16:00", 1.89, 2.02),
        ("2026-04-30 16:00", 1.94, 2.01),
    )
    entry = only(build_earnings_calendar({"AAPL": raw(quote(ts(2026, 10, 29)), {}, dates)}, retrieved_at=RETRIEVED))
    assert entry["last_reported"] == {"date": "2026-07-30", "timing": "after_close"}


def test_last_reported_ignores_rows_after_retrieval():
    dates = history(("2026-10-29 16:00", 1.98, 2.10), ("2026-07-30 07:00", 1.89, 2.02))
    entry = only(build_earnings_calendar({"X": raw(None, {}, dates)}, retrieved_at=RETRIEVED))
    assert entry["last_reported"] == {"date": "2026-07-30", "timing": "before_open"}


def test_a_fund_with_no_calendar_is_undated_without_warnings():
    entry = only(build_earnings_calendar(
        {"SPY": raw({"quoteType": "ETF", "earningsTimestampStart": None}, {}, None)}, retrieved_at=RETRIEVED))
    assert entry == {"ticker": "SPY", "quote_type": "ETF", "next_earnings": None, "last_reported": None,
                     "dividend": None, "warnings": []}


def test_a_failed_call_becomes_a_warning_on_its_ticker():
    entry = only(build_earnings_calendar(
        {"X": raw(quote(ts(2026, 10, 29)), None, None, errors=["calendar: TimeoutError: timed out"])},
        retrieved_at=RETRIEVED))
    assert entry["warnings"] == ["calendar: TimeoutError: timed out"]
    assert entry["next_earnings"]["eps"] == {"avg": None, "low": None, "high": None}


def test_tickers_are_listed_in_order():
    snapshot = build_earnings_calendar({"NVDA": raw(), "AAPL": raw()}, retrieved_at=RETRIEVED)
    assert [entry["ticker"] for entry in snapshot["tickers"]] == ["AAPL", "NVDA"]


def test_snapshot_is_plain_json():
    snapshot = build_earnings_calendar(
        {"AAPL": raw(quote(ts(2026, 10, 29)), aapl_calendar(), history(("2026-07-30 16:00", 1.89, 2.02)))},
        retrieved_at=RETRIEVED)
    assert json.loads(json.dumps(snapshot, allow_nan=False)) == snapshot


# ---------- CLI ----------

def _archive(root: Path, *tickers: str) -> Path:
    for ticker in tickers:
        run = root / "runs" / f"{ticker}_2026-09-23_full"
        (run / "report" / "5_portfolio").mkdir(parents=True)
        (run / "report" / "complete_report.md").write_text(f"# Trading Analysis Report: {ticker}", encoding="utf-8")
        (run / "report" / "5_portfolio" / "decision.md").write_text("**Rating**: Hold", encoding="utf-8")
        (run / "run_manifest.json").write_text(json.dumps(
            {"ticker": ticker, "analysis_date": "2026-09-23", "status": "completed", "final_rating": "Hold"}),
            encoding="utf-8")
    return root


def test_cli_collects_every_archived_ticker(tmp_path, monkeypatch, capsys):
    root = _archive(tmp_path / "archive", "NVDA", "AAPL", "DXYZ")
    fetched = []

    def fake_fetch(ticker):
        fetched.append(ticker)
        if ticker == "DXYZ":
            return raw({"quoteType": "EQUITY"}, {})
        return raw(quote(ts(2026, 10, 29)), {})

    monkeypatch.setattr(earnings_calendar, "_fetch", fake_fetch)
    output = tmp_path / "earnings-calendar.json"
    assert earnings_calendar.main(["--scan-root", str(root), "--output", str(output)]) == 0
    assert sorted(fetched) == ["AAPL", "DXYZ", "NVDA"]
    snapshot = json.loads(output.read_text(encoding="utf-8"))
    assert [entry["ticker"] for entry in snapshot["tickers"]] == ["AAPL", "DXYZ", "NVDA"]
    assert "(2 dated, 1 undated, 0 failed)" in capsys.readouterr().out


def test_cli_keeps_the_existing_file_when_every_ticker_fails(tmp_path, monkeypatch):
    root = _archive(tmp_path / "archive", "AAPL", "NVDA")
    output = tmp_path / "earnings-calendar.json"
    output.write_text('{"previous": true}', encoding="utf-8")
    monkeypatch.setattr(earnings_calendar, "_fetch", lambda ticker: raw(errors=["info: x", "calendar: x", "dates: x"]))
    with pytest.raises(RuntimeError, match="preserved"):
        earnings_calendar.main(["--scan-root", str(root), "--output", str(output)])
    assert output.read_text(encoding="utf-8") == '{"previous": true}'
