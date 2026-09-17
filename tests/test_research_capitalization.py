from __future__ import annotations

import json

import pytest

from tradingagents.research import ResearchPacket, build_packet
from tradingagents.research.capitalization import CapitalizationResult, analyze_capitalization
from tradingagents.research.models import BusinessModel, EvidenceFact


def fact(
    fact_id: str,
    metric: str,
    value: float,
    end: str,
    *,
    unit: str = "USD",
    start: str | None = None,
    basis: str = "as_reported",
    definition: str | None = None,
    published: str = "2026-03-31T20:00:00Z",
) -> EvidenceFact:
    return EvidenceFact.from_dict({
        "fact_id": fact_id,
        "metric": metric,
        "value": value,
        "unit": unit,
        "period_start": start,
        "period_end": end,
        "published_at": published,
        "retrieved_at": "2026-03-31T21:00:00Z",
        "source_url": f"https://example.test/{fact_id}",
        "definition": definition or metric.replace("_", " "),
        "adjustment_basis": basis,
        "kind": "reported",
    })


def closed_inputs() -> list[EvidenceFact]:
    return [
        fact("price", "current_share_price", 10, "2026-03-31", unit="USD/share", basis="vendor_split_adjusted"),
        fact("shares", "current_shares", 100, "2026-01-31", unit="shares"),
        fact("split-complete", "split_history_complete", 1, "2026-03-31", unit="ratio", start="2026-01-01"),
        fact("split", "split_ratio", 2, "2026-02-15", unit="ratio"),
        fact("classes", "share_class_coverage_ratio", 1, "2026-03-31", unit="ratio"),
        fact("adr", "adr_ratio", 1, "2026-03-31", unit="ratio"),
        fact("cash", "cash", 300, "2026-03-31"),
        fact("debt", "total_debt", 500, "2026-03-31", definition="Total debt: all interest-bearing obligations"),
        fact("preferred", "preferred_stock", 0, "2026-03-31"),
        fact("nci", "noncontrolling_interest", 0, "2026-03-31"),
        fact("revenue", "revenue_ttm", 1_000, "2026-03-31", start="2025-04-01"),
        fact("income", "net_income_attributable_to_common_ttm", 200, "2026-03-31", start="2025-04-01"),
        fact("fcf", "free_cash_flow_ttm", 100, "2026-03-31", start="2025-04-01"),
    ]


def derived(result: CapitalizationResult, metric: str) -> EvidenceFact | None:
    return next((f for f in result.derived_facts if f.metric == metric), None)


@pytest.mark.unit
def test_closed_numeric_example_has_full_lineage_and_multiples():
    result = analyze_capitalization(closed_inputs(), BusinessModel.INDUSTRIAL, "2026-04-01")

    assert derived(result, "current_shares_split_adjusted").value == 200
    assert derived(result, "market_cap").value == 2_000
    assert derived(result, "net_debt").value == 200
    assert derived(result, "enterprise_value").value == 2_200
    assert derived(result, "enterprise_value_to_revenue").value == pytest.approx(2.2)
    assert derived(result, "price_to_earnings").value == pytest.approx(10)
    assert derived(result, "price_to_free_cash_flow").value == pytest.approx(20)
    assert derived(result, "market_cap").input_fact_ids == (
        "price", derived(result, "current_shares_split_adjusted").fact_id, "classes", "adr"
    )
    assert result.summary["status"] == "sufficient"


@pytest.mark.unit
def test_vendor_market_cap_shares_and_close_are_observations_only():
    inputs = [
        fact("vendor-cap", "market_cap_reported", 999, "2026-03-31"),
        fact("vendor-shares", "shares_outstanding_market", 100, "2026-03-31", unit="shares"),
        fact("close", "close", 10, "2026-03-31", unit="USD/share", basis="vendor_split_adjusted"),
    ]
    result = analyze_capitalization(inputs, BusinessModel.GENERAL, "2026-04-01")

    assert derived(result, "market_cap") is None
    assert result.summary["status"] == "unsupported"
    assert result.summary["provider_observations"]["reported_market_cap"][0]["fact_id"] == "vendor-cap"


def test_evidence_plan_tracks_split_interval_without_inventing_completeness():
    inputs = [f for f in closed_inputs() if f.metric != "split_history_complete"]
    inputs.append(fact("outside", "split_ratio", 3, "2026-01-31", unit="ratio"))
    result = analyze_capitalization(inputs, BusinessModel.GENERAL, "2026-04-01")
    plan = result.summary["market_cap_evidence_plan"]
    assert not plan["ready"]
    assert plan["split_interval"]["start_exclusive"] == "2026-01-31"
    assert plan["split_interval"]["end_inclusive"] == "2026-03-31"
    assert plan["split_interval"]["observed_split_fact_ids"] == ["split"]
    assert plan["split_interval"]["absence_of_events_proves_no_split"] is False
    assert "split_history_complete" in {item["requirement"] for item in plan["outstanding"]}
    complete = analyze_capitalization(closed_inputs(), BusinessModel.GENERAL, "2026-04-01")
    assert complete.summary["market_cap_evidence_plan"]["ready"]
    assert complete.summary["market_cap_evidence_plan"]["outstanding"] == []


def test_stale_inputs_are_both_reported_and_readiness_stays_false():
    result = analyze_capitalization(closed_inputs(), BusinessModel.GENERAL, "2026-09-01")
    stale = [i.metric for i in result.issues if i.code == "STALE_CAPITALIZATION_INPUT"]
    assert "current_share_price" in stale and "current_shares" in stale
    assert not result.summary["market_cap_evidence_plan"]["ready"]
    assert not result.summary["market_cap_prerequisites"]["fresh_positive_price_and_shares"]
    assert derived(result, "market_cap") is None


@pytest.mark.unit
def test_missing_split_completeness_withholds_cap_and_event_before_shares_is_ignored():
    missing = [f for f in closed_inputs() if f.metric != "split_history_complete"]
    assert derived(analyze_capitalization(missing, BusinessModel.GENERAL, "2026-04-01"), "market_cap") is None

    inputs = [f for f in closed_inputs() if f.metric != "split_ratio"]
    inputs.append(fact("old-split", "split_ratio", 5, "2026-01-01", unit="ratio"))
    result = analyze_capitalization(inputs, BusinessModel.GENERAL, "2026-04-01")
    assert derived(result, "current_shares_split_adjusted").value == 100
    assert derived(result, "market_cap").value == 1_000


@pytest.mark.unit
@pytest.mark.parametrize("ratio", [0, -2])
def test_nonpositive_in_window_split_ratio_blocks_cap(ratio):
    inputs = [f for f in closed_inputs() if f.metric != "split_ratio"]
    inputs.append(fact("bad-split", "split_ratio", ratio, "2026-02-15", unit="ratio"))
    result = analyze_capitalization(inputs, BusinessModel.GENERAL, "2026-04-01")
    assert derived(result, "market_cap") is None
    assert result.summary["status"] == "material_conflict"
    assert any(i.code == "INVALID_SPLIT_RATIO" for i in result.issues)


@pytest.mark.unit
def test_staleness_and_debt_components_fail_closed():
    inputs = closed_inputs()
    inputs = [
        fact(f.fact_id, f.metric, (-1 if f.metric == "net_income_attributable_to_common_ttm" else f.value), f.period_end,
             unit=("EUR" if f.metric == "cash" else f.unit), start=f.period_start,
             basis=f.adjustment_basis, definition=f.definition)
        for f in inputs
        if f.metric != "total_debt"
    ]
    inputs.append(fact("long", "long_term_debt", 400, "2026-03-31"))
    inputs.append(fact("short", "short_term_debt", 100, "2026-03-31"))
    # Stale shares independently blocks equity capitalization.
    inputs = [
        fact(f.fact_id, f.metric, f.value, ("2025-01-01" if f.metric == "current_shares" else f.period_end),
             unit=f.unit, start=f.period_start, basis=f.adjustment_basis, definition=f.definition)
        for f in inputs
    ]
    result = analyze_capitalization(inputs, BusinessModel.GENERAL, "2026-04-01")

    assert derived(result, "market_cap") is None
    assert derived(result, "total_debt") is None
    assert derived(result, "enterprise_value") is None
    assert any(i.code == "DEBT_COMPONENTS_INCOMPLETE" for i in result.issues)
    assert any(i.code == "STALE_CAPITALIZATION_INPUT" for i in result.issues)


@pytest.mark.unit
def test_currency_mismatch_blocks_net_debt_and_ev_without_hiding_equity_cap():
    inputs = [
        fact(f.fact_id, f.metric, f.value, f.period_end, unit=("EUR" if f.metric == "cash" else f.unit),
             start=f.period_start, basis=f.adjustment_basis, definition=f.definition)
        for f in closed_inputs()
    ]
    result = analyze_capitalization(inputs, BusinessModel.GENERAL, "2026-04-01")
    assert derived(result, "market_cap") is not None
    assert derived(result, "price_to_earnings") is not None
    assert derived(result, "net_debt") is None
    assert derived(result, "enterprise_value") is None
    assert any(i.code == "CAPITALIZATION_CURRENCY_CONFLICT" for i in result.issues)


@pytest.mark.unit
def test_negative_common_income_withholds_only_pe_and_negative_price_blocks_cap():
    loss_inputs = [
        fact(f.fact_id, f.metric,
             (-1 if f.metric == "net_income_attributable_to_common_ttm" else f.value),
             f.period_end, unit=f.unit, start=f.period_start, basis=f.adjustment_basis,
             definition=f.definition)
        for f in closed_inputs()
    ]
    loss_result = analyze_capitalization(loss_inputs, BusinessModel.GENERAL, "2026-04-01")
    assert derived(loss_result, "market_cap") is not None
    assert derived(loss_result, "enterprise_value") is not None
    assert derived(loss_result, "price_to_earnings") is None
    assert any(i.code == "NONPOSITIVE_MULTIPLE_DENOMINATOR" for i in loss_result.issues)

    negative_price = [
        fact(f.fact_id, f.metric, (-10 if f.metric == "current_share_price" else f.value),
             f.period_end, unit=f.unit, start=f.period_start, basis=f.adjustment_basis,
             definition=f.definition)
        for f in closed_inputs()
    ]
    price_result = analyze_capitalization(negative_price, BusinessModel.GENERAL, "2026-04-01")
    assert derived(price_result, "market_cap") is None
    assert price_result.summary["status"] == "material_conflict"
    assert any(i.code == "NONPOSITIVE_CAPITALIZATION_INPUT" for i in price_result.issues)


@pytest.mark.unit
def test_bank_suppresses_ev_and_fcf_but_allows_positive_pe():
    result = analyze_capitalization(closed_inputs(), BusinessModel.BANK, "2026-04-01")

    assert derived(result, "market_cap") is not None
    assert derived(result, "price_to_earnings") is not None
    assert derived(result, "enterprise_value") is None
    assert derived(result, "enterprise_value_to_revenue") is None
    assert derived(result, "price_to_free_cash_flow") is None
    assert any(i.code == "BANK_EV_UNSUPPORTED" for i in result.issues)


@pytest.mark.unit
def test_future_fact_is_guarded_even_if_upstream_filter_is_bypassed():
    inputs = closed_inputs() + [
        fact("future-price", "current_share_price", 20, "2026-04-02", unit="USD/share", basis="vendor_split_adjusted", published="2026-04-02T12:00:00Z")
    ]
    result = analyze_capitalization(inputs, BusinessModel.GENERAL, "2026-04-01")
    assert derived(result, "market_cap").value == 2_000
    assert any(i.code == "CAP_FACT_AFTER_CUTOFF" for i in result.issues)


@pytest.mark.unit
def test_generic_net_income_and_mismatched_balance_dates_are_not_combined():
    inputs = [f for f in closed_inputs() if f.metric != "net_income_attributable_to_common_ttm"]
    inputs.append(fact("generic-income", "net_income_ttm", 200, "2026-03-31", start="2025-04-01"))
    inputs = [
        fact(f.fact_id, f.metric, f.value, ("2026-03-30" if f.metric == "cash" else f.period_end),
             unit=f.unit, start=f.period_start, basis=f.adjustment_basis, definition=f.definition)
        for f in inputs
    ]
    result = analyze_capitalization(inputs, BusinessModel.GENERAL, "2026-04-01")

    assert derived(result, "price_to_earnings") is None
    assert derived(result, "net_debt") is None
    assert derived(result, "enterprise_value") is None
    assert any(i.code == "EARNINGS_SCOPE_UNRESOLVED" for i in result.issues)
    assert any(i.code == "BALANCE_DATE_MISMATCH" for i in result.issues)


@pytest.mark.unit
def test_same_date_split_duplicates_are_deduplicated_and_disagreement_blocks():
    identical = closed_inputs() + [fact("split-copy", "split_ratio", 2, "2026-02-15", unit="ratio")]
    result = analyze_capitalization(identical, BusinessModel.GENERAL, "2026-04-01")
    assert derived(result, "current_shares_split_adjusted").value == 200

    conflicting = closed_inputs() + [fact("split-conflict", "split_ratio", 3, "2026-02-15", unit="ratio")]
    result = analyze_capitalization(conflicting, BusinessModel.GENERAL, "2026-04-01")
    assert derived(result, "market_cap") is None
    assert result.summary["status"] == "material_conflict"
    assert any(i.code == "SPLIT_RATIO_CONFLICT" for i in result.issues)


@pytest.mark.unit
def test_pre_adjusted_shares_are_not_adjusted_again_and_incomplete_debt_label_is_rejected():
    inputs = [
        fact(f.fact_id, f.metric, f.value, f.period_end, unit=f.unit, start=f.period_start,
             basis=("split_adjusted" if f.metric == "current_shares" else f.adjustment_basis),
             definition=("Total debt excluding leases" if f.metric == "total_debt" else f.definition))
        for f in closed_inputs()
    ]
    result = analyze_capitalization(inputs, BusinessModel.GENERAL, "2026-04-01")

    assert derived(result, "current_shares_split_adjusted") is None
    assert derived(result, "market_cap") is None
    assert result.summary["capital_structure_inputs"]["total_debt"] is None
    assert any(i.code == "SHARE_SPLIT_BASIS_UNRESOLVED" for i in result.issues)
    assert any(i.code == "TOTAL_DEBT_DEFINITION_UNRESOLVED" for i in result.issues)


@pytest.mark.unit
def test_stale_class_and_adr_proofs_withhold_market_cap():
    inputs = [
        fact(f.fact_id, f.metric, f.value,
             ("2025-01-01" if f.metric in {"share_class_coverage_ratio", "adr_ratio"} else f.period_end),
             unit=f.unit, start=f.period_start, basis=f.adjustment_basis, definition=f.definition)
        for f in closed_inputs()
    ]
    result = analyze_capitalization(inputs, BusinessModel.GENERAL, "2026-04-01")
    assert derived(result, "market_cap") is None
    assert result.summary["market_cap_prerequisites"]["share_class_coverage_ratio_equal_1"] is False
    assert result.summary["market_cap_prerequisites"]["adr_ratio_equal_1"] is False


@pytest.mark.unit
def test_preferred_and_nci_carrying_disclosures_are_candidates_not_ev_inputs():
    inputs = [f for f in closed_inputs() if f.metric not in {"preferred_stock", "noncontrolling_interest"}]
    inputs.extend([
        fact("preferred-carrying", "preferred_stock_value", 10, "2026-03-31"),
        fact("preferred-liquidation", "preferred_stock_liquidation_preference", 20, "2026-03-31"),
        fact("ordinary-nci", "noncontrolling_interest_carrying", -5, "2026-03-31"),
        fact("redeemable-nci", "redeemable_nci_equity_carrying_amount", 7, "2026-03-31"),
    ])
    result = analyze_capitalization(inputs, BusinessModel.GENERAL, "2026-04-01")

    assert derived(result, "enterprise_value") is None
    assert result.summary["capital_structure_inputs"]["preferred_stock"] is None
    assert result.summary["capital_structure_inputs"]["noncontrolling_interest"] is None
    assert len(result.summary["preferred_component_candidates"]) == 2
    assert len(result.summary["noncontrolling_interest_component_candidates"]) == 2
    assert not any(i.code in {"CAPITALIZATION_INPUT_CONFLICT", "NEGATIVE_CAPITAL_STRUCTURE_INPUT"} for i in result.issues)


@pytest.mark.unit
def test_closed_inputs_survive_build_packet_and_serialization_roundtrip():
    class Provider:
        def fetch(self, ticker, as_of):
            return {
                "identity": {"ticker": ticker, "sic": "7372", "currency": "USD"},
                "facts": [item.to_dict() for item in closed_inputs()],
            }

    packet = build_packet("TEST", "2026-04-01", [Provider()])
    metrics = {item.metric: item for item in packet.facts}

    assert metrics["market_cap"].value == 2_000
    assert metrics["enterprise_value"].value == 2_200
    assert metrics["price_to_earnings"].value == 10
    assert packet.financial_analysis["capitalization"]["status"] == "sufficient"
    restored = ResearchPacket.from_dict(json.loads(json.dumps(packet.to_dict())))
    assert restored.to_dict() == packet.to_dict()
