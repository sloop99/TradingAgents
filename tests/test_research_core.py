from __future__ import annotations

import json

import pytest

from tradingagents.research import (
    BusinessModel,
    CoverageStatus,
    EvidenceFact,
    ResearchPacket,
    build_packet,
    parse_as_of,
)


class StaticProvider:
    def __init__(self, name: str, payload=None, failure: Exception | None = None):
        self.name = name
        self.payload = payload
        self.failure = failure
        self.calls = []

    def fetch(self, ticker: str, as_of: str) -> dict:
        self.calls.append((ticker, as_of))
        if self.failure:
            raise self.failure
        return self.payload


def identity(ticker="ACME", sic="3560", **overrides):
    result = {
        "ticker": ticker,
        "name": "Acme Corporation",
        "cik": "0000000001",
        "exchange": "NYSE",
        "currency": "USD",
        "fiscal_year_end": "1231",
        "sic": sic,
        "business_model": None,
    }
    result.update(overrides)
    return result


def fact(
    fact_id: str,
    metric: str,
    value,
    *,
    unit="USD",
    start="2025-01-01",
    end="2025-12-31",
    published="2026-02-15",
    definition=None,
    adjustment_basis="unknown",
):
    return {
        "fact_id": fact_id,
        "metric": metric,
        "value": value,
        "unit": unit,
        "period_start": start,
        "period_end": end,
        "published_at": published,
        "retrieved_at": "2026-09-16T12:00:00Z",
        "source_url": f"https://example.test/{fact_id}",
        "accession": "0000000001-26-000001",
        "source_tag": "fixture",
        "definition": definition or metric.replace("_", " "),
        "adjustment_basis": adjustment_basis,
        "kind": "reported",
        "input_fact_ids": [],
    }


def document(document_id="10k", published="2026-02-15"):
    return {
        "document_id": document_id,
        "form": "10-K",
        "period_end": "2025-12-31",
        "published_at": published,
        "source_url": f"https://example.test/{document_id}",
        "accession": "0000000001-26-000001",
        "title": "Annual report",
    }


def operating_company_facts(*, net_income=80):
    return [
        fact("revenue", "revenue", 1_000),
        fact("income", "net_income", net_income),
        fact("assets", "total_assets", 2_000, start=None),
        fact("equity", "equity", 900, start=None),
        fact("cfo", "operating_cash_flow", 140),
        fact(
            "capex",
            "capital_expenditures",
            40,
            adjustment_basis="cash_outflow_positive",
        ),
        fact("shares", "shares_outstanding", 100, unit="shares", start=None),
    ]


@pytest.mark.unit
def test_date_cutoff_is_end_of_day_utc_and_aware_timestamp_is_exact():
    assert parse_as_of("2026-02-15").isoformat() == "2026-02-15T23:59:59.999999+00:00"
    assert parse_as_of("2026-02-15T08:30:00-07:00").isoformat() == "2026-02-15T15:30:00+00:00"
    with pytest.raises(ValueError, match="timezone"):
        parse_as_of("2026-02-15T08:30:00")


@pytest.mark.unit
def test_complete_industrial_packet_derives_fcf_with_lineage_and_round_trips():
    provider = StaticProvider(
        "filings",
        {
            "identity": identity(),
            "facts": operating_company_facts(),
            "documents": [document()],
            "issues": [],
            "coverage": {"filings": "sufficient"},
        },
    )
    packet = build_packet("acme", "2026-03-01", [provider], thesis="Margins can improve")

    assert provider.calls == [("ACME", "2026-03-01")]
    assert packet.status is CoverageStatus.SUFFICIENT
    assert packet.business_model is BusinessModel.INDUSTRIAL
    fcf = next(item for item in packet.facts if item.metric == "free_cash_flow")
    assert fcf.value == 100
    assert fcf.formula == "operating_cash_flow - capital_expenditures"
    assert fcf.input_fact_ids == ("cfo", "capex")
    assert ResearchPacket.from_dict(json.loads(json.dumps(packet.to_dict()))).to_dict() == packet.to_dict()
    assert packet.to_dict()["status"] == "sufficient"
    assert "Investment rating: not produced" in packet.to_markdown()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("sic", "expected"),
    [
        ("6022", BusinessModel.BANK),
        ("6798", BusinessModel.REIT),
        ("5411", BusinessModel.RETAIL),
        ("7372", BusinessModel.SOFTWARE),
        ("3560", BusinessModel.INDUSTRIAL),
        ("1311", BusinessModel.GENERAL),
    ],
)
def test_business_classification_uses_sic_not_ticker(sic, expected):
    packet = build_packet(
        "SAME",
        "2026-03-01",
        [StaticProvider("identity", {"identity": identity("SAME", sic), "facts": [], "documents": [], "issues": [], "coverage": {}})],
    )
    assert packet.business_model is expected


@pytest.mark.unit
def test_bank_coverage_does_not_require_industrial_cash_flow_metrics():
    bank_facts = [
        fact("nii", "net_interest_income", 50),
        fact("ni", "net_income", 20),
        fact("assets", "total_assets", 2_000, start=None),
        fact("deposits", "deposits", 1_600, start=None),
        fact("equity", "equity", 180, start=None),
    ]
    packet = build_packet(
        "BANK",
        "2026-03-01",
        [StaticProvider("sec", {"identity": identity("BANK", "6022"), "facts": bank_facts, "documents": [document()], "issues": [], "coverage": {}})],
    )
    assert packet.status is CoverageStatus.SUFFICIENT
    assert packet.coverage["cash_flow"] == "unsupported"
    assert packet.coverage["valuation"] == "unsupported"


@pytest.mark.unit
def test_reit_and_loss_making_company_remain_evidence_not_ratings():
    reit = build_packet(
        "REIT",
        "2026-03-01",
        [StaticProvider("reit", {"identity": identity("REIT", "6798"), "facts": operating_company_facts(net_income=-25), "documents": [document()], "issues": [], "coverage": {}})],
    )
    loss_maker = build_packet(
        "LOSS",
        "2026-03-01",
        [StaticProvider("loss", {"identity": identity("LOSS", "7372"), "facts": operating_company_facts(net_income=-90), "documents": [document()], "issues": [], "coverage": {}})],
    )
    assert reit.business_model is BusinessModel.REIT
    assert loss_maker.business_model is BusinessModel.SOFTWARE
    assert reit.status is CoverageStatus.SUFFICIENT
    assert loss_maker.status is CoverageStatus.SUFFICIENT
    assert "Evidence status: sufficient" in loss_maker.render_context()


@pytest.mark.unit
def test_cutoff_excludes_future_amendment_and_publication_unknown_fact():
    original = fact("revenue-original", "revenue", 100, published="2025-02-01")
    amendment = fact("revenue-amended", "revenue", 110, published="2025-04-01")
    unknown = fact("unknown-income", "net_income", 4, published=None)
    packet = build_packet(
        "ACME",
        "2025-03-01",
        [StaticProvider("sec", {"identity": identity(), "facts": [original, amendment, unknown], "documents": [], "issues": [], "coverage": {}})],
    )
    assert [item.fact_id for item in packet.facts] == ["revenue-original"]
    codes = {issue.code for issue in packet.issues}
    assert {"FACT_AFTER_CUTOFF", "PUBLICATION_TIME_UNKNOWN"}.issubset(codes)


@pytest.mark.unit
def test_exact_timestamp_cutoff_excludes_later_same_day_fact():
    early = fact("early", "revenue", 100, published="2026-02-15T15:29:59Z")
    late = fact("late", "net_income", 10, published="2026-02-15T15:30:01Z")
    packet = build_packet("ACME", "2026-02-15T08:30:00-07:00", [StaticProvider("p", {"identity": identity(), "facts": [early, late], "documents": [], "issues": [], "coverage": {}})])
    assert [item.fact_id for item in packet.facts] == ["early"]


@pytest.mark.unit
def test_competing_same_period_facts_are_material_conflict_but_ytd_and_quarter_are_not():
    ytd = fact("ytd", "revenue", 300, start="2025-01-01", end="2025-09-30")
    quarter = fact("quarter", "revenue", 110, start="2025-07-01", end="2025-09-30")
    disagreement = fact("other-ytd", "revenue", 305, start="2025-01-01", end="2025-09-30")
    packet = build_packet("ACME", "2026-03-01", [StaticProvider("p", {"identity": identity(), "facts": [ytd, quarter, disagreement], "documents": [], "issues": [], "coverage": {}})])
    assert packet.status is CoverageStatus.MATERIAL_CONFLICT
    conflicts = [issue for issue in packet.issues if issue.code == "FACT_CONFLICT"]
    assert len(conflicts) == 1
    assert "2025-01-01" in conflicts[0].message


@pytest.mark.unit
def test_fcf_is_withheld_without_explicit_sign_basis_or_matching_publication():
    cfo = fact("cfo", "operating_cash_flow", 100)
    unsafe_capex = fact("capex", "capital_expenditures", -20)
    different_publication = fact(
        "capex-later",
        "capital_expenditures",
        20,
        published="2026-02-16",
        adjustment_basis="cash_outflow_positive",
    )
    packet = build_packet("ACME", "2026-03-01", [StaticProvider("p", {"identity": identity(), "facts": [cfo, unsafe_capex, different_publication], "documents": [], "issues": [], "coverage": {}})])
    assert all(item.metric != "free_cash_flow" for item in packet.facts)
    assert any(issue.code == "FCF_DERIVATION_WITHHELD" for issue in packet.issues)


@pytest.mark.unit
def test_fcf_is_withheld_when_competing_inputs_exist():
    cfo = fact("cfo", "operating_cash_flow", 100)
    contested_cfo = fact("cfo-other", "operating_cash_flow", 101)
    capex = fact(
        "capex",
        "capital_expenditures",
        20,
        adjustment_basis="cash_outflow_positive",
    )
    packet = build_packet("ACME", "2026-03-01", [StaticProvider("p", {"identity": identity(), "facts": [cfo, contested_cfo, capex], "documents": [], "issues": [], "coverage": {}})])
    assert packet.status is CoverageStatus.MATERIAL_CONFLICT
    assert all(item.metric != "free_cash_flow" for item in packet.facts)
    assert any(issue.code == "FCF_DERIVATION_WITHHELD" for issue in packet.issues)


@pytest.mark.unit
def test_provider_failure_degrades_transparently_while_other_provider_succeeds():
    packet = build_packet(
        "ACME",
        "2026-03-01",
        [
            StaticProvider("broken", failure=RuntimeError("offline")),
            StaticProvider("working", {"identity": identity(), "facts": operating_company_facts(), "documents": [document()], "issues": [], "coverage": {}}),
        ],
    )
    assert packet.status is CoverageStatus.SUFFICIENT
    assert packet.provider_results == {"broken": "failed", "working": "ok"}
    assert any(issue.code == "PROVIDER_FAILURE" for issue in packet.issues)


@pytest.mark.unit
def test_cross_provider_cik_conflict_is_visible_and_material():
    first = StaticProvider("one", {"identity": identity(cik="111"), "facts": [], "documents": [], "issues": [], "coverage": {}})
    second = StaticProvider("two", {"identity": identity(cik="222"), "facts": [], "documents": [], "issues": [], "coverage": {}})
    packet = build_packet("ACME", "2026-03-01", [first, second])
    assert packet.identity is not None and packet.identity.cik is None
    assert packet.status is CoverageStatus.MATERIAL_CONFLICT
    assert any(issue.code == "IDENTITY_CONFLICT" for issue in packet.issues)


@pytest.mark.unit
def test_wrong_ticker_payload_is_discarded_and_unsupported():
    packet = build_packet(
        "RIGHT",
        "2026-03-01",
        [StaticProvider("wrong", {"identity": identity("WRONG"), "facts": operating_company_facts(), "documents": [document()], "issues": [], "coverage": {}})],
    )
    assert packet.identity is None
    assert packet.facts == []
    assert packet.status is CoverageStatus.UNSUPPORTED
    assert packet.provider_results["wrong"] == "identity_mismatch"


@pytest.mark.unit
def test_bare_ticker_identity_without_evidence_is_unsupported():
    packet = build_packet(
        "EMPTY",
        "2026-03-01",
        [StaticProvider("empty", {"identity": {"ticker": "EMPTY"}, "facts": [], "documents": [], "issues": [], "coverage": {}})],
    )
    assert packet.status is CoverageStatus.UNSUPPORTED
    assert packet.coverage["identity"] == "unsupported"


@pytest.mark.unit
def test_reused_fact_id_with_different_payload_is_excluded_and_material():
    first = fact("same", "revenue", 100)
    second = fact("same", "revenue", 101)
    packet = build_packet("ACME", "2026-03-01", [StaticProvider("p", {"identity": identity(), "facts": [first, second], "documents": [], "issues": [], "coverage": {}})])
    assert packet.facts == []
    assert packet.status is CoverageStatus.MATERIAL_CONFLICT
    assert any(issue.code == "FACT_ID_COLLISION" for issue in packet.issues)


@pytest.mark.unit
def test_calculated_fact_requires_formula_and_input_lineage():
    raw = fact("calculated", "free_cash_flow", 10)
    raw["kind"] = "calculated"
    with pytest.raises(ValueError, match="formula and input_fact_ids"):
        EvidenceFact.from_dict(raw)


@pytest.mark.unit
@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf"), True, None, "10"])
def test_fact_value_must_be_a_finite_number(bad_value):
    raw = fact("bad", "revenue", bad_value)
    with pytest.raises(ValueError, match="finite number"):
        EvidenceFact.from_dict(raw)


@pytest.mark.unit
def test_packet_from_dict_cannot_bypass_cutoff_or_calculated_lineage():
    packet = build_packet(
        "ACME",
        "2026-03-01",
        [StaticProvider("p", {"identity": identity(), "facts": operating_company_facts(), "documents": [document()], "issues": [], "coverage": {}})],
    ).to_dict()
    packet["facts"][0]["published_at"] = "2026-03-02"
    with pytest.raises(ValueError, match="after the packet cutoff"):
        ResearchPacket.from_dict(packet)

    packet = build_packet(
        "ACME",
        "2026-03-01",
        [StaticProvider("p", {"identity": identity(), "facts": operating_company_facts(), "documents": [document()], "issues": [], "coverage": {}})],
    ).to_dict()
    calculated = next(item for item in packet["facts"] if item["kind"] == "calculated")
    calculated["input_fact_ids"].append("missing")
    with pytest.raises(ValueError, match="references missing inputs"):
        ResearchPacket.from_dict(packet)


@pytest.mark.unit
def test_render_context_is_bounded_and_cites_evidence_ids_and_material_limits():
    facts = [fact(f"rev-{year}", "revenue", year, start=f"{year}-01-01", end=f"{year}-12-31", published=f"{year + 1}-02-01") for year in range(1980, 2026)]
    packet = build_packet("ACME", "2026-03-01", [StaticProvider("p", {"identity": identity(), "facts": facts, "documents": [], "issues": [], "coverage": {}})])
    rendered = packet.render_context(max_facts=5)
    assert len(rendered) <= 8000
    assert rendered.count("[F:") == 5
    assert "additional facts retained" in rendered
    assert "does not produce a valuation target or investment rating" in rendered
