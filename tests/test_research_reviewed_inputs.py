from __future__ import annotations

import pytest

from tradingagents.research.models import EvidenceFact
from tradingagents.research.reviewed_inputs import (
    ReviewedInputsResult,
    apply_reviewed_inputs,
    draft_review_manifest,
    evidence_sha256,
)


def fact(
    fact_id: str,
    metric: str,
    source_tag: str,
    value: float,
    *,
    unit: str,
    end: str = "2026-06-30",
    start: str | None = None,
    basis: str = "as_reported",
    definition: str | None = None,
) -> EvidenceFact:
    return EvidenceFact.from_dict({
        "fact_id": fact_id,
        "metric": metric,
        "value": value,
        "unit": unit,
        "period_start": start,
        "period_end": end,
        "published_at": "2026-07-30T20:00:00Z",
        "retrieved_at": "2026-09-01T20:00:00Z",
        "source_url": f"https://example.test/{fact_id}",
        "accession": "0001000000-26-000001",
        "source_tag": source_tag,
        "definition": definition or metric,
        "adjustment_basis": basis,
        "kind": "reported",
    })


def base_facts() -> list[EvidenceFact]:
    return [
        fact("shares", "shares_outstanding", "dei:EntityCommonStockSharesOutstanding", 100, unit="shares"),
        fact("debt-current", "long_term_debt_current", "us-gaap:LongTermDebtCurrent", 40, unit="USD"),
        fact("debt-noncurrent", "long_term_debt_noncurrent", "us-gaap:LongTermDebtNoncurrent", 160, unit="USD"),
    ]


def manifest(facts: list[EvidenceFact]) -> dict:
    return {
        "schema_version": 1,
        "ticker": "TEST",
        "as_of": "2026-09-16",
        "base_evidence_sha256": evidence_sha256(facts),
        "reviewer": {
            "name": "Casey Reviewer",
            "reviewed_at": "2026-09-10T12:00:00Z",
            "rationale": "Reviewed source contexts and issuer scope.",
        },
        "rules": [
            {
                "rule_id": "shares-review",
                "output_metric": "current_shares",
                "operation": "copy",
                "input_fact_ids": ["shares"],
                "attestations": ["point_in_time_common_shares", "raw_as_reported_basis"],
                "rationale": "Point-in-time common shares on the filing date.",
            },
            {
                "rule_id": "debt-review",
                "output_metric": "total_debt",
                "operation": "sum",
                "input_fact_ids": ["debt-current", "debt-noncurrent"],
                "attestations": ["complete_interest_bearing_debt", "disjoint_scopes"],
                "rationale": "Current and noncurrent scopes are disjoint and complete.",
            },
        ],
    }


def promoted(result: ReviewedInputsResult, metric: str) -> EvidenceFact | None:
    return next((fact for fact in result.derived_facts if fact.metric == metric), None)


@pytest.mark.parametrize("definition,tag", [
    ("Not dividend adjusted in one series; dividend adjusted in this series", "yahoo:close"),
    ("Not dividend adjusted", "yahoo:dividend_adjusted"),
    ("Split/dividend-adjusted quote", "yahoo:close"),
    ("Historical quote", "yahoo:adjusted_close"),
])
def test_contradictory_adjustment_labels_cannot_hide_behind_negation(definition, tag):
    source = fact("price", "close", tag, 42, unit="USD/share",
                  basis="vendor_split_adjusted", definition=definition)
    draft = draft_review_manifest([source], "TEST", "2026-09-16")
    assert draft["candidates"]["current_share_price"] == []


@pytest.mark.unit
def test_valid_review_copies_shares_and_sums_debt_with_full_lineage():
    facts = base_facts()
    result = apply_reviewed_inputs(facts, manifest(facts), "TEST", "2026-09-16")

    shares = promoted(result, "current_shares")
    debt = promoted(result, "total_debt")
    assert shares.value == 100
    assert shares.input_fact_ids == ("shares",)
    assert shares.adjustment_basis == "as_reported"
    assert debt.value == 200
    assert debt.input_fact_ids == ("debt-current", "debt-noncurrent")
    assert debt.kind.value == "calculated"
    assert debt.definition.startswith("Reviewed analyst assertion by Casey Reviewer")
    assert "complete interest-bearing debt including all obligations" in debt.definition
    assert result.summary["status"] == "applied"
    assert result.summary["applied_metrics"] == ["current_shares", "total_debt"]
    assert {item.metric for item in result.derived_facts} == {"current_shares", "total_debt"}
    assert "share_class_completeness" in result.summary["unresolved_proofs"]


@pytest.mark.unit
def test_valid_price_review_promotes_an_attested_current_quote_and_retains_source_basis():
    price = fact(
        "close", "close", "yahoo_daily_close", 42.25,
        unit="USD/share", end="2026-09-15", basis="vendor_split_adjusted",
    )
    facts = base_facts() + [price]
    review = manifest(facts)
    review["rules"].append({
        "rule_id": "price-review",
        "output_metric": "current_share_price",
        "operation": "copy",
        "input_fact_ids": ["close"],
        "attestations": ["listing_currency", "non_dividend_adjusted_quote", "quote_date_split_basis"],
        "rationale": "Verified USD listing currency and a dividend-unadjusted Yahoo close.",
    })

    result = apply_reviewed_inputs(facts, review, "TEST", "2026-09-16")
    promoted_price = promoted(result, "current_share_price")
    assert promoted_price.value == 42.25
    assert promoted_price.unit == "USD/share"
    assert promoted_price.adjustment_basis == "current_quote"
    assert promoted_price.input_fact_ids == ("close",)
    assert "listing currency" in promoted_price.definition
    assert "'vendor_split_adjusted'" in promoted_price.definition
    assert "dividend adjusted" not in promoted_price.definition.replace("-", " ").casefold()

    draft = draft_review_manifest(facts, "TEST", "2026-09-16")
    assert [item["fact_id"] for item in draft["candidates"]["current_share_price"]] == ["close"]
    assert draft["required_attestations"]["current_share_price"] == [
        "listing_currency", "non_dividend_adjusted_quote", "quote_date_split_basis",
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("metric", "unit", "value", "basis"),
    [
        ("close_adjusted", "USD/share", 42.25, "split_dividend_adjusted"),
        ("close", "USD/share", 42.25, "split_dividend_adjusted"),
        ("close", "native_currency/share", 42.25, "vendor_split_adjusted"),
        ("regular_market_price", "USD/share", 0, "raw_quote"),
        ("market_price", "USD/share", -1, "raw_quote"),
        ("market_price", "USD/share", 42.25, "as_reported"),
    ],
)
def test_price_review_rejects_adjusted_ambiguous_currency_or_nonpositive_sources(metric, unit, value, basis):
    price = fact("price", metric, "market_quote", value, unit=unit, basis=basis)
    review = manifest([price])
    review["rules"] = [{
        "rule_id": "bad-price",
        "output_metric": "current_share_price",
        "operation": "copy",
        "input_fact_ids": ["price"],
        "attestations": ["listing_currency", "non_dividend_adjusted_quote", "quote_date_split_basis"],
        "rationale": "Attempted price promotion.",
    }]

    result = apply_reviewed_inputs([price], review, "TEST", "2026-09-16")
    assert result.summary["status"] == "rejected"
    assert promoted(result, "current_share_price") is None
    assert draft_review_manifest([price], "TEST", "2026-09-16")["candidates"]["current_share_price"] == []


@pytest.mark.unit
def test_price_review_requires_listing_currency_non_dividend_and_quote_date_basis_attestations():
    price = fact("price", "regular_market_price", "market_quote", 42.25, unit="USD/share", basis="raw_quote")
    review = manifest([price])
    review["rules"] = [{
        "rule_id": "missing-price-attestations",
        "output_metric": "current_share_price",
        "operation": "copy",
        "input_fact_ids": ["price"],
        "attestations": ["listing_currency"],
        "rationale": "Quote lacks the required attestation.",
    }]

    result = apply_reviewed_inputs([price], review, "TEST", "2026-09-16")
    assert not result.derived_facts
    assert "non_dividend_adjusted_quote" in result.summary["rule_decisions"][0]["reasons"][0]
    assert "quote_date_split_basis" in result.summary["rule_decisions"][0]["reasons"][0]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("source_tag", "definition", "start"),
    [
        ("market_quote", "Vendor dividend-adjusted close.", None),
        ("market_quote_total_return_adjusted", "Vendor close.", None),
        ("market_quote", "Vendor close.", "2026-09-01"),
        ("market_quote", "Vendor close.", None),
    ],
)
def test_price_review_rejects_hidden_adjustments_durations_and_unknown_split_basis(source_tag, definition, start):
    basis = "unknown_split_basis" if source_tag == "market_quote" and definition == "Vendor close." and start is None else "vendor_split_adjusted"
    price = fact(
        "price", "close", source_tag, 42.25, unit="USD/share", basis=basis,
        definition=definition, start=start,
    )
    review = manifest([price])
    review["rules"] = [{
        "rule_id": "bad-price-source",
        "output_metric": "current_share_price",
        "operation": "copy",
        "input_fact_ids": ["price"],
        "attestations": ["listing_currency", "non_dividend_adjusted_quote", "quote_date_split_basis"],
        "rationale": "Attempted price promotion.",
    }]

    result = apply_reviewed_inputs([price], review, "TEST", "2026-09-16")
    assert not result.derived_facts
    assert draft_review_manifest([price], "TEST", "2026-09-16")["candidates"]["current_share_price"] == []


@pytest.mark.unit
def test_price_review_allows_an_explicit_not_dividend_adjusted_source_label():
    price = fact(
        "price", "market_price", "market_quote", 42.25, unit="USD/share", basis="raw_quote",
        definition="Vendor states this is not dividend adjusted.",
    )
    review = manifest([price])
    review["rules"] = [{
        "rule_id": "not-adjusted-price",
        "output_metric": "current_share_price",
        "operation": "copy",
        "input_fact_ids": ["price"],
        "attestations": ["listing_currency", "non_dividend_adjusted_quote", "quote_date_split_basis"],
        "rationale": "Reviewed the provider's explicit non-dividend-adjusted label.",
    }]

    promoted_price = promoted(apply_reviewed_inputs([price], review, "TEST", "2026-09-16"), "current_share_price")
    assert promoted_price is not None
    assert "dividend adjusted" not in promoted_price.definition.replace("-", " ").casefold()


@pytest.mark.unit
def test_base_hash_is_order_stable_and_ignores_calculated_promotions_but_detects_reported_drift():
    facts = base_facts()
    result = apply_reviewed_inputs(facts, manifest(facts), "TEST", "2026-09-16")
    assert evidence_sha256(facts) == evidence_sha256(list(reversed(facts)))
    assert evidence_sha256(facts) == evidence_sha256(facts + result.derived_facts)

    changed = base_facts()
    changed[0] = fact("shares", "shares_outstanding", "dei:EntityCommonStockSharesOutstanding", 101, unit="shares")
    rejected = apply_reviewed_inputs(changed, manifest(facts), "TEST", "2026-09-16")
    assert rejected.summary["status"] == "rejected"
    assert not rejected.derived_facts
    assert any(issue.code == "REVIEW_MANIFEST_REJECTED" for issue in rejected.issues)


@pytest.mark.unit
def test_draft_is_bound_and_pending_but_never_applies_by_default():
    facts = base_facts()
    draft = draft_review_manifest(facts, "test", "2026-09-16")

    assert draft["status"] == "pending"
    assert draft["draft_only"] is True
    assert draft["rules"] == []
    assert draft["base_evidence_sha256"] == evidence_sha256(facts)
    assert {item["fact_id"] for item in draft["candidates"]["total_debt"]} == {
        "debt-current", "debt-noncurrent"
    }
    result = apply_reviewed_inputs(facts, draft, "TEST", "2026-09-16")
    assert result.summary["status"] == "rejected"
    assert not result.derived_facts

    draft["draft_only"] = False
    assert apply_reviewed_inputs(facts, draft, "TEST", "2026-09-16").summary["status"] == "rejected"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ticker", "OTHER"),
        ("as_of", "2026-09-15"),
    ],
)
def test_manifest_identity_binding_is_exact(field, value):
    facts = base_facts()
    review = manifest(facts)
    review[field] = value
    result = apply_reviewed_inputs(facts, review, "TEST", "2026-09-16")
    assert result.summary["status"] == "rejected"
    assert not result.derived_facts


@pytest.mark.unit
def test_future_or_pre_evidence_review_timestamp_rejects_manifest():
    facts = base_facts()
    future = manifest(facts)
    future["reviewer"]["reviewed_at"] = "2026-09-17T00:00:00Z"
    assert apply_reviewed_inputs(facts, future, "TEST", "2026-09-16").summary["status"] == "rejected"

    early = manifest(facts)
    early["reviewer"]["reviewed_at"] = "2026-08-01T00:00:00Z"
    assert apply_reviewed_inputs(facts, early, "TEST", "2026-09-16").summary["status"] == "rejected"


@pytest.mark.unit
def test_weighted_average_shares_and_missing_attestations_cannot_be_promoted():
    weighted = fact(
        "weighted", "weighted_average_shares",
        "us-gaap:WeightedAverageNumberOfSharesOutstandingBasic", 100,
        unit="shares", start="2026-01-01",
    )
    facts = [weighted]
    review = manifest(facts)
    review["rules"] = [{
        "rule_id": "bad-shares",
        "output_metric": "current_shares",
        "operation": "copy",
        "input_fact_ids": ["weighted"],
        "attestations": [],
        "rationale": "Not sufficient.",
    }]
    result = apply_reviewed_inputs(facts, review, "TEST", "2026-09-16")
    assert result.summary["status"] == "rejected"
    assert not result.derived_facts
    assert "weighted" not in {item["fact_id"] for item in draft_review_manifest(facts, "TEST", "2026-09-16")["candidates"]["current_shares"]}


@pytest.mark.unit
def test_known_parent_component_overlap_and_custom_debt_are_rejected():
    parent = fact("parent", "long_term_debt_reported", "us-gaap:LongTermDebt", 200, unit="USD")
    current = fact("current", "long_term_debt_current", "us-gaap:LongTermDebtCurrent", 40, unit="USD")
    custom = fact("custom", "custom_notes", "acme:NotesPayable", 10, unit="USD")
    facts = [parent, current, custom]
    review = manifest(facts)
    review["rules"] = [{
        "rule_id": "overlap",
        "output_metric": "total_debt",
        "operation": "sum",
        "input_fact_ids": ["parent", "current", "custom"],
        "attestations": ["complete_interest_bearing_debt", "disjoint_scopes"],
        "rationale": "Attempted overlap.",
    }]
    result = apply_reviewed_inputs(facts, review, "TEST", "2026-09-16")
    assert result.summary["status"] == "rejected"
    assert promoted(result, "total_debt") is None


@pytest.mark.unit
def test_mixed_units_dates_negative_values_missing_ids_and_reuse_fail_closed():
    facts = base_facts() + [
        fact("eur", "long_term_debt_current", "us-gaap:LongTermDebtCurrent", 1, unit="EUR"),
        fact("old", "long_term_debt_noncurrent", "us-gaap:LongTermDebtNoncurrent", 1, unit="USD", end="2026-03-31"),
        fact("negative", "commercial_paper", "us-gaap:CommercialPaper", -1, unit="USD"),
    ]
    review = manifest(facts)
    review["rules"] = [
        {
            "rule_id": "bad-debt",
            "output_metric": "total_debt",
            "operation": "sum",
            "input_fact_ids": ["eur", "old", "negative", "missing"],
            "attestations": ["complete_interest_bearing_debt", "disjoint_scopes"],
            "rationale": "Invalid inputs.",
        },
        {
            "rule_id": "reuse",
            "output_metric": "current_shares",
            "operation": "copy",
            "input_fact_ids": ["eur"],
            "attestations": ["point_in_time_common_shares", "raw_as_reported_basis"],
            "rationale": "Reuses a source.",
        },
    ]
    result = apply_reviewed_inputs(facts, review, "TEST", "2026-09-16")
    assert result.summary["status"] == "rejected"
    assert not result.derived_facts
    assert all(decision["status"] == "rejected" for decision in result.summary["rule_decisions"])


@pytest.mark.unit
def test_no_manifest_is_nonmutating_and_not_provided():
    result = apply_reviewed_inputs(base_facts(), None, "TEST", "2026-09-16")
    assert result.summary["status"] == "not_provided"
    assert result.derived_facts == []
    assert result.issues == []


@pytest.mark.unit
def test_duplicate_rule_ids_or_outputs_reject_every_ambiguous_rule():
    facts = base_facts()
    review = manifest(facts)
    duplicate = dict(review["rules"][0])
    duplicate["rule_id"] = "shares-review-2"
    review["rules"] = [review["rules"][0], duplicate]
    result = apply_reviewed_inputs(facts, review, "TEST", "2026-09-16")
    assert result.summary["status"] == "rejected"
    assert not result.derived_facts
    assert all(decision["status"] == "rejected" for decision in result.summary["rule_decisions"])

    review = manifest(facts)
    review["rules"][1]["rule_id"] = review["rules"][0]["rule_id"]
    result = apply_reviewed_inputs(facts, review, "TEST", "2026-09-16")
    assert result.summary["status"] == "rejected"
    assert not result.derived_facts


@pytest.mark.unit
def test_current_shares_sum_is_rejected_and_large_copy_value_is_exact():
    large = 9_007_199_254_740_993
    first = fact("class-a", "filing_common_stock_shares_outstanding", "us-gaap:CommonStockSharesOutstanding", large, unit="shares")
    second = fact("class-b", "filing_common_stock_shares_outstanding", "us-gaap:CommonStockSharesOutstanding", 1, unit="shares")
    facts = [first, second]
    review = manifest(facts)
    review["rules"] = [{
        "rule_id": "share-sum",
        "output_metric": "current_shares",
        "operation": "sum",
        "input_fact_ids": ["class-a", "class-b"],
        "attestations": ["point_in_time_common_shares", "raw_as_reported_basis", "disjoint_scopes"],
        "rationale": "Attempted class sum.",
    }]
    assert not apply_reviewed_inputs(facts, review, "TEST", "2026-09-16").derived_facts

    review["rules"][0].update(operation="copy", input_fact_ids=["class-a"])
    result = apply_reviewed_inputs(facts, review, "TEST", "2026-09-16")
    assert promoted(result, "current_shares").value == large
