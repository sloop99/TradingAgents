"""A trading day silently missing from vendor OHLCV must be flagged.

Yahoo intermittently serves an empty bar for a ticker, sometimes for hours, and
yfinance drops empty rows by default, so the frame arrives one session short with
nothing marking the hole. On 2026-09-23 OUST, CRWD and FTNT all lost 2026-09-22
this way, and the indicators and "verified" snapshot were computed across the gap
with no caveat.
"""
from __future__ import annotations

import logging

import pandas as pd
import pytest

import tradingagents.dataflows.market_data_validator as validator
import tradingagents.dataflows.stockstats_utils as su
import tradingagents.dataflows.y_finance as yfin

# Full-day NYSE closures, hard-coded so the tests don't check the calendar
# against itself.
NYSE_CLOSED = {
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18",
    "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27",
    "2025-12-25",
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
}


def _sessions(start, end, drop=()):
    days = pd.bdate_range(start, end)
    closed = pd.DatetimeIndex(sorted(NYSE_CLOSED | set(drop)))
    return days.difference(closed)


def _frame(dates):
    n = len(dates)
    closes = [30.0 + i * 0.1 for i in range(n)]
    return pd.DataFrame({
        "Date": dates,
        "Open": closes, "High": [c + 1 for c in closes],
        "Low": [c - 1 for c in closes], "Close": closes,
        "Volume": [1_000_000] * n,
    })


@pytest.mark.unit
class TestFindMissingSessions:
    def test_flags_interior_missing_session(self):
        dates = _sessions("2026-08-24", "2026-09-23", drop={"2026-09-22"})
        assert su.find_missing_sessions(dates, "OUST") == ["2026-09-22"]

    def test_exchange_holidays_are_not_gaps(self):
        dates = _sessions("2025-01-02", "2026-12-31")
        assert su.find_missing_sessions(dates, "AAPL", lookback_days=800) == []

    def test_gap_older_than_lookback_is_ignored(self):
        dates = _sessions("2026-01-02", "2026-09-23", drop={"2026-03-10"})
        assert su.find_missing_sessions(dates, "OUST") == []

    def test_missing_latest_bar_is_not_flagged(self):
        # The newest bar may simply not be published yet; only interior holes count.
        dates = _sessions("2026-08-24", "2026-09-22")
        assert su.find_missing_sessions(dates, "OUST") == []

    @pytest.mark.parametrize("symbol", ["BTC-USD", "EURUSD=X", "GC=F", "^GDAXI", "SHOP.TO"])
    def test_other_calendars_are_skipped(self, symbol):
        dates = _sessions("2026-08-24", "2026-09-23", drop={"2026-09-22"})
        assert su.find_missing_sessions(dates, symbol) == []

    def test_us_share_class_symbol_is_checked(self):
        dates = _sessions("2026-08-24", "2026-09-23", drop={"2026-09-22"})
        assert su.find_missing_sessions(dates, "BRK-B") == ["2026-09-22"]


@pytest.mark.unit
class TestGapIsSurfaced:
    def test_verified_snapshot_warns(self, monkeypatch):
        data = _frame(_sessions("2026-05-01", "2026-09-23", drop={"2026-09-22"}))
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d: data)
        snap = validator.build_verified_market_snapshot("OUST", "2026-09-23")
        assert "DATA GAP" in snap
        assert "2026-09-22" in snap

    def test_verified_snapshot_is_quiet_for_complete_data(self, monkeypatch):
        data = _frame(_sessions("2026-05-01", "2026-09-23"))
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d: data)
        snap = validator.build_verified_market_snapshot("OUST", "2026-09-23")
        assert "DATA GAP" not in snap

    # The two tests below cover a gap that can't be rebuilt (Yahoo has no hourly
    # bars either); rebuilding is covered in test_ohlcv_rebuild_sessions.

    def test_price_table_header_flags_unrebuildable_gap(self, monkeypatch):
        frame = _frame(_sessions("2026-08-24", "2026-09-23", drop={"2026-09-22"})).set_index("Date")

        class FakeTicker:
            def __init__(self, symbol):
                pass

            def history(self, start, end, interval="1d", **kwargs):
                return pd.DataFrame() if interval == "1h" else frame.copy()

        monkeypatch.setattr(yfin.yf, "Ticker", FakeTicker)
        out = yfin.get_YFin_data_online("OUST", "2026-08-24", "2026-09-23")
        header = out.split("\n\n")[0]
        assert "DATA GAP" in header
        assert "2026-09-22" in header

    def test_load_ohlcv_warns_when_gap_cannot_be_rebuilt(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr(su, "get_config", lambda: {"data_cache_dir": str(tmp_path)})
        monkeypatch.setattr(su.pd.Timestamp, "today", staticmethod(lambda: pd.Timestamp("2026-09-23")))
        frame = _frame(_sessions("2026-05-01", "2026-09-23", drop={"2026-09-22"})).set_index("Date")
        monkeypatch.setattr(su.yf, "download", lambda *a, **k: frame.copy())

        class NoHourly:
            def __init__(self, symbol):
                pass

            def history(self, *a, **k):
                return pd.DataFrame()

        monkeypatch.setattr(su.yf, "Ticker", NoHourly)

        with caplog.at_level(logging.WARNING, logger=su.logger.name):
            su.load_ohlcv("OUST", "2026-09-23")

        assert "no bar" in caplog.text
        assert "2026-09-22" in caplog.text
