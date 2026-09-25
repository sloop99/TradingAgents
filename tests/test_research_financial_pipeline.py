import json

import pytest

from tradingagents.research import ResearchPacket, build_packet


class Provider:
    def __init__(self, facts):
        self.facts = facts

    def fetch(self, ticker, as_of):
        return {"identity": {"ticker": ticker, "name": "Example issuer", "sic": "7372"},
                "facts": self.facts}


def reported(identifier, value, start, end, published="2026-02-01T12:00:00Z"):
    return {"fact_id": identifier, "metric": "revenue", "value": value, "unit": "USD",
            "period_start": start, "period_end": end, "published_at": published,
            "retrieved_at": "2026-03-01T12:00:00Z", "source_url": "https://example.test/filing",
            "source_tag": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
            "definition": "Revenue excluding assessed tax", "adjustment_basis": "as_reported",
            "kind": "reported"}


def test_revisions_preserve_source_facts_and_respect_cutoff():
    old = reported("original", 100, "2025-01-01", "2025-03-31", "2025-05-01T12:00:00Z")
    new = reported("revised", 105, "2025-01-01", "2025-03-31", "2025-08-01T12:00:00Z")
    provider = Provider([old, new])
    early = build_packet("TEST", "2025-06-01", [provider])
    later = build_packet("TEST", "2026-03-01", [provider])
    assert early.financial_analysis["selected_fact_ids"] == ["original"]
    assert later.financial_analysis["selected_fact_ids"] == ["revised"]
    assert {f.fact_id for f in later.facts} >= {"original", "revised"}
    assert any(i.code == "RESTATED_OR_REVISED_FACT" for i in later.issues)
    restored = ResearchPacket.from_dict(json.loads(json.dumps(later.to_dict())))
    assert restored.financial_analysis == later.financial_analysis
    assert "original]" not in later.render_context()


def test_annual_less_nine_months_produces_auditable_fourth_quarter():
    annual = reported("annual", 11480, "2025-08-01", "2026-07-31", "2026-08-26T12:00:00Z")
    nine = reported("nine", 8070, "2025-08-01", "2026-04-30", "2026-05-26T12:00:00Z")
    packet = build_packet("TEST", "2026-09-16", [Provider([annual, nine])])
    fourth = [fact for fact in packet.facts if fact.metric == "revenue"
              and fact.period_start == "2026-05-01" and fact.period_end == "2026-07-31"]
    assert len(fourth) == 1
    assert fourth[0].value == 3410
    assert set(fourth[0].input_fact_ids) == {"annual", "nine"}
    assert fourth[0].published_at == annual["published_at"]
    assert ResearchPacket.from_dict(packet.to_dict()).to_dict() == packet.to_dict()
    assert "Financial reconciliation" in packet.to_markdown()


@pytest.mark.parametrize("field,value", [("value", 999), ("published_at", "2030-01-01T00:00:00Z"),
                                         ("source_url", "https://example.test/changed")])
def test_selected_views_cannot_override_source_evidence(field, value):
    packet = build_packet("TEST", "2026-03-01", [Provider([
        reported("original", 100, "2025-01-01", "2025-03-31")])])
    payload = packet.to_dict()
    payload["financial_analysis"]["selected_inputs"][0][field] = value
    with pytest.raises(ValueError, match="differs from"):
        ResearchPacket.from_dict(payload)
