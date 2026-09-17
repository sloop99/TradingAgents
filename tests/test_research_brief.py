from tradingagents.research import build_packet
from tradingagents.research.brief import create_research_brief


def test_brief_keeps_missing_evidence_visible_without_inventing_rating():
    packet = build_packet("TEST", "2026-09-16", [], thesis="Test customer demand")
    brief = create_research_brief(packet)
    assert "Test customer demand" in brief
    assert "No supported calculations" in brief
    assert "No eligible analyst-target snapshot" in brief
    assert "not an AI investment recommendation" in brief
    assert "BUY" not in brief
    assert "SELL" not in brief


def test_brief_distinguishes_vendor_aggregate_from_valuation():
    packet = build_packet("TEST", "2026-09-16", [])
    packet.financial_analysis["analyst_targets"] = {"snapshots": [{
        "values": {"mean": 100}, "currency": "USD",
        "retrieved_at": "2026-09-16T12:00:00Z", "source_url": "https://example.com",
    }]}
    brief = create_research_brief(packet)
    assert "mean=100 USD" in brief
    assert "do not identify constituent firms" in brief
    assert "opinions, not our valuation" in brief
