import json

from tradingagents.research import ResearchPacket, build_packet
from tradingagents.research.__main__ import main
from tradingagents.research.reviewed_inputs import draft_review_manifest, evidence_sha256


class Provider:
    def fetch(self, ticker, as_of):
        return {"identity": {"ticker": ticker}, "facts": [{
            "fact_id": "cover-shares", "metric": "filing_shares_outstanding", "value": 1234,
            "unit": "shares", "period_end": "2026-07-28",
            "published_at": "2026-07-30T21:00:00Z", "retrieved_at": "2026-09-16T12:00:00Z",
            "source_url": "https://example.test/filing#cover", "source_tag": "dei:EntityCommonStockSharesOutstanding",
            "definition": "Reported cover-page common shares", "adjustment_basis": "filing_context_unreconciled",
            "kind": "reported", "accession": "0000009999-26-000001",
        }]}


def manifest(packet):
    return {"schema_version": 1, "ticker": "WIDE", "as_of": packet.as_of,
            "base_evidence_sha256": evidence_sha256(packet.facts),
            "reviewer": {"name": "Test reviewer", "reviewed_at": "2026-09-16T13:00:00Z",
                         "rationale": "Read the cover-page common-share disclosure."},
            "rules": [{"rule_id": "cover", "output_metric": "current_shares", "operation": "copy",
                       "input_fact_ids": ["cover-shares"], "rationale": "Point-in-time raw cover shares; class completeness is not asserted.",
                       "attestations": ["point_in_time_common_shares", "raw_as_reported_basis"]}]}


def test_reviewed_copy_keeps_lineage_and_does_not_unlock_share_completeness():
    base = build_packet("WIDE", "2026-09-16", [Provider()])
    reviewed = build_packet("WIDE", "2026-09-16", [Provider()], review_manifest=manifest(base))
    promoted = [f for f in reviewed.facts if f.metric == "current_shares"]
    assert len(promoted) == 1 and promoted[0].value == 1234
    assert promoted[0].input_fact_ids == ("cover-shares",)
    assert not any(f.metric in {"market_cap", "share_class_coverage_ratio", "split_history_complete"}
                   for f in reviewed.facts)
    assert "Reviewed capital inputs" in reviewed.to_markdown()
    assert ResearchPacket.from_dict(reviewed.to_dict()).to_dict() == reviewed.to_dict()
    assert evidence_sha256(reviewed.facts) == evidence_sha256(base.facts)


def test_review_draft_cannot_promote_and_wrong_issuer_review_rejects():
    base = build_packet("WIDE", "2026-09-16", [Provider()])
    draft = draft_review_manifest(base.facts, "WIDE", base.as_of)
    pending = build_packet("WIDE", "2026-09-16", [Provider()], review_manifest=draft)
    assert not any(f.metric == "current_shares" for f in pending.facts)
    approved = manifest(base)
    approved["ticker"] = "OTHER"
    rejected = build_packet("WIDE", "2026-09-16", [Provider()], review_manifest=approved)
    assert rejected.financial_analysis["reviewed_inputs"]["status"] == "rejected"


def test_offline_cli_writes_draft_then_applies_explicit_review(tmp_path):
    source = tmp_path / "fixture.json"
    source.write_text(json.dumps(Provider().fetch("WIDE", "2026-09-16")))
    args = ["WIDE", "--as-of", "2026-09-16", "--fixture", str(source)]
    first = tmp_path / "base"
    assert main(args + ["--write-review-draft", "--output-dir", str(first)]) == 0
    assert json.loads((first / "capital-review-draft.json").read_text())["draft_only"] is True
    packet = ResearchPacket.from_dict(json.loads((first / "packet.json").read_text()))
    approved = tmp_path / "approved.json"
    approved.write_text(json.dumps(manifest(packet)))
    second = tmp_path / "reviewed"
    assert main(args + ["--capital-review", str(approved), "--output-dir", str(second)]) == 0
    result = json.loads((second / "packet.json").read_text())
    assert any(f["metric"] == "current_shares" for f in result["facts"])
    stale = manifest(packet)
    stale["base_evidence_sha256"] = "0" * 64
    approved.write_text(json.dumps(stale))
    assert main(args + ["--capital-review", str(approved), "--output-dir", str(second)]) == 2
    rejected = json.loads((second / "packet.json").read_text())
    assert rejected["financial_analysis"]["reviewed_inputs"]["status"] == "rejected"


def test_explicit_complete_debt_review_reaches_net_debt_calculator():
    base_payload = Provider().fetch("WIDE", "2026-09-16")
    common = dict(base_payload["facts"][0])
    common.update(unit="USD", period_end="2026-06-30")
    debt = dict(common, fact_id="debt", metric="filing_long_term_debt_noncurrent", value=500,
                source_tag="us-gaap:LongTermDebtNoncurrent", source_url="https://example.test/filing#debt")
    cash = dict(common, fact_id="cash", metric="cash", value=200,
                source_tag="us-gaap:CashAndCashEquivalentsAtCarryingValue", source_url="https://example.test/filing#cash")

    class DebtProvider:
        def fetch(self, ticker, as_of):
            return {"identity": {"ticker": ticker}, "facts": [debt, cash]}

    base = build_packet("WIDE", "2026-09-16", [DebtProvider()])
    review = manifest(base)
    review["rules"] = [{"rule_id": "complete-debt", "output_metric": "total_debt", "operation": "copy",
                        "input_fact_ids": ["debt"], "attestations": ["complete_interest_bearing_debt"],
                        "rationale": "Synthetic fixture: source review establishes no other interest-bearing obligations."}]
    packet = build_packet("WIDE", "2026-09-16", [DebtProvider()], review_manifest=review)
    assert next(f for f in packet.facts if f.metric == "net_debt").value == 300
    assert not any(f.metric == "enterprise_value" for f in packet.facts)


def test_reviewed_quote_and_shares_expose_exact_split_interval_without_unlocking_cap():
    payload = Provider().fetch("WIDE", "2026-09-16")
    payload["facts"].append(dict(payload["facts"][0], fact_id="quote", metric="close", value=25,
                                 unit="USD/share", period_end="2026-09-15", source_tag="yahoo:close",
                                 source_url="https://example.test/quote", adjustment_basis="vendor_split_adjusted"))

    class QuoteProvider:
        def fetch(self, ticker, as_of):
            return payload

    base = build_packet("WIDE", "2026-09-16", [QuoteProvider()])
    review = manifest(base)
    review["rules"].append({"rule_id": "quote-review", "output_metric": "current_share_price",
                            "operation": "copy", "input_fact_ids": ["quote"],
                            "attestations": ["listing_currency", "non_dividend_adjusted_quote", "quote_date_split_basis"],
                            "rationale": "Synthetic quote verified on its observation date's split basis in USD."})
    packet = build_packet("WIDE", "2026-09-16", [QuoteProvider()], review_manifest=review)
    quote = next(f for f in packet.facts if f.metric == "current_share_price")
    assert quote.value == 25 and quote.input_fact_ids == ("quote",)
    plan = packet.financial_analysis["capitalization"]["market_cap_evidence_plan"]
    assert plan["split_interval"]["end_inclusive"] == "2026-09-15"
    assert not plan["ready"]
    assert "share_class_coverage_ratio_equal_1" in {r["requirement"] for r in plan["outstanding"]}
    assert not any(f.metric == "market_cap" for f in packet.facts)
    assert "Required split interval:" in packet.to_markdown()
    assert ResearchPacket.from_dict(packet.to_dict()).to_dict() == packet.to_dict()
    # Controlled positive path: these proof facts are synthetic, not inferred
    # from a real listing or an absent vendor action.
    for metric in ("share_class_coverage_ratio", "adr_ratio", "split_history_complete"):
        payload["facts"].append(dict(payload["facts"][0], fact_id=metric, metric=metric,
                                     value=1, unit="ratio", period_end="2026-09-15",
                                     period_start="2026-07-28" if metric == "split_history_complete" else None,
                                     source_tag="test:proof", source_url=f"https://example.test/{metric}"))
    updated = build_packet("WIDE", "2026-09-16", [QuoteProvider()])
    review["base_evidence_sha256"] = evidence_sha256(updated.facts)
    complete = build_packet("WIDE", "2026-09-16", [QuoteProvider()], review_manifest=review)
    assert complete.financial_analysis["capitalization"]["market_cap_evidence_plan"]["ready"], [i.to_dict() for i in complete.issues]
    assert next(f for f in complete.facts if f.metric == "market_cap").value == 1234 * 25
    assert complete.financial_analysis["capitalization"]["market_cap_evidence_plan"]["ready"]
