from __future__ import annotations

import pytest

from tradingagents.research.models import EvidenceFact
from tradingagents.research.reconciliation import normalize_concept, reconcile_facts


def fact(
    fact_id: str,
    metric: str,
    value: int,
    *,
    tag: str | None = None,
    definition: str = "Reported amount",
    unit: str = "USD",
    start: str | None = "2024-01-01",
    end: str = "2024-12-31",
    published: str = "2025-02-01T12:00:00Z",
    basis: str = "as_reported",
) -> EvidenceFact:
    return EvidenceFact.from_dict(
        {
            "fact_id": fact_id,
            "metric": metric,
            "value": value,
            "unit": unit,
            "period_start": start,
            "period_end": end,
            "published_at": published,
            "retrieved_at": "2025-02-01T12:01:00Z",
            "source_url": f"https://example.test/{fact_id}",
            "source_tag": tag,
            "definition": definition,
            "adjustment_basis": basis,
            "kind": "reported",
        }
    )


@pytest.mark.unit
def test_reconciliation_selects_latest_restatement_and_keeps_full_lineage():
    result = reconcile_facts(
        [
            fact("original", "revenue", 100, tag="us-gaap:Revenues"),
            fact("restated", "revenue", 101, tag="us-gaap:Revenues", published="2025-03-01T12:00:00Z"),
        ]
    )

    assert [item.fact_id for item in result.selected_facts] == ["restated"]
    assert [issue.code for issue in result.issues] == ["RESTATED_OR_REVISED_FACT"]
    assert result.decisions == [
        {
            "concept": "us-gaap:revenues",
            "metric": "revenue",
            "unit": "usd",
            "period_start": "2024-01-01",
            "period_end": "2024-12-31",
            "adjustment_basis": "as_reported",
            "candidate_fact_ids": ["original", "restated"],
            "selected_fact_id": "restated",
            "outcome": "selected",
            "reason": "latest_published",
        }
    ]


@pytest.mark.unit
def test_same_latest_timestamp_with_different_values_is_excluded_as_an_error():
    result = reconcile_facts(
        [
            fact("one", "revenue", 100, tag="us-gaap:Revenues"),
            fact("two", "revenue", 101, tag="us-gaap:Revenues"),
        ]
    )

    assert result.selected_facts == []
    assert [(issue.code, issue.severity.value) for issue in result.issues] == [("FACT_CONFLICT", "error")]
    assert result.decisions[0]["candidate_fact_ids"] == ["one", "two"]
    assert result.decisions[0]["selected_fact_id"] is None


@pytest.mark.unit
def test_generic_metric_with_different_definitions_and_units_is_withheld():
    definitions = reconcile_facts(
        [
            fact("gaap", "revenue", 100, definition="Revenue excluding tax"),
            fact("gross", "revenue", 110, definition="Gross billings"),
        ]
    )
    units = reconcile_facts(
        [
            fact("usd", "revenue", 100),
            fact("shares", "revenue", 100, unit="shares"),
        ]
    )

    assert definitions.selected_facts == []
    assert {issue.code for issue in definitions.issues} == {"DEFINITION_CONFLICT"}
    assert units.selected_facts == []
    assert {issue.code for issue in units.issues} == {"UNIT_CONFLICT"}


@pytest.mark.unit
def test_sec_cash_debt_and_equity_are_remapped_without_summing_components():
    old_cached = [
        fact("cash", "cash", 10, tag="us-gaap:CashAndCashEquivalentsAtCarryingValue", start=None),
        fact("restricted", "cash", 11, tag="us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", start=None),
        fact("current", "debt", 3, tag="us-gaap:LongTermDebtCurrent", start=None),
        fact("noncurrent", "debt", 7, tag="us-gaap:LongTermDebtNoncurrent", start=None),
        fact("equity", "equity", 20, tag="us-gaap:StockholdersEquity", start=None),
        fact("nci", "equity", 21, tag="us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", start=None),
    ]

    result = reconcile_facts(old_cached)

    assert {item.metric for item in result.selected_facts} == {
        "cash",
        "cash_including_restricted",
        "long_term_debt_current",
        "long_term_debt_noncurrent",
        "equity",
        "equity_including_noncontrolling",
    }
    assert not result.issues
    assert normalize_concept(old_cached[2]).metric == "long_term_debt_current"


@pytest.mark.unit
def test_sec_capitalization_tags_normalize_without_scope_equivalence_or_synthesis():
    facts = [
        fact("st", "legacy", 2, tag="US-GAAP:ShortTermBorrowings", start=None),
        fact("cp", "legacy", 3, tag="us-gaap:CommercialPaper", start=None),
        fact("reported", "legacy", 10, tag="us-gaap:LongTermDebt", start=None),
        fact("lease", "legacy", 4, tag="us-gaap:FinanceLeaseLiability", start=None),
        fact("lease-current", "legacy", 1, tag="us-gaap:FinanceLeaseLiabilityCurrent", start=None),
        fact("pref", "legacy", 5, tag="us-gaap:PreferredStockValue", start=None),
        fact("pref-liquidation", "legacy", 8, tag="us-gaap:PreferredStockLiquidationPreferenceValue", start=None),
        fact("nci", "legacy", 4, tag="us-gaap:MinorityInterest", start=None),
        fact("redeemable-nci", "legacy", 6, tag="us-gaap:RedeemableNoncontrollingInterestEquityCarryingAmount", start=None),
    ]
    result = reconcile_facts(facts)
    metrics = {item.metric: item.value for item in result.selected_facts}

    assert metrics == {
        "short_term_borrowings": 2,
        "commercial_paper": 3,
        "long_term_debt_reported": 10,
        "finance_lease_liability": 4,
        "finance_lease_liability_current": 1,
        "preferred_stock_value": 5,
        "preferred_stock_liquidation_preference": 8,
        "noncontrolling_interest_carrying": 4,
        "redeemable_nci_equity_carrying_amount": 6,
    }
    assert normalize_concept(facts[2]).metric == "long_term_debt_reported"
    assert not any(issue.code == "UNIT_CONFLICT" for issue in result.issues)


TOTAL = "us-gaap:Revenues"
CONTRACT = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"


@pytest.mark.unit
def test_identical_revenue_under_two_sec_tags_is_not_a_conflict():
    result = reconcile_facts([
        fact("total", "revenue", 90_234_000_000, tag=TOTAL, definition="Revenues"),
        fact("contract", "revenue", 90_234_000_000, tag=CONTRACT,
             definition="Revenue from Contract with Customer, Excluding Assessed Tax"),
    ])

    assert [f.fact_id for f in result.selected_facts] == ["total"]
    assert not any(issue.code == "DEFINITION_CONFLICT" for issue in result.issues)
    resolved = next(issue for issue in result.issues if issue.code == "REVENUE_DEFINITION_RESOLVED")
    assert resolved.severity.value == "info" and "same amount" in resolved.message


@pytest.mark.unit
def test_total_revenues_are_preferred_over_the_contract_revenue_subset():
    result = reconcile_facts([
        fact("total", "revenue", 206_168_000, tag=TOTAL, definition="Revenues"),
        fact("contract", "revenue", 203_412_000, tag=CONTRACT,
             definition="Revenue from Contract with Customer, Excluding Assessed Tax"),
    ])

    assert [f.fact_id for f in result.selected_facts] == ["total"]
    resolved = next(issue for issue in result.issues if issue.code == "REVENUE_DEFINITION_RESOLVED")
    assert "subset" in resolved.message


@pytest.mark.unit
def test_contract_revenue_above_total_revenues_is_still_a_conflict():
    result = reconcile_facts([
        fact("total", "revenue", 200_000_000, tag=TOTAL, definition="Revenues"),
        fact("contract", "revenue", 210_000_000, tag=CONTRACT,
             definition="Revenue from Contract with Customer, Excluding Assessed Tax"),
    ])

    assert result.selected_facts == []
    assert any(issue.code == "DEFINITION_CONFLICT" for issue in result.issues)


@pytest.mark.unit
def test_identical_contract_revenue_tags_without_a_total_are_not_a_conflict():
    result = reconcile_facts([
        fact("excl", "revenue", 18_471_000, tag=CONTRACT,
             definition="Revenue from Contract with Customer, Excluding Assessed Tax"),
        fact("incl", "revenue", 18_471_000, tag="us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",
             definition="Revenue from Contract with Customer, Including Assessed Tax"),
    ])

    assert [f.fact_id for f in result.selected_facts] == ["excl"]
    assert not any(issue.code == "DEFINITION_CONFLICT" for issue in result.issues)
