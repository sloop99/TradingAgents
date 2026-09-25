import json

from tradingagents.research import ResearchPacket, build_packet
from tradingagents.research.__main__ import main
from tradingagents.research.models import EvidenceFact
from tradingagents.research.reconciliation import normalize_concept, reconcile_facts


def candidate(identifier, value, context):
    return {
        "fact_id": identifier, "metric": "filing_cash", "value": value, "unit": "USD",
        "period_end": "2026-06-30", "published_at": "2026-07-30T21:00:00Z",
        "retrieved_at": "2026-09-16T12:00:00Z", "source_url": "https://example.test/filing#" + identifier,
        "source_tag": "us-gaap:CashAndCashEquivalentsAtCarryingValue",
        "definition": "Filing context " + context, "adjustment_basis": "as_reported", "kind": "reported",
    }


def test_dimensioned_filing_facts_cannot_be_promoted_by_standard_source_tag():
    raw = [candidate("one", 12, "parent"), candidate("two", 9, "subsidiary")]
    facts = [EvidenceFact.from_dict(item) for item in raw]
    assert normalize_concept(facts[0]).metric == "filing_cash"
    result = reconcile_facts(facts)
    assert result.selected_facts == []
    assert result.issues == []
    assert len(result.decisions) == 2
    assert all(item["outcome"] == "candidate_only" for item in result.decisions)

    class Provider:
        def fetch(self, ticker, as_of):
            return {"identity": {"ticker": ticker}, "facts": raw}

    packet = build_packet("TEST", "2026-09-16", [Provider()])
    assert len(packet.facts) == 2
    assert packet.financial_analysis["selected_fact_ids"] == []
    assert "Inline filing candidates: 2" in packet.render_context()
    assert "Filing context review" in packet.to_markdown()
    assert ResearchPacket.from_dict(packet.to_dict()).to_dict() == packet.to_dict()


def test_cli_filing_pass_reuses_sec_and_saves_review_payload(tmp_path, monkeypatch):
    from tradingagents.research.providers.filing import SecFilingProvider
    from tradingagents.research.providers.sec import SecEdgarProvider

    calls = []

    def sec_fetch(self, ticker, as_of):
        calls.append(ticker)
        return {"identity": {"ticker": ticker}, "facts": []}

    def filing_fetch(self, ticker, as_of):
        self.sec_provider.fetch(ticker, as_of)
        return {"identity": {"ticker": ticker}, "facts": [candidate("one", 12, "parent")],
                "metadata": {"filings": [{"source_sha256": "test-hash"}]}}

    monkeypatch.setattr(SecEdgarProvider, "fetch", sec_fetch)
    monkeypatch.setattr(SecFilingProvider, "fetch", filing_fetch)
    out = tmp_path / "output"
    assert main(["TEST", "--as-of", "2026-09-16", "--no-market", "--filings",
                 "--cache-dir", str(tmp_path / "cache"), "--output-dir", str(out)]) == 0
    assert calls == ["TEST"]
    review = json.loads((out / "filing-evidence.json").read_text())
    assert review[0]["metadata"]["filings"][0]["source_sha256"] == "test-hash"
    assert json.loads((out / "packet.json").read_text())["facts"][0]["metric"] == "filing_cash"


def test_fixture_cli_with_filings_never_enables_network(tmp_path):
    source = tmp_path / "fixture.json"
    source.write_text(json.dumps({"facts": [candidate("one", 12, "parent")]}))
    out = tmp_path / "out"
    assert main(["TEST", "--as-of", "2026-09-16", "--fixture", str(source), "--filings",
                 "--output-dir", str(out)]) == 0
    assert not (out / "filing-evidence.json").exists()
