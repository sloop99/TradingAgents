"""A small, free-first adapter for SEC EDGAR company evidence.

The SEC APIs return current aggregates.  This adapter uses submission acceptance
timestamps to decide whether a fact was available at an ``as_of`` cutoff; it
does not claim that the current ticker mapping itself is historical.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from typing import Any

import requests

SEC_DATA = "https://data.sec.gov"
SEC_WWW = "https://www.sec.gov"
_ALLOWED_FACT_FORMS = {"10-K", "10-Q", "20-F", "40-F"}
_DOCUMENT_FORMS = _ALLOWED_FACT_FORMS | {"8-K", "6-K"}
_TRANSIENT_STATUS = {429, 500, 502, 503, 504}

# Keep related concepts as separate evidence.  In particular, debt components
# must be reconciled by a later accounting layer, not summed here.
_METRICS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "revenue": (
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax", "Revenue excluding assessed tax"),
        ("us-gaap", "SalesRevenueNet", "Net sales revenue"),
        ("us-gaap", "Revenues", "Revenues"),
        ("us-gaap", "RevenueFromContractWithCustomerIncludingAssessedTax", "Revenue including assessed tax"),
    ),
    "net_income": (("us-gaap", "NetIncomeLoss", "Net income (loss)"),),
    "net_income_attributable_to_parent": (("us-gaap", "NetIncomeLossAttributableToParent", "Net income (loss) attributable to parent"),),
    "operating_income": (("us-gaap", "OperatingIncomeLoss", "Operating income (loss)"),),
    "operating_cash_flow": (("us-gaap", "NetCashProvidedByUsedInOperatingActivities", "Net cash from operating activities"),),
    "capital_expenditures": (("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment", "Payments to acquire property, plant and equipment"),),
    # This includes software and other intangibles as well as PP&E.  It stays
    # distinct from the PP&E-only metric above; callers must opt in explicitly.
    "capital_expenditures_productive_assets": (("us-gaap", "PaymentsToAcquireProductiveAssets", "Payments to acquire productive assets, including software and other intangibles"),),
    "cash": (("us-gaap", "CashAndCashEquivalentsAtCarryingValue", "Cash and cash equivalents"),),
    "cash_including_restricted": (("us-gaap", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash, cash equivalents, restricted cash"),),
    # Do not use these as total company debt: LongTermDebt excludes other debt
    # classes, and lease-inclusive tags may overlap their non-lease counterparts.
    "long_term_debt_total": (("us-gaap", "LongTermDebt", "Long-term debt"),),
    "long_term_debt_current": (("us-gaap", "LongTermDebtCurrent", "Current portion of long-term debt"),),
    "long_term_debt_noncurrent": (("us-gaap", "LongTermDebtNoncurrent", "Noncurrent long-term debt"),),
    "long_term_debt_including_finance_leases_total": (("us-gaap", "LongTermDebtAndFinanceLeaseObligations", "Long-term debt and finance leases"),),
    "long_term_debt_including_finance_leases_current": (("us-gaap", "LongTermDebtAndFinanceLeaseObligationsCurrent", "Current long-term debt and finance leases"),),
    "long_term_debt_including_finance_leases_noncurrent": (("us-gaap", "LongTermDebtAndFinanceLeaseObligationsNoncurrent", "Noncurrent long-term debt and finance leases"),),
    "total_assets": (("us-gaap", "Assets", "Total assets"),),
    "total_liabilities": (("us-gaap", "Liabilities", "Total liabilities"),),
    "equity": (("us-gaap", "StockholdersEquity", "Stockholders' equity"),),
    "equity_including_noncontrolling": (("us-gaap", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "Equity including noncontrolling interest"),),
    "shares_outstanding": (("dei", "EntityCommonStockSharesOutstanding", "Entity common shares outstanding"),),
    "weighted_average_shares": (("us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding", "Weighted-average diluted shares"),),
    "weighted_average_shares_basic": (("us-gaap", "WeightedAverageNumberOfSharesOutstandingBasic", "Weighted-average basic shares"),),
    "stock_based_compensation": (("us-gaap", "ShareBasedCompensation", "Share-based compensation"),),
}


class SecEdgarProvider:
    """Retrieve SEC issuer metadata, filing references, and reported XBRL facts.

    ``cache`` follows the research cache contract: ``get_json(key,
    max_age_seconds=None)`` and ``put_json(key, payload)``.  Raw SEC response
    snapshots are immutable timestamped records; a small latest-pointer is the
    only mutable cache entry.
    """

    def __init__(self, user_agent: str, cache: Any = None, session: Any = None) -> None:
        configured_agent = user_agent or os.getenv("SEC_USER_AGENT") or os.getenv("SEC_EDGAR_USER_AGENT", "")
        self.user_agent = configured_agent.strip()
        self.cache = cache
        self.session = session or requests.Session()
        self._last_request_at = 0.0
        self._retrieved_at_by_url: dict[str, str] = {}

    def fetch(self, ticker: str, as_of: str) -> dict[str, Any]:
        """Return plain-dict SEC evidence available no later than ``as_of``.

        A date-only cutoff means the conservative end of that UTC day.  Filing
        records without an SEC acceptance timestamp are similarly timestamped
        at end-of-day and explicitly flagged as imprecise.
        """
        normalized_ticker = self._normalize_ticker(ticker)
        cutoff = self._parse_cutoff(as_of)
        retrieved_at = self._now_iso()
        issues: list[dict[str, Any]] = []
        result: dict[str, Any] = {
            "identity": {"ticker": normalized_ticker},
            "facts": [],
            "documents": [],
            "issues": issues,
            "coverage": {
                "identity": "unsupported",
                "documents": "unsupported",
                "facts": "unsupported",
                "point_in_time": "partial",
                "ticker_history": "partial",
            },
        }
        if not self._is_identifying_user_agent(self.user_agent):
            issues.append(
                self._issue(
                    "SEC_USER_AGENT_REQUIRED",
                    "Set SEC_USER_AGENT or pass an identifying SEC User-Agent; no contact value was invented.",
                    "error",
                )
            )
            result["coverage"]["identity"] = "unsupported"
            result["coverage"]["point_in_time"] = "unsupported"
            return result

        try:
            listing = self._resolve_listing(normalized_ticker)
        except Exception as exc:  # return an evidence packet even if SEC is unavailable
            issues.append(self._issue("sec_request_failed", str(exc), "error"))
            return result
        if listing is None:
            issues.append(self._issue("unsupported_ticker", f"No SEC issuer mapping for {normalized_ticker}", "warning"))
            result["coverage"]["identity"] = "unsupported"
            result["coverage"]["point_in_time"] = "unsupported"
            return result

        cik = str(listing["cik"]).zfill(10)
        try:
            submissions = self._get_json(f"{SEC_DATA}/submissions/CIK{cik}.json")
        except Exception as exc:
            issues.append(self._issue("submissions_unavailable", str(exc), "error"))
            result["identity"] = self._identity_from_listing(normalized_ticker, cik, listing)
            result["coverage"]["identity"] = "partial"
            return result

        identity = self._identity_from_submissions(normalized_ticker, cik, listing, submissions)
        result["identity"] = identity
        result["coverage"]["identity"] = "sufficient"
        filings = self._recent_filings(submissions)
        filing_by_accession = {item["accession"]: item for item in filings if item.get("accession")}
        documents, document_issues = self._documents(cik, filings, cutoff)
        result["documents"] = documents
        issues.extend(document_issues)
        result["coverage"]["documents"] = "sufficient" if documents else "partial"

        try:
            company_facts = self._get_json(f"{SEC_DATA}/api/xbrl/companyfacts/CIK{cik}.json")
        except Exception as exc:
            issues.append(self._issue("companyfacts_unavailable", str(exc), "error"))
            result["coverage"]["facts"] = "unsupported"
            return result
        facts_retrieved_at = self._retrieved_at_by_url.get(
            f"{SEC_DATA}/api/xbrl/companyfacts/CIK{cik}.json", retrieved_at
        )
        facts, fact_issues = self._facts(company_facts, filing_by_accession, cutoff, facts_retrieved_at)
        result["facts"] = facts
        issues.extend(fact_issues)
        result["coverage"]["facts"] = "sufficient" if facts else "partial"
        return result

    @staticmethod
    def _is_identifying_user_agent(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        cleaned = value.strip()
        generic = {"python-requests", "requests", "mozilla", "user-agent"}
        return len(cleaned) >= 8 and cleaned.lower() not in generic

    @staticmethod
    def _normalize_ticker(ticker: str) -> str:
        cleaned = str(ticker or "").strip().upper()
        if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", cleaned):
            raise ValueError("ticker must contain only letters, digits, periods, or hyphens")
        return cleaned

    @staticmethod
    def _parse_cutoff(as_of: str) -> datetime:
        value = str(as_of).strip()
        try:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                return datetime.combine(date.fromisoformat(value), datetime_time.max, tzinfo=timezone.utc)
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("as_of must be an ISO date or timestamp") from exc
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)

    def _resolve_listing(self, ticker: str) -> dict[str, Any] | None:
        # The exchange file contains the exchange; fall back to SEC's older
        # ticker map if it is temporarily unavailable or changes shape.
        try:
            exchange_payload = self._get_json(f"{SEC_WWW}/files/company_tickers_exchange.json")
            columns = exchange_payload.get("fields", [])
            for row in exchange_payload.get("data", []):
                record = dict(zip(columns, row, strict=False))
                if str(record.get("ticker", "")).upper() == ticker:
                    return {
                        "cik": record.get("cik"),
                        "name": record.get("name"),
                        "exchange": record.get("exchange"),
                    }
        except RuntimeError as exc:
            if self._http_status(exc) != 404:
                raise
        ticker_payload = self._get_json(f"{SEC_WWW}/files/company_tickers.json")
        values = ticker_payload.values() if isinstance(ticker_payload, dict) else []
        for record in values:
            if str(record.get("ticker", "")).upper() == ticker:
                return {"cik": record.get("cik_str"), "name": record.get("title")}
        return None

    def _get_json(self, url: str) -> dict[str, Any]:
        key_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
        latest_key = f"sec:latest:{key_hash}"
        if self.cache is not None:
            latest = self.cache.get_json(latest_key, max_age_seconds=24 * 60 * 60)
            if isinstance(latest, dict) and isinstance(latest.get("snapshot_key"), str):
                cached = self.cache.get_json(latest["snapshot_key"])
                if isinstance(cached, dict) and isinstance(cached.get("payload"), dict):
                    cached_retrieved_at = cached.get("retrieved_at")
                    if isinstance(cached_retrieved_at, str):
                        self._retrieved_at_by_url[url] = cached_retrieved_at
                    return cached["payload"]

        payload = self._request_json(url)
        retrieved_at = self._now_iso()
        self._retrieved_at_by_url[url] = retrieved_at
        if self.cache is not None:
            snapshot_key = f"sec:snapshot:{key_hash}:{retrieved_at}"
            record = {"retrieved_at": retrieved_at, "url": url, "payload": payload}
            digest = self.cache.put_json(snapshot_key, record)
            self.cache.put_json(
                latest_key,
                {"snapshot_key": snapshot_key, "digest": digest, "retrieved_at": retrieved_at, "url": url},
            )
        return payload

    def _request_json(self, url: str) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(3):
            self._respect_rate_limit()
            try:
                response = self.session.get(
                    url,
                    headers={
                        "User-Agent": self.user_agent,
                        "Accept": "application/json",
                        "Accept-Encoding": "gzip, deflate",
                    },
                    timeout=15,
                )
                status = getattr(response, "status_code", 200)
                if status in _TRANSIENT_STATUS:
                    raise _TransientSecError(f"SEC returned HTTP {status}")
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("SEC returned a non-object JSON payload")
                return payload
            except (requests.RequestException, _TransientSecError, ValueError) as exc:
                last_error = exc
                if attempt == 2 or not self._is_transient(exc):
                    break
                time.sleep(0.4 * (attempt + 1))
        raise RuntimeError(f"SEC request failed for {url}: {last_error}") from last_error

    @staticmethod
    def _http_status(exc: Exception) -> int | None:
        cause = exc.__cause__
        if isinstance(cause, requests.HTTPError) and cause.response is not None:
            return cause.response.status_code
        return None

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        if isinstance(exc, _TransientSecError):
            return True
        if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
            return True
        if isinstance(exc, requests.HTTPError):
            response = exc.response
            return response is not None and response.status_code in _TRANSIENT_STATUS
        return False

    def _respect_rate_limit(self) -> None:
        delay = 0.35 - (time.monotonic() - self._last_request_at)
        if delay > 0:
            time.sleep(delay)
        self._last_request_at = time.monotonic()

    @staticmethod
    def _identity_from_listing(ticker: str, cik: str, listing: dict[str, Any]) -> dict[str, Any]:
        identity = {"ticker": ticker, "cik": cik}
        for key in ("name", "exchange"):
            if listing.get(key):
                identity[key] = listing[key]
        return identity

    def _identity_from_submissions(
        self, ticker: str, cik: str, listing: dict[str, Any], submissions: dict[str, Any]
    ) -> dict[str, Any]:
        identity = self._identity_from_listing(ticker, cik, listing)
        identity["name"] = submissions.get("name") or identity.get("name")
        exchanges = submissions.get("exchanges") or []
        tickers = submissions.get("tickers") or []
        try:
            index = [str(item).upper() for item in tickers].index(ticker)
            if index < len(exchanges) and exchanges[index]:
                identity["exchange"] = exchanges[index]
        except ValueError:
            pass
        for key, source_key in (("fiscal_year_end", "fiscalYearEnd"), ("sic", "sic")):
            if submissions.get(source_key) not in (None, ""):
                identity[key] = str(submissions[source_key])
        # SEC company facts identifies USD concepts but not a universal reporting
        # currency field, so currency is intentionally omitted rather than guessed.
        return {key: value for key, value in identity.items() if value not in (None, "")}

    def _recent_filings(self, submissions: dict[str, Any]) -> list[dict[str, Any]]:
        recent = submissions.get("filings", {}).get("recent", {})
        if not isinstance(recent, dict):
            return []
        rows: list[dict[str, Any]] = []
        size = max((len(value) for value in recent.values() if isinstance(value, list)), default=0)
        for index in range(size):
            row = {key: values[index] for key, values in recent.items() if isinstance(values, list) and index < len(values)}
            accession = row.get("accessionNumber")
            if accession:
                row["accession"] = str(accession)
            row["form"] = str(row.get("form", ""))
            row["published_at"], row["timestamp_precision"] = self._filing_timestamp(row)
            rows.append(row)
        return rows

    @staticmethod
    def _filing_timestamp(row: dict[str, Any]) -> tuple[datetime | None, bool]:
        accepted = row.get("acceptanceDateTime")
        if accepted:
            value = str(accepted)
            try:
                if re.fullmatch(r"\d{14}", value):
                    return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc), False
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return (parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)), False
            except ValueError:
                pass
        filed = row.get("filingDate")
        if filed:
            try:
                return datetime.combine(date.fromisoformat(str(filed)), datetime_time.max, tzinfo=timezone.utc), True
            except ValueError:
                pass
        return None, True

    def _documents(self, cik: str, filings: list[dict[str, Any]], cutoff: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        documents: list[dict[str, Any]] = []
        issues: list[dict[str, Any]] = []
        for filing in filings:
            form = filing.get("form", "")
            published_at = filing.get("published_at")
            if form not in _DOCUMENT_FORMS or not published_at or published_at > cutoff:
                continue
            if form.endswith("/A"):
                continue
            accession = filing.get("accession")
            if not accession:
                continue
            document = {
                "document_id": f"sec:{accession}",
                "form": form,
                "published_at": self._iso(published_at),
                "source_url": self._filing_index_url(cik, accession),
                "accession": accession,
            }
            if filing.get("reportDate"):
                document["period_end"] = str(filing["reportDate"])
            if filing.get("primaryDocument"):
                document["title"] = str(filing["primaryDocument"])
            documents.append(document)
            if filing.get("timestamp_precision"):
                issues.append(self._issue("filing_timestamp_imprecise", f"{accession} uses filing-date end-of-day because acceptanceDateTime is absent", "warning"))
        return documents, issues

    def _facts(
        self,
        company_facts: dict[str, Any],
        filing_by_accession: dict[str, dict[str, Any]],
        cutoff: datetime,
        retrieved_at: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        output: list[dict[str, Any]] = []
        issues: list[dict[str, Any]] = []
        earliest_period = cutoff.date() - timedelta(days=365 * 5 + 2)
        seen: set[tuple[Any, ...]] = set()
        taxonomies = company_facts.get("facts", {})
        for metric, concepts in _METRICS.items():
            for taxonomy, concept, definition in concepts:
                concept_payload = taxonomies.get(taxonomy, {}).get(concept)
                if not isinstance(concept_payload, dict):
                    continue
                for unit, entries in concept_payload.get("units", {}).items():
                    if not isinstance(entries, list):
                        continue
                    for entry in entries:
                        form = str(entry.get("form", ""))
                        accession = str(entry.get("accn", ""))
                        filing = filing_by_accession.get(accession)
                        published_at, imprecise = self._fact_timestamp(entry, filing)
                        period_end = entry.get("end")
                        if (
                            form not in _ALLOWED_FACT_FORMS
                            or form.endswith("/A")
                            or not accession
                            or not published_at
                            or published_at > cutoff
                            or not period_end
                        ):
                            continue
                        try:
                            if date.fromisoformat(str(period_end)) < earliest_period:
                                continue
                        except ValueError:
                            continue
                        dedupe_key = (taxonomy, concept, unit, entry.get("start"), period_end, accession, entry.get("val"), form)
                        if dedupe_key in seen:
                            continue
                        seen.add(dedupe_key)
                        # The US-GAAP concept itself is an outflow payment.  It
                        # is standardized here so FCF derivation can safely use
                        # CFO minus capex without guessing a vendor sign.
                        adjustment_basis = (
                            "cash_outflow_positive"
                            if taxonomy == "us-gaap" and concept in {
                                "PaymentsToAcquirePropertyPlantAndEquipment",
                                "PaymentsToAcquireProductiveAssets",
                            }
                            else "as_reported"
                        )
                        fact = {
                            "fact_id": f"sec:{taxonomy}:{concept}:{accession}:{unit}:{entry.get('start') or 'instant'}:{period_end}",
                            "metric": metric,
                            "value": entry.get("val"),
                            "unit": unit,
                            "period_end": str(period_end),
                            "published_at": self._iso(published_at),
                            "retrieved_at": retrieved_at,
                            "source_url": self._filing_index_url(company_facts.get("cik", ""), accession),
                            "accession": accession,
                            "source_tag": f"{taxonomy}:{concept}",
                            "definition": concept_payload.get("label") or definition,
                            "adjustment_basis": adjustment_basis,
                            "kind": "reported",
                        }
                        if entry.get("start"):
                            fact["period_start"] = str(entry["start"])
                        output.append(fact)
                        if imprecise:
                            issues.append(self._issue("fact_timestamp_imprecise", f"{accession} uses filed-date end-of-day because acceptanceDateTime is unavailable", "warning", metric))
        output.sort(key=lambda item: (item["metric"], item["period_end"], item["published_at"], item["source_tag"]))
        return output, self._unique_issues(issues)

    def _fact_timestamp(self, entry: dict[str, Any], filing: dict[str, Any] | None) -> tuple[datetime | None, bool]:
        if filing and filing.get("published_at"):
            return filing["published_at"], bool(filing.get("timestamp_precision"))
        filed = entry.get("filed")
        if filed:
            try:
                return datetime.combine(date.fromisoformat(str(filed)), datetime_time.max, tzinfo=timezone.utc), True
            except ValueError:
                pass
        return None, True

    @staticmethod
    def _filing_index_url(cik: Any, accession: str) -> str:
        cik_number = str(cik).lstrip("0") or "0"
        compact_accession = accession.replace("-", "")
        return f"{SEC_WWW}/Archives/edgar/data/{cik_number}/{compact_accession}/{accession}-index.html"

    @staticmethod
    def _issue(code: str, message: str, severity: str, metric: str | None = None) -> dict[str, Any]:
        issue = {"code": code, "message": message, "severity": severity}
        if metric:
            issue["metric"] = metric
        return issue

    @staticmethod
    def _unique_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unique: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for issue in issues:
            key = (issue["code"], issue["message"], issue.get("metric"))
            if key not in seen:
                seen.add(key)
                unique.append(issue)
        return unique

    @staticmethod
    def _now_iso() -> str:
        return SecEdgarProvider._iso(datetime.now(timezone.utc))

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class _TransientSecError(RuntimeError):
    pass
