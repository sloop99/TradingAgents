import copy

import pytest

from tradingagents.research import ResearchPacket, build_packet
from tradingagents.research.capitalization import analyze_capitalization
from tradingagents.research.models import BusinessModel, EvidenceFact
from tradingagents.research.reviewed_inputs import (
    apply_reviewed_inputs,
    draft_review_manifest,
    evidence_sha256,
)


def fact(key, metric, value, *, end="2026-06-30", start=None, unit="ratio", tag=None):
    return EvidenceFact.from_dict({
        "fact_id": key, "metric": metric, "value": value, "unit": unit,
        "period_start": start, "period_end": end, "source_tag": tag,
        "source_url": "https://example.test/report#" + key,
        "published_at": "2026-09-01T10:00:00Z", "retrieved_at": "2026-09-01T12:00:00Z",
        "definition": "Controlled source", "adjustment_basis": "as_reported", "kind": "reported",
    })


def source():
    return fact("ratio", "filing_stock_split_conversion_ratio", 4, start="2026-01-01",
                unit="pure", tag="us-gaap:StockSplitConversionRatio1")


def manifest(facts, as_of="2026-09-16"):
    return {"schema_version": 1, "ticker": "TEST", "as_of": as_of,
            "base_evidence_sha256": evidence_sha256(facts),
            "reviewer": {"name": "Test reviewer", "reviewed_at": "2026-09-16T12:00:00Z",
                         "rationale": "Reviewed the source event disclosure and share scope."},
            "rules": [{"rule_id": "event", "output_metric": "split_ratio", "operation": "copy",
                       "input_fact_ids": ["ratio"], "effective_date": "2026-07-01",
                       "date_basis": "first_split_adjusted_trading_date",
                       "effective_date_source_url": "https://example.test/report#event-date",
                       "attestations": ["effective_date_confirmed", "new_shares_per_old_share",
                                        "applies_to_all_counted_common_shares"],
                       "rationale": "Controlled source discloses the effective date and a four-for-one ratio."}]}


def test_filing_period_is_not_the_reviewed_effective_date_and_lineage_is_preserved():
    original = source()
    result = apply_reviewed_inputs([original], manifest([original]), "TEST", "2026-09-16")
    event = result.derived_facts[0]
    assert event.period_end == "2026-07-01" and event.period_start is None
    assert event.value == 4 and event.unit == "ratio"
    assert event.input_fact_ids == (original.fact_id,)
    assert "2026-06-30" in event.definition
    assert "split_history" in result.summary["unresolved_proofs"]
    assert result.summary["rule_decisions"][0]["effective_date"] == "2026-07-01"
    draft = draft_review_manifest([original], "TEST", "2026-09-16")
    assert len(draft["candidates"]["split_ratio"]) == 1
    assert not apply_reviewed_inputs([original], draft, "TEST", "2026-09-16").derived_facts


@pytest.mark.parametrize("change", ["missing_date", "date_basis", "future", "invalid", "citation", "scope", "sum", "hash"])
def test_incomplete_or_changed_event_review_rejects(change):
    original = source()
    review = manifest([original])
    rule = review["rules"][0]
    if change == "missing_date":
        del rule["effective_date"]
    elif change == "date_basis":
        rule["date_basis"] = "record_date"
    elif change == "future":
        rule["effective_date"] = "2026-09-17"
    elif change == "invalid":
        rule["effective_date"] = "2026-02-30"
    elif change == "citation":
        rule["effective_date_source_url"] = "https://example.test/other-report"
    elif change == "scope":
        rule["attestations"].remove("applies_to_all_counted_common_shares")
    elif change == "sum":
        rule["operation"] = "sum"
    else:
        review["base_evidence_sha256"] = "0" * 64
    assert not apply_reviewed_inputs([original], review, "TEST", "2026-09-16").derived_facts


def test_multiple_events_allowed_but_duplicate_dates_and_dated_event_moves_reject():
    first = fact("ratio", "split_ratio", 4, end="2026-07-01")
    second = fact("second", "split_ratio", 0.5, end="2026-08-01")
    # Vendor event rows may share a source page without sharing an event.
    first = EvidenceFact.from_dict(dict(first.to_dict(), source_url="https://example.test/report"))
    second = EvidenceFact.from_dict(dict(second.to_dict(), source_url=first.source_url))
    review = manifest([first, second])
    other = copy.deepcopy(review["rules"][0])
    other.update(rule_id="second", input_fact_ids=["second"], effective_date="2026-08-01")
    review["rules"].append(other)
    result = apply_reviewed_inputs([first, second], review, "TEST", "2026-09-16")
    assert [f.value for f in sorted(result.derived_facts, key=lambda f: f.period_end)] == [4, 0.5]
    other["effective_date"] = "2026-07-01"
    assert not apply_reviewed_inputs([first, second], review, "TEST", "2026-09-16").derived_facts
    review["rules"] = [other]
    assert not apply_reviewed_inputs([first, second], review, "TEST", "2026-09-16").derived_facts


def test_reviewed_event_enters_split_arithmetic_only_with_complete_interval_proof():
    original = source()
    event = apply_reviewed_inputs([original], manifest([original]), "TEST", "2026-09-16").derived_facts[0]
    shares = fact("shares", "current_shares", 100, unit="shares")
    price = EvidenceFact.from_dict(dict(fact("price", "current_share_price", 10,
                                           end="2026-09-15", unit="USD/share").to_dict(), adjustment_basis="current_quote"))
    inputs = [shares, price, event]
    incomplete = analyze_capitalization(inputs, BusinessModel.GENERAL, "2026-09-16")
    assert not any(f.metric == "current_shares_split_adjusted" for f in incomplete.derived_facts)
    inputs += [fact("coverage", "split_history_complete", 1, start="2026-06-30", end="2026-09-15"),
               fact("class", "share_class_coverage_ratio", 1, end="2026-09-15"),
               fact("adr", "adr_ratio", 1, end="2026-09-15")]
    complete = analyze_capitalization(inputs, BusinessModel.GENERAL, "2026-09-16")
    assert next(f for f in complete.derived_facts if f.metric == "market_cap").value == 4000


def test_split_review_reaches_packet_and_roundtrips_without_claiming_history():
    class Provider:
        def fetch(self, ticker, as_of):
            return {"identity": {"ticker": ticker}, "facts": [source().to_dict()]}
    base = build_packet("TEST", "2026-09-16", [Provider()])
    packet = build_packet("TEST", "2026-09-16", [Provider()],
                          review_manifest=manifest(base.facts, base.as_of))
    assert next(f for f in packet.facts if f.metric == "split_ratio").period_end == "2026-07-01"
    assert not any(f.metric == "split_history_complete" for f in packet.facts)
    assert ResearchPacket.from_dict(packet.to_dict()).to_dict() == packet.to_dict()
