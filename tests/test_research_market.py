from datetime import date, timedelta

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


def test_market_coverage_uses_status_values_and_engine_keeps_eligible_facts(monkeypatch):
    monkeypatch.setattr(YahooMarketProvider, "_retrieved_at", staticmethod(lambda: "2026-09-16T12:00:00Z"))
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


def test_verified_yahoo_metadata_adds_currency_and_vendor_snapshots(monkeypatch):
    monkeypatch.setattr(YahooMarketProvider, "_retrieved_at", staticmethod(lambda: "2026-09-16T12:00:00Z"))

    class FakeTicker:
        def history(self, **kwargs):
            return pd.DataFrame(
                {"Close": [10.0], "Adj Close": [10.0], "Stock Splits": [0.0], "Dividends": [0.0]},
                index=pd.to_datetime(["2026-09-15"]),
            )

        def get_info(self):
            return {
                "symbol": "ABC",
                "currency": "USD",
                "marketCap": 123_000_000,
                "sharesOutstanding": 12_300_000,
                "quoteType": "EQUITY",
                "exchange": "NYQ",
            }

    packet = YahooMarketProvider(ticker_factory=lambda symbol: FakeTicker()).fetch("ABC", "2026-09-16")
    facts = {fact["metric"]: fact for fact in packet["facts"]}
    assert packet["identity"]["currency"] == "USD"
    assert packet["identity"]["exchange"] == "NYQ"
    assert packet["metadata"]["quote_type"] == "EQUITY"
    assert facts["close"]["unit"] == "USD/share"
    assert facts["market_cap_reported"]["unit"] == "USD"
    assert facts["market_cap_reported"]["period_end"] == "2026-09-16"
    assert facts["market_cap_reported"]["published_at"] == "2026-09-16T12:00:00Z"
    assert facts["shares_outstanding_market"]["unit"] == "shares"
    assert "share classes" in facts["shares_outstanding_market"]["definition"]


def test_mismatched_metadata_cannot_supply_identity_currency_or_snapshots():
    class FakeTicker:
        def history(self, **kwargs):
            return pd.DataFrame(
                {"Close": [10.0], "Adj Close": [10.0], "Stock Splits": [0.0], "Dividends": [0.0]},
                index=pd.to_datetime(["2026-09-15"]),
            )

        def get_info(self):
            return {"symbol": "OTHER", "currency": "CAD", "marketCap": 5, "sharesOutstanding": 1}

    packet = YahooMarketProvider(ticker_factory=lambda symbol: FakeTicker()).fetch("ABC", "2026-09-16")
    assert packet["identity"].get("currency") is None
    assert next(fact for fact in packet["facts"] if fact["metric"] == "close")["unit"] == "native_currency/share"
    assert not {"market_cap_reported", "shares_outstanding_market"} & {
        fact["metric"] for fact in packet["facts"]
    }
    assert any(issue["code"] == "MARKET_METADATA_IDENTITY_UNVERIFIED" for issue in packet["issues"])


def test_missing_metadata_keeps_price_evidence():
    class FakeTicker:
        def history(self, **kwargs):
            return pd.DataFrame(
                {"Close": [10.0], "Adj Close": [10.0], "Stock Splits": [0.0], "Dividends": [0.0]},
                index=pd.to_datetime(["2026-09-15"]),
            )

        def get_info(self):
            return {}

    packet = YahooMarketProvider(ticker_factory=lambda symbol: FakeTicker()).fetch("ABC", "2026-09-16")
    assert {fact["metric"] for fact in packet["facts"]} == {"close", "close_adjusted"}
    assert not any(issue["code"] == "MARKET_METADATA_PROVIDER_ERROR" for issue in packet["issues"])


def test_all_returned_corporate_actions_survive_the_30_day_price_limit():
    cutoff = date(2026, 9, 16)
    days = [cutoff - timedelta(days=35 - index) for index in range(35)]
    rows = pd.DataFrame(
        {
            "Close": [float(index) for index in range(35)],
            "Adj Close": [float(index) for index in range(35)],
            "Stock Splits": [2.0] + [0.0] * 34,
            "Dividends": [0.0] * 35,
        },
        index=pd.to_datetime(days),
    )

    class FakeTicker:
        def history(self, **kwargs):
            return rows

        def get_info(self):
            return {}

    packet = YahooMarketProvider(ticker_factory=lambda symbol: FakeTicker()).fetch("ABC", cutoff)
    close_days = {fact["period_end"] for fact in packet["facts"] if fact["metric"] == "close"}
    split = next(fact for fact in packet["facts"] if fact["metric"] == "split_ratio")
    assert len(close_days) == 30
    assert split["period_end"] == days[0].isoformat()
    assert split["period_end"] not in close_days
    assert "does not establish that no split occurred" in split["definition"]


def test_historical_market_metadata_is_excluded_when_retrieved_after_cutoff(monkeypatch):
    monkeypatch.setattr(YahooMarketProvider, "_retrieved_at", staticmethod(lambda: "2026-09-16T12:00:00Z"))

    class FakeTicker:
        def history(self, **kwargs):
            return pd.DataFrame(index=pd.to_datetime([]))

        def get_info(self):
            return {"symbol": "ABC", "currency": "USD", "marketCap": 10, "sharesOutstanding": 1}

    packet = build_packet("ABC", "2020-01-02", [YahooMarketProvider(ticker_factory=lambda symbol: FakeTicker())])
    assert not packet.facts
    assert {issue.metric for issue in packet.issues if issue.code == "FACT_AFTER_CUTOFF"} == {
        "market_cap_reported",
        "shares_outstanding_market",
    }
