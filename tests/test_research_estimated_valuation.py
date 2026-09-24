"""Estimated (unverified) valuation from packet facts: price x shares, TTM multiples, EV."""

import pytest

from tradingagents.research.estimated_valuation import estimate_valuation

AS_OF = "2026-09-23"


def fact(metric, value, period_end, *, unit="USD", period_start=None, tag="us-gaap:X", url="https://sec.example/f"):
    return {
        "metric": metric, "value": value, "unit": unit, "period_end": period_end,
        "period_start": period_start, "source_tag": tag, "source_url": url, "kind": "reported",
    }


def base_facts():
    return [
        fact("close", 100.0, "2026-09-18", unit="USD/share", tag="yahoo:close"),
        fact("close", 110.0, "2026-09-22", unit="USD/share", tag="yahoo:close"),
        fact("shares_outstanding", 1_000_000_000, "2026-07-17", unit="shares", tag="dei:EntityCommonStockSharesOutstanding"),
        fact("shares_outstanding_market", 1_010_000_000, "2026-09-23", unit="shares", tag="yahoo:sharesOutstanding"),
        fact("market_cap_reported", 111_000_000_000, "2026-09-23", tag="yahoo:marketCap"),
        fact("revenue_ttm", 50_000_000_000, "2026-06-27", period_start="2025-06-29"),
        fact("net_income_ttm", 5_500_000_000, "2026-06-27", period_start="2025-06-29"),
        fact("free_cash_flow_ttm", 4_400_000_000, "2026-06-27", period_start="2025-06-29"),
        fact("long_term_debt_current", 2_000_000_000, "2026-06-27"),
        fact("long_term_debt_noncurrent", 8_000_000_000, "2026-06-27"),
        fact("commercial_paper", 1_000_000_000, "2026-06-27"),
        fact("cash", 6_000_000_000, "2026-06-27"),
        fact("cash", 9_999_000_000, "2026-03-28"),
    ]


def test_market_cap_uses_the_latest_close_and_the_sec_cover_share_count():
    result = estimate_valuation(base_facts(), AS_OF)
    assert result["status"] == "estimated"
    assert result["price"]["value"] == 110.0 and result["price"]["date"] == "2026-09-22"
    assert result["shares"]["source"] == "sec_cover"
    assert result["market_cap"] == pytest.approx(110e9)
    assert result["vendor_market_cap"] == pytest.approx(111e9)


def test_multiples_and_enterprise_value():
    result = estimate_valuation(base_facts(), AS_OF)
    assert result["total_debt"] == pytest.approx(11e9)
    assert result["cash"] == pytest.approx(6e9)
    assert result["balance_date"] == "2026-06-27"
    assert result["enterprise_value"] == pytest.approx(115e9)
    multiples = result["multiples"]
    assert multiples["price_to_earnings"] == pytest.approx(20.0)
    assert multiples["price_to_sales"] == pytest.approx(2.2)
    assert multiples["ev_to_revenue"] == pytest.approx(2.3)
    assert multiples["price_to_free_cash_flow"] == pytest.approx(25.0)


def test_vendor_share_count_is_used_when_the_filing_has_no_single_count():
    facts = [f for f in base_facts() if f["metric"] != "shares_outstanding"]
    result = estimate_valuation(facts, AS_OF)
    assert result["shares"]["source"] == "vendor"
    assert result["market_cap"] == pytest.approx(110.0 * 1_010_000_000)


def test_disagreeing_sec_count_defers_to_the_vendor_and_says_so():
    facts = base_facts()
    facts.append(fact("shares_outstanding", 250_000_000, "2026-08-20", unit="shares",
                      tag="dei:EntityCommonStockSharesOutstanding"))
    result = estimate_valuation(facts, AS_OF)
    assert result["shares"]["source"] == "vendor"
    assert any("disagree" in note for note in result["notes"])


def test_stale_and_negative_denominators_are_not_used():
    facts = [f for f in base_facts() if f["metric"] not in ("revenue_ttm", "net_income_ttm")]
    facts += [
        fact("revenue_ttm", 40e9, "2023-09-30", period_start="2022-10-01"),
        fact("net_income_ttm", -1e9, "2026-06-27", period_start="2025-06-29"),
    ]
    result = estimate_valuation(facts, AS_OF)
    assert result["multiples"]["price_to_sales"] is None
    assert result["multiples"]["ev_to_revenue"] is None
    assert result["multiples"]["price_to_earnings"] is None
    assert any("revenue" in note and "stale" in note for note in result["notes"])
    assert any("earnings" in note and "negative" in note for note in result["notes"])


def test_enterprise_value_is_withheld_without_balance_sheet_facts():
    facts = [f for f in base_facts() if f["metric"] not in (
        "long_term_debt_current", "long_term_debt_noncurrent", "commercial_paper", "cash")]
    result = estimate_valuation(facts, AS_OF)
    assert result["market_cap"] is not None
    assert result["enterprise_value"] is None
    assert result["multiples"]["ev_to_revenue"] is None


def test_facts_after_the_analysis_date_are_ignored():
    facts = base_facts() + [fact("close", 999.0, "2026-09-24", unit="USD/share")]
    assert estimate_valuation(facts, AS_OF)["price"]["value"] == 110.0


def test_no_price_means_no_estimate():
    facts = [f for f in base_facts() if f["metric"] != "close"]
    assert estimate_valuation(facts, AS_OF) is None


def test_multi_class_share_gap_defers_to_the_vendor_market_cap():
    # Vendor share counts can cover one class while its market cap covers all of them.
    facts = [f for f in base_facts() if f["metric"] not in ("shares_outstanding", "market_cap_reported")]
    facts.append(fact("market_cap_reported", 222_000_000_000, "2026-09-23", tag="yahoo:marketCap"))
    result = estimate_valuation(facts, AS_OF)
    assert result["market_cap"] == pytest.approx(222e9)
    assert result["shares"]["source"] == "vendor_market_cap"
    assert any("share classes" in note for note in result["notes"])


def test_debt_absent_from_the_latest_balance_sheet_counts_as_zero_with_a_note():
    facts = [f for f in base_facts() if f["metric"] not in (
        "long_term_debt_current", "long_term_debt_noncurrent", "commercial_paper")]
    facts.append(fact("long_term_debt_reported", 3_000_000_000, "2023-07-31"))
    result = estimate_valuation(facts, AS_OF)
    assert result["balance_date"] == "2026-06-27"
    assert result["total_debt"] == 0.0
    assert result["enterprise_value"] == pytest.approx(110e9 - 6e9)
    assert any("no debt" in note.lower() for note in result["notes"])


def test_missing_ttm_figures_are_noted():
    facts = [f for f in base_facts() if f["metric"] != "free_cash_flow_ttm"]
    result = estimate_valuation(facts, AS_OF)
    assert result["multiples"]["price_to_free_cash_flow"] is None
    assert any("free cash flow" in note and "not available" in note for note in result["notes"])


def test_description_labels_the_estimate_as_unverified_and_lists_multiples():
    from tradingagents.research.estimated_valuation import describe_estimate

    text = describe_estimate(estimate_valuation(base_facts(), AS_OF))
    assert text.startswith("Estimated valuation (unverified")
    assert "market cap $110.0B" in text
    assert "P/E 20.0x" in text and "P/FCF 25.0x" in text


def test_packet_context_gives_agents_the_estimate():
    from tradingagents.research.models import ResearchPacket

    # Recomputed from facts when an older packet has no stored estimate.
    packet = {
        "ticker": "TEST", "as_of": AS_OF, "horizon": "long_term", "thesis": None, "status": "partial",
        "business_model": "general", "identity": None, "documents": [], "issues": [], "coverage": {},
        "provider_results": {}, "financial_analysis": {},
        "facts": [dict(f, fact_id=f"f{i}", published_at="2026-09-23T00:00:00Z", retrieved_at="2026-09-23T00:00:00Z",
                       definition=f["metric"], adjustment_basis="unknown", accession=None, formula=None,
                       input_fact_ids=[], period_start=f.get("period_start"))
                  for i, f in enumerate(base_facts())],
    }
    assert "Estimated valuation (unverified" in ResearchPacket.from_dict(packet).render_context()


def test_near_breakeven_earnings_flag_the_pe_as_not_useful():
    facts = [f for f in base_facts() if f["metric"] != "net_income_ttm"]
    facts.append(fact("net_income_ttm", 50_000_000, "2026-06-27", period_start="2025-06-29"))
    result = estimate_valuation(facts, AS_OF)
    assert result["multiples"]["price_to_earnings"] == pytest.approx(2200.0)
    assert any("near breakeven" in note for note in result["notes"])
