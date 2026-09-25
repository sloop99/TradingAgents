"""Sessions dropped from Yahoo's daily feed are rebuilt from its hourly bars.

Flagging a gap (test_ohlcv_missing_sessions) keeps reports honest; rebuilding it
keeps them complete. Hourly bars are unadjusted and miss auction prints, so the
rebuilt bar is scaled to the daily feed using the intact sessions either side.
"""
from __future__ import annotations

import pandas as pd
import pytest

import tradingagents.dataflows.market_data_validator as validator
import tradingagents.dataflows.stockstats_utils as su
import tradingagents.dataflows.y_finance as yfin

# 2026-09-07 is Labor Day.
SESSIONS = pd.bdate_range("2026-09-01", "2026-09-25").difference(pd.DatetimeIndex(["2026-09-07"]))
GAP = pd.Timestamp("2026-09-22")
PRICE_SCALE = 0.99      # daily feed is dividend-adjusted, hourly bars are not
DAILY_VOLUME = 1000     # hourly bars sum to 700; the rest is auction prints


def _hourly(days=SESSIONS, ex_dividend=None):
    """Seven regular-session 1h bars per day. Day i aggregates to
    Open=b, High=b+1.1, Low=b-0.5, Close=b+0.65, Volume=700 with b=30+i.
    Like Yahoo, a dividend is reported on the ex-date's first bar."""
    frames = []
    for i, day in enumerate(days):
        opens = [30.0 + i + 0.1 * k for k in range(7)]
        idx = pd.date_range(f"{day.date()} 09:30", periods=7, freq="h", tz="America/New_York")
        frames.append(pd.DataFrame({
            "Open": opens, "High": [o + 0.5 for o in opens], "Low": [o - 0.5 for o in opens],
            "Close": [o + 0.05 for o in opens], "Volume": [100] * 7,
            "Dividends": [0.2 if day == ex_dividend and k == 0 else 0.0 for k in range(7)],
        }, index=idx))
    return pd.concat(frames)


def _daily(scale=lambda day: PRICE_SCALE):
    """The daily feed for SESSIONS with GAP dropped, as yf.download returns it."""
    rows = []
    for i, day in enumerate(SESSIONS):
        if day == GAP:
            continue
        s = scale(day)
        b = 30.0 + i
        rows.append({"Date": day, "Open": b * s, "High": (b + 1.1) * s, "Low": (b - 0.5) * s,
                     "Close": (b + 0.65) * s, "Volume": DAILY_VOLUME})
    return pd.DataFrame(rows)


def _fake_ticker(hourly=None, daily=None, error=None):
    class FakeTicker:
        def __init__(self, symbol):
            pass

        def history(self, start=None, end=None, interval="1d", **kwargs):
            if error is not None:
                raise error
            if interval == "1h":
                return pd.DataFrame() if hourly is None else hourly.copy()
            return daily.copy()

    return FakeTicker


def _expected_gap_bar(s=PRICE_SCALE):
    b = 30.0 + list(SESSIONS).index(GAP)
    return {"Open": b * s, "High": (b + 1.1) * s, "Low": (b - 0.5) * s,
            "Close": (b + 0.65) * s, "Volume": DAILY_VOLUME}


# Yahoo's adjusted feed scales every row *before* an ex-dividend date.
DIV_FACTOR = 0.995
NEXT_SESSION = SESSIONS[list(SESSIONS).index(GAP) + 1]


@pytest.mark.unit
class TestFillMissingSessions:
    def test_rebuilds_dropped_session_scaled_to_the_daily_feed(self, monkeypatch):
        monkeypatch.setattr(su.yf, "Ticker", _fake_ticker(hourly=_hourly()))
        out = su.fill_missing_sessions(_daily(), "OUST")

        assert su.find_missing_sessions(out["Date"], "OUST") == []
        assert su.rebuilt_sessions(out) == ["2026-09-22"]
        bar = out.loc[out["Date"] == GAP].iloc[0]
        for field, value in _expected_gap_bar().items():
            assert bar[field] == pytest.approx(value), field
        assert list(out["Date"]) == sorted(out["Date"])

    def test_split_between_neighbours_leaves_the_gap(self, monkeypatch):
        # The two sides disagree on price scale (a split), so there is no safe
        # calibration: better flagged than wrong.
        monkeypatch.setattr(su.yf, "Ticker", _fake_ticker(hourly=_hourly()))
        out = su.fill_missing_sessions(_daily(lambda d: 0.5 if d > GAP else PRICE_SCALE), "OUST")

        assert su.find_missing_sessions(out["Date"], "OUST") == ["2026-09-22"]
        assert su.rebuilt_sessions(out) == []

    def test_gap_on_ex_dividend_date_uses_the_later_scale(self, monkeypatch):
        # The ex-date itself is not dividend-adjusted, like the sessions after it.
        monkeypatch.setattr(su.yf, "Ticker", _fake_ticker(hourly=_hourly(ex_dividend=GAP)))
        daily = _daily(lambda d: PRICE_SCALE * (DIV_FACTOR if d < GAP else 1))
        bar = su.fill_missing_sessions(daily, "KO").set_index("Date").loc[GAP]
        assert bar["Close"] == pytest.approx(_expected_gap_bar(PRICE_SCALE)["Close"])

    def test_gap_before_ex_dividend_date_uses_the_earlier_scale(self, monkeypatch):
        monkeypatch.setattr(su.yf, "Ticker", _fake_ticker(hourly=_hourly(ex_dividend=NEXT_SESSION)))
        daily = _daily(lambda d: PRICE_SCALE * (DIV_FACTOR if d < NEXT_SESSION else 1))
        bar = su.fill_missing_sessions(daily, "KO").set_index("Date").loc[GAP]
        assert bar["Close"] == pytest.approx(_expected_gap_bar(PRICE_SCALE * DIV_FACTOR)["Close"])

    def test_no_hourly_data_leaves_the_gap(self, monkeypatch):
        monkeypatch.setattr(su.yf, "Ticker", _fake_ticker(hourly=None))
        out = su.fill_missing_sessions(_daily(), "OUST")
        assert su.find_missing_sessions(out["Date"], "OUST") == ["2026-09-22"]

    def test_hourly_fetch_error_leaves_the_gap(self, monkeypatch):
        monkeypatch.setattr(su.yf, "Ticker", _fake_ticker(error=ConnectionError("down")))
        out = su.fill_missing_sessions(_daily(), "OUST")
        assert su.find_missing_sessions(out["Date"], "OUST") == ["2026-09-22"]

    def test_complete_frame_is_returned_untouched(self, monkeypatch):
        monkeypatch.setattr(su.yf, "Ticker", _fake_ticker(error=AssertionError("must not fetch")))
        frame = _daily()
        frame = pd.concat([frame, pd.DataFrame([{"Date": GAP, **_expected_gap_bar()}])])
        frame = frame.sort_values("Date", ignore_index=True)
        out = su.fill_missing_sessions(frame, "OUST")
        assert "Rebuilt" not in out.columns


@pytest.mark.unit
class TestRebuiltSessionsReachTheReport:
    def test_load_ohlcv_caches_the_rebuilt_bar(self, tmp_path, monkeypatch):
        monkeypatch.setattr(su, "get_config", lambda: {"data_cache_dir": str(tmp_path)})
        monkeypatch.setattr(su.pd.Timestamp, "today", staticmethod(lambda: pd.Timestamp("2026-09-25")))
        monkeypatch.setattr(su.yf, "download", lambda *a, **k: _daily().set_index("Date"))
        monkeypatch.setattr(su.yf, "Ticker", _fake_ticker(hourly=_hourly()))

        out = su.load_ohlcv("OUST", "2026-09-25")
        assert su.rebuilt_sessions(out) == ["2026-09-22"]

        # A later call served from the cache still knows the bar was rebuilt.
        def _fail(*a, **k):
            raise AssertionError("fresh cache must not refetch")

        monkeypatch.setattr(su.yf, "download", _fail)
        cached = su.load_ohlcv("OUST", "2026-09-25")
        assert su.rebuilt_sessions(cached) == ["2026-09-22"]
        assert GAP in set(cached["Date"])

    def test_verified_snapshot_notes_rebuilt_bar(self, monkeypatch):
        monkeypatch.setattr(su.yf, "Ticker", _fake_ticker(hourly=_hourly()))
        data = su.fill_missing_sessions(_daily(), "OUST")
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d: data)

        snap = validator.build_verified_market_snapshot("OUST", "2026-09-25")
        assert "DATA GAP" not in snap
        assert "rebuilt from hourly bars" in snap
        assert "| 2026-09-22 |" in snap

    def test_price_table_includes_rebuilt_row(self, monkeypatch):
        daily = _daily().set_index("Date")
        daily["Dividends"] = 0.0
        daily["Stock Splits"] = 0.0
        monkeypatch.setattr(yfin.yf, "Ticker", _fake_ticker(hourly=_hourly(), daily=daily))

        out = yfin.get_YFin_data_online("OUST", "2026-09-01", "2026-09-25")
        header, table = out.split("\n\n", 1)
        assert "REBUILT" in header and "2026-09-22" in header
        assert "DATA GAP" not in header
        assert "2026-09-22," in table
        assert "Rebuilt" not in table
