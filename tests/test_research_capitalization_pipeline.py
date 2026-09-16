import json

from tradingagents.research import ResearchPacket, build_packet


class Provider:
    def __init__(self, facts):
        self.facts = facts

    def fetch(self, ticker, as_of):
        return {"identity": {"ticker": ticker, "sic": "7372", "currency": "USD"},
                "facts": self.facts}


def observation(identifier, metric, value, definition, unit="USD"):
    return {"fact_id": identifier, "metric": metric, "value": value, "unit": unit,
            "period_end": "2026-09-15", "published_at": "2026-09-16T12:00:00Z",
            "retrieved_at": "2026-09-16T12:00:00Z", "source_url": "https://example.test/filing",
            "definition": definition, "adjustment_basis": "as_reported", "kind": "reported"}


def test_net_debt_retains_lineage_but_missing_market_inputs_withhold_ev():
    facts = [observation("debt", "total_debt", 500, "Complete total interest-bearing debt, all obligations"),
             observation("cash", "cash", 200, "Unrestricted cash and cash equivalents")]
    packet = build_packet("TEST", "2026-09-16", [Provider(facts)])
    net_debt = [f for f in packet.facts if f.metric == "net_debt"]
    assert len(net_debt) == 1 and net_debt[0].value == 300
    assert set(net_debt[0].input_fact_ids) == {"debt", "cash"}
    assert not any(f.metric == "enterprise_value" for f in packet.facts)
    assert packet.coverage["capitalization"] == "partial"
    assert ResearchPacket.from_dict(json.loads(json.dumps(packet.to_dict()))).to_dict() == packet.to_dict()


def test_reported_market_cap_is_not_silently_verified():
    packet = build_packet("TEST", "2026-09-16", [Provider([
        observation("vendor-cap", "market_cap_reported", 100000, "Vendor-reported market capitalization")])])
    assert not any(f.kind.value == "calculated" and f.metric in {"market_cap", "enterprise_value"}
                   for f in packet.facts)
    assert packet.financial_analysis["capitalization"]["status"] in {"partial", "unsupported"}
    assert "Capitalization readiness" in packet.to_markdown()
    assert "unresolved" in packet.to_markdown()
    assert "Capitalization status:" in packet.render_context()
    assert "Vendor market cap is not independently verified" in packet.render_context()


def test_older_selected_input_names_still_roundtrip_without_value_tampering():
    import pytest

    fact = observation("legacy-debt", "long_term_debt_total", 123, "Long-term debt")
    fact["source_tag"] = "us-gaap:LongTermDebt"
    packet = build_packet("TEST", "2026-09-16", [Provider([fact])])
    payload = packet.to_dict()
    original = next(f for f in payload["facts"] if f["fact_id"] == "legacy-debt")
    payload["financial_analysis"]["selected_inputs"] = [dict(original)]
    restored = ResearchPacket.from_dict(payload)
    assert restored.financial_analysis["selected_inputs"][0]["value"] == 123
    payload["financial_analysis"]["selected_inputs"][0]["value"] = 999
    with pytest.raises(ValueError):
        ResearchPacket.from_dict(payload)
