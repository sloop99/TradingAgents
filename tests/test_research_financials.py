from __future__ import annotations

import json

import pytest

from tradingagents.research.financials import FinancialResult, analyze_financials
from tradingagents.research.models import BusinessModel, EvidenceFact


def fact(
    fact_id: str,
    metric: str,
    value: float,
    start: str | None,
    end: str,
    *,
    unit: str = "USD",
    definition: str | None = None,
    basis: str = "as_reported",
    published: str = "2025-11-01",
    retrieved: str = "2026-09-16T12:00:00Z",
    source_tag: str | None = None,
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
            "retrieved_at": retrieved,
            "source_url": f"https://example.test/{fact_id}",
            "accession": fact_id.split(":")[0],
            "source_tag": source_tag or f"us-gaap:{metric}",
            "definition": definition or metric.replace("_", " "),
            "adjustment_basis": basis,
            "kind": "reported",
        }
    )


def metrics(result: FinancialResult, name: str) -> list[EvidenceFact]:
    return [item for item in result.derived_facts if item.metric == name]


@pytest.mark.unit
def test_cumulative_ytd_subtraction_produces_only_quarter_with_lineage_and_latest_timestamps():
    q1 = fact("q1-ytd", "revenue", 100, "2025-01-01", "2025-03-31", published="2025-05-01")
    q2_ytd = fact(
        "q2-ytd",
        "revenue",
        230,
        "2025-01-01",
        "2025-06-30",
        published="2025-08-02T10:00:00-04:00",
        retrieved="2026-09-16T13:00:00Z",
    )

    result = analyze_financials([q1, q2_ytd], BusinessModel.INDUSTRIAL)

    quarter = metrics(result, "revenue")[0]
    assert quarter.value == 130
    assert (quarter.period_start, quarter.period_end) == ("2025-04-01", "2025-06-30")
    assert quarter.fact_id.startswith("calc:quarter:revenue:2025-04-01:2025-06-30:")
    assert quarter.input_fact_ids == ("q2-ytd", "q1-ytd")
    assert quarter.published_at == q2_ytd.published_at
    assert quarter.retrieved_at == q2_ytd.retrieved_at
    assert quarter.source_url == q2_ytd.source_url
    assert quarter.adjustment_basis == "as_reported"


@pytest.mark.unit
def test_q4_is_fiscal_year_less_nine_months_but_negative_revenue_and_capex_are_withheld():
    inputs = [
        fact("rev-9m", "revenue", 900, "2025-01-01", "2025-09-30"),
        fact("rev-fy", "revenue", 1_250, "2025-01-01", "2025-12-31"),
        fact("capex-9m", "capital_expenditures", 80, "2025-01-01", "2025-09-30", basis="cash_outflow_positive"),
        fact("capex-fy", "capital_expenditures", 70, "2025-01-01", "2025-12-31", basis="cash_outflow_positive"),
    ]

    result = analyze_financials(inputs, BusinessModel.GENERAL)

    q4 = metrics(result, "revenue")[0]
    assert q4.value == 350
    assert q4.period_start == "2025-10-01"
    assert not metrics(result, "capital_expenditures")
    assert any(issue.code == "NEGATIVE_DERIVED_VALUE" for issue in result.issues)


@pytest.mark.unit
def test_ttm_accepts_contiguous_53_week_year_and_preserves_capex_sign_basis():
    periods = [
        ("2024-09-29", "2024-12-28"),
        ("2024-12-29", "2025-03-29"),
        ("2025-03-30", "2025-06-28"),
        ("2025-06-29", "2025-10-04"),
    ]
    inputs = []
    for index, (start, end) in enumerate(periods, 1):
        inputs.append(fact(f"rev-q{index}", "revenue", index * 10, start, end))
        inputs.append(
            fact(
                f"capex-q{index}",
                "capital_expenditures",
                index,
                start,
                end,
                basis="cash_outflow_positive",
            )
        )

    result = analyze_financials(inputs, BusinessModel.INDUSTRIAL)

    revenue_ttm = metrics(result, "revenue_ttm")[0]
    capex_ttm = metrics(result, "capital_expenditures_ttm")[0]
    assert revenue_ttm.value == 100
    assert capex_ttm.value == 10
    assert capex_ttm.adjustment_basis == "cash_outflow_positive"
    assert len(revenue_ttm.input_fact_ids) == 4
    assert result.summary["periods"]["ttm"] == ["2024-09-29 to 2025-10-04"]


@pytest.mark.unit
def test_ttm_combines_reported_first_three_quarters_with_derived_q4():
    inputs = [
        fact("q1", "revenue", 100, "2025-01-01", "2025-03-31"),
        fact("q2", "revenue", 120, "2025-04-01", "2025-06-30"),
        fact("q3", "revenue", 140, "2025-07-01", "2025-09-30"),
        fact("nine-month", "revenue", 360, "2025-01-01", "2025-09-30"),
        fact("fiscal-year", "revenue", 520, "2025-01-01", "2025-12-31"),
        fact("next-q1", "revenue", 180, "2026-01-01", "2026-03-31"),
    ]

    result = analyze_financials(inputs, BusinessModel.GENERAL)

    q4 = next(
        item
        for item in metrics(result, "revenue")
        if item.kind.value == "calculated" and item.period_start == "2025-10-01"
    )
    ttm = metrics(result, "revenue_ttm")[0]
    assert q4.source_tag == inputs[0].source_tag
    assert q4.value == 160
    assert ttm.value == 600
    assert q4.fact_id in ttm.input_fact_ids


@pytest.mark.unit
def test_ttm_requires_four_contiguous_nonoverlapping_quarters():
    quarters = [
        fact("q1", "revenue", 10, "2024-01-01", "2024-03-31"),
        fact("q2", "revenue", 20, "2024-04-01", "2024-06-30"),
        fact("q3-gap", "revenue", 30, "2024-07-02", "2024-09-30"),
        fact("q4", "revenue", 40, "2024-10-01", "2024-12-31"),
    ]
    assert not metrics(analyze_financials(quarters, BusinessModel.GENERAL), "revenue_ttm")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"unit": "EUR"}, "FINANCIAL_UNIT_CONFLICT"),
        ({"definition": "Revenue excluding excise taxes"}, "FINANCIAL_DEFINITION_CONFLICT"),
    ],
)
def test_mixed_units_and_inconsistent_definitions_block_period_arithmetic(override, code):
    q1 = fact("q1", "revenue", 100, "2025-01-01", "2025-03-31")
    duplicate = fact("q1-other", "revenue", 101, "2025-01-01", "2025-03-31", **override)
    ytd = fact("ytd", "revenue", 220, "2025-01-01", "2025-06-30")

    result = analyze_financials([q1, duplicate, ytd], BusinessModel.GENERAL)

    assert not [f for f in metrics(result, "revenue") if f.kind.value == "calculated"]
    assert any(issue.code == code for issue in result.issues)


@pytest.mark.unit
def test_weighted_and_current_shares_are_never_subtracted_and_unknown_split_basis_blocks_growth():
    inputs = [
        fact("current", "shares_outstanding", 120, None, "2025-12-31", unit="shares"),
        fact("weighted-q1", "weighted_average_shares", 100, "2025-01-01", "2025-03-31", unit="shares"),
        fact("weighted-ytd", "weighted_average_shares", 105, "2025-01-01", "2025-06-30", unit="shares"),
    ]

    result = analyze_financials(inputs, BusinessModel.SOFTWARE)

    assert not any("shares" in fact.metric for fact in result.derived_facts)
    assert result.summary["shares"]["current_shares"]["fact_id"] == "current"
    assert result.summary["shares"]["weighted_average_shares_diluted"]["fact_id"] == "weighted-ytd"
    assert result.summary["shares"]["split_basis_resolved"] is False
    assert any(issue.code == "SHARE_SPLIT_BASIS_UNRESOLVED" for issue in result.issues)


@pytest.mark.unit
def test_share_change_requires_same_metric_definition_and_explicit_split_adjustment():
    inputs = [
        fact("s24", "shares_outstanding", 100, None, "2024-12-31", unit="shares", basis="split_adjusted"),
        fact("s25", "shares_outstanding", 110, None, "2025-12-31", unit="shares", basis="split_adjusted"),
        fact("w24", "weighted_average_shares", 90, "2024-01-01", "2024-12-31", unit="shares", basis="split_adjusted"),
        fact("w25", "weighted_average_shares", 99, "2025-01-01", "2025-12-31", unit="shares", basis="split_adjusted"),
    ]

    result = analyze_financials(inputs, BusinessModel.SOFTWARE)

    assert metrics(result, "shares_outstanding_change_yoy")[0].value == pytest.approx(10)
    assert metrics(result, "weighted_average_shares_change_yoy")[0].value == pytest.approx(10)
    assert len(result.summary["shares"]["comparative_changes"]) == 2


@pytest.mark.unit
def test_loss_making_company_gets_negative_margins_but_nonpositive_prior_revenue_blocks_growth():
    inputs = [
        fact("rev-prior", "revenue", 0, "2024-01-01", "2024-12-31"),
        fact("rev", "revenue", 200, "2025-01-01", "2025-12-31"),
        fact("op", "operating_income", -40, "2025-01-01", "2025-12-31"),
        fact("net", "net_income", -60, "2025-01-01", "2025-12-31"),
        fact("cfo", "operating_cash_flow", -10, "2025-01-01", "2025-12-31"),
        fact("capex", "capital_expenditures", 20, "2025-01-01", "2025-12-31", basis="cash_outflow_positive"),
    ]

    result = analyze_financials(inputs, BusinessModel.SOFTWARE)

    assert metrics(result, "operating_margin")[0].value == -20
    assert metrics(result, "net_margin")[0].value == -30
    assert metrics(result, "free_cash_flow")[0].value == -30
    assert metrics(result, "free_cash_flow_margin")[0].value == -15
    assert not metrics(result, "revenue_growth_yoy")
    assert any(issue.code == "YOY_GROWTH_WITHHELD" for issue in result.issues)


@pytest.mark.unit
def test_ppe_only_fcf_is_labeled_as_a_limited_definition():
    inputs = [
        fact("cfo", "operating_cash_flow", 100, "2025-01-01", "2025-12-31"),
        fact(
            "ppe",
            "capital_expenditures",
            20,
            "2025-01-01",
            "2025-12-31",
            basis="cash_outflow_positive",
            definition="Payments to acquire property, plant and equipment",
            source_tag="us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
        ),
    ]

    result = analyze_financials(inputs, BusinessModel.SOFTWARE)

    assert metrics(result, "free_cash_flow")[0].value == 80
    assert "not an issuer-defined" in metrics(result, "free_cash_flow")[0].definition
    assert any(issue.code == "FCF_DEFINITION_LIMITED" for issue in result.issues)


@pytest.mark.unit
def test_bank_explicitly_omits_generic_fcf_and_margin_analytics():
    inputs = [
        fact("rev", "revenue", 500, "2025-01-01", "2025-12-31"),
        fact("net", "net_income", 100, "2025-01-01", "2025-12-31"),
        fact("cfo", "operating_cash_flow", 120, "2025-01-01", "2025-12-31"),
        fact("capex", "capital_expenditures", 20, "2025-01-01", "2025-12-31", basis="cash_outflow_positive"),
    ]

    result = analyze_financials(inputs, BusinessModel.BANK)

    assert not any("margin" in item.metric or "free_cash_flow" in item.metric for item in result.derived_facts)
    assert "free_cash_flow" not in result.summary["missing"]
    assert any(issue.code == "BANK_GENERIC_ANALYTICS_UNSUPPORTED" for issue in result.issues)


@pytest.mark.unit
def test_exact_reported_annual_prevents_duplicate_ttm_and_disagreement_is_visible():
    quarters = [
        fact("q1", "revenue", 10, "2025-01-01", "2025-03-31"),
        fact("q2", "revenue", 20, "2025-04-01", "2025-06-30"),
        fact("q3", "revenue", 30, "2025-07-01", "2025-09-30"),
        fact("q4", "revenue", 40, "2025-10-01", "2025-12-31"),
    ]
    matching = analyze_financials(quarters + [fact("fy", "revenue", 100, "2025-01-01", "2025-12-31")], BusinessModel.GENERAL)
    conflict = analyze_financials(quarters + [fact("fy", "revenue", 101, "2025-01-01", "2025-12-31")], BusinessModel.GENERAL)

    assert not metrics(matching, "revenue_ttm")
    assert not metrics(conflict, "revenue_ttm")
    assert any(issue.code == "TTM_ANNUAL_CONFLICT" for issue in conflict.issues)


@pytest.mark.unit
def test_reported_quarter_conflict_is_error_and_period_is_blocked_downstream():
    inputs = [
        fact("q1-ytd", "revenue", 100, "2025-01-01", "2025-03-31"),
        fact("q2-ytd", "revenue", 230, "2025-01-01", "2025-06-30"),
        fact("q2-direct", "revenue", 131, "2025-04-01", "2025-06-30"),
        fact("q2-net", "net_income", 13, "2025-04-01", "2025-06-30"),
    ]

    result = analyze_financials(inputs, BusinessModel.GENERAL)

    conflict = next(issue for issue in result.issues if issue.code == "QUARTER_DERIVATION_CONFLICT")
    assert conflict.severity.value == "error"
    assert not [
        item
        for item in result.derived_facts
        if item.period_end == "2025-06-30" and item.metric in {"net_margin", "revenue"}
    ]


@pytest.mark.unit
def test_summary_separates_period_types_and_marks_old_cash_flow_stale():
    inputs = [
        fact("rev-q", "revenue", 80, "2025-10-01", "2025-12-31"),
        fact("rev-fy", "revenue", 300, "2025-01-01", "2025-12-31"),
        fact("net-q", "net_income", 8, "2025-10-01", "2025-12-31"),
        fact("cfo-old", "operating_cash_flow", 50, "2024-01-01", "2024-12-31"),
        fact("capex-old", "capital_expenditures", 10, "2024-01-01", "2024-12-31", basis="cash_outflow_positive"),
    ]

    result = analyze_financials(inputs, BusinessModel.GENERAL)

    assert result.summary["latest_metrics"]["revenue"]["fact_id"] == "rev-q"
    assert result.summary["latest_by_period"]["quarter"]["revenue"]["fact_id"] == "rev-q"
    assert result.summary["latest_by_period"]["annual"]["revenue"]["fact_id"] == "rev-fy"
    assert "free_cash_flow" not in result.summary["latest_metrics"]
    assert {item["metric"] for item in result.summary["stale_inputs"]} == {
        "capital_expenditures",
        "free_cash_flow",
        "operating_cash_flow",
    }


@pytest.mark.unit
def test_result_summary_is_json_safe_and_all_derived_lineage_is_closed():
    inputs = [
        fact("rev", "revenue", 200, "2025-01-01", "2025-12-31"),
        fact("net", "net_income", 20, "2025-01-01", "2025-12-31"),
    ]
    result = analyze_financials(inputs, BusinessModel.GENERAL)
    known = {item.fact_id for item in inputs + result.derived_facts}
    for item in result.derived_facts:
        assert set(item.input_fact_ids) <= known
    assert json.loads(json.dumps(result.summary)) == result.summary
    assert result.summary["latest_metrics"]["net_margin"]["fact_id"].startswith("calc:margin:")
    assert set(result.summary) == {
        "latest_metrics",
        "latest_by_period",
        "periods",
        "shares",
        "missing",
        "stale_inputs",
        "limitations",
    }


@pytest.mark.unit
def test_quarter_gap_within_filing_rounding_is_reconciled_not_a_conflict():
    # Figures reported in millions: 12,189M - 6,001M = 6,188M vs a reported 6,187M (one rounding unit).
    inputs = [
        fact("q1-ytd", "revenue", 6_001_000_000, "2025-01-01", "2025-03-31"),
        fact("h1-ytd", "revenue", 12_189_000_000, "2025-01-01", "2025-06-30"),
        fact("q2-direct", "revenue", 6_187_000_000, "2025-04-01", "2025-06-30"),
        fact("q2-net", "net_income", 600_000_000, "2025-04-01", "2025-06-30"),
    ]

    result = analyze_financials(inputs, BusinessModel.GENERAL)

    assert not any(issue.code == "QUARTER_DERIVATION_CONFLICT" for issue in result.issues)
    reconciled = next(issue for issue in result.issues if issue.code == "QUARTER_DERIVATION_RECONCILED")
    assert "rounding" in reconciled.message and reconciled.severity.value == "info"
    assert [f for f in metrics(result, "net_margin") if f.period_end == "2025-06-30"]


@pytest.mark.unit
def test_quarter_gap_beyond_rounding_still_conflicts():
    inputs = [
        fact("q1-ytd", "revenue", 6_001_000_000, "2025-01-01", "2025-03-31"),
        fact("h1-ytd", "revenue", 12_189_000_000, "2025-01-01", "2025-06-30"),
        fact("q2-direct", "revenue", 6_185_000_000, "2025-04-01", "2025-06-30"),
    ]

    result = analyze_financials(inputs, BusinessModel.GENERAL)

    assert any(issue.code == "QUARTER_DERIVATION_CONFLICT" for issue in result.issues)


@pytest.mark.unit
def test_quarter_gap_from_a_restated_earlier_filing_is_reconciled():
    # A later filing restated the six-month loss; the nine-month figure is the original edition.
    h1_restated = fact("c:h1", "net_income", -174_417_000, "2025-02-01", "2025-07-31", published="2026-06-01")
    nine_months = fact("b:9m", "net_income", -221_879_000, "2025-02-01", "2025-10-31", published="2025-12-01")
    q3_direct = fact("b:q3", "net_income", -33_997_000, "2025-08-01", "2025-10-31", published="2025-12-01")
    h1_original = fact("a:h1", "net_income", -187_882_000, "2025-02-01", "2025-07-31", published="2025-09-01")

    result = analyze_financials(
        [h1_restated, nine_months, q3_direct], BusinessModel.GENERAL,
        vintages=[h1_original, h1_restated, nine_months, q3_direct],
    )

    assert not any(issue.code == "QUARTER_DERIVATION_CONFLICT" for issue in result.issues)
    reconciled = next(issue for issue in result.issues if issue.code == "QUARTER_DERIVATION_RECONCILED")
    assert "restated" in reconciled.message


@pytest.mark.unit
def test_restatement_that_does_not_explain_the_gap_still_conflicts():
    h1_restated = fact("c:h1", "net_income", -174_417_000, "2025-02-01", "2025-07-31", published="2026-06-01")
    nine_months = fact("b:9m", "net_income", -221_879_000, "2025-02-01", "2025-10-31", published="2025-12-01")
    q3_direct = fact("b:q3", "net_income", -26_539_000, "2025-08-01", "2025-10-31", published="2025-12-01")
    h1_original = fact("a:h1", "net_income", -187_882_000, "2025-02-01", "2025-07-31", published="2025-09-01")

    result = analyze_financials(
        [h1_restated, nine_months, q3_direct], BusinessModel.GENERAL,
        vintages=[h1_original, h1_restated, nine_months, q3_direct],
    )

    assert any(issue.code == "QUARTER_DERIVATION_CONFLICT" for issue in result.issues)


@pytest.mark.unit
def test_ttm_within_rounding_of_the_annual_figure_is_reconciled():
    # Four quarters reported in millions sum to 2M below the rounded annual total.
    quarters = [
        fact(f"q{i}", "revenue", value, start, end)
        for i, (value, start, end) in enumerate([
            (10_001_000_000, "2025-01-01", "2025-03-31"),
            (10_001_000_000, "2025-04-01", "2025-06-30"),
            (10_001_000_000, "2025-07-01", "2025-09-30"),
            (10_001_000_000, "2025-10-01", "2025-12-31"),
        ])
    ]
    annual = fact("fy", "revenue", 40_006_000_000, "2025-01-01", "2025-12-31")

    result = analyze_financials(quarters + [annual], BusinessModel.GENERAL)

    assert not any(issue.code == "TTM_ANNUAL_CONFLICT" for issue in result.issues)
    assert any(issue.code == "TTM_ANNUAL_RECONCILED" for issue in result.issues)
