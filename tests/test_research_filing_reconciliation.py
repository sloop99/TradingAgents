from __future__ import annotations

import pytest

from tradingagents.research.filing_reconciliation import (
    FilingContextResult,
    analyze_filing_contexts,
)
from tradingagents.research.models import EvidenceFact

ACCESSION = "0001000000-26-000001"
BASE_URL = "https://www.sec.gov/Archives/edgar/data/1000000/report.htm"


def item(
    fact_id: str,
    metric: str,
    concept: str,
    value: float,
    *,
    unit: str = "USD",
    end: str = "2026-06-30",
    start: str | None = None,
    dimensions: dict[str, str] | None = None,
    context: str | None = None,
    decimals: str | None = None,
) -> tuple[EvidenceFact, dict]:
    source_url = f"{BASE_URL}#{fact_id}"
    fact = EvidenceFact.from_dict({
        "fact_id": fact_id,
        "metric": metric,
        "value": value,
        "unit": unit,
        "period_start": start,
        "period_end": end,
        "published_at": "2026-07-30T20:00:00Z",
        "retrieved_at": "2026-09-01T20:00:00Z",
        "source_url": source_url,
        "accession": ACCESSION,
        "source_tag": concept,
        "definition": "Definition text is deliberately not parsed.",
        "adjustment_basis": "filing_context_unreconciled",
        "kind": "reported",
    })
    candidate = {
        "status": "accepted",
        "fact_id": fact_id,
        "metric": metric,
        "concept": concept,
        "value": value,
        "unit": unit,
        "period_start": start,
        "period_end": end,
        "source_url": source_url,
        "context_id": context or f"ctx-{fact_id}",
        "source_node_id": fact_id,
        "dimensions": dimensions or {},
    }
    if decimals is not None:
        candidate["decimals"] = decimals
    return fact, candidate


def analyze(items: list[tuple[EvidenceFact, dict]], *, as_of: str = "2026-09-16") -> FilingContextResult:
    contexts = {
        candidate["context_id"]: {
            "safe": True,
            "period_start": candidate["period_start"],
            "period_end": candidate["period_end"],
            "dimensions": candidate["dimensions"],
        }
        for _, candidate in items
    }
    return analyze_filing_contexts(
        [fact for fact, _ in items],
        [{
            "accession": ACCESSION,
            "source_url": BASE_URL,
            "published_at": "2026-07-30T20:00:00Z",
            "retrieved_at": "2026-09-01T20:00:00Z",
            "contexts": contexts,
            "candidates": [candidate for _, candidate in items],
        }],
        as_of,
    )


@pytest.mark.unit
def test_long_term_debt_components_produce_only_filing_scoped_subtotal_with_lineage():
    current = item("current", "filing_long_term_debt_current", "us-gaap:LongTermDebtCurrent", 40)
    noncurrent = item("noncurrent", "filing_long_term_debt_noncurrent", "us-gaap:LongTermDebtNoncurrent", 160)
    parent = item("parent", "filing_long_term_debt_reported", "us-gaap:LongTermDebt", 200)

    result = analyze([current, noncurrent, parent])

    assert len(result.derived_facts) == 1
    subtotal = result.derived_facts[0]
    assert subtotal.metric == "filing_long_term_debt_subtotal"
    assert subtotal.value == 200
    assert subtotal.input_fact_ids == ("current", "noncurrent")
    assert subtotal.accession == ACCESSION
    assert not any(fact.metric == "total_debt" for fact in result.derived_facts)
    assert result.summary["status"] == "partial"
    assert result.summary["debt_groups"][0]["parent_comparison"] == "equal_within_reported_precision"
    assert result.summary["debt_groups"][0]["complete_debt_proven"] is False


@pytest.mark.unit
def test_share_classes_compare_only_same_concept_date_and_precision_without_proving_coverage():
    axis = "us-gaap:StatementClassOfStockAxis"
    total = item("total", "filing_common_stock_shares_outstanding", "us-gaap:CommonStockSharesOutstanding", 200, unit="shares", decimals="0")
    class_a = item("class-a", "filing_common_stock_shares_outstanding", "us-gaap:CommonStockSharesOutstanding", 100, unit="shares", dimensions={axis: "acme:ClassAMember"}, context="class-a", decimals="0")
    class_b = item("class-b", "filing_common_stock_shares_outstanding", "us-gaap:CommonStockSharesOutstanding", 99.6, unit="shares", dimensions={axis: "acme:ClassBMember"}, context="class-b", decimals="0")
    dei = item("dei", "filing_shares_outstanding", "dei:EntityCommonStockSharesOutstanding", 999, unit="shares")

    result = analyze([total, class_a, class_b, dei])

    gaap = next(group for group in result.summary["share_groups"] if group["concept"].startswith("us-gaap"))
    assert gaap["comparison"] == "equal_within_reported_precision"
    assert gaap["class_completeness_proven"] is False
    assert set(gaap["undimensioned_fact_ids"]) == {"total"}
    assert "share_class_completeness" in result.summary["unresolved_proofs"]
    dei_group = next(group for group in result.summary["share_groups"] if group["concept"].startswith("dei"))
    assert dei_group["comparison"] == "not_comparable"


@pytest.mark.unit
def test_equal_duplicate_contexts_are_deduplicated_but_unequal_same_context_conflicts():
    first = item("first", "filing_long_term_debt_current", "us-gaap:LongTermDebtCurrent", 40)
    duplicate = item("duplicate", "filing_long_term_debt_current", "us-gaap:LongTermDebtCurrent", 40, context="other-id")
    noncurrent = item("noncurrent", "filing_long_term_debt_noncurrent", "us-gaap:LongTermDebtNoncurrent", 160)
    deduped = analyze([first, duplicate, noncurrent])

    assert deduped.summary["counts"]["deduplicated_nodes"] == 1
    assert deduped.derived_facts[0].value == 200
    assert len(deduped.derived_facts[0].input_fact_ids) == 2

    unequal = item("unequal", "filing_long_term_debt_current", "us-gaap:LongTermDebtCurrent", 41, context="third-id")
    conflicted = analyze([first, unequal, noncurrent])
    assert conflicted.summary["status"] == "material_conflict"
    assert not conflicted.derived_facts
    assert any(issue.code == "FILING_CONTEXT_VALUE_CONFLICT" for issue in conflicted.issues)

    exact_repeat = analyze([first, first, noncurrent])
    assert exact_repeat.summary["counts"]["deduplicated_nodes"] == 1
    assert exact_repeat.derived_facts[0].value == 200


@pytest.mark.unit
def test_bad_metadata_and_future_evidence_are_excluded_without_definition_inference():
    bad = item("bad", "filing_long_term_debt_current", "us-gaap:LongTermDebtCurrent", 40)
    bad[1]["value"] = 400
    future = item("future", "filing_long_term_debt_noncurrent", "us-gaap:LongTermDebtNoncurrent", 160, end="2026-10-01")
    result = analyze([bad, future])

    assert result.summary["status"] == "unsupported"
    assert set(result.summary["excluded_fact_ids"]) == {"bad", "future"}
    assert not result.derived_facts
    assert {issue.code for issue in result.issues} >= {
        "FILING_METADATA_BINDING_INVALID",
        "FILING_FACT_AFTER_CUTOFF",
    }


@pytest.mark.unit
def test_dates_dimensions_leases_commercial_paper_and_custom_concepts_are_not_combined():
    current = item("current", "filing_long_term_debt_current", "us-gaap:LongTermDebtCurrent", 40)
    other_date = item("noncurrent-old", "filing_long_term_debt_noncurrent", "us-gaap:LongTermDebtNoncurrent", 160, end="2026-03-31")
    dimensional = item("noncurrent-sub", "filing_long_term_debt_noncurrent", "us-gaap:LongTermDebtNoncurrent", 10, dimensions={"us-gaap:LegalEntityAxis": "acme:SubMember"})
    lease = item("lease", "filing_finance_lease_liability_current", "us-gaap:FinanceLeaseLiabilityCurrent", 5)
    paper = item("paper", "filing_commercial_paper", "us-gaap:CommercialPaper", 6)
    custom = item("custom", "filing_custom_notes_payable", "acme:NotesPayable", 7)

    result = analyze([current, other_date, dimensional, lease, paper, custom])

    assert not result.derived_facts
    assert all(group["subtotal_fact_id"] is None for group in result.summary["debt_groups"])
    assert result.summary["status"] == "partial"


@pytest.mark.unit
def test_share_discrepancy_is_warning_and_does_not_claim_material_conflict():
    axis = "us-gaap:StatementClassOfStockAxis"
    inputs = [
        item("total", "filing_common_stock_shares_outstanding", "us-gaap:CommonStockSharesOutstanding", 300, unit="shares"),
        item("a", "filing_common_stock_shares_outstanding", "us-gaap:CommonStockSharesOutstanding", 100, unit="shares", dimensions={axis: "acme:AMember"}),
        item("b", "filing_common_stock_shares_outstanding", "us-gaap:CommonStockSharesOutstanding", 100, unit="shares", dimensions={axis: "acme:BMember"}),
    ]
    result = analyze(inputs)

    assert result.summary["status"] == "partial"
    assert len(result.summary["discrepancies"]) == 1
    assert any(issue.code == "FILING_SHARE_CLASS_DISCREPANCY" and issue.severity.value == "warning" for issue in result.issues)
    assert all("current_shares" not in fact.metric for fact in result.derived_facts)


@pytest.mark.unit
def test_unrelated_retained_provider_facts_are_out_of_scope_and_silent():
    unrelated = EvidenceFact.from_dict({
        "fact_id": "sec-cash",
        "metric": "cash",
        "value": 50,
        "unit": "USD",
        "period_end": "2026-06-30",
        "published_at": "2026-07-30T20:00:00Z",
        "retrieved_at": "2026-09-01T20:00:00Z",
        "source_url": "https://data.sec.gov/companyfacts",
        "accession": ACCESSION,
        "source_tag": "us-gaap:CashAndCashEquivalentsAtCarryingValue",
        "definition": "Cash",
        "kind": "reported",
    })
    result = analyze_filing_contexts([unrelated], [], "2026-09-16")
    assert result.summary["status"] == "unsupported"
    assert result.summary["counts"]["input_facts"] == 0
    assert result.issues == []


@pytest.mark.unit
def test_context_precision_scope_and_debt_guards_fail_closed():
    huge_precision = item("precision", "filing_long_term_debt_current", "us-gaap:LongTermDebtCurrent", 40, decimals="1000000")
    precision_result = analyze([huge_precision])
    assert precision_result.summary["status"] == "unsupported"
    assert any(issue.code == "FILING_METADATA_BINDING_INVALID" for issue in precision_result.issues)

    duration_share = item(
        "duration-share", "filing_common_stock_shares_outstanding",
        "us-gaap:CommonStockSharesOutstanding", 100, unit="shares", start="2026-01-01",
    )
    custom_axis = item(
        "custom-axis", "filing_common_stock_shares_outstanding",
        "us-gaap:CommonStockSharesOutstanding", 100, unit="shares",
        dimensions={"acme:StatementClassOfStockAxis": "acme:ClassAMember"},
    )
    shares_result = analyze([duration_share, custom_axis])
    assert all(not group["class_members"] for group in shares_result.summary["share_groups"])

    negative = item("negative", "filing_long_term_debt_current", "us-gaap:LongTermDebtCurrent", -1)
    noncurrent = item("noncurrent", "filing_long_term_debt_noncurrent", "us-gaap:LongTermDebtNoncurrent", 10)
    debt_result = analyze([negative, noncurrent])
    assert not debt_result.derived_facts
    assert any(issue.code == "FILING_DEBT_COMPONENT_INVALID" for issue in debt_result.issues)


@pytest.mark.unit
def test_same_concept_with_different_metric_labels_is_ambiguous():
    canonical = item("canonical", "filing_long_term_debt_current", "us-gaap:LongTermDebtCurrent", 40)
    mislabeled = item("mislabeled", "filing_other_current_debt", "us-gaap:LongTermDebtCurrent", 40)
    noncurrent = item("noncurrent", "filing_long_term_debt_noncurrent", "us-gaap:LongTermDebtNoncurrent", 160)
    result = analyze([canonical, mislabeled, noncurrent])

    assert result.summary["status"] == "material_conflict"
    assert not result.derived_facts
    assert any(issue.code == "FILING_CONCEPT_METRIC_AMBIGUITY" for issue in result.issues)


@pytest.mark.unit
@pytest.mark.parametrize("published", [None, "not-a-timestamp"])
def test_missing_or_malformed_metadata_timestamps_are_excluded_without_crashing(published):
    fact, candidate = item("current", "filing_long_term_debt_current", "us-gaap:LongTermDebtCurrent", 40)
    filing = {
        "accession": ACCESSION,
        "published_at": published,
        "retrieved_at": "2026-09-01T20:00:00Z",
        "contexts": {
            candidate["context_id"]: {
                "safe": True,
                "period_start": candidate["period_start"],
                "period_end": candidate["period_end"],
                "dimensions": candidate["dimensions"],
            }
        },
        "candidates": [candidate],
    }
    result = analyze_filing_contexts([fact], [filing], "2026-09-16")
    assert result.summary["status"] == "unsupported"
    assert any(issue.code == "FILING_METADATA_TIMESTAMP_INVALID" for issue in result.issues)
