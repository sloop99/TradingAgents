from __future__ import annotations

import copy

import pytest

from tradingagents.research.models import EvidenceFact
from tradingagents.research.share_inventory import analyze_share_inventory

URL = "https://example.test/filing.htm"
HASH = "a" * 64
ACCESSION = "0001000000-26-000001"
AXIS = "us-gaap:StatementClassOfStockAxis"


def item(fact_id: str, metric: str, concept: str, value: int, *, start=None, unit="shares",
         dimensions=None, end="2026-06-30"):
    source_url = f"{URL}#{fact_id}"
    context_id = f"ctx-{fact_id}"
    dimensions = dimensions or {}
    fact = EvidenceFact.from_dict({
        "fact_id": fact_id, "metric": metric, "value": value, "unit": unit,
        "period_start": start, "period_end": end, "published_at": "2026-07-30T20:00:00Z",
        "retrieved_at": "2026-09-01T20:00:00Z", "source_url": source_url,
        "accession": ACCESSION, "source_tag": concept, "definition": "reported", "kind": "reported",
    })
    candidate = {"status": "accepted", "fact_id": fact_id, "metric": metric,
                 "concept": concept, "value": value, "unit": unit, "period_start": start,
                 "period_end": end, "source_url": source_url, "source_sha256": HASH,
                 "context_id": context_id, "context_signature": "c" * 64,
                 "dimensions": dimensions}
    return fact, candidate


def analyze(items, listings=None):
    contexts = {candidate["context_id"]: {"safe": True, "signature": candidate["context_signature"],
                "period_start": candidate["period_start"], "period_end": candidate["period_end"],
                "dimensions": candidate["dimensions"]} for _, candidate in items}
    filing = {"accession": ACCESSION, "source_url": URL, "source_sha256": HASH,
              "published_at": "2026-07-30T20:00:00Z", "retrieved_at": "2026-09-01T20:00:00Z",
              "contexts": contexts, "candidates": [candidate for _, candidate in items]}
    return analyze_share_inventory([fact for fact, _ in items], [filing], listings or {}, "2026-09-16")


def test_binds_exact_provenance_and_keeps_share_roles_separate():
    inputs = [
        item("out", "filing_shares_outstanding", "dei:EntityCommonStockSharesOutstanding", 100),
        item("issued", "filing_common_stock_shares_issued", "us-gaap:CommonStockSharesIssued", 120),
        item("authorized", "filing_common_stock_shares_authorized", "us-gaap:CommonStockSharesAuthorized", 200),
        item("treasury", "filing_treasury_stock_shares", "us-gaap:TreasuryStockShares", 20),
    ]
    result = analyze(inputs)
    assert {key: [row["fact_id"] for row in value] for key, value in result["observations"].items()} == {
        "outstanding": ["out"], "issued": ["issued"], "authorized": ["authorized"], "treasury": ["treasury"],
    }
    record = result["observations"]["outstanding"][0]
    assert record["value"] == 100 and record["source_sha256"] == HASH
    assert record["context_signature"] == "c" * 64 and record["source_tag"] == "dei:EntityCommonStockSharesOutstanding"
    assert result["counts"]["unallocated_observations"] == 4


def test_weighted_duration_and_mismatched_metadata_are_not_point_in_time_share_observations():
    weighted = item("weighted", "filing_weighted_average_shares_basic", "us-gaap:WeightedAverageNumberOfSharesOutstandingBasic", 95, start="2026-01-01")
    valid = item("out", "filing_shares_outstanding", "dei:EntityCommonStockSharesOutstanding", 100)
    wrong = item("wrong", "filing_shares_outstanding", "dei:EntityCommonStockSharesOutstanding", 99)
    wrong[1]["value"] = 98
    result = analyze([weighted, valid, wrong])
    assert [row["fact_id"] for row in result["observations"]["outstanding"]] == ["out"]
    assert result["counts"]["rejected_bindings"] == 1
    assert all(row["fact_id"] != "weighted" for rows in result["observations"].values() for row in rows)


def test_listing_link_requires_same_hash_and_supported_exact_share_class_dimensions():
    dim = {AXIS: "acme:ClassAMember"}
    classified = item("class-a", "filing_common_stock_shares_outstanding", "us-gaap:CommonStockSharesOutstanding", 100, dimensions=dim)
    listing = {"accession": ACCESSION, "source_sha256": HASH, "context_signature": "l" * 64,
               "period_end": "2026-03-31", "dimensions": dim}
    result = analyze([classified], {"groups": [listing]})
    assert result["listing_links"][0]["fact_id"] == "class-a"
    assert "dates may differ" in result["listing_links"][0]["period_caveat"]
    bad_dim = item("geo", "filing_shares_outstanding", "dei:EntityCommonStockSharesOutstanding", 4,
                   dimensions={"us-gaap:GeographicalAreasAxis": "acme:USMember"})
    assert analyze([bad_dim], {"groups": [listing]})["listing_links"] == []


def test_multiple_listing_matches_are_ambiguous_and_empty_dimensions_never_allocate():
    dim = {AXIS: "acme:ClassAMember"}
    classified = item("class-a", "filing_common_stock_shares_outstanding", "us-gaap:CommonStockSharesOutstanding", 100, dimensions=dim)
    unallocated = item("total", "filing_shares_outstanding", "dei:EntityCommonStockSharesOutstanding", 120)
    listing = {"accession": ACCESSION, "source_sha256": HASH, "context_signature": "l" * 64,
               "period_end": "2026-06-30", "dimensions": dim}
    result = analyze([classified, unallocated], {"groups": [listing, copy.deepcopy(listing)]})
    assert result["listing_links"] == []
    assert result["counts"]["ambiguous_listing_observations"] == 1
    assert result["counts"]["unallocated_observations"] == 2


def test_split_is_separate_and_carries_effective_date_warning_without_adjustment():
    split = item("split", "filing_stock_split_conversion_ratio", "us-gaap:StockSplitConversionRatio", 2, unit="pure", start="2026-01-01")
    result = analyze([split])
    assert result["observations"] == {"outstanding": [], "issued": [], "authorized": [], "treasury": []}
    assert result["split_candidates"][0]["fact_id"] == "split"
    assert "effective date" in result["split_candidates"][0]["warning"]


@pytest.mark.parametrize("key,value", [
    ("source_url", URL + "#different-node"),
    ("source_sha256", "b" * 64),
    ("metric", "filing_common_stock_shares_authorized"),
    ("unit", "USD"),
])
def test_changed_candidate_binding_is_rejected(key, value):
    source = item("out", "filing_shares_outstanding", "dei:EntityCommonStockSharesOutstanding", 100)
    source[1][key] = value
    result = analyze([source])
    assert result["observations"]["outstanding"] == []
    assert result["counts"]["rejected_bindings"] == 1


def test_custom_and_negative_share_tags_cannot_enter_standard_inventory():
    custom = item("custom", "filing_custom_shares_outstanding", "acme:CommonStockSharesOutstanding", 100)
    negative = item("negative", "filing_shares_outstanding", "dei:EntityCommonStockSharesOutstanding", -1)
    assert not analyze([custom, negative])["observations"]["outstanding"]


def test_split_effective_date_is_not_trusted_from_candidate_metadata():
    split = item("split", "filing_stock_split_conversion_ratio", "us-gaap:StockSplitConversionRatio", 2, unit="pure")
    split[1]["event_effective_date"] = "2026-06-30"
    assert analyze([split])["split_candidates"][0]["event_effective_date"] is None


def test_ambiguous_titles_in_one_listing_context_do_not_create_class_link():
    dim = {AXIS: "acme:ClassAMember"}
    source = item("class-a", "filing_common_stock_shares_outstanding", "us-gaap:CommonStockSharesOutstanding", 100, dimensions=dim)
    group = {"accession": ACCESSION, "source_sha256": HASH, "dimensions": dim,
             "context_signature": "c" * 64, "ambiguous_fields": ["security_title"]}
    result = analyze([source], {"groups": [group]})
    assert result["listing_links"] == []
    assert result["counts"]["ambiguous_listing_observations"] == 1
