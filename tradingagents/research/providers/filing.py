"""Bounded retrieval of primary SEC filing HTML for supplemental evidence.

The SEC submissions feed identifies a filing and its primary document.  This
adapter deliberately accepts only that derived Archives URL; it never follows
links embedded in a filing.  Extracted observations remain filing candidates
and therefore do not assert completeness for capitalization or valuation.
"""

from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import requests

from .sec import SEC_WWW, SecEdgarProvider, _TransientSecError

_ELIGIBLE_FORMS = {"10-K", "10-Q"}
_TRANSIENT_STATUS = {429, 500, 502, 503, 504}
_MAX_FILINGS = 3
_MAX_HTML_BYTES = 15 * 1024 * 1024
_REQUEST_TIMEOUT_SECONDS = 20
_MIN_REQUEST_INTERVAL_SECONDS = 0.5
_SAFE_PRIMARY_DOCUMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.html?", re.IGNORECASE)
_DECLARED_CHARSET = re.compile(
    rb"(?:<\?xml[^>]*encoding\s*=\s*[\"']|charset\s*=\s*[\"']?)([A-Za-z0-9._-]+)",
    re.IGNORECASE,
)
_SUPPORTED_ENCODINGS = {
    "utf-8", "utf8", "utf-16", "utf-16le", "utf-16be", "ascii", "us-ascii",
    "windows-1252", "cp1252", "iso-8859-1", "latin-1",
}


class SecFilingProvider:
    """Retrieve a small number of SEC primary filing documents and parse them.

    ``sec_provider`` is injectable so callers can reuse a previously retrieved
    SEC submissions result.  It must implement the ordinary provider
    ``fetch(ticker, as_of)`` contract.
    """

    name = "sec_filing"

    def __init__(
        self,
        user_agent: str,
        cache: Any = None,
        session: Any = None,
        sec_provider: Any = None,
        max_filings: int = 1,
    ) -> None:
        if not isinstance(max_filings, int) or isinstance(max_filings, bool) or not 1 <= max_filings <= _MAX_FILINGS:
            raise ValueError(f"max_filings must be an integer from 1 to {_MAX_FILINGS}")
        self.cache = cache
        self.session = session or requests.Session()
        self.max_filings = max_filings
        # Construct through the established provider so SEC_USER_AGENT and
        # SEC_EDGAR_USER_AGENT fallback follows exactly the same policy.
        default_sec_provider = SecEdgarProvider(
            user_agent, cache=cache, session=self.session
        )
        self.user_agent = default_sec_provider.user_agent
        self.sec_provider = sec_provider or default_sec_provider
        self._last_request_at = 0.0

    def fetch(self, ticker: str, as_of: str) -> dict[str, Any]:
        normalized_ticker = SecEdgarProvider._normalize_ticker(ticker)
        cutoff = SecEdgarProvider._parse_cutoff(as_of)
        retrieved_at = SecEdgarProvider._now_iso()
        packet = self._packet(normalized_ticker, as_of, retrieved_at)
        packet["metadata"]["max_filings"] = self.max_filings

        # Match the primary SEC provider's identifying-agent rule before any
        # request, including through an injected provider.
        if not SecEdgarProvider._is_identifying_user_agent(self.user_agent):
            packet["issues"].append(self._issue(
                "SEC_USER_AGENT_REQUIRED",
                "Set SEC_USER_AGENT or pass an identifying SEC User-Agent; no contact value was invented.",
                "error",
            ))
            return packet

        try:
            base = self.sec_provider.fetch(normalized_ticker, as_of)
        except Exception as exc:
            packet["issues"].append(self._issue(
                "SEC_FILING_INDEX_UNAVAILABLE",
                f"SEC filing index unavailable: {type(exc).__name__}: {exc}",
                "error",
            ))
            return packet
        if not isinstance(base, dict):
            packet["issues"].append(self._issue(
                "SEC_FILING_INDEX_INVALID", "SEC filing index returned a non-object payload.", "error"
            ))
            return packet

        identity = base.get("identity")
        if isinstance(identity, dict) and str(identity.get("ticker", "")).upper() == normalized_ticker:
            packet["identity"] = dict(identity)
        cik = str(packet["identity"].get("cik") or "").strip()
        if not cik:
            packet["issues"].append(self._issue(
                "SEC_FILING_CIK_UNAVAILABLE",
                "SEC filing HTML was not requested because the issuer CIK is unavailable.",
                "warning",
            ))
            return packet

        eligible, selection_issues = self._eligible_documents(base.get("documents"), cik, cutoff)
        packet["issues"].extend(selection_issues)
        if not eligible:
            packet["issues"].append(self._issue(
                "SEC_FILING_UNAVAILABLE",
                "No accepted 10-K or 10-Q primary HTML document is available at the requested cutoff.",
                "warning",
            ))
            return packet

        parser_metadata: list[dict[str, Any]] = []
        for document in eligible:
            source_url = document["primary_url"]
            try:
                envelope = self._get_html(source_url)
                extracted = self._extract(
                    envelope["html"],
                    ticker=normalized_ticker,
                    cik=cik,
                    accession=document["accession"],
                    source_url=source_url,
                    published_at=document["published_at"],
                    retrieved_at=envelope["retrieved_at"],
                )
            except Exception as exc:
                packet["issues"].append(self._issue(
                    "SEC_FILING_HTML_UNAVAILABLE",
                    f"{document['accession']}: {type(exc).__name__}: {exc}",
                    "warning",
                ))
                continue
            self._merge_extracted(packet, extracted)
            parser_item = dict(extracted.get("metadata", {})) if isinstance(extracted.get("metadata"), dict) else {}
            parser_item.update({
                "accession": document["accession"],
                "form": document["form"],
                "published_at": document["published_at"],
                "source_url": source_url,
                "retrieved_at": envelope["retrieved_at"],
                "source_sha256": envelope["sha256"],
                "source_bytes": envelope["bytes"],
            })
            parser_metadata.append(parser_item)

        packet["metadata"]["filings"] = parser_metadata
        packet["metadata"]["filings_requested"] = len(eligible)
        packet["metadata"]["filings_parsed"] = len(parser_metadata)
        if parser_metadata:
            packet["coverage"]["documents"] = "partial"
            packet["coverage"]["facts"] = "partial"
        return packet

    @staticmethod
    def _packet(ticker: str, as_of: str, retrieved_at: str) -> dict[str, Any]:
        return {
            "identity": {"ticker": ticker},
            "facts": [],
            "documents": [],
            "issues": [],
            "coverage": {
                "identity": "partial",
                "documents": "unsupported",
                "facts": "unsupported",
                "point_in_time": "partial",
                # Filing candidates are a supplement, not proof that all
                # capitalization inputs have been found.
                "capitalization": "unsupported",
            },
            "metadata": {
                "provider": "sec_filing_html",
                "as_of": str(as_of),
                "retrieved_at": retrieved_at,
                "max_filings": 1,
                "max_html_bytes": _MAX_HTML_BYTES,
                "candidate_only": True,
                "filings": [],
            },
        }

    def _eligible_documents(
        self, documents: Any, cik: str, cutoff: datetime
    ) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        eligible: list[dict[str, str]] = []
        issues: list[dict[str, str]] = []
        if not isinstance(documents, list):
            return eligible, issues
        for document in documents:
            if not isinstance(document, dict):
                continue
            form = str(document.get("form") or "").upper()
            accession = str(document.get("accession") or "").strip()
            published_at = str(document.get("published_at") or "").strip()
            title = str(document.get("title") or "").strip()
            if form not in _ELIGIBLE_FORMS or not accession or not published_at:
                continue
            try:
                published = SecEdgarProvider._parse_cutoff(published_at)
            except ValueError:
                issues.append(self._issue(
                    "SEC_FILING_PUBLICATION_INVALID",
                    f"{accession} has an invalid SEC publication timestamp.",
                    "warning",
                ))
                continue
            if published > cutoff:
                continue
            if not self._is_expected_index_url(document.get("source_url"), cik, accession):
                issues.append(self._issue(
                    "SEC_FILING_SOURCE_REJECTED",
                    f"{accession} has an unexpected SEC filing index URL.",
                    "warning",
                ))
                continue
            if not _SAFE_PRIMARY_DOCUMENT.fullmatch(title):
                issues.append(self._issue(
                    "SEC_FILING_PRIMARY_DOCUMENT_REJECTED",
                    f"{accession} has no safe primary .htm/.html filename.",
                    "warning",
                ))
                continue
            primary_url = self._primary_url(cik, accession, title)
            eligible.append({
                "accession": accession,
                "form": form,
                "published_at": SecEdgarProvider._iso(published),
                "primary_url": primary_url,
            })
        eligible.sort(key=lambda item: (item["published_at"], item["accession"]), reverse=True)
        return eligible[: self.max_filings], issues

    @staticmethod
    def _primary_url(cik: str, accession: str, primary_document: str) -> str:
        cik_number = str(cik).lstrip("0") or "0"
        compact_accession = accession.replace("-", "")
        return f"{SEC_WWW}/Archives/edgar/data/{cik_number}/{compact_accession}/{primary_document}"

    @staticmethod
    def _is_expected_index_url(value: Any, cik: str, accession: str) -> bool:
        if not isinstance(value, str):
            return False
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.netloc.lower() != "www.sec.gov" or parsed.query or parsed.fragment:
            return False
        cik_number = str(cik).lstrip("0") or "0"
        compact_accession = accession.replace("-", "")
        expected = f"/Archives/edgar/data/{cik_number}/{compact_accession}/{accession}-index.html"
        return parsed.path == expected

    def _get_html(self, url: str) -> dict[str, Any]:
        key_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
        cache_key = f"sec_filing:raw:{key_hash}"
        if self.cache is not None:
            cached = self.cache.get_json(cache_key)
            if self._valid_envelope(cached, url):
                return cached

        raw = self._request_html(url)
        retrieved_at = SecEdgarProvider._now_iso()
        # Do not use requests.Response.text: its text/html default can decode
        # UTF-8 filing prose as Latin-1.  Prefer strict UTF-8, then a declared
        # BOM/XML/meta encoding from the raw response; unsupported or malformed
        # documents are evidence gaps instead of silently corrupted text.
        html = self._decode_html(raw)
        canonical_bytes = html.encode("utf-8")
        envelope = {
            "url": url,
            "retrieved_at": retrieved_at,
            "sha256": hashlib.sha256(canonical_bytes).hexdigest(),
            "bytes": len(canonical_bytes),
            "html": html,
        }
        if self.cache is not None:
            self.cache.put_json(cache_key, envelope)
        return envelope

    @staticmethod
    def _decode_html(raw: bytes) -> str:
        if raw.startswith(b"\xef\xbb\xbf"):
            return raw.decode("utf-8-sig")
        if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
            return raw.decode("utf-16")
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as utf8_error:
            match = _DECLARED_CHARSET.search(raw[:16 * 1024])
            encoding = match.group(1).decode("ascii", errors="ignore").lower() if match else ""
            if encoding not in _SUPPORTED_ENCODINGS:
                detail = encoding or "none"
                raise ValueError(f"SEC filing is not UTF-8 and declares unsupported charset {detail!r}") from utf8_error
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError as exc:
                raise ValueError(f"SEC filing cannot be decoded as declared charset {encoding!r}") from exc

    @staticmethod
    def _valid_envelope(value: Any, url: str) -> bool:
        if not isinstance(value, dict) or value.get("url") != url:
            return False
        html = value.get("html")
        if not isinstance(html, str) or not isinstance(value.get("retrieved_at"), str):
            return False
        try:
            retrieved = datetime.fromisoformat(value["retrieved_at"].replace("Z", "+00:00"))
        except ValueError:
            return False
        if retrieved.tzinfo is None:
            return False
        encoded = html.encode("utf-8")
        return (
            isinstance(value.get("sha256"), str)
            and value["sha256"] == hashlib.sha256(encoded).hexdigest()
            and value.get("bytes") == len(encoded)
            and len(encoded) <= _MAX_HTML_BYTES
        )

    def _request_html(self, url: str) -> bytes:
        last_error: Exception | None = None
        for attempt in range(3):
            self._respect_rate_limit()
            response: Any = None
            try:
                response = self.session.get(
                    url,
                    headers={
                        "User-Agent": self.user_agent,
                        "Accept": "text/html,application/xhtml+xml",
                        "Accept-Encoding": "gzip, deflate",
                    },
                    timeout=_REQUEST_TIMEOUT_SECONDS,
                    stream=True,
                    allow_redirects=False,
                )
                status = getattr(response, "status_code", 200)
                if status in _TRANSIENT_STATUS:
                    raise _TransientSecError(f"SEC returned HTTP {status}")
                if 300 <= status < 400:
                    raise ValueError(f"SEC filing redirect rejected (HTTP {status})")
                response.raise_for_status()
                chunks: list[bytes] = []
                size = 0
                iterator = getattr(response, "iter_content", None)
                if callable(iterator):
                    raw_chunks = iterator(chunk_size=64 * 1024)
                else:
                    raw_chunks = [getattr(response, "content", b"")]
                for chunk in raw_chunks:
                    if not chunk:
                        continue
                    if isinstance(chunk, str):
                        chunk = chunk.encode("utf-8")
                    if not isinstance(chunk, bytes):
                        raise ValueError("SEC returned a non-bytes HTML chunk")
                    size += len(chunk)
                    if size > _MAX_HTML_BYTES:
                        raise ValueError(f"SEC filing exceeds {_MAX_HTML_BYTES} byte limit")
                    chunks.append(chunk)
                return b"".join(chunks)
            except (requests.RequestException, _TransientSecError, ValueError) as exc:
                last_error = exc
                if attempt == 2 or not self._is_transient(exc):
                    break
                time.sleep(0.5 * (attempt + 1))
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
        raise RuntimeError(f"SEC filing request failed for {url}: {last_error}") from last_error

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        if isinstance(exc, _TransientSecError):
            return True
        if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
            return True
        if isinstance(exc, requests.HTTPError):
            return exc.response is not None and exc.response.status_code in _TRANSIENT_STATUS
        return False

    def _respect_rate_limit(self) -> None:
        delay = _MIN_REQUEST_INTERVAL_SECONDS - (time.monotonic() - self._last_request_at)
        if delay > 0:
            time.sleep(delay)
        self._last_request_at = time.monotonic()

    @staticmethod
    def _extract(html: str, **kwargs: str) -> dict[str, Any]:
        # Imported lazily while keeping the HTML transport independently usable.
        from ..filings import extract_filing_evidence

        result = extract_filing_evidence(html, **kwargs)
        if not isinstance(result, dict):
            raise ValueError("filing extractor returned a non-object payload")
        return result

    @staticmethod
    def _merge_extracted(packet: dict[str, Any], extracted: dict[str, Any]) -> None:
        for key in ("facts", "documents", "issues"):
            value = extracted.get(key)
            if isinstance(value, list):
                packet[key].extend(value)

    @staticmethod
    def _issue(code: str, message: str, severity: str) -> dict[str, str]:
        return {"code": code, "message": message, "severity": severity}
