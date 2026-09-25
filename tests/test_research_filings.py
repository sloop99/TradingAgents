from __future__ import annotations

import hashlib

import pytest

from tradingagents.research.filings import extract_filing_evidence
from tradingagents.research.models import EvidenceFact

CIK = "0001262039"
ACCESSION = "0001262039-26-000021"
URL = "https://www.sec.gov/Archives/edgar/data/1262039/report.htm"


def filing(*facts: str, context_cik: str = CIK) -> str:
    joined = "".join(facts)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
 xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
 xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
 xmlns:xbrli="http://www.xbrl.org/2003/instance"
 xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
 xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
 xmlns:dei="http://xbrl.sec.gov/dei/2025"
 xmlns:us-gaap="http://fasb.org/us-gaap/2025"
 xmlns:acme="https://example.test/acme/2026">
<head><title>ACME 10-Q</title></head><body>
<ix:resources>
  <xbrli:unit id="shares"><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unit>
  <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
  <xbrli:unit id="pure"><xbrli:measure>xbrli:pure</xbrli:measure></xbrli:unit>
  <xbrli:unit id="bad"><xbrli:measure>acme:widgets</xbrli:measure></xbrli:unit>
  <xbrli:context id="instant"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{context_cik}</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2026-06-30</xbrli:instant></xbrli:period></xbrli:context>
  <xbrli:context id="duration"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{context_cik}</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2026-01-01</xbrli:startDate><xbrli:endDate>2026-06-30</xbrli:endDate></xbrli:period></xbrli:context>
  <xbrli:context id="class-a"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{context_cik}</xbrli:identifier><xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">acme:ClassAMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2026-06-30</xbrli:instant></xbrli:period></xbrli:context>
  <xbrli:context id="class-b"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{context_cik}</xbrli:identifier><xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">acme:ClassBMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2026-06-30</xbrli:instant></xbrli:period></xbrli:context>
  <xbrli:context id="typed"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{context_cik}</xbrli:identifier><xbrli:segment><xbrldi:typedMember dimension="acme:UnsafeAxis"><acme:value>one</acme:value></xbrldi:typedMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2026-06-30</xbrli:instant></xbrli:period></xbrli:context>
</ix:resources>{joined}</body></html>"""


def extract(source: str, *, cik: str = CIK):
    return extract_filing_evidence(
        source,
        ticker="FTNT",
        cik=cik,
        accession=ACCESSION,
        source_url=URL,
        published_at="2026-07-30T21:30:32Z",
        retrieved_at="2026-09-16T22:09:36Z",
    )


@pytest.mark.unit
def test_inline_filing_preserves_classes_context_and_deterministic_provenance():
    source = filing(
        '<ix:nonFraction id="cover" name="dei:EntityCommonStockSharesOutstanding" contextRef="instant" unitRef="shares" format="ixt:num-dot-decimal" scale="0">733,713,653</ix:nonFraction>',
        '<ix:nonFraction id="classA" name="us-gaap:CommonStockSharesOutstanding" contextRef="class-a" unitRef="shares">100</ix:nonFraction>',
        '<ix:nonFraction id="classB" name="us-gaap:CommonStockSharesOutstanding" contextRef="class-b" unitRef="shares">100</ix:nonFraction>',
        '<ix:nonFraction id="customB" name="acme:ClassBCommonStockSharesOutstanding" contextRef="class-b" unitRef="shares">25</ix:nonFraction>',
    )

    result = extract(source)
    facts = result["facts"]

    assert [fact["value"] for fact in facts] == [733_713_653, 100, 100, 25]
    assert {fact["metric"] for fact in facts} == {
        "filing_shares_outstanding",
        "filing_common_stock_shares_outstanding",
        "filing_custom_class_b_common_stock_shares_outstanding",
    }
    assert all(fact["metric"].startswith("filing_") for fact in facts)
    assert len({fact["fact_id"] for fact in facts}) == 4
    class_facts = [fact for fact in facts if fact["value"] == 100]
    assert class_facts[0]["definition"] != class_facts[1]["definition"]
    assert "ClassAMember" in class_facts[0]["definition"]
    assert "ClassBMember" in class_facts[1]["definition"]
    assert facts[0]["source_url"] == f"{URL}#cover"
    assert result["metadata"]["source_sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert extract(source)["facts"] == facts
    assert all(EvidenceFact.from_dict(fact) for fact in facts)
    accepted = [item for item in result["metadata"]["candidates"] if item["status"] == "accepted"]
    assert {item["fact_id"] for item in accepted} == {fact["fact_id"] for fact in facts}
    assert "733,713,653" in accepted[0]["snippet"]
    assert all(item["source_sha256"] == result["metadata"]["source_sha256"] for item in accepted)
    assert all(item["context_signature"] == result["metadata"]["contexts"][item["context_id"]]["signature"] for item in accepted)


def test_share_inventory_is_connected_to_packet_without_promoting_class_coverage():
    from tradingagents.research import ResearchPacket, build_packet

    parsed = extract(filing(
        '<ix:nonFraction id="outstanding" name="us-gaap:CommonStockSharesOutstanding" contextRef="class-a" unitRef="shares">100</ix:nonFraction>',
        '<ix:nonFraction id="authorized" name="us-gaap:CommonStockSharesAuthorized" contextRef="class-a" unitRef="shares">1000</ix:nonFraction>',
        '<ix:nonNumeric id="title" name="dei:Security12bTitle" contextRef="class-a">Class A common stock</ix:nonNumeric>',
        '<ix:nonNumeric id="symbol" name="dei:TradingSymbol" contextRef="class-a">TEST</ix:nonNumeric>',
        '<ix:nonNumeric id="exchange" name="dei:SecurityExchangeName" contextRef="class-a">Nasdaq</ix:nonNumeric>',
    ))

    class Provider:
        def fetch(self, ticker, as_of):
            return {**parsed, "identity": {"ticker": ticker, "cik": CIK},
                    "metadata": {"filings": [parsed["metadata"]]}}

    packet = build_packet("TEST", "2026-09-16", [Provider()])
    inventory = packet.financial_analysis["share_inventory"]
    assert inventory["observations"]["outstanding"][0]["value"] == 100
    assert inventory["observations"]["authorized"][0]["value"] == 1000
    assert inventory["listing_links"]
    assert "Share-class inventory" in packet.to_markdown()
    assert not any(f.metric in {"share_class_coverage_ratio", "split_history_complete"} for f in packet.facts)
    assert ResearchPacket.from_dict(packet.to_dict()).to_dict() == packet.to_dict()


@pytest.mark.unit
def test_inline_filing_applies_only_supported_numeric_transforms_scale_and_sign():
    result = extract(
        filing(
            '<ix:nonFraction id="cash" name="us-gaap:CashAndCashEquivalentsAtCarryingValue" contextRef="instant" unitRef="usd" format="ixt:num-comma-decimal" scale="3">1.234,5</ix:nonFraction>',
            '<ix:nonFraction id="debt" name="us-gaap:LongTermDebtCurrent" contextRef="instant" unitRef="usd" format="ixt:num-dot-decimal" scale="3" sign="-">1,234.5</ix:nonFraction>',
            '<ix:nonFraction id="zero" name="us-gaap:DebtCurrent" contextRef="instant" unitRef="usd" format="ixt:fixed-zero" scale="6">no debt</ix:nonFraction>',
            '<ix:nonFraction id="bad-transform" name="us-gaap:LongTermDebtNoncurrent" contextRef="instant" unitRef="usd" format="ixt:numwordsen">one</ix:nonFraction>',
        )
    )

    assert {fact["value"] for fact in result["facts"]} == {1_234_500, -1_234_500, 0}
    rejected = [item for item in result["metadata"]["candidates"] if item["status"] == "rejected"]
    assert rejected[0]["reason"] == "unsupported_transform"
    assert any(issue["code"] == "filing_numeric_unsafe" for issue in result["issues"])


@pytest.mark.unit
def test_inline_filing_fails_closed_for_nil_missing_or_unsafe_context_and_unit():
    result = extract(
        filing(
            '<ix:nonFraction id="nil" name="us-gaap:PreferredStockValue" contextRef="instant" unitRef="usd" xsi:nil="true"/>',
            '<ix:nonFraction id="missing" name="us-gaap:LongTermDebtCurrent" contextRef="absent" unitRef="usd">1</ix:nonFraction>',
            '<ix:nonFraction id="typed-fact" name="us-gaap:LongTermDebtCurrent" contextRef="typed" unitRef="usd">2</ix:nonFraction>',
            '<ix:nonFraction id="bad-unit" name="us-gaap:LongTermDebtCurrent" contextRef="instant" unitRef="bad">3</ix:nonFraction>',
        )
    )

    assert result["facts"] == []
    assert {item["reason"] for item in result["metadata"]["candidates"]} == {
        "nil",
        "missing_context",
        "typed_member",
        "invalid_unit",
    }
    assert {issue["code"] for issue in result["issues"]} >= {
        "filing_nil_fact",
        "filing_context_missing",
        "filing_typed_dimension",
        "filing_unit_invalid",
    }


@pytest.mark.unit
def test_inline_filing_rejects_wrong_issuer_context():
    result = extract(
        filing(
            '<ix:nonFraction id="cash" name="us-gaap:CashAndCashEquivalentsAtCarryingValue" contextRef="instant" unitRef="usd">10</ix:nonFraction>',
            context_cik="0000000001",
        )
    )

    assert result["facts"] == []
    assert result["coverage"]["identity"] == "partial"
    assert any(issue["code"] == "filing_entity_mismatch" for issue in result["issues"])


@pytest.mark.unit
def test_inline_filing_requires_real_ix_and_standard_taxonomy_bindings_and_real_anchors():
    source = filing(
        '<nonFraction id="fake-element" name="us-gaap:LongTermDebtCurrent" contextRef="instant" unitRef="usd">90</nonFraction>',
        '<ix:nonFraction name="us-gaap:LongTermDebtCurrent" contextRef="instant" unitRef="usd">91</ix:nonFraction>',
    ).replace(
        'xmlns:us-gaap="http://fasb.org/us-gaap/2025"',
        'xmlns:us-gaap="https://example.test/rebound"',
    )
    rebound = extract(source)
    assert rebound["facts"] == []

    no_id = extract(
        filing(
            '<ix:nonFraction name="us-gaap:LongTermDebtCurrent" contextRef="instant" unitRef="usd">91</ix:nonFraction>'
        )
    )
    candidate = no_id["metadata"]["candidates"][0]
    assert candidate["source_node_id"] is None
    assert candidate["source_locator"].startswith("xpath:")
    assert no_id["facts"][0]["source_url"] == URL


@pytest.mark.unit
def test_inline_filing_preserves_duration_dates_for_common_income():
    result = extract(
        filing(
            '<ix:nonFraction id="income" name="us-gaap:NetIncomeLossAvailableToCommonStockholdersBasic" contextRef="duration" unitRef="usd" sign="-">499.7</ix:nonFraction>'
        )
    )

    fact = result["facts"][0]
    assert fact["period_start"] == "2026-01-01"
    assert fact["period_end"] == "2026-06-30"
    assert fact["value"] == -499.7
    assert fact["metric"] == "filing_net_income_available_to_common_basic"


@pytest.mark.unit
def test_inline_filing_rejects_duplicate_ids_bad_cik_scheme_and_invalid_duration_start():
    fact = '<ix:nonFraction id="debt" name="us-gaap:LongTermDebtCurrent" contextRef="instant" unitRef="usd">10</ix:nonFraction>'
    duplicate_context = filing(fact).replace(
        "</ix:resources>",
        '<xbrli:context id="instant"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0001262039</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2026-06-30</xbrli:instant></xbrli:period></xbrli:context></ix:resources>',
    )
    assert extract(duplicate_context)["facts"] == []

    duplicate_unit = filing(fact).replace(
        "</ix:resources>",
        '<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit></ix:resources>',
    )
    assert extract(duplicate_unit)["facts"] == []

    bad_scheme = filing(fact).replace("http://www.sec.gov/CIK", "https://example.test/CIK")
    assert extract(bad_scheme)["facts"] == []

    duration_fact = '<ix:nonFraction id="income" name="us-gaap:NetIncomeLossAvailableToCommonStockholdersBasic" contextRef="duration" unitRef="usd">10</ix:nonFraction>'
    bad_start = filing(duration_fact).replace("2026-01-01", "not-a-date")
    assert extract(bad_start)["facts"] == []
