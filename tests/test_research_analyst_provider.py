from tradingagents.research.providers.analyst import YahooAnalystTargetsProvider, validate_snapshot


class MemoryCache:
    def __init__(self):
        self.values = {}
        self.puts = []

    def get_json(self, key, max_age_seconds=None):
        return self.values.get(key)

    def put_json(self, key, value):
        self.values[key] = value
        self.puts.append((key, value))
        return "digest"


def _ticker(*, symbol="ABC", currency="USD", targets=None, count=7):
    class FakeTicker:
        def get_analyst_price_targets(self):
            return targets if targets is not None else {"current": 12, "low": 10, "high": 20, "mean": 15, "median": 14}

        def get_info(self):
            return {"symbol": symbol, "currency": currency, "numberOfAnalystOpinions": count}

    return FakeTicker()


def test_yahoo_aggregate_targets_are_metadata_not_evidence(monkeypatch):
    monkeypatch.setattr(YahooAnalystTargetsProvider, "_retrieved_at", staticmethod(lambda: "2026-09-16T12:00:00Z"))
    packet = YahooAnalystTargetsProvider(ticker_factory=lambda _: _ticker()).fetch("abc", "2999-01-01")
    snapshot = packet["metadata"]["analyst_targets"]
    assert packet["facts"] == []
    assert snapshot == {
        "provider": "yahoo_finance",
        "feed_kind": "aggregate_feed_observation",
        "ticker": "ABC",
        "currency": "USD",
        "retrieved_at": "2026-09-16T12:00:00Z",
        "published_at": None,
        "horizon": "unspecified",
        "values": {"mean": 15, "median": 14, "low": 10, "high": 20},
        "analyst_count": 7,
        "source_url": "https://finance.yahoo.com/quote/ABC/analysis/",
        "limitations": snapshot["limitations"],
    }
    assert "current" not in snapshot["values"]
    assert "upside" in snapshot["limitations"][-1]


def test_historical_cutoff_never_fetches_current_yahoo_targets():
    def factory(_):
        raise AssertionError("historical request must not construct a Yahoo ticker")

    packet = YahooAnalystTargetsProvider(ticker_factory=factory).fetch("ABC", "2020-01-02")
    assert "analyst_targets" not in packet["metadata"]
    assert any(issue["code"] == "ANALYST_TARGETS_HISTORICAL_UNAVAILABLE" for issue in packet["issues"])


def test_identity_and_currency_must_be_verified(monkeypatch):
    monkeypatch.setattr(YahooAnalystTargetsProvider, "_retrieved_at", staticmethod(lambda: "2026-09-16T12:00:00Z"))
    mismatch = YahooAnalystTargetsProvider(ticker_factory=lambda _: _ticker(symbol="OTHER")).fetch("ABC", "2999-01-01")
    bad_currency = YahooAnalystTargetsProvider(ticker_factory=lambda _: _ticker(currency="Usd")).fetch("ABC", "2999-01-01")
    assert "analyst_targets" not in mismatch["metadata"]
    assert any(issue["code"] == "ANALYST_TARGETS_IDENTITY_UNVERIFIED" for issue in mismatch["issues"])
    assert "analyst_targets" not in bad_currency["metadata"]
    assert any(issue["code"] == "ANALYST_TARGETS_CURRENCY_UNVERIFIED" for issue in bad_currency["issues"])


def test_invalid_cached_snapshot_is_quarantined_without_refetch():
    cache = MemoryCache()
    cache.values["analyst_targets:yahoo:v1:ABC"] = {"metadata": {"analyst_targets": {"ticker": "OTHER"}}}

    def factory(_):
        raise AssertionError("invalid cached values must not be silently overwritten")

    packet = YahooAnalystTargetsProvider(cache=cache, ticker_factory=factory).fetch("ABC", "2999-01-01")
    assert "analyst_targets" not in packet["metadata"]
    assert not cache.puts
    assert any(issue["code"] == "ANALYST_TARGETS_CACHE_INVALID" for issue in packet["issues"])


def test_validator_rejects_invalid_target_values_and_after_cutoff(monkeypatch):
    monkeypatch.setattr(YahooAnalystTargetsProvider, "_retrieved_at", staticmethod(lambda: "2026-09-16T12:00:00Z"))
    packet = YahooAnalystTargetsProvider(
        ticker_factory=lambda _: _ticker(targets={"low": 20, "high": 10, "mean": 15, "median": 15})
    ).fetch("ABC", "2999-01-01")
    assert "analyst_targets" not in packet["metadata"]
    assert any(issue["code"] == "ANALYST_TARGETS_INVALID" for issue in packet["issues"])
    raw = {
        "provider": "yahoo_finance", "feed_kind": "aggregate_feed_observation", "ticker": "ABC", "currency": "USD",
        "retrieved_at": "2026-09-17T00:00:00Z", "published_at": None, "horizon": "unspecified",
        "values": {"low": 10, "high": 20, "mean": 15, "median": 15}, "analyst_count": None,
        "source_url": "https://example.test", "limitations": ["aggregate only"],
    }
    assert validate_snapshot(raw, "ABC", "2026-09-16") is None
