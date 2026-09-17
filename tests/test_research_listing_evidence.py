from __future__ import annotations

import hashlib

import pytest

from tradingagents.research.filings import extract_filing_evidence

CIK = "0001262039"
URL = "https://www.sec.gov/Archives/edgar/data/1262039/report.htm"


def filing(body: str, *, context_cik: str = CIK, extra_contexts: str = "") -> str:
    return f'''<html xmlns="http://www.w3.org/1999/xhtml"
 xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
 xmlns:xbrli="http://www.xbrl.org/2003/instance"
 xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
 xmlns:dei="http://xbrl.sec.gov/dei/2025"
 xmlns:us-gaap="http://fasb.org/us-gaap/2025"
 xmlns:acme="https://example.test/acme/2026">
<head><title>ACME filing</title></head><body><ix:resources>
<xbrli:context id="cover"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{context_cik}</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2026-06-30</xbrli:instant></xbrli:period></xbrli:context>
{extra_contexts}</ix:resources>{body}</body></html>'''


def extract(source: str):
    return extract_filing_evidence(
        source,
        ticker="ACME",
        cik=CIK,
        accession="0001262039-26-000021",
        source_url=URL,
        published_at="2026-07-30T21:30:32Z",
        retrieved_at="2026-09-16T22:09:36Z",
    )


def listing(context: str, suffix: str = "") -> str:
    return (
        f'<ix:nonNumeric id="title{suffix}" name="dei:Security12bTitle" contextRef="{context}">Class {suffix or "A"} Common Stock</ix:nonNumeric>'
        f'<ix:nonNumeric id="ticker{suffix}" name="dei:TradingSymbol" contextRef="{context}">ACM{suffix}</ix:nonNumeric>'
        f'<ix:nonNumeric id="exchange{suffix}" name="dei:SecurityExchangeName" contextRef="{context}">Nasdaq</ix:nonNumeric>'
    )


@pytest.mark.unit
def test_listing_text_is_accepted_and_paired_only_by_safe_context():
    source = filing(listing("cover"))
    result = extract(source)
    candidates = result["metadata"]["listing_candidates"]

    assert [item["field"] for item in candidates] == [
        "security_title",
        "trading_symbol",
        "exchange_name",
    ]
    assert {item["status"] for item in candidates} == {"accepted"}
    assert {item["context_id"] for item in candidates} == {"cover"}
    assert len({item["context_signature"] for item in candidates}) == 1
    assert candidates[0]["value"] == "Class A Common Stock"
    assert candidates[1]["source_url"] == f"{URL}#ticker"
    assert candidates[0]["source_sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert result["facts"] == []


@pytest.mark.unit
def test_multiple_security_classes_remain_separate_context_groups():
    class_contexts = "".join(
        f'<xbrli:context id="class-{letter}"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{CIK}</xbrli:identifier><xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">acme:Class{letter.upper()}Member</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2026-06-30</xbrli:instant></xbrli:period></xbrli:context>'
        for letter in ("a", "b")
    )
    result = extract(filing(listing("class-a", "A") + listing("class-b", "B"), extra_contexts=class_contexts))
    accepted = result["metadata"]["listing_candidates"]

    assert len(accepted) == 6
    assert {item["context_id"] for item in accepted} == {"class-a", "class-b"}
    assert len({item["context_signature"] for item in accepted}) == 2
    assert {tuple(item["dimensions"].values()) for item in accepted} == {
        ("acme:ClassAMember",),
        ("acme:ClassBMember",),
    }


@pytest.mark.unit
def test_listing_text_rejects_wrong_cik_missing_context_nil_transform_and_continuation():
    source = filing(
        '<ix:nonNumeric id="wrong" name="dei:TradingSymbol" contextRef="cover">ACME</ix:nonNumeric>',
        context_cik="1",
    ).replace(
        "</body>",
        '<ix:nonNumeric id="missing" name="dei:TradingSymbol" contextRef="missing">MISS</ix:nonNumeric>'
        '<ix:nonNumeric id="nil" name="dei:TradingSymbol" contextRef="cover" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:nil="true" />'
        '<ix:nonNumeric id="format" name="dei:TradingSymbol" contextRef="cover" format="dei:any">X</ix:nonNumeric>'
        '<ix:nonNumeric id="continued" name="dei:TradingSymbol" contextRef="cover" continuedAt="next">Y</ix:nonNumeric></body>',
    )
    result = extract(source)

    assert result["facts"] == []
    assert {item["reason"] for item in result["metadata"]["listing_candidates"]} == {
        "cik_mismatch",
        "missing_context",
        "nil",
        "transform_unsupported",
        "continuation_unsupported",
    }

    cycle = filing(
        '<ix:nonNumeric id="first" name="dei:TradingSymbol" contextRef="cover" continuedAt="second">A</ix:nonNumeric>'
        '<ix:continuation id="second" continuedAt="first">CME</ix:continuation>'
    )
    assert extract(cycle)["metadata"]["listing_candidates"][0]["reason"] == (
        "continuation_unsupported"
    )


@pytest.mark.unit
def test_listing_text_rejects_spoofed_dei_duplicate_ids_and_overlong_text():
    spoofed = filing(listing("cover")).replace(
        'xmlns:dei="http://xbrl.sec.gov/dei/2025"',
        'xmlns:dei="https://example.test/dei/2025"',
    )
    assert extract(spoofed)["metadata"]["listing_candidates"] == []

    duplicate = filing(
        '<ix:nonNumeric id="same" name="dei:TradingSymbol" contextRef="cover">ONE</ix:nonNumeric>'
        '<ix:nonNumeric id="same" name="dei:SecurityExchangeName" contextRef="cover">TWO</ix:nonNumeric>'
    )
    assert {item["reason"] for item in extract(duplicate)["metadata"]["listing_candidates"]} == {
        "duplicate_node_id"
    }

    nonlisting_collision = filing(
        '<div id="collision">ordinary HTML</div>'
        '<ix:nonNumeric id="collision" name="dei:TradingSymbol" contextRef="cover">ACME</ix:nonNumeric>'
    )
    collided = extract(nonlisting_collision)["metadata"]["listing_candidates"][0]
    assert collided["reason"] == "duplicate_node_id"
    assert collided["source_node_id"] is None
    assert collided["source_locator"].startswith("xpath:")

    too_long = filing(
        f'<ix:nonNumeric name="dei:Security12bTitle" contextRef="cover">{"x" * 501}</ix:nonNumeric>'
    )
    candidate = extract(too_long)["metadata"]["listing_candidates"][0]
    assert candidate["reason"] == "text_too_long"
    assert "value" not in candidate

    spoofed_context = filing(listing("cover")).replace(
        'xmlns:xbrli="http://www.xbrl.org/2003/instance"',
        'xmlns:xbrli="https://example.test/xbrli"',
    )
    spoofed_candidates = extract(spoofed_context)["metadata"]["listing_candidates"]
    assert {item["reason"] for item in spoofed_candidates} == {"missing_context"}


@pytest.mark.unit
def test_listing_concepts_accept_an_alias_bound_to_the_trusted_dei_namespace():
    source = filing(
        '<ix:nonNumeric id="aliased" name="cover:TradingSymbol" contextRef="cover">ACME</ix:nonNumeric>'
    ).replace(
        'xmlns:dei="http://xbrl.sec.gov/dei/2025"',
        'xmlns:dei="http://xbrl.sec.gov/dei/2025" xmlns:cover="http://xbrl.sec.gov/dei/2025"',
    )
    candidate = extract(source)["metadata"]["listing_candidates"][0]
    assert candidate["status"] == "accepted"
    assert candidate["concept"] == "cover:TradingSymbol"


@pytest.mark.unit
def test_exchange_name_preserves_sec_transformed_display_text_without_canonicalizing():
    source = filing(
        '<ix:nonNumeric id="exchange" name="dei:SecurityExchangeName" contextRef="cover" '
        'format="ixt-sec:exchnameen">The Nasdaq Stock Market LLC</ix:nonNumeric>'
    ).replace(
        'xmlns:dei="http://xbrl.sec.gov/dei/2025"',
        'xmlns:dei="http://xbrl.sec.gov/dei/2025" '
        'xmlns:ixt-sec="http://www.sec.gov/inlineXBRL/transformation/2020-02-12"',
    )
    candidate = extract(source)["metadata"]["listing_candidates"][0]

    assert candidate["status"] == "accepted"
    assert candidate["value"] == "The Nasdaq Stock Market LLC"
    assert candidate["value_kind"] == "display_text"
    assert candidate["format"] == "ixt-sec:exchnameen"
    assert candidate["transformed_value"] is None


@pytest.mark.unit
def test_exchange_display_transform_rejects_spoofed_namespace():
    source = filing(
        '<ix:nonNumeric name="dei:SecurityExchangeName" contextRef="cover" '
        'format="ixt-sec:exchnameen">The Nasdaq Stock Market LLC</ix:nonNumeric>'
    ).replace(
        'xmlns:dei="http://xbrl.sec.gov/dei/2025"',
        'xmlns:dei="http://xbrl.sec.gov/dei/2025" '
        'xmlns:ixt-sec="https://example.test/inlineXBRL/transformation/2020"',
    )
    candidate = extract(source)["metadata"]["listing_candidates"][0]
    assert candidate["status"] == "rejected"
    assert candidate["reason"] == "transform_unsupported"


@pytest.mark.unit
def test_numeric_ratios_are_not_promoted_to_listing_candidates():
    result = extract(
        filing(
            '<ix:nonFraction name="dei:TradingSymbol" contextRef="cover" unitRef="pure">2</ix:nonFraction>'
            '<ix:nonNumeric name="us-gaap:StockSplitConversionRatio" contextRef="cover">2:1</ix:nonNumeric>'
            '<ix:nonNumeric name="acme:ADRRatio" contextRef="cover">1:2</ix:nonNumeric>'
        )
    )
    assert result["metadata"]["listing_candidates"] == []
