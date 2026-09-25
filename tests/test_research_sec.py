from __future__ import annotations

from copy import deepcopy

import pytest
import requests

from tradingagents.research.engine import build_packet
from tradingagents.research.providers import sec as sec_module
from tradingagents.research.providers.sec import SecEdgarProvider


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            response = requests.Response()
            response.status_code = self.status_code
            raise requests.HTTPError(f"HTTP {self.status_code}", response=response)

    def json(self):
        return deepcopy(self.payload)


class FakeSession:
    def __init__(self, by_url):
        self.by_url = by_url
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.by_url[url])


class SequencedSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class MemoryCache:
    def __init__(self):
        self.values = {}
        self.puts = []

    def get_json(self, key, max_age_seconds=None):
        return deepcopy(self.values.get(key))

    def put_json(self, key, payload):
        self.puts.append((key, deepcopy(payload)))
        self.values[key] = deepcopy(payload)
        return f"digest-{len(self.puts)}"


CIK = "0000320193"
ACCESSION = "0000320193-25-000001"
AMENDMENT = "0000320193-25-000002"
LATE_ACCESSION = "0000320193-26-000003"
TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{CIK}.json"
FACTS_URL = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json"


def fixtures():
    return {
        TICKERS_URL: {
            "fields": ["cik", "name", "ticker", "exchange"],
            "data": [[320193, "APPLE INC", "AAPL", "Nasdaq"]],
        },
        SUBMISSIONS_URL: {
            "name": "Apple Inc.",
            "tickers": ["AAPL"],
            "exchanges": ["Nasdaq"],
            "sic": "3571",
            "fiscalYearEnd": "0928",
            "filings": {
                "recent": {
                    "accessionNumber": [ACCESSION, AMENDMENT, LATE_ACCESSION],
                    "form": ["10-K", "10-K/A", "10-Q"],
                    "filingDate": ["2025-01-30", "2025-02-01", "2026-01-30"],
                    "acceptanceDateTime": ["20250130123000", "20250201123000", "20260130123000"],
                    "reportDate": ["2024-12-31", "2024-12-31", "2025-12-31"],
                    "primaryDocument": ["annual.htm", "amendment.htm", "quarter.htm"],
                }
            },
        },
        FACTS_URL: {
            "cik": CIK,
            "facts": {
                "us-gaap": {
                    "RevenueFromContractWithCustomerExcludingAssessedTax": {
                        "label": "Revenue from contracts",
                        "units": {
                            "USD": [
                                {
                                    "start": "2024-01-01",
                                    "end": "2024-12-31",
                                    "val": 100,
                                    "accn": ACCESSION,
                                    "form": "10-K",
                                    "filed": "2025-01-30",
                                },
                                {
                                    "start": "2024-01-01",
                                    "end": "2024-12-31",
                                    "val": 101,
                                    "accn": AMENDMENT,
                                    "form": "10-K/A",
                                    "filed": "2025-02-01",
                                },
                                {
                                    "start": "2025-01-01",
                                    "end": "2025-12-31",
                                    "val": 200,
                                    "accn": LATE_ACCESSION,
                                    "form": "10-Q",
                                    "filed": "2026-01-30",
                                },
                            ]
                        },
                    },
                    "LongTermDebtCurrent": {
                        "units": {
                            "USD": [
                                {
                                    "end": "2024-12-31",
                                    "val": 10,
                                    "accn": ACCESSION,
                                    "form": "10-K",
                                    "filed": "2025-01-30",
                                }
                            ]
                        }
                    },
                    "LongTermDebtNoncurrent": {
                        "units": {
                            "USD": [
                                {
                                    "end": "2024-12-31",
                                    "val": 90,
                                    "accn": ACCESSION,
                                    "form": "10-K",
                                    "filed": "2025-01-30",
                                }
                            ]
                        }
                    },
                },
                "dei": {
                    "EntityCommonStockSharesOutstanding": {
                        "units": {
                            "shares": [
                                {
                                    "end": "2024-12-31",
                                    "val": 15,
                                    "accn": ACCESSION,
                                    "form": "10-K",
                                    "filed": "2025-01-30",
                                }
                            ]
                        }
                    }
                },
            },
        },
    }


@pytest.mark.unit
def test_sec_provider_preserves_sources_periods_and_concept_conflicts():
    session = FakeSession(fixtures())
    packet = SecEdgarProvider("Research Test research@example.com", session=session).fetch("aapl", "2025-12-31")

    assert packet["identity"] == {
        "ticker": "AAPL",
        "cik": CIK,
        "name": "Apple Inc.",
        "exchange": "Nasdaq",
        "fiscal_year_end": "0928",
        "sic": "3571",
    }
    assert [document["form"] for document in packet["documents"]] == ["10-K"]
    assert packet["documents"][0]["source_url"].endswith(f"/{ACCESSION.replace('-', '')}/{ACCESSION}-index.html")
    revenue = [fact for fact in packet["facts"] if fact["metric"] == "revenue"]
    assert len(revenue) == 1
    assert revenue[0]["value"] == 100
    assert revenue[0]["period_start"] == "2024-01-01"
    assert revenue[0]["published_at"] == "2025-01-30T12:30:00Z"
    assert revenue[0]["retrieved_at"].endswith("Z")
    assert revenue[0]["source_tag"] == "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
    assert next(fact for fact in packet["facts"] if fact["metric"] == "long_term_debt_current")["value"] == 10
    assert next(fact for fact in packet["facts"] if fact["metric"] == "long_term_debt_noncurrent")["value"] == 90
    assert all(fact["kind"] == "reported" for fact in packet["facts"])
    assert packet["coverage"]["ticker_history"] == "partial"


@pytest.mark.unit
def test_sec_capex_uses_standardized_cash_outflow_positive_basis():
    source = fixtures()
    source[FACTS_URL]["facts"]["us-gaap"]["PaymentsToAcquirePropertyPlantAndEquipment"] = {
        "label": "Payments to Acquire Property, Plant, and Equipment",
        "units": {
            "USD": [
                {
                    "start": "2024-01-01",
                    "end": "2024-12-31",
                    "val": 20,
                    "accn": ACCESSION,
                    "form": "10-K",
                    "filed": "2025-01-30",
                }
            ]
        },
    }
    packet = SecEdgarProvider("Research Test research@example.com", session=FakeSession(source)).fetch("AAPL", "2025-12-31")

    capex = next(fact for fact in packet["facts"] if fact["metric"] == "capital_expenditures")
    assert capex["adjustment_basis"] == "cash_outflow_positive"


@pytest.mark.unit
def test_sec_productive_assets_capex_stays_separate_from_ppe_only_capex():
    source = fixtures()
    source[FACTS_URL]["facts"]["us-gaap"]["PaymentsToAcquireProductiveAssets"] = {
        "label": "Payments to Acquire Productive Assets",
        "units": {
            "USD": [
                {
                    "start": "2024-01-01",
                    "end": "2024-12-31",
                    "val": 25,
                    "accn": ACCESSION,
                    "form": "10-K",
                    "filed": "2025-01-30",
                }
            ]
        },
    }
    packet = SecEdgarProvider("Research Test research@example.com", session=FakeSession(source)).fetch("AAPL", "2025-12-31")

    productive = next(fact for fact in packet["facts"] if fact["metric"] == "capital_expenditures_productive_assets")
    assert productive["source_tag"] == "us-gaap:PaymentsToAcquireProductiveAssets"
    assert productive["adjustment_basis"] == "cash_outflow_positive"


@pytest.mark.unit
def test_sec_provider_cache_uses_immutable_snapshot_and_latest_pointer():
    cache = MemoryCache()
    session = FakeSession(fixtures())
    provider = SecEdgarProvider("Research Test research@example.com", cache=cache, session=session)
    provider.fetch("AAPL", "2025-12-31")
    first_call_count = len(session.calls)
    provider.fetch("AAPL", "2025-12-31")

    assert first_call_count == 3
    assert len(session.calls) == first_call_count
    keys = [key for key, _ in cache.puts]
    assert any(key.startswith("sec:snapshot:") for key in keys)
    assert any(key.startswith("sec:latest:") for key in keys)
    snapshots = [payload for key, payload in cache.puts if key.startswith("sec:snapshot:")]
    assert all({"retrieved_at", "url", "payload"} <= snapshot.keys() for snapshot in snapshots)


@pytest.mark.unit
def test_sec_provider_uses_original_companyfacts_snapshot_time_for_cached_facts(monkeypatch):
    timestamps = iter(
        [
            "2026-09-16T00:00:00Z",  # first fetch packet
            "2026-09-16T00:00:01Z",  # ticker mapping snapshot
            "2026-09-16T00:00:02Z",  # submissions snapshot
            "2026-09-16T00:00:03Z",  # companyfacts snapshot
            "2026-09-16T00:01:00Z",  # second fetch packet
        ]
    )
    monkeypatch.setattr(SecEdgarProvider, "_now_iso", staticmethod(lambda: next(timestamps)))
    provider = SecEdgarProvider(
        "Research Test research@example.com", cache=MemoryCache(), session=FakeSession(fixtures())
    )

    provider.fetch("AAPL", "2025-12-31")
    cached_packet = provider.fetch("AAPL", "2025-12-31")

    assert {fact["retrieved_at"] for fact in cached_packet["facts"]} == {"2026-09-16T00:00:03Z"}


@pytest.mark.unit
def test_missing_user_agent_and_unresolved_ticker_stop_before_follow_on_requests(monkeypatch):
    # The package loads .env on import, so a contributor's own agent would otherwise fill the gap.
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.delenv("SEC_EDGAR_USER_AGENT", raising=False)
    no_agent_session = FakeSession(fixtures())
    no_agent = SecEdgarProvider("", session=no_agent_session).fetch("AAPL", "2025-12-31")
    assert no_agent_session.calls == []
    assert no_agent["issues"][0]["code"] == "SEC_USER_AGENT_REQUIRED"
    assert no_agent["coverage"]["identity"].startswith("unsupported")

    unresolved_fixtures = fixtures()
    unresolved_fixtures[TICKERS_URL]["data"] = [[999, "Other", "OTHER", "NYSE"]]
    unresolved_fixtures["https://www.sec.gov/files/company_tickers.json"] = {}
    unresolved_session = FakeSession(unresolved_fixtures)
    unresolved = SecEdgarProvider("Research Test research@example.com", session=unresolved_session).fetch("MISS", "2025-12-31")
    assert len(unresolved_session.calls) == 2
    assert unresolved["identity"] == {"ticker": "MISS"}
    assert unresolved["issues"][0]["code"] == "unsupported_ticker"
    assert unresolved["coverage"]["documents"] == "unsupported"


@pytest.mark.unit
def test_sec_provider_does_not_retry_permanent_http_errors():
    session = SequencedSession([FakeResponse({}, status_code=403)])
    provider = SecEdgarProvider("Research Test research@example.com", session=session)

    with pytest.raises(RuntimeError, match="HTTP 403") as error:
        provider._request_json(TICKERS_URL)

    assert len(session.calls) == 1
    assert isinstance(error.value.__cause__, requests.HTTPError)
    assert error.value.__cause__.response.status_code == 403


@pytest.mark.unit
def test_sec_provider_uses_legacy_ticker_map_after_primary_404():
    session = SequencedSession(
        [
            FakeResponse({}, status_code=404),
            FakeResponse({"0": {"cik_str": 320193, "ticker": "AAPL", "title": "APPLE INC"}}),
        ]
    )
    provider = SecEdgarProvider("Research Test research@example.com", session=session)

    assert provider._resolve_listing("AAPL") == {"cik": 320193, "name": "APPLE INC"}
    assert len(session.calls) == 2


@pytest.mark.unit
def test_sec_provider_stops_after_mapping_403_without_invalid_packet_records():
    session = SequencedSession([FakeResponse({}, status_code=403)])
    provider = SecEdgarProvider("Research Test research@example.com", session=session)

    packet = build_packet("AAPL", "2025-12-31", [provider])

    assert len(session.calls) == 1
    assert any(issue.code == "sec_request_failed" for issue in packet.issues)
    assert not any(issue.code == "INVALID_PROVIDER_RECORD" for issue in packet.issues)
    assert packet.coverage["facts"] == "unsupported"


@pytest.mark.unit
def test_sec_provider_retries_transient_http_429_with_bounded_attempts(monkeypatch):
    session = SequencedSession([
        FakeResponse({}, status_code=429),
        FakeResponse({}, status_code=429),
        FakeResponse({"ok": True}),
    ])
    provider = SecEdgarProvider("Research Test research@example.com", session=session)
    monkeypatch.setattr(sec_module.time, "sleep", lambda _: None)

    assert provider._request_json(TICKERS_URL) == {"ok": True}
    assert len(session.calls) == 3


@pytest.mark.unit
def test_sec_provider_uses_core_coverage_and_metric_names():
    provider = SecEdgarProvider("Research Test research@example.com", session=FakeSession(fixtures()))
    packet = build_packet("AAPL", "2025-12-31", [provider])

    assert {fact.metric for fact in packet.facts} >= {"revenue", "shares_outstanding"}
    assert not [issue for issue in packet.issues if issue.code == "INVALID_PROVIDER_RECORD"]
    assert packet.coverage["point_in_time"] == "partial"


@pytest.mark.unit
def test_sec_capitalization_concepts_keep_exact_reported_scopes_and_absence():
    source = fixtures()
    concepts = source[FACTS_URL]["facts"]["us-gaap"]
    for concept, value in {
        "ShortTermBorrowings": 11,
        "CommercialPaper": 12,
        "LongTermDebt": 90,
        "FinanceLeaseLiability": 13,
        "FinanceLeaseLiabilityCurrent": 3,
        "FinanceLeaseLiabilityNoncurrent": 10,
        "PreferredStockValue": 14,
        "PreferredStockLiquidationPreferenceValue": 19,
        "MinorityInterest": 6,
        "RedeemableNoncontrollingInterestEquityCarryingAmount": 7,
    }.items():
        concepts[concept] = {
            "label": concept,
            "description": f"SEC description for {concept}",
            "units": {"USD": [{"end": "2024-12-31", "val": value, "accn": ACCESSION, "form": "10-K", "filed": "2025-01-30"}]},
        }

    packet = SecEdgarProvider("Research Test research@example.com", session=FakeSession(source)).fetch("AAPL", "2025-12-31")
    by_metric = {fact["metric"]: fact for fact in packet["facts"]}

    assert by_metric["long_term_debt_reported"]["value"] == 90
    assert by_metric["finance_lease_liability"]["value"] == 13
    assert by_metric["finance_lease_liability_current"]["value"] == 3
    assert by_metric["finance_lease_liability_noncurrent"]["value"] == 10
    assert by_metric["preferred_stock_value"]["value"] == 14
    assert by_metric["preferred_stock_liquidation_preference"]["value"] == 19
    assert by_metric["noncontrolling_interest_carrying"]["value"] == 6
    assert by_metric["redeemable_nci_equity_carrying_amount"]["value"] == 7
    assert "preferred_stock_liquidation_preference" in by_metric
    assert "cash_including_restricted" not in by_metric
    assert "long_term_debt_total" not in by_metric
