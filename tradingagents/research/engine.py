"""Provider orchestration and point-in-time packet construction."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timezone
from typing import Any, Protocol

from .capitalization import analyze_capitalization
from .checks import classify_business, run_checks
from .financials import analyze_financials
from .models import (
    CoverageStatus,
    EvidenceDocument,
    EvidenceFact,
    IssuerIdentity,
    IssueSeverity,
    ResearchIssue,
    ResearchPacket,
)
from .reconciliation import reconcile_facts

_EXCHANGE_ALIASES = {
    "nasdaq": "Nasdaq",
    "nasdaqgs": "Nasdaq",
    "nasdaqgm": "Nasdaq",
    "nasdaqcm": "Nasdaq",
    "nms": "Nasdaq",
    "ngm": "Nasdaq",
    "ncm": "Nasdaq",
    "nyse": "NYSE",
    "nyq": "NYSE",
}


class ResearchProvider(Protocol):
    def fetch(self, ticker: str, as_of: str) -> dict[str, Any]: ...


def parse_as_of(value: str) -> datetime:
    """Parse an explicit point-in-time cutoff.

    A calendar date means the end of that day in UTC. A timestamp must carry a
    timezone and is respected exactly after conversion to UTC.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("as_of must be an ISO date or timezone-aware timestamp string")
    text = value.strip()
    try:
        if "T" not in text and " " not in text:
            return datetime.combine(date.fromisoformat(text), time.max, timezone.utc)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("as_of must be an ISO date or timezone-aware timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp as_of must include a timezone")
    return parsed.astimezone(timezone.utc)


def build_packet(
    ticker: str,
    as_of: str,
    providers: Iterable[ResearchProvider],
    horizon: str = "long_term",
    thesis: str | None = None,
) -> ResearchPacket:
    """Build one evidence packet without making any network calls itself."""
    normalized_ticker = str(ticker).strip().upper()
    if not normalized_ticker:
        raise ValueError("ticker is required")
    cutoff = parse_as_of(as_of)
    normalized_as_of = cutoff.isoformat().replace("+00:00", "Z")
    if not str(horizon).strip():
        raise ValueError("horizon is required")

    identities: list[IssuerIdentity] = []
    facts: list[EvidenceFact] = []
    documents: list[EvidenceDocument] = []
    issues: list[ResearchIssue] = []
    supplied_coverage: dict[str, list[str]] = {}
    provider_results: dict[str, str] = {}

    for index, provider in enumerate(providers):
        provider_name = _provider_name(provider, index)
        try:
            payload = provider.fetch(normalized_ticker, as_of)
            if not isinstance(payload, Mapping):
                raise TypeError("provider result must be a mapping")
        except Exception as exc:  # provider isolation is part of the public contract
            provider_results[provider_name] = "failed"
            issues.append(
                ResearchIssue(
                    code="PROVIDER_FAILURE",
                    message=f"Provider {provider_name} failed: {type(exc).__name__}: {exc}",
                    severity=IssueSeverity.WARNING,
                )
            )
            continue

        raw_identity = payload.get("identity")
        identity: IssuerIdentity | None = None
        if raw_identity is not None:
            try:
                identity = IssuerIdentity.from_dict(raw_identity)
            except (TypeError, ValueError) as exc:
                provider_results[provider_name] = "invalid"
                issues.append(_invalid_record_issue(provider_name, "identity", exc))
                continue
            if identity.ticker != normalized_ticker:
                provider_results[provider_name] = "identity_mismatch"
                issues.append(
                    ResearchIssue(
                        code="ISSUER_MISMATCH",
                        message=(
                            f"Provider {provider_name} returned ticker {identity.ticker} for "
                            f"requested ticker {normalized_ticker}; its payload was discarded."
                        ),
                        severity=IssueSeverity.ERROR,
                    )
                )
                continue
            identities.append(identity)

        accepted_count = 0
        for raw_fact in payload.get("facts", []) or []:
            try:
                fact = EvidenceFact.from_dict(raw_fact)
            except (TypeError, ValueError) as exc:
                issues.append(_invalid_record_issue(provider_name, "fact", exc))
                continue
            if fact.published_at is None:
                issues.append(
                    ResearchIssue(
                        code="PUBLICATION_TIME_UNKNOWN",
                        message=(
                            f"Fact {fact.fact_id} was excluded because its publication time is "
                            "unknown; fiscal period dates do not establish availability."
                        ),
                        severity=IssueSeverity.WARNING,
                        metric=fact.metric,
                    )
                )
                continue
            if _temporal_instant(fact.published_at) > cutoff:
                issues.append(
                    ResearchIssue(
                        code="FACT_AFTER_CUTOFF",
                        message=f"Fact {fact.fact_id} was published after the research cutoff.",
                        severity=IssueSeverity.INFO,
                        metric=fact.metric,
                    )
                )
                continue
            facts.append(fact)
            accepted_count += 1

        for raw_document in payload.get("documents", []) or []:
            try:
                document = EvidenceDocument.from_dict(raw_document)
            except (TypeError, ValueError) as exc:
                issues.append(_invalid_record_issue(provider_name, "document", exc))
                continue
            if _temporal_instant(document.published_at) > cutoff:
                issues.append(
                    ResearchIssue(
                        code="DOCUMENT_AFTER_CUTOFF",
                        message=(
                            f"Document {document.document_id} was published after the research cutoff."
                        ),
                        severity=IssueSeverity.INFO,
                    )
                )
                continue
            documents.append(document)
            accepted_count += 1

        for raw_issue in payload.get("issues", []) or []:
            try:
                issues.append(ResearchIssue.from_dict(raw_issue))
            except (TypeError, ValueError) as exc:
                issues.append(_invalid_record_issue(provider_name, "issue", exc))
        raw_coverage = payload.get("coverage", {}) or {}
        if not isinstance(raw_coverage, Mapping):
            issues.append(
                _invalid_record_issue(provider_name, "coverage", TypeError("must be a mapping"))
            )
        else:
            for key, value in raw_coverage.items():
                status = str(value).strip().lower()
                if status not in {item.value for item in CoverageStatus}:
                    issues.append(
                        _invalid_record_issue(
                            provider_name,
                            "coverage",
                            ValueError(f"{key} has unknown status {value!r}"),
                        )
                    )
                    continue
                supplied_coverage.setdefault(str(key), []).append(status)
        provider_results[provider_name] = "ok" if accepted_count or identity else "empty"

    identity, identity_issues = _merge_identities(normalized_ticker, identities)
    issues.extend(identity_issues)
    facts, fact_id_issues = _dedupe_facts(facts)
    issues.extend(fact_id_issues)
    documents = _dedupe_documents(documents)

    reconciled = reconcile_facts(facts)
    issues.extend(reconciled.issues)
    if any(issue.severity is IssueSeverity.ERROR and issue.metric in {
        "operating_cash_flow", "capital_expenditures"
    } for issue in reconciled.issues):
        issues.append(ResearchIssue(
            code="FCF_DERIVATION_WITHHELD",
            message="Conflicting CFO or capex inputs were excluded; affected periods cannot produce free cash flow.",
            severity=IssueSeverity.WARNING,
            metric="free_cash_flow",
        ))
    business_model, _ = classify_business(identity)
    financials = analyze_financials(reconciled.selected_facts, business_model)
    issues.extend(financials.issues)
    capitalization = analyze_capitalization(
        reconciled.selected_facts + financials.derived_facts, business_model, normalized_as_of
    )
    issues.extend(capitalization.issues)
    stale_inputs = financials.summary.get("stale_inputs", [])
    for stale in stale_inputs:
        issues.append(ResearchIssue(
            code="FINANCIAL_INPUT_STALE",
            message=(f"Latest {stale['metric']} ends {stale['latest_period_end']}, before the "
                     f"latest reporting window {stale['latest_reporting_end']}; no current value was inferred."),
            severity=IssueSeverity.WARNING,
            metric=stale["metric"],
        ))
    checked = run_checks(
        identity,
        reconciled.selected_facts,
        has_documents=bool(documents),
        prior_issues=issues,
        derive_metrics=False,
    )
    coverage = dict(checked.coverage)
    for area, statuses in supplied_coverage.items():
        provider_status = _combine_coverage(statuses)
        if area not in coverage or provider_status == CoverageStatus.MATERIAL_CONFLICT.value:
            coverage[area] = provider_status
    status = checked.status
    coverage["financial_calculations"] = "partial" if financials.derived_facts else "unsupported"
    coverage["capitalization"] = capitalization.summary["status"]
    if any(fact.metric in {"enterprise_value_to_revenue", "price_to_earnings", "price_to_free_cash_flow"}
           for fact in capitalization.derived_facts):
        coverage["valuation"] = "partial"
    if stale_inputs and status is CoverageStatus.SUFFICIENT:
        status = CoverageStatus.PARTIAL
    if any(item["metric"] in {"operating_cash_flow", "capital_expenditures"} for item in stale_inputs):
        coverage["cash_flow"] = CoverageStatus.PARTIAL.value
    if any(value == CoverageStatus.MATERIAL_CONFLICT.value for value in coverage.values()):
        status = CoverageStatus.MATERIAL_CONFLICT
    if any(
        issue.code in {"IDENTITY_CONFLICT", "FACT_ID_COLLISION"}
        for issue in checked.issues
    ):
        status = CoverageStatus.MATERIAL_CONFLICT

    return ResearchPacket(
        ticker=normalized_ticker,
        as_of=normalized_as_of,
        horizon=str(horizon).strip(),
        thesis=str(thesis).strip() if thesis is not None and str(thesis).strip() else None,
        status=status,
        business_model=checked.business_model,
        identity=identity,
        facts=facts + financials.derived_facts + capitalization.derived_facts,
        documents=sorted(
            documents,
            key=lambda item: (item.published_at, item.document_id),
        ),
        issues=checked.issues,
        coverage=dict(sorted(coverage.items())),
        provider_results=dict(sorted(provider_results.items())),
        financial_analysis={
            "selected_fact_ids": [fact.fact_id for fact in reconciled.selected_facts],
            "selected_inputs": [fact.to_dict() for fact in reconciled.selected_facts],
            "decisions": reconciled.decisions,
            "summary": financials.summary,
            "capitalization": capitalization.summary,
        },
    )


def _provider_name(provider: object, index: int) -> str:
    name = getattr(provider, "name", None)
    if name is None:
        name = provider.__class__.__name__
    name = str(name).strip() or f"provider_{index + 1}"
    return name if name not in {"object", "function"} else f"provider_{index + 1}"


def _invalid_record_issue(provider: str, record: str, exc: Exception) -> ResearchIssue:
    return ResearchIssue(
        code="INVALID_PROVIDER_RECORD",
        message=f"Provider {provider} returned an invalid {record}: {exc}",
        severity=IssueSeverity.WARNING,
    )


def _temporal_instant(value: str) -> datetime:
    if "T" not in value and " " not in value:
        return datetime.combine(date.fromisoformat(value), time.max, timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:  # guarded by model validation; fail closed if constructed manually
        raise ValueError("publication timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _merge_identities(
    ticker: str, identities: list[IssuerIdentity]
) -> tuple[IssuerIdentity | None, list[ResearchIssue]]:
    if not identities:
        return None, []
    merged: dict[str, str | None] = {"ticker": ticker}
    issues: list[ResearchIssue] = []
    for field in (
        "name",
        "cik",
        "exchange",
        "currency",
        "fiscal_year_end",
        "sic",
        "business_model",
    ):
        values = {
            _canonical_exchange(getattr(identity, field)) if field == "exchange" else getattr(identity, field)
            for identity in identities
            if getattr(identity, field)
        }
        if len(values) == 1:
            merged[field] = values.pop()
        elif len(values) > 1:
            merged[field] = None
            issues.append(
                ResearchIssue(
                    code="IDENTITY_CONFLICT",
                    message=f"Providers disagree on issuer {field}: {sorted(values)}.",
                    severity=IssueSeverity.ERROR,
                )
            )
        else:
            merged[field] = None
    return IssuerIdentity.from_dict(merged), issues


def _canonical_exchange(value: str) -> str:
    """Normalize only documented Yahoo/SEC exchange aliases used by this adapter."""
    label = str(value).strip()
    return _EXCHANGE_ALIASES.get(label.casefold(), label)


def _dedupe_facts(
    facts: list[EvidenceFact],
) -> tuple[list[EvidenceFact], list[ResearchIssue]]:
    by_id: dict[str, EvidenceFact] = {}
    blocked: set[str] = set()
    issues: list[ResearchIssue] = []
    for fact in facts:
        if fact.fact_id in blocked:
            continue
        previous = by_id.get(fact.fact_id)
        if previous is None:
            by_id[fact.fact_id] = fact
        elif previous.to_dict() != fact.to_dict():
            del by_id[fact.fact_id]
            blocked.add(fact.fact_id)
            issues.append(
                ResearchIssue(
                    code="FACT_ID_COLLISION",
                    message=(
                        f"Fact ID {fact.fact_id} was reused for different payloads; all versions "
                        "with that ID were excluded."
                    ),
                    severity=IssueSeverity.ERROR,
                    metric=fact.metric,
                )
            )
    return list(by_id.values()), issues


def _dedupe_documents(documents: list[EvidenceDocument]) -> list[EvidenceDocument]:
    by_id: dict[str, EvidenceDocument] = {}
    for document in documents:
        previous = by_id.get(document.document_id)
        if previous is None or previous.published_at < document.published_at:
            by_id[document.document_id] = document
    return list(by_id.values())


def _combine_coverage(statuses: list[str]) -> str:
    if CoverageStatus.MATERIAL_CONFLICT.value in statuses:
        return CoverageStatus.MATERIAL_CONFLICT.value
    for candidate in (
        CoverageStatus.SUFFICIENT.value,
        CoverageStatus.PARTIAL.value,
        CoverageStatus.UNSUPPORTED.value,
    ):
        if candidate in statuses:
            return candidate
    return CoverageStatus.UNSUPPORTED.value
