import copy

import pytest

from tradingagents.research import ResearchPacket, build_packet
from tradingagents.research.listing_review import summarize_listing_evidence
from tradingagents.research.models import EvidenceDocument


def fixture():
    url = "https://example.test/filing.htm"
    context = {"safe": True, "entity_cik": "123", "signature": "c" * 64,
               "period_start": "2026-01-01", "period_end": "2026-06-30",
               "period_type": "duration", "dimensions": {}}
    filing = {"accession": "acc", "source_url": url, "source_sha256": "a" * 64,
              "published_at": "2026-07-30", "retrieved_at": "2026-09-01",
              "contexts": {"c1": context}, "listing_candidates": []}
    for index, (field, value) in enumerate((("security_title", "Class A common stock"),
                                          ("trading_symbol", "WIDE"), ("exchange_name", "Nasdaq"))):
        filing["listing_candidates"].append({
            **{key: context[key] for key in ("period_start", "period_end", "period_type", "dimensions")},
            "status": "accepted", "context_id": "c1", "context_signature": context["signature"],
            "field": field, "value": value, "source_sha256": filing["source_sha256"],
            "source_locator": f"id:n{index}", "source_url": f"{url}#n{index}"})
    document = EvidenceDocument.from_dict({"document_id": "doc", "accession": "acc",
                                           "source_url": url, "published_at": "2026-07-30", "form": "10-Q"})
    return filing, document


def summarize(filing, document):
    return summarize_listing_evidence([filing], [document], "WIDE", "123", "2026-09-16")


def test_same_context_triplet_is_review_evidence_not_class_proof():
    filing, document = fixture()
    summary = summarize(filing, document)
    assert len(summary["groups"]) == 1
    assert summary["groups"][0]["complete_listing_triplet"]
    assert summary["groups"][0]["requested_symbol_observed"]
    assert summary["groups"][0]["period_type"] == "duration"
    assert not summary["class_completeness_established"]
    assert not summary["adr_conversion_established"]
    assert not summary["split_completeness_established"]


@pytest.mark.parametrize("change", ["future", "hash", "source", "context", "issuer", "malformed"])
def test_unbound_text_is_excluded(change):
    filing, document = fixture()
    if change == "future":
        filing["retrieved_at"] = "2026-09-17"
    for candidate in filing["listing_candidates"]:
        if change == "hash":
            candidate["source_sha256"] = "b" * 64
        elif change == "source":
            candidate["source_url"] = "https://example.test/other.htm#node"
        elif change == "context":
            candidate["period_end"] = "2026-07-01"
        elif change == "issuer":
            filing["contexts"]["c1"]["entity_cik"] = "999"
        elif change == "malformed":
            candidate["context_id"] = []
    summary = summarize(filing, document)
    assert summary["groups"] == [] and summary["rejected_counts"]


def test_classes_are_not_cross_joined_and_conflicting_titles_stay_ambiguous():
    filing, document = fixture()
    extra = copy.deepcopy(filing["listing_candidates"][0])
    extra.update(value="Class B common stock", source_locator="id:extra")
    filing["listing_candidates"].append(extra)
    summary = summarize(filing, document)
    assert summary["groups"][0]["ambiguous_fields"] == ["security_title"]
    assert not summary["groups"][0]["complete_listing_triplet"]
    second_context = dict(filing["contexts"]["c1"], signature="d" * 64)
    filing["contexts"]["c2"] = second_context
    extra.update(context_id="c2", context_signature="d" * 64)
    summary = summarize(filing, document)
    assert len(summary["groups"]) == 2
    assert sum(g["complete_listing_triplet"] for g in summary["groups"]) == 1


def test_packet_retains_and_renders_listing_sources_without_numeric_promotions():
    filing, document = fixture()

    class Provider:
        def fetch(self, ticker, as_of):
            return {"identity": {"ticker": ticker, "cik": "123"}, "facts": [],
                    "documents": [document.to_dict()], "metadata": {"filings": [filing]}}

    packet = build_packet("WIDE", "2026-09-16", [Provider()])
    assert not packet.facts
    assert "Listed-security evidence" in packet.to_markdown()
    assert "Class A common stock" in packet.to_markdown()
    assert ResearchPacket.from_dict(packet.to_dict()).to_dict() == packet.to_dict()
