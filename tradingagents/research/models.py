"""Typed, serializable records for sourced equity research.

The records deliberately store source timestamps as normalized ISO strings.  That
keeps packets portable across CLI, checkpoint, and cache boundaries while the
engine remains responsible for point-in-time eligibility.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from enum import Enum
from math import isfinite
from numbers import Real
from typing import Any


class FactKind(str, Enum):
    REPORTED = "reported"
    CALCULATED = "calculated"
    ESTIMATED = "estimated"


class IssueSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class CoverageStatus(str, Enum):
    SUFFICIENT = "sufficient"
    PARTIAL = "partial"
    MATERIAL_CONFLICT = "material_conflict"
    UNSUPPORTED = "unsupported"


class BusinessModel(str, Enum):
    BANK = "bank"
    REIT = "reit"
    RETAIL = "retail"
    INDUSTRIAL = "industrial"
    SOFTWARE = "software"
    GENERAL = "general"


def _required_text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if value is None or not str(value).strip():
        raise ValueError(f"{key} is required")
    return str(value).strip()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _iso_date(value: Any, key: str, *, required: bool = False) -> str | None:
    text = _optional_text(value)
    if text is None:
        if required:
            raise ValueError(f"{key} is required")
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(f"{key} must be an ISO date") from exc


def _iso_temporal(value: Any, key: str, *, required: bool = False) -> str | None:
    text = _optional_text(value)
    if text is None:
        if required:
            raise ValueError(f"{key} is required")
        return None
    try:
        if "T" not in text and " " not in text:
            return date.fromisoformat(text).isoformat()
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.isoformat().replace("+00:00", "Z")
    except ValueError as exc:
        raise ValueError(f"{key} must be an ISO date or timezone-aware datetime") from exc


@dataclass(frozen=True)
class IssuerIdentity:
    ticker: str
    name: str | None = None
    cik: str | None = None
    exchange: str | None = None
    currency: str | None = None
    fiscal_year_end: str | None = None
    sic: str | None = None
    business_model: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> IssuerIdentity:
        return cls(
            ticker=_required_text(data, "ticker").upper(),
            name=_optional_text(data.get("name")),
            cik=_optional_text(data.get("cik")),
            exchange=_optional_text(data.get("exchange")),
            currency=_optional_text(data.get("currency")),
            fiscal_year_end=_optional_text(data.get("fiscal_year_end")),
            sic=_optional_text(data.get("sic")),
            business_model=_optional_text(data.get("business_model")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "name": self.name,
            "cik": self.cik,
            "exchange": self.exchange,
            "currency": self.currency,
            "fiscal_year_end": self.fiscal_year_end,
            "sic": self.sic,
            "business_model": self.business_model,
        }


@dataclass(frozen=True)
class EvidenceFact:
    fact_id: str
    metric: str
    value: Any
    unit: str
    period_end: str
    retrieved_at: str
    source_url: str
    definition: str
    kind: FactKind
    period_start: str | None = None
    published_at: str | None = None
    accession: str | None = None
    source_tag: str | None = None
    adjustment_basis: str = "unknown"
    formula: str | None = None
    input_fact_ids: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EvidenceFact:
        kind = FactKind(_required_text(data, "kind").lower())
        fact_id = _required_text(data, "fact_id")
        value = data.get("value")
        if not isinstance(value, Real) or isinstance(value, bool) or not isfinite(float(value)):
            raise ValueError(f"value for fact {fact_id!r} must be a finite number")
        source_url = _required_text(data, "source_url")
        period_end = _iso_date(data.get("period_end"), "period_end", required=True)
        assert period_end is not None
        retrieved_at = _iso_temporal(data.get("retrieved_at"), "retrieved_at", required=True)
        assert retrieved_at is not None
        formula = _optional_text(data.get("formula"))
        input_fact_ids = tuple(str(item).strip() for item in data.get("input_fact_ids", []) if str(item).strip())
        if kind is FactKind.CALCULATED and (not formula or not input_fact_ids):
            raise ValueError(
                f"calculated fact {fact_id!r} requires formula and input_fact_ids"
            )
        if kind is not FactKind.CALCULATED and input_fact_ids:
            raise ValueError(f"{kind.value} fact {fact_id!r} cannot claim calculated inputs")
        return cls(
            fact_id=fact_id,
            metric=_required_text(data, "metric"),
            value=value,
            unit=_required_text(data, "unit"),
            period_start=_iso_date(data.get("period_start"), "period_start"),
            period_end=period_end,
            published_at=_iso_temporal(data.get("published_at"), "published_at"),
            retrieved_at=retrieved_at,
            source_url=source_url,
            accession=_optional_text(data.get("accession")),
            source_tag=_optional_text(data.get("source_tag")),
            definition=_required_text(data, "definition"),
            adjustment_basis=_optional_text(data.get("adjustment_basis")) or "unknown",
            kind=kind,
            formula=formula,
            input_fact_ids=input_fact_ids,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "metric": self.metric,
            "value": self.value,
            "unit": self.unit,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "published_at": self.published_at,
            "retrieved_at": self.retrieved_at,
            "source_url": self.source_url,
            "accession": self.accession,
            "source_tag": self.source_tag,
            "definition": self.definition,
            "adjustment_basis": self.adjustment_basis,
            "kind": self.kind.value,
            "formula": self.formula,
            "input_fact_ids": list(self.input_fact_ids),
        }


@dataclass(frozen=True)
class EvidenceDocument:
    document_id: str
    form: str
    published_at: str
    source_url: str
    period_end: str | None = None
    accession: str | None = None
    title: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EvidenceDocument:
        published_at = _iso_temporal(data.get("published_at"), "published_at", required=True)
        assert published_at is not None
        return cls(
            document_id=_required_text(data, "document_id"),
            form=_required_text(data, "form"),
            period_end=_iso_date(data.get("period_end"), "period_end"),
            published_at=published_at,
            source_url=_required_text(data, "source_url"),
            accession=_optional_text(data.get("accession")),
            title=_optional_text(data.get("title")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "form": self.form,
            "period_end": self.period_end,
            "published_at": self.published_at,
            "source_url": self.source_url,
            "accession": self.accession,
            "title": self.title,
        }


@dataclass(frozen=True)
class ResearchIssue:
    code: str
    message: str
    severity: IssueSeverity
    metric: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ResearchIssue:
        return cls(
            code=_required_text(data, "code"),
            message=_required_text(data, "message"),
            severity=IssueSeverity(_required_text(data, "severity").lower()),
            metric=_optional_text(data.get("metric")),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "severity": self.severity.value,
        }
        if self.metric is not None:
            result["metric"] = self.metric
        return result


@dataclass
class ResearchPacket:
    ticker: str
    as_of: str
    horizon: str
    status: CoverageStatus
    business_model: BusinessModel
    identity: IssuerIdentity | None = None
    thesis: str | None = None
    facts: list[EvidenceFact] = field(default_factory=list)
    documents: list[EvidenceDocument] = field(default_factory=list)
    issues: list[ResearchIssue] = field(default_factory=list)
    coverage: dict[str, str] = field(default_factory=dict)
    provider_results: dict[str, str] = field(default_factory=dict)
    financial_analysis: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ResearchPacket:
        identity_data = data.get("identity")
        packet = cls(
            ticker=_required_text(data, "ticker").upper(),
            as_of=_required_text(data, "as_of"),
            horizon=_required_text(data, "horizon"),
            thesis=_optional_text(data.get("thesis")),
            status=CoverageStatus(_required_text(data, "status").lower()),
            business_model=BusinessModel(_required_text(data, "business_model").lower()),
            identity=IssuerIdentity.from_dict(identity_data) if identity_data else None,
            facts=[EvidenceFact.from_dict(item) for item in data.get("facts", [])],
            documents=[EvidenceDocument.from_dict(item) for item in data.get("documents", [])],
            issues=[ResearchIssue.from_dict(item) for item in data.get("issues", [])],
            coverage={str(k): str(v) for k, v in dict(data.get("coverage", {})).items()},
            provider_results={
                str(k): str(v) for k, v in dict(data.get("provider_results", {})).items()
            },
            financial_analysis=dict(data.get("financial_analysis", {})),
        )
        if packet.identity is not None and packet.identity.ticker != packet.ticker:
            raise ValueError("packet identity ticker does not match packet ticker")
        cutoff = _cutoff_instant(packet.as_of)
        fact_ids = [fact.fact_id for fact in packet.facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("packet contains duplicate fact IDs")
        document_ids = [document.document_id for document in packet.documents]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("packet contains duplicate document IDs")
        known_fact_ids = set(fact_ids)
        selected_ids = set(packet.financial_analysis.get("selected_fact_ids", []))
        if selected_ids - known_fact_ids:
            raise ValueError("Financial analysis references missing selected facts")
        for fact in packet.facts:
            if fact.published_at is None:
                raise ValueError(
                    f"packet fact {fact.fact_id!r} has unknown publication timing"
                )
            if _temporal_instant(fact.published_at) > cutoff:
                raise ValueError(f"packet fact {fact.fact_id!r} is after the packet cutoff")
            if fact.kind is FactKind.CALCULATED:
                missing = set(fact.input_fact_ids) - known_fact_ids
                if missing:
                    raise ValueError(
                        f"calculated fact {fact.fact_id!r} references missing inputs {sorted(missing)}"
                    )
                if fact.fact_id in fact.input_fact_ids:
                    raise ValueError(f"calculated fact {fact.fact_id!r} references itself")
        for document in packet.documents:
            if _temporal_instant(document.published_at) > cutoff:
                raise ValueError(
                    f"packet document {document.document_id!r} is after the packet cutoff"
                )
        if "selected_inputs" in packet.financial_analysis:
            from .reconciliation import normalize_concept

            original_by_id = {fact.fact_id: fact for fact in packet.facts}
            selected_inputs = [EvidenceFact.from_dict(item)
                               for item in packet.financial_analysis["selected_inputs"]]
            input_ids = [fact.fact_id for fact in selected_inputs]
            if len(input_ids) != len(set(input_ids)) or set(input_ids) != selected_ids:
                raise ValueError("Selected input views do not match the selected fact IDs")
            for selected in selected_inputs:
                original = original_by_id[selected.fact_id]
                # Older packets can retain the exact original provider view.
                # Neither path permits altered values, dates, or sources.
                if selected not in (original, normalize_concept(original)):
                    raise ValueError("Selected input view differs from its original source fact")
        return packet

    def to_dict(self) -> dict[str, Any]:
        """Return a stable JSON-compatible representation."""
        return {
            "ticker": self.ticker,
            "as_of": self.as_of,
            "horizon": self.horizon,
            "thesis": self.thesis,
            "status": self.status.value,
            "business_model": self.business_model.value,
            "identity": self.identity.to_dict() if self.identity else None,
            "facts": [fact.to_dict() for fact in self.facts],
            "documents": [document.to_dict() for document in self.documents],
            "issues": [issue.to_dict() for issue in self.issues],
            "coverage": dict(sorted(self.coverage.items())),
            "provider_results": dict(sorted(self.provider_results.items())),
            "financial_analysis": self.financial_analysis,
        }

    def render_context(self, max_facts: int = 40) -> str:
        """Render bounded, source-linked context suitable for downstream agents."""
        max_facts = max(0, max_facts)
        active_ids = self.financial_analysis.get("selected_fact_ids")
        eligible = self.facts
        if active_ids is not None:
            active_ids = set(active_ids)
            eligible = [fact for fact in self.facts if fact.fact_id in active_ids or fact.kind is FactKind.CALCULATED]
            normalized = {item["fact_id"]: EvidenceFact.from_dict(item)
                          for item in self.financial_analysis.get("selected_inputs", [])}
            eligible = [normalized.get(fact.fact_id, fact) for fact in eligible]
        selected = _representative_facts(eligible, max_facts)
        rendered = self._render_context_with_facts(selected, len(self.facts))
        while selected and len(rendered) > 8000:
            selected.pop()
            rendered = self._render_context_with_facts(selected, len(self.facts))
        if len(rendered) <= 8000:
            return rendered
        # Very long provider issue messages are clipped individually while every
        # material issue code remains visible.
        return rendered[:7997] + "..."

    def _render_context_with_facts(
        self, selected: list[EvidenceFact], total_fact_count: int
    ) -> str:
        identity = self.identity
        lines = [
            f"# Evidence packet: {self.ticker}",
            f"As of: {self.as_of}",
            f"Evidence status: {self.status.value}",
            f"Business model: {self.business_model.value}",
            "Coverage: "
            + ", ".join(f"{key}={value}" for key, value in sorted(self.coverage.items())),
        ]
        if identity:
            lines.append(
                "Identity: "
                + "; ".join(
                    part
                    for part in (
                        identity.name,
                        f"CIK {identity.cik}" if identity.cik else None,
                        identity.exchange,
                        identity.currency,
                        f"SIC {identity.sic}" if identity.sic else None,
                    )
                    if part
                )
            )
        if self.thesis:
            lines.append(f"User thesis (unverified): {self.thesis}")
        if self.financial_analysis:
            lines.append("Financial inputs reconciled by concept and period; superseded reported facts remain in the full packet. Calculations do not establish valuation readiness.")
        filing_count = sum(f.kind is FactKind.REPORTED and f.metric.startswith("filing_") for f in self.facts)
        if filing_count:
            lines.append(f"Inline filing candidates: {filing_count}; retained for context review, excluded from consolidated calculations.")
        filing_review = self.financial_analysis.get("filing_reconciliation", {})
        if filing_review.get("status") not in (None, "unsupported"):
            lines.append(f"Filing reconciliation: {filing_review['status']}; scoped subtotals do not establish total debt or complete share-class coverage.")
        capitalization = self.financial_analysis.get("capitalization", {})
        if capitalization:
            missing = ", ".join(capitalization.get("missing_metrics", [])) or "none"
            lines.append(f"Capitalization status: {capitalization.get('status', 'unsupported')}; withheld metrics: {missing}. Vendor market cap is not independently verified.")
        lines.extend(["", "## Eligible facts"])
        for fact in selected:
            period = fact.period_end
            if fact.period_start:
                period = f"{fact.period_start} to {period}"
            lines.append(
                f"- [F:{fact.fact_id}] {fact.metric} = {fact.value} {fact.unit}; "
                f"period {period}; published {fact.published_at}; {fact.kind.value}; "
                f"definition: {fact.definition}; source: {fact.source_url}"
            )
        if total_fact_count > len(selected):
            lines.append(f"- {total_fact_count - len(selected)} additional facts retained in packet.")
        lines.extend(["", "## Material limits and issues"])
        material = [issue for issue in self.issues if issue.severity is not IssueSeverity.INFO]
        grouped = {}
        for issue in material:
            grouped.setdefault((issue.severity, issue.code), []).append(issue)
        material = [items[0] for items in grouped.values()]
        issue_budget = max(80, min(500, 5000 // max(1, len(material))))
        for issue in material:
            metric = f" ({issue.metric})" if issue.metric else ""
            message = issue.message
            count = len(grouped[(issue.severity, issue.code)])
            if count > 1:
                message = f"{count} occurrences; example: {message}"
            if len(message) > issue_budget:
                message = message[: issue_budget - 3] + "..."
            lines.append(f"- [{issue.severity.value.upper()}:{issue.code}]{metric} {message}")
        if not material:
            lines.append("- No material issue recorded by the evidence checks.")
        lines.append(
            "- The evidence layer does not produce a valuation target or investment rating; "
            "evidence coverage is independent of BUY/HOLD/SELL judgments."
        )
        return "\n".join(lines)

    def to_markdown(self) -> str:
        """Render the complete packet, including every retained fact and source."""
        lines = [
            f"# Research evidence: {self.ticker}",
            "",
            f"- Cutoff: {self.as_of}",
            f"- Horizon: {self.horizon}",
            f"- Evidence status: **{self.status.value}**",
            f"- Business model: {self.business_model.value}",
            "- Investment rating: not produced by the evidence layer",
            "",
            "## Coverage",
            "",
            "| Area | Status |",
            "|---|---|",
        ]
        lines.extend(f"| {key} | {value} |" for key, value in sorted(self.coverage.items()))
        if self.financial_analysis:
            lines.extend(["", "## Financial reconciliation", "",
                          f"Selected input facts: {len(self.financial_analysis.get('selected_fact_ids', []))}. All source versions remain below.",
                          "Calculated metrics are not a target price or investment rating.", "",
                          "| Calculated metric | Value | Unit | Period | Fact ID |",
                          "|---|---:|---|---|---|"])
            calculated = [fact for fact in self.facts if fact.kind is FactKind.CALCULATED]
            latest = {}
            for fact in sorted(calculated, key=lambda item: (item.period_end, item.period_start or "")):
                latest[fact.metric] = fact
            for metric, fact in sorted(latest.items()):
                lines.append(f"| {metric} | {fact.value:,.4f} | {fact.unit} | {fact.period_start or 'instant'} to {fact.period_end} | {fact.fact_id} |")
        capitalization = self.financial_analysis.get("capitalization", {})
        if capitalization:
            lines.extend(["", "## Capitalization readiness", "",
                          f"Status: **{capitalization.get('status', 'unsupported')}**.",
                          "Vendor market cap and shares remain observations until independently reconciled.",
                          "Withheld metrics: " + (", ".join(capitalization.get("missing_metrics", [])) or "none") + ".", ""])
            for requirement, satisfied in capitalization.get("market_cap_prerequisites", {}).items():
                lines.append(f"- {requirement}: {'supported' if satisfied else 'unresolved'}")
        filing_candidates = [fact for fact in self.facts
                             if fact.kind is FactKind.REPORTED and fact.metric.startswith("filing_")]
        if filing_candidates:
            lines.extend(["", "## Filing context review", "",
                          f"{len(filing_candidates)} inline filing candidates retained with source anchors and context definitions.",
                          "These may represent individual share classes, debt instruments or other dimensions; they are excluded from consolidated calculations pending reconciliation."])
        filing_review = self.financial_analysis.get("filing_reconciliation", {})
        if filing_review.get("status") not in (None, "unsupported"):
            counts = filing_review.get("counts", {})
            lines.extend(["", "### Scoped reconciliation", "",
                          f"Status: **{filing_review['status']}**. Supported equalities: {len(filing_review.get('supported_equalities', []))}; discrepancies: {len(filing_review.get('discrepancies', []))}.",
                          "Long-term debt subtotals cover only the identified current and noncurrent components; they are not complete total debt.",
                          "A matching sum of observed share classes does not establish complete class coverage, ADR conversion or split history.",
                          f"Validated source bindings: {counts.get('validated_bindings', 0)}; rejected bindings: {counts.get('invalid_bindings', 0)}."])
        lines.extend(["", "## Facts", ""])
        if not self.facts:
            lines.append("No eligible facts were retained.")
        for fact in self.facts:
            lines.extend(
                [
                    f"### {fact.metric} `[F:{fact.fact_id}]`",
                    "",
                    f"- Value: {fact.value} {fact.unit}",
                    f"- Period: {fact.period_start or 'instant'} to {fact.period_end}",
                    f"- Published: {fact.published_at}",
                    f"- Definition: {fact.definition}",
                    f"- Basis/kind: {fact.adjustment_basis}; {fact.kind.value}",
                    f"- Source: {fact.source_url}",
                ]
            )
            if fact.accession:
                lines.append(f"- Accession: {fact.accession}")
            if fact.formula:
                lines.append(
                    f"- Formula: `{fact.formula}` using {', '.join(fact.input_fact_ids)}"
                )
            lines.append("")
        lines.extend(["## Documents", ""])
        if not self.documents:
            lines.append("No eligible documents were retained.")
        for document in self.documents:
            lines.append(
                f"- [D:{document.document_id}] {document.form}, published "
                f"{document.published_at}: {document.source_url}"
            )
        lines.extend(["", "## Issues", ""])
        if not self.issues:
            lines.append("No issues recorded.")
        for issue in self.issues:
            metric = f" ({issue.metric})" if issue.metric else ""
            lines.append(f"- **{issue.severity.value} / {issue.code}**{metric}: {issue.message}")
        return "\n".join(lines).rstrip() + "\n"


def _representative_facts(facts: list[EvidenceFact], limit: int) -> list[EvidenceFact]:
    if limit <= 0:
        return []
    # Prefer recent facts while retaining breadth across metrics. A second pass fills
    # remaining capacity with additional annual/interim versions.
    ordered = sorted(
        facts,
        key=lambda fact: (fact.period_end, fact.published_at or "", fact.fact_id),
        reverse=True,
    )
    chosen: list[EvidenceFact] = []
    seen_metrics: set[str] = set()
    for fact in ordered:
        key = fact.metric.casefold()
        if key not in seen_metrics:
            chosen.append(fact)
            seen_metrics.add(key)
        if len(chosen) == limit:
            return chosen
    for fact in ordered:
        if fact not in chosen:
            chosen.append(fact)
        if len(chosen) == limit:
            break
    return chosen


def _cutoff_instant(value: str) -> datetime:
    text = value.strip()
    try:
        if "T" not in text and " " not in text:
            return datetime.combine(date.fromisoformat(text), time.max, timezone.utc)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("packet as_of must be an ISO date or timezone-aware timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("packet timestamp as_of must include a timezone")
    return parsed.astimezone(timezone.utc)


def _temporal_instant(value: str) -> datetime:
    if "T" not in value and " " not in value:
        return datetime.combine(date.fromisoformat(value), time.max, timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("publication timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


# Short aliases make provider implementations readable without weakening the API.
Fact = EvidenceFact
Document = EvidenceDocument
Issue = ResearchIssue
