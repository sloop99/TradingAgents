"""The OHLCV cache file must be replaced atomically.

Two writers saving the same per-day cache file at once (e.g. parallel tool calls
on a cold cache) truncated and rewrote it concurrently; when the second payload
was a few bytes shorter, the first one's tail survived as a garbage last line
(seen in the AAPL and BRO caches). A write must land whole or not at all.
"""
from __future__ import annotations

import os
import time

import pandas as pd
import pytest

import tradingagents.dataflows.stockstats_utils as su

TODAY = pd.Timestamp("2026-07-18")
OLD = "Date,Close\n2026-07-17,100.0\n"


@pytest.fixture
def stale_cache(tmp_path, monkeypatch):
    """A same-day cache past its TTL, so load_ohlcv downloads and rewrites it."""
    monkeypatch.setattr(su, "get_config", lambda: {"data_cache_dir": str(tmp_path)})
    monkeypatch.setattr(su.pd.Timestamp, "today", staticmethod(lambda: TODAY))
    monkeypatch.setattr(su.yf, "download", lambda *a, **k: pd.DataFrame(
        {"Date": pd.to_datetime(["2026-07-17", "2026-07-18"]), "Close": [100.0, 222.0]}
    ).set_index("Date"))

    start = (TODAY - pd.DateOffset(years=5)).strftime("%Y-%m-%d")
    end = (TODAY + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    f = tmp_path / f"AAPL-YFin-data-{start}-{end}.csv"
    f.write_text(OLD, encoding="utf-8")
    old = time.time() - su.OHLCV_CACHE_TTL_SECONDS - 60
    os.utime(f, (old, old))
    return f


@pytest.mark.unit
def test_interrupted_write_leaves_previous_cache_intact(stale_cache, monkeypatch):
    real_to_csv = pd.DataFrame.to_csv

    def _torn(self, path, *a, **k):
        # Write half the payload, then fail, as a crash or racing writer would.
        text = real_to_csv(self, None, *a, **k)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text[: len(text) // 2])
        raise OSError("disk full")

    monkeypatch.setattr(pd.DataFrame, "to_csv", _torn)
    out = su.load_ohlcv("AAPL", TODAY.strftime("%Y-%m-%d"))

    assert stale_cache.read_text(encoding="utf-8") == OLD
    assert 222.0 in out["Close"].values, "the analysis still gets the fresh download"
    assert [p.name for p in stale_cache.parent.iterdir()] == [stale_cache.name], "no temp file left"


@pytest.mark.unit
def test_replace_blocked_by_open_reader_does_not_fail_the_call(stale_cache, monkeypatch):
    # Windows refuses to replace a file another process has open.
    def _locked(src, dst):
        raise PermissionError(13, "file in use")

    monkeypatch.setattr(su.os, "replace", _locked)
    out = su.load_ohlcv("AAPL", TODAY.strftime("%Y-%m-%d"))

    assert 222.0 in out["Close"].values
    assert [p.name for p in stale_cache.parent.iterdir()] == [stale_cache.name], "no temp file left"
