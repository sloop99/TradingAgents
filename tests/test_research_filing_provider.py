from __future__ import annotations

import hashlib
from copy import deepcopy

import pytest
import requests

from tradingagents.research.providers.filing import SecFilingProvider

CIK = "0000320193"
ACCESSION = "0000320193-25-000001"
INDEX_URL = f"https://www.sec.gov/Archives/edgar/data/320193/{ACCESSION.replace('-', '')}/{ACCESSION}-index.html"
PRIMARY_URL = f"https://www.sec.gov/Archives/edgar/data/320193/{ACCESSION.replace('-', '')}/annual.htm"


class FakeResponse:
    def __init__(self, content=b"<html><body>annual filing</body></html>", status_code=200):
        self.content = content
        self.status_code = status_code
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            response = requests.Response()
            response.status_code = self.status_code
            raise requests.HTTPError(f"HTTP {self.status_code}", response=response)

    def iter_content(self, chunk_size=1):
        del chunk_size
        yield self.content

    def close(self):
        self.closed = True


class FakeSession:
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
        del max_age_seconds
        return deepcopy(self.values.get(key))

    def put_json(self, key, payload):
        self.puts.append((key, deepcopy(payload)))
        self.values[key] = deepcopy(payload)
        return "digest"


class BaseProvider:
    def __init__(self, documents=None):
        self.documents = documents if documents is not None else [document()]
        self.calls = []

    def fetch(self, ticker, as_of):
        self.calls.append((ticker, as_of))
        return {
            "identity": {"ticker": ticker, "cik": CIK, "name": "Apple Inc."},
            "documents": deepcopy(self.documents),
            "facts": [],
            "issues": [],
            "coverage": {},
        }


def document(**updates):
    value = {
        "document_id": f"sec:{ACCESSION}",
        "form": "10-K",
        "published_at": "2025-01-30T12:30:00Z",
        "source_url": INDEX_URL,
        "accession": ACCESSION,
        "title": "annual.htm",
    }
    value.update(updates)
    return value


def extracted(**updates):
    value = {
        "identity": {"ticker": "AAPL", "cik": CIK},
        "facts": [{
            "fact_id": "filing:revenue:1", "metric": "filing_revenue", "value": 100,
            "unit": "USD", "period_end": "2024-12-31", "published_at": "2025-01-30T12:30:00Z",
            "retrieved_at": "2026-09-16T12:00:00Z", "source_url": PRIMARY_URL,
            "definition": "Filing candidate", "kind": "reported",
        }],
        "documents": [],
        "issues": [],
        "coverage": {"facts": "partial"},
        "metadata": {"parser": "test", "candidates": 1},
    }
    value.update(updates)
    return value


@pytest.mark.unit
def test_filing_provider_fetches_only_safe_primary_html_and_returns_candidates(monkeypatch):
    session = FakeSession([FakeResponse()])
    provider = SecFilingProvider(
        "Research Test research@example.com", session=session, sec_provider=BaseProvider()
    )
    monkeypatch.setattr(provider, "_extract", lambda html, **kwargs: extracted())

    packet = provider.fetch("aapl", "2025-12-31")

    assert session.calls[0][0] == PRIMARY_URL
    assert session.calls[0][1]["timeout"] == 20
    assert session.calls[0][1]["stream"] is True
    assert packet["facts"][0]["metric"] == "filing_revenue"
    assert packet["metadata"]["candidate_only"] is True
    assert packet["coverage"]["capitalization"] == "unsupported"
    assert packet["metadata"]["filings"][0]["source_url"] == PRIMARY_URL


@pytest.mark.unit
def test_filing_provider_rejects_arbitrary_or_unsafe_document_sources():
    provider = SecFilingProvider(
        "Research Test research@example.com", sec_provider=BaseProvider([
            document(source_url="https://evil.example/annual.htm"),
            document(title="../annual.htm"),
        ])
    )

    packet = provider.fetch("AAPL", "2025-12-31")

    assert packet["facts"] == []
    assert any(issue["code"] == "SEC_FILING_SOURCE_REJECTED" for issue in packet["issues"])
    assert any(issue["code"] == "SEC_FILING_PRIMARY_DOCUMENT_REJECTED" for issue in packet["issues"])


@pytest.mark.unit
def test_filing_provider_uses_immutable_raw_html_cache(monkeypatch):
    cache = MemoryCache()
    session = FakeSession([FakeResponse()])
    provider = SecFilingProvider(
        "Research Test research@example.com", cache=cache, session=session, sec_provider=BaseProvider()
    )
    monkeypatch.setattr(provider, "_extract", lambda html, **kwargs: extracted())

    provider.fetch("AAPL", "2025-12-31")
    provider.fetch("AAPL", "2025-12-31")

    assert len(session.calls) == 1
    raw = next(payload for key, payload in cache.puts if key.startswith("sec_filing:raw:"))
    assert {"url", "retrieved_at", "sha256", "bytes", "html"} <= raw.keys()


@pytest.mark.unit
def test_filing_provider_does_not_retry_403_but_retries_transient(monkeypatch):
    permanent = SecFilingProvider("Research Test research@example.com", session=FakeSession([FakeResponse(status_code=403)]))
    with pytest.raises(RuntimeError, match="HTTP 403"):
        permanent._request_html(PRIMARY_URL)
    assert len(permanent.session.calls) == 1

    transient = SecFilingProvider("Research Test research@example.com", session=FakeSession([
        FakeResponse(status_code=429), FakeResponse(status_code=500), FakeResponse(),
    ]))
    monkeypatch.setattr("tradingagents.research.providers.filing.time.sleep", lambda _: None)
    assert b"annual filing" in transient._request_html(PRIMARY_URL)
    assert len(transient.session.calls) == 3


@pytest.mark.unit
def test_filing_provider_rejects_redirects_and_oversized_cached_html():
    redirect = SecFilingProvider(
        "Research Test research@example.com", session=FakeSession([FakeResponse(status_code=302)])
    )
    with pytest.raises(RuntimeError, match="redirect rejected"):
        redirect._request_html(PRIMARY_URL)
    assert redirect.session.calls[0][1]["allow_redirects"] is False

    oversized_html = "x" * (15 * 1024 * 1024 + 1)
    envelope = {
        "url": PRIMARY_URL,
        "retrieved_at": "2026-09-16T12:00:00Z",
        "html": oversized_html,
        "bytes": len(oversized_html.encode("utf-8")),
        "sha256": hashlib.sha256(oversized_html.encode("utf-8")).hexdigest(),
    }
    assert not SecFilingProvider._valid_envelope(envelope, PRIMARY_URL)


@pytest.mark.unit
def test_filing_provider_enforces_bounded_filings_and_html_size(monkeypatch):
    with pytest.raises(ValueError, match="1 to 3"):
        SecFilingProvider("Research Test research@example.com", max_filings=4)

    oversized = FakeResponse(content=b"x" * (15 * 1024 * 1024 + 1))
    provider = SecFilingProvider("Research Test research@example.com", session=FakeSession([oversized]))
    monkeypatch.setattr("tradingagents.research.providers.filing.time.sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="byte limit"):
        provider._request_html(PRIMARY_URL)


@pytest.mark.unit
def test_filing_provider_decodes_utf8_html_without_latin1_mojibake():
    html = "<html><body>Cash \u2014 unrestricted</body></html>"
    raw = html.encode()

    assert SecFilingProvider._decode_html(raw) == html
