"""Offline contract checks for sourced firm-level target reconstruction."""

from copy import deepcopy

import pytest

from tradingagents.research.analyst_consensus import analyze_targets, render_consensus_markdown

AS_OF = "2026-09-22T23:59:59Z"


def record(firm_id="firm_a", target=100, **changes):
    item = {"firm_id": firm_id, "firm": firm_id.replace("_", " ").title(),
            "security_id": "NASDAQ:FTNT", "currency": "USD", "share_basis": "common_2026_09",
            "target": target, "published_at": "2026-09-01T12:00:00Z",
            "retrieved_at": "2026-09-02T12:00:00Z", "source_url": "https://example.test/report",
            "horizon_months": 12, "rating": "Buy", "action": "raised", "thesis": "Demand remains strong."}
    item.update(changes)
    return item


def payload(*records, **changes):
    item = {"ticker": "FTNT", "security_id": "NASDAQ:FTNT", "records": list(records)}
    item.update(changes)
    return item


def analyze(*records, **changes):
    return analyze_targets(payload(*records, **changes), ticker="FTNT", as_of=AS_OF)


def test_distinct_firm_stats_horizon_basis_age_and_end_dates():
    result = analyze(record(target=100), record("firm_b", 80, published_at="2026-08-31"),
                     record("firm_c", 70, horizon_months=None),
                     record("firm_d", 50, share_basis="old_split_basis"))
    assert len(result["cohorts"]) == 3
    main = next(c for c in result["cohorts"] if c["key"]["horizon"]["months"] == 12
                and c["key"]["share_basis"] == "common_2026_09")
    assert main["summary"]["count"] == 2
    assert (main["summary"]["mean"], main["summary"]["median"], main["summary"]["min"], main["summary"]["max"]) == (90, 90, 80, 100)
    assert main["summary"]["target_end_date_min"] == "2027-08-31"
    assert main["summary"]["target_end_date_max"] == "2027-09-01"
    assert main["summary"]["median_age_days"] > 0
    assert any(c["key"]["horizon"] == {"months": None, "label": "unknown"} for c in result["cohorts"])
    assert "firm-level" in render_consensus_markdown(result).lower()


def test_reprint_and_rating_only_do_not_refresh_original_publication():
    old = record(published_at="2026-06-01T12:00:00Z", retrieved_at="2026-06-02T12:00:00Z")
    reprint = deepcopy(old)
    reprint["retrieved_at"] = "2026-09-01T12:00:00Z"
    reprint["source_url"] = "https://example.test/reprint"
    rating = record(status="rating_only", target=None, published_at="2026-09-10T12:00:00Z",
                    retrieved_at="2026-09-10T13:00:00Z")
    result = analyze(old, reprint, rating)
    assert result["cohorts"] == []
    assert {e["reason"] for e in result["exclusions"]} == {"duplicate_reprint", "rating_only_no_refresh", "target_too_old"}


def test_latest_wrong_basis_or_invalid_target_blocks_older_target():
    old = record(target=90)
    newer = record(target=120, published_at="2026-09-10T12:00:00Z",
                   retrieved_at="2026-09-11T12:00:00Z", share_basis="different_basis")
    result = analyze(old, newer)
    assert result["cohorts"][0]["key"]["share_basis"] == "different_basis"
    assert result["cohorts"][0]["summary"]["count"] == 1
    assert result["exclusions"][0]["reason"] == "superseded"
    newer["target"] = float("nan")
    result = analyze(old, newer)
    assert result["cohorts"] == []
    assert {e["reason"] for e in result["exclusions"]} == {"superseded", "target_or_basis_invalid"}


def test_withdrawn_and_equal_time_conflict_exclude_firm():
    withdrawal = record(status="withdrawn", target=None, published_at="2026-09-12T12:00:00Z",
                        retrieved_at="2026-09-12T13:00:00Z")
    assert analyze(record(), withdrawal)["cohorts"] == []
    same_time = record(target=105, source_url="https://example.test/other")
    conflict = analyze(record(), same_time)
    assert conflict["cohorts"] == []
    assert all(e["reason"] == "conflicting_latest" for e in conflict["exclusions"])


def test_cutoff_provenance_security_and_numeric_validation():
    late_retrieval = record("late", retrieved_at="2026-09-23T00:00:00Z")
    future = record("future", published_at="2026-09-23", retrieved_at="2026-09-24")
    wrong = record("wrong", security_id="NYSE:OTHER")
    invalid = record("invalid", target=True)
    result = analyze(late_retrieval, future, wrong, invalid)
    assert not result["cohorts"]
    assert {e["reason"] for e in result["exclusions"]} == {"after_as_of_cutoff", "security_mismatch", "target_or_basis_invalid"}
    with pytest.raises(ValueError, match="ticker"):
        analyze_targets(payload(record(), ticker="OTHER"), ticker="FTNT", as_of=AS_OF)
    with pytest.raises(ValueError, match="as_of"):
        analyze_targets(payload(), ticker="FTNT", as_of="2026-09-22T12:00:00")


def test_explicit_ranking_top_subset_and_insufficient_rankings():
    common = {"universe_size": 10, "provider": "Provider", "method": "published coverage score",
              "as_of": "2026-09-01", "source_url": "https://example.test/ranks"}
    a = record(ranking={**common, "rank": 1})
    b = record("firm_b", 80, ranking={**common, "rank": 7})
    result = analyze(a, b)
    top = result["cohorts"][0]["top_group"]
    assert top["status"] == "available"
    assert top["selection"]["max_rank"] == 2
    assert top["summary"]["count"] == 1
    assert top["summary"]["mean"] == 100
    assert "does not establish target accuracy" in render_consensus_markdown(result)
    del b["ranking"]
    assert analyze(a, b)["cohorts"][0]["top_group"]["status"] == "unavailable"
    assert analyze(a, b)["cohorts"][0]["top_group"]["summary"] is None


def test_quote_requires_matching_basis_timeliness_and_known_horizon():
    quote = {"price": 80, "currency": "USD", "security_id": "NASDAQ:FTNT",
             "share_basis": "common_2026_09", "observed_at": "2026-09-22T15:00:00Z",
             "source_url": "https://example.test/quote"}
    result = analyze(record(), quote=quote)
    assert result["cohorts"][0]["implied_price_return"]["mean_percent"] == 25
    assert analyze(record(horizon_months=None), quote=quote)["cohorts"][0]["implied_price_return"] is None
    assert analyze(record(), quote={**quote, "share_basis": "other"})["cohorts"][0]["implied_price_return"] is None
    stale = analyze(record(), quote={**quote, "observed_at": "2026-09-01T12:00:00Z"})
    assert stale["quote_issue"] == "quote_outside_as_of_or_age_limit"
    assert stale["cohorts"][0]["implied_price_return"] is None


def test_no_arbitrary_metadata_passes_into_derived_archive():
    entry = record(secret_marker="untrusted", ranking={"rank": 1})
    output = analyze(entry)
    assert "secret_marker" not in str(output)
    assert output["cohorts"][0]["targets"][0]["ranking"] is None


def test_requested_firm_universe_reports_coverage_without_inventing_targets():
    result = analyze(record(), requested_firms=["firm_c", "firm_a", "firm_b", "firm_a"])
    assert result["firm_universe"] == {"requested": ["firm_a", "firm_b", "firm_c"],
                                       "observed": ["firm_a"], "eligible": ["firm_a"],
                                       "missing": ["firm_b", "firm_c"]}


def test_extreme_numbers_and_unsafe_source_links_are_withheld():
    result = analyze(record("huge", target=1e308),
                     record("unsafe", source_url="https://example.test/a)evil"),
                     record("broken_url", source_url="http://["),
                     record("broken_status", status={"active": True}))
    assert not result["cohorts"]
    assert {e["reason"] for e in result["exclusions"]} == {
        "target_or_basis_invalid", "provenance_missing_or_invalid", "invalid_status"}


def test_stated_horizon_expiry_even_when_publication_within_age_limit():
    expired = record(horizon_months=1, published_at="2026-07-01",
                     retrieved_at="2026-07-02T12:00:00Z")
    result = analyze(expired)
    assert result["cohorts"] == []
    assert result["exclusions"][0]["reason"] == "target_horizon_expired"
    end_day = record(horizon_months=1, published_at="2026-08-22",
                     retrieved_at="2026-08-23T12:00:00Z")
    result = analyze_targets(payload(end_day), ticker="FTNT", as_of="2026-09-22T23:59:59Z")
    assert result["cohorts"][0]["summary"]["count"] == 1


def test_requested_firms_filter_cohorts_and_report_exclusion():
    result = analyze(record("firm_a"), record("firm_b", 80), requested_firms=["firm_a", "firm_c"])
    assert result["cohorts"][0]["summary"]["count"] == 1
    assert result["cohorts"][0]["targets"][0]["firm_id"] == "firm_a"
    assert result["exclusions"][0]["reason"] == "not_requested_firm"
    assert result["firm_universe"] == {"requested": ["firm_a", "firm_c"],
                                       "observed": ["firm_a"], "eligible": ["firm_a"],
                                       "missing": ["firm_c"]}


def test_duplicate_attribution_or_ranking_disables_top_subset():
    rank = {"rank": 1, "universe_size": 10, "provider": "Provider",
            "method": "published score", "as_of": "2026-09-01",
            "source_url": "https://example.test/ranks"}
    first = record(analyst="Analyst One", ranking=rank)
    duplicate = record(analyst="Analyst Two", ranking={**rank, "rank": 2},
                       retrieved_at="2026-09-03T12:00:00Z")
    result = analyze(first, duplicate)
    cohort = result["cohorts"][0]
    assert cohort["summary"]["count"] == 1
    assert cohort["targets"][0]["ranking"] is None
    assert cohort["top_group"]["reason"] == "conflicting_duplicate_attribution_or_ranking"


def test_renderer_keeps_excerpt_after_table_and_explains_rolling_dates():
    output = render_consensus_markdown(analyze(record(), record("firm_b", 80)))
    assert output.index("| Firm |") < output.index("| Firm A |") < output.index("| Firm B |")
    assert output.index("| Firm B |") < output.index("- Firm A: Demand remains strong.")
    assert "rolling targets do not share a common endpoint" in output
