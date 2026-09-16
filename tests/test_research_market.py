from datetime import date

import pandas as pd

from tradingagents.research import build_packet
from tradingagents.research.providers.market import YahooMarketProvider


class MemoryCache:
    def __init__(self):
        self.values = {}
        self.get_calls = []

    def get_json(self, key, max_age_seconds=None):
        self.get_calls.append((key, max_age_seconds))
        return self.values.get(key)

    def put_json(self, key, payload):
        self.values[key] = payload
        return "digest"


def test_daily_prices_actions_and_current_day_excluded():
    rows = pd.DataFrame(
        {
            "Close": [10.0, 11.0, 12.0],
            "Adj Close": [9.5, 10.5, 11.5],
            "Stock Splits": [0.0, 2.0, 0.0],
            "Dividends": [0.0, 0.0, 0.25],
        },
        index=pd.to_datetime(["2026-09-12", "2026-09-15", "2026-09-16"]),
    )

    class FakeTicker:
        def history(self, **kwargs):
            assert kwargs["auto_adjust"] is False
            assert kwargs["actions"] is True
            return rows

    packet = YahooMarketProvider(ticker_factory=lambda symbol: FakeTicker()).fetch("oust", "2026-09-16")
    assert {fact["period_end"] for fact in packet["facts"]} == {"2026-09-12", "2026-09-15"}
    assert {fact["metric"] for fact in packet["facts"]} == {
        "close",
        "close_adjusted",
        "split_ratio",
    }
    assert all("MARKET_PUBLICATION_PROXY" in packet["issues"][0]["code"] for _ in [0])
    assert all("not a verified exchange publication timestamp" in fact["definition"] for fact in packet["facts"])
    assert next(fact for fact in packet["facts"] if fact["metric"] == "close")["adjustment_basis"] == "vendor_split_adjusted"


def test_historical_fetch_warns_about_unverified_vintage():
    class FakeTicker:
        def history(self, **kwargs):
            return pd.DataFrame(index=pd.to_datetime([]))

    packet = YahooMarketProvider(ticker_factory=lambda symbol: FakeTicker()).fetch("ABC", date(2020, 1, 2))
    assert any(issue["code"] == "MARKET_HISTORICAL_VINTAGE_UNVERIFIED" for issue in packet["issues"])


def test_network_failure_is_graceful_and_cached():
    cache = MemoryCache()
    calls = {"count": 0}

    class FakeTicker:
        def history(self, **kwargs):
            calls["count"] += 1
            raise RuntimeError("rate limited")

    provider = YahooMarketProvider(cache=cache, ticker_factory=lambda symbol: FakeTicker())
    first = provider.fetch("ABC", "2026-09-16")
    second = provider.fetch("ABC", "2026-09-16")
    assert calls["count"] == 1
    assert first == second
    assert first["facts"] == []
    assert any(issue["code"] == "MARKET_PROVIDER_ERROR" for issue in first["issues"])


def test_market_coverage_uses_status_values_and_engine_keeps_eligible_facts():
    class FakeTicker:
        def history(self, **kwargs):
            return pd.DataFrame(
                {
                    "Close": [10.0],
                    "Adj Close": [10.0],
                    "Stock Splits": [0.0],
                    "Dividends": [0.0],
                },
                index=pd.to_datetime(["2026-09-15"]),
            )

    provider = YahooMarketProvider(ticker_factory=lambda symbol: FakeTicker())
    packet = build_packet("ABC", "2026-09-16", [provider])
    assert packet.facts
    assert packet.coverage["facts"] == "sufficient"
    assert not any(issue.code == "INVALID_PROVIDER_RECORD" for issue in packet.issues)
