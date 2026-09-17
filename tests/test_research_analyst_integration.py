import copy

import pytest

from tradingagents.research import build_packet
from tradingagents.research.models import ResearchPacket


def _snapshot(*, ticker="ABC", retrieved_at="2026-09-16T12:00:00Z"):
    return {
        "provider": "yahoo_finance",
        "feed_kind": "aggregate_feed_observation",
        "ticker": ticker,
        "currency": "USD",
        "retrieved_at": retrieved_at,
        "published_at": None,
        "horizon": "unspecified",
        "values": {"mean": 15, "median": 14, "low": 10, "high": 20},
        "analyst_count": 7,
        "source_url": "https://finance.yahoo.com/quote/ABC/analysis/",
        "limitations": ["aggregate vendor observation"],
    }


class AnalystProvider:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def fetch(self, ticker, as_of):
        del ticker, as_of
        return {
            "identity": {"ticker": "ABC"}, "facts": [], "documents": [], "issues": [],
            "coverage": {"analyst_targets": "partial"},
            "metadata": {"analyst_targets": self.snapshot},
        }


def test_engine_targets_roundtrip_render_as_vendor_aggregate_only():
    packet = build_packet("ABC", "2026-09-16", [AnalystProvider(_snapshot())])
    assert packet.financial_analysis["analyst_targets"]["status"] == "partial"
    restored = ResearchPacket.from_dict(packet.to_dict())
    context = restored.render_context(max_facts=0)
    markdown = restored.to_markdown()
    assert "Yahoo aggregate vendor observations only" in context
    assert "target horizon is unknown" in context
    assert "no firms are inferred" in context
    assert "## Analyst targets" in markdown
    assert "not used to calculate return or upside" in markdown
    assert "ABC | USD | 15" in markdown


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("ticker", "OTHER", "invalid snapshot"),
        ("retrieved_at", "2026-09-17T00:00:00Z", "invalid snapshot"),
        ("values", {"mean": 30, "median": 14, "low": 10, "high": 20}, "invalid snapshot"),
    ],
)
def test_packet_roundtrip_rejects_untrusted_analyst_snapshots(field, value, message):
    packet = build_packet("ABC", "2026-09-16", [AnalystProvider(_snapshot())]).to_dict()
    packet = copy.deepcopy(packet)
    packet["financial_analysis"]["analyst_targets"]["snapshots"][0][field] = value
    with pytest.raises(ValueError, match=message):
        ResearchPacket.from_dict(packet)
