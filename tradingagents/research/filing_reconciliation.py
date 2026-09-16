"""Context-aware reconciliation for retained Inline XBRL filing candidates.

This layer consumes parser metadata; it never reparses filing prose or HTML.
Its outputs remain explicitly filing-scoped and cannot establish current shares,
complete debt, class coverage, ADR conversion, or split history.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any

from .models import EvidenceFact, IssueSeverity, ResearchIssue

_CLASS_AXIS = "StatementClassOfStockAxis"
_SHARE_CONCEPTS = {
    "dei:EntityCommonStockSharesOutstanding",
    "us-gaap:CommonStockSharesOutstanding",
}
_DEBT_CURRENT = "us-gaap:LongTermDebtCurrent"
_DEBT_NONCURRENT = "us-gaap:LongTermDebtNoncurrent"
_DEBT_PARENT = "us-gaap:LongTermDebt"


@dataclass(frozen=True)
class FilingContextResult:
    derived_facts: list[EvidenceFact]
    issues: list[ResearchIssue]
    summary: dict[str, Any]


@dataclass(frozen=True)
class _Binding:
    fact: EvidenceFact
    candidate: dict[str, Any]
    accession: str
    concept: str
    dimensions: tuple[tuple[str, str], ...]


def analyze_filing_contexts(
    facts: list[EvidenceFact], filing_metadata: list[dict], as_of: str
) -> FilingContextResult:
    """Validate parser bindings, reconcile safe contexts, and derive one subtotal."""

    cutoff = _parse_cutoff(as_of)
    issues: list[ResearchIssue] = []
    excluded: set[str] = set()
    eligible: dict[str, EvidenceFact] = {}
    duplicate_fact_ids: set[str] = set()
    duplicate_input_nodes = 0
    scoped_facts = [
        fact for fact in facts
        if fact.kind.value == "reported" and fact.metric.casefold().startswith("filing_")
    ]
    for fact in scoped_facts:
        if fact.fact_id in eligible:
            if fact.to_dict() == eligible[fact.fact_id].to_dict():
                duplicate_input_nodes += 1
            else:
                duplicate_fact_ids.add(fact.fact_id)
                excluded.add(fact.fact_id)
            continue
        if _after_cutoff(fact, cutoff):
            excluded.add(fact.fact_id)
            issues.append(_issue(
                "FILING_FACT_AFTER_CUTOFF",
                f"Fact {fact.fact_id} was excluded because its period or evidence timestamp is after the as-of cutoff.",
                IssueSeverity.WARNING,
                fact.metric,
            ))
            continue
        eligible[fact.fact_id] = fact
    for fact_id in duplicate_fact_ids:
        eligible.pop(fact_id, None)
        issues.append(_issue(
            "FILING_FACT_ID_CONFLICT",
            f"Duplicate fact_id {fact_id} was excluded from filing reconciliation.",
            IssueSeverity.ERROR,
        ))

    candidates_by_fact: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    input_fact_ids = {fact.fact_id for fact in scoped_facts}
    orphan_candidates = 0
    for filing in filing_metadata:
        if not isinstance(filing, dict):
            continue
        published = _optional_text(filing.get("published_at"))
        retrieved = _optional_text(filing.get("retrieved_at"))
        try:
            published_time = _temporal(published) if published else None
            retrieved_time = _temporal(retrieved) if retrieved else None
        except (TypeError, ValueError):
            published_time = retrieved_time = None
        if published_time is None or retrieved_time is None:
            issues.append(_issue(
                "FILING_METADATA_TIMESTAMP_INVALID",
                "A filing metadata item was excluded because publication and retrieval timestamps must both be valid.",
                IssueSeverity.WARNING,
            ))
            continue
        if published_time > cutoff or retrieved_time > cutoff:
            issues.append(_issue(
                "FILING_METADATA_AFTER_CUTOFF",
                "A filing metadata item was excluded in full because its publication or retrieval timestamp is after the as-of cutoff.",
                IssueSeverity.WARNING,
            ))
            continue
        candidates = filing.get("candidates", [])
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if not isinstance(candidate, dict) or candidate.get("status") != "accepted":
                continue
            fact_id = str(candidate.get("fact_id") or "").strip()
            if fact_id:
                candidates_by_fact[fact_id].append((filing, candidate))
                if fact_id not in input_fact_ids:
                    orphan_candidates += 1
                    issues.append(_issue(
                        "FILING_METADATA_ORPHAN",
                        f"Accepted parser candidate {fact_id} has no retained EvidenceFact and was excluded.",
                        IssueSeverity.WARNING,
                        str(candidate.get("metric") or "") or None,
                    ))

    bindings: list[_Binding] = []
    invalid_bindings = 0
    for fact_id, fact in eligible.items():
        pairs = candidates_by_fact.get(fact_id, [])
        valid: list[_Binding] = []
        for filing, candidate in pairs:
            binding = _validated_binding(fact, filing, candidate)
            if binding is None:
                invalid_bindings += 1
            else:
                valid.append(binding)
        unique = {_binding_signature(item): item for item in valid}
        if len(unique) != 1 or len(valid) != len(pairs):
            excluded.add(fact_id)
            invalid_bindings += 1
            issues.append(_issue(
                "FILING_METADATA_BINDING_INVALID",
                f"Fact {fact_id} has no unique metadata binding matching concept, value, unit, period, source URL, and accession.",
                IssueSeverity.WARNING,
                fact.metric,
            ))
            continue
        bindings.append(next(iter(unique.values())))

    grouped: dict[tuple[Any, ...], list[_Binding]] = defaultdict(list)
    for binding in bindings:
        fact = binding.fact
        grouped[(
            binding.accession,
            fact.period_start,
            fact.period_end,
            fact.unit.casefold(),
            fact.metric.casefold(),
            binding.concept,
            binding.dimensions,
        )].append(binding)

    reconciled: list[_Binding] = []
    deduplicated = duplicate_input_nodes
    context_conflicts = 0
    for key, peers in sorted(grouped.items(), key=lambda item: repr(item[0])):
        values = {_decimal(item.fact.value) for item in peers}
        if len(values) != 1:
            context_conflicts += 1
            excluded.update(item.fact.fact_id for item in peers)
            issues.append(_issue(
                "FILING_CONTEXT_VALUE_CONFLICT",
                f"Unequal values were reported for the same filing context: {key[0]} {key[2]} {key[4]}.",
                IssueSeverity.ERROR,
                peers[0].fact.metric,
            ))
            continue
        ordered = sorted(peers, key=lambda item: (item.fact.fact_id, str(item.candidate.get("source_node_id") or "")))
        reconciled.append(ordered[0])
        deduplicated += len(ordered) - 1

    # A parser metric label is not part of XBRL context identity.  Exclude a
    # concept/context that acquired more than one label instead of allowing a
    # later concept-keyed dictionary to choose one by iteration order.
    by_semantic_context: dict[tuple[Any, ...], list[_Binding]] = defaultdict(list)
    for binding in reconciled:
        fact = binding.fact
        by_semantic_context[(
            binding.accession, fact.period_start, fact.period_end,
            fact.unit.casefold(), binding.concept, binding.dimensions,
        )].append(binding)
    ambiguous_ids: set[str] = set()
    for key, peers in by_semantic_context.items():
        if len({item.fact.metric.casefold() for item in peers}) > 1:
            context_conflicts += 1
            ambiguous_ids.update(item.fact.fact_id for item in peers)
            excluded.update(item.fact.fact_id for item in peers)
            issues.append(_issue(
                "FILING_CONCEPT_METRIC_AMBIGUITY",
                f"Concept {key[4]} has multiple metric labels in the same filing context and was excluded.",
                IssueSeverity.ERROR,
            ))
    if ambiguous_ids:
        reconciled = [item for item in reconciled if item.fact.fact_id not in ambiguous_ids]

    equalities: list[dict[str, Any]] = []
    discrepancies: list[dict[str, Any]] = []
    share_groups = _share_groups(reconciled, equalities, discrepancies, issues)
    debt_groups, derived = _debt_groups(reconciled, equalities, discrepancies, issues)

    if context_conflicts or duplicate_fact_ids:
        status = "material_conflict"
    elif reconciled:
        status = "partial"
    else:
        status = "unsupported"
    summary: dict[str, Any] = {
        "status": status,
        "as_of": cutoff.isoformat().replace("+00:00", "Z"),
        "counts": {
            "input_facts": len(scoped_facts),
            "validated_bindings": len(bindings),
            "reconciled_contexts": len(reconciled),
            "invalid_bindings": invalid_bindings + orphan_candidates,
            "deduplicated_nodes": deduplicated,
            "context_conflicts": context_conflicts,
        },
        "share_groups": share_groups,
        "debt_groups": debt_groups,
        "supported_equalities": equalities,
        "discrepancies": discrepancies,
        "unresolved_proofs": [
            "share_class_completeness",
            "adr_ratio",
            "split_history",
            "complete_interest_bearing_debt",
        ],
        "excluded_fact_ids": sorted(excluded),
    }
    return FilingContextResult(
        derived_facts=sorted(derived, key=lambda fact: (fact.period_end, fact.fact_id)),
        issues=_dedupe_issues(issues),
        summary=summary,
    )


def _validated_binding(
    fact: EvidenceFact, filing: dict[str, Any], candidate: dict[str, Any]
) -> _Binding | None:
    dimensions = candidate.get("dimensions")
    if not isinstance(dimensions, dict) or not all(
        isinstance(key, str) and isinstance(value, str) and key and value
        for key, value in dimensions.items()
    ):
        return None
    accession = str(candidate.get("accession") or filing.get("accession") or "").strip()
    concept = str(candidate.get("concept") or "").strip()
    if not accession or not concept or fact.accession != accession:
        return None
    if _optional_text(filing.get("published_at")) != fact.published_at:
        return None
    if _optional_text(filing.get("retrieved_at")) != fact.retrieved_at:
        return None
    context_id = str(candidate.get("context_id") or "").strip()
    contexts = filing.get("contexts")
    context = contexts.get(context_id) if isinstance(contexts, dict) else None
    if not isinstance(context, dict) or context.get("safe") is not True:
        return None
    if context.get("dimensions") != dimensions:
        return None
    if _optional_text(context.get("period_start")) != fact.period_start:
        return None
    if str(context.get("period_end") or "").strip() != fact.period_end:
        return None
    if not _valid_decimals(candidate.get("decimals")):
        return None
    try:
        candidate_value = _decimal(candidate.get("value"))
        fact_value = _decimal(fact.value)
    except (InvalidOperation, TypeError, ValueError):
        return None
    checks = (
        str(candidate.get("metric") or "").strip() == fact.metric,
        concept == (fact.source_tag or ""),
        candidate_value == fact_value,
        str(candidate.get("unit") or "").strip().casefold() == fact.unit.casefold(),
        _optional_text(candidate.get("period_start")) == fact.period_start,
        str(candidate.get("period_end") or "").strip() == fact.period_end,
        str(candidate.get("source_url") or "").strip() == fact.source_url,
    )
    if not all(checks):
        return None
    return _Binding(
        fact=fact,
        candidate=candidate,
        accession=accession,
        concept=concept,
        dimensions=tuple(sorted((str(key), str(value)) for key, value in dimensions.items())),
    )


def _share_groups(
    bindings: list[_Binding],
    equalities: list[dict[str, Any]],
    discrepancies: list[dict[str, Any]],
    issues: list[ResearchIssue],
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], list[_Binding]] = defaultdict(list)
    for binding in bindings:
        if (
            binding.concept in _SHARE_CONCEPTS
            and binding.fact.unit.casefold() == "shares"
            and binding.fact.period_start is None
        ):
            groups[(binding.accession, binding.fact.period_end, binding.fact.unit, binding.concept)].append(binding)
    result: list[dict[str, Any]] = []
    for key, peers in sorted(groups.items()):
        totals = [item for item in peers if not item.dimensions]
        members = [item for item in peers if _is_class_member(item.dimensions)]
        record: dict[str, Any] = {
            "accession": key[0], "period_end": key[1], "unit": key[2], "concept": key[3],
            "undimensioned_fact_ids": [item.fact.fact_id for item in totals],
            "class_members": [
                {"member": dict(item.dimensions)[next(iter(dict(item.dimensions)))], "fact_id": item.fact.fact_id, "value": item.fact.value}
                for item in members
            ],
            "comparison": "not_comparable",
            "class_completeness_proven": False,
        }
        if len(totals) == 1 and members:
            total_value = _decimal(totals[0].fact.value)
            class_sum = sum((_decimal(item.fact.value) for item in members), Decimal(0))
            tolerance = _rounding_tolerance(totals + members)
            difference = total_value - class_sum
            record.update(class_sum=float(class_sum), difference=float(difference))
            comparison = {
                "type": "share_class_sum",
                "accession": key[0], "period_end": key[1], "unit": key[2], "concept": key[3],
                "total_fact_id": totals[0].fact.fact_id,
                "class_fact_ids": [item.fact.fact_id for item in members],
                "difference": float(difference),
            }
            if abs(difference) <= tolerance:
                record["comparison"] = "equal_within_reported_precision"
                equalities.append(comparison)
            else:
                record["comparison"] = "discrepancy"
                discrepancies.append(comparison)
                issues.append(_issue(
                    "FILING_SHARE_CLASS_DISCREPANCY",
                    f"Class-member shares do not equal the comparable undimensioned total for {key[0]} at {key[1]}; class completeness remains unresolved.",
                    IssueSeverity.WARNING,
                    totals[0].fact.metric,
                ))
        result.append(record)
    return result


def _debt_groups(
    bindings: list[_Binding],
    equalities: list[dict[str, Any]],
    discrepancies: list[dict[str, Any]],
    issues: list[ResearchIssue],
) -> tuple[list[dict[str, Any]], list[EvidenceFact]]:
    recognized = {_DEBT_CURRENT, _DEBT_NONCURRENT, _DEBT_PARENT}
    groups: dict[tuple[str, str, str], list[_Binding]] = defaultdict(list)
    for binding in bindings:
        valid_debt = (
            binding.concept in recognized
            and not binding.dimensions
            and binding.fact.period_start is None
            and bool(re.fullmatch(r"[A-Z]{3}", binding.fact.unit))
            and _decimal(binding.fact.value) >= 0
        )
        if valid_debt:
            groups[(binding.accession, binding.fact.period_end, binding.fact.unit)].append(binding)
        elif binding.concept in recognized:
            issues.append(_issue(
                "FILING_DEBT_COMPONENT_INVALID",
                f"Debt candidate {binding.fact.fact_id} must be an undimensioned, nonnegative instant in a three-letter currency.",
                IssueSeverity.WARNING,
                binding.fact.metric,
            ))
    summaries: list[dict[str, Any]] = []
    derived: list[EvidenceFact] = []
    for key, peers in sorted(groups.items()):
        by_concept = {item.concept: item for item in peers}
        record: dict[str, Any] = {
            "accession": key[0], "period_end": key[1], "unit": key[2],
            "recognized_fact_ids": {concept: item.fact.fact_id for concept, item in sorted(by_concept.items())},
            "subtotal_fact_id": None,
            "parent_comparison": "not_available",
            "reported_long_term_debt_scope": "unresolved_issuer_specific",
            "complete_debt_proven": False,
        }
        current = by_concept.get(_DEBT_CURRENT)
        noncurrent = by_concept.get(_DEBT_NONCURRENT)
        if current and noncurrent:
            subtotal = _calculated_subtotal(current.fact, noncurrent.fact)
            derived.append(subtotal)
            record["subtotal_fact_id"] = subtotal.fact_id
            parent = by_concept.get(_DEBT_PARENT)
            if parent:
                difference = _decimal(parent.fact.value) - _decimal(subtotal.value)
                comparison = {
                    "type": "long_term_debt_parent_subtotal",
                    "accession": key[0], "period_end": key[1], "unit": key[2],
                    "parent_fact_id": parent.fact.fact_id,
                    "subtotal_fact_id": subtotal.fact_id,
                    "difference": float(difference),
                    "scope_note": "LongTermDebt has unresolved issuer-specific scope; this is an arithmetic comparison only.",
                }
                if abs(difference) <= _rounding_tolerance([parent, current, noncurrent]):
                    record["parent_comparison"] = "equal_within_reported_precision"
                    equalities.append(comparison)
                else:
                    record["parent_comparison"] = "discrepancy"
                    discrepancies.append(comparison)
                    issues.append(_issue(
                        "FILING_DEBT_PARENT_DISCREPANCY",
                        f"LongTermDebt differs from the current plus noncurrent filing subtotal for {key[0]} at {key[1]}; the difference may reflect definition or scope and is not by itself an accounting inconsistency.",
                        IssueSeverity.WARNING,
                        "filing_long_term_debt_subtotal",
                    ))
        summaries.append(record)
    return summaries, derived


def _calculated_subtotal(current: EvidenceFact, noncurrent: EvidenceFact) -> EvidenceFact:
    parents = [current, noncurrent]
    identity = "|".join([current.fact_id, noncurrent.fact_id, current.period_end, current.unit])
    source = max(parents, key=lambda fact: (fact.published_at or "", fact.retrieved_at, fact.fact_id))
    return EvidenceFact.from_dict({
        "fact_id": f"calc:filing-context:filing_long_term_debt_subtotal:{current.period_end}:{sha256(identity.encode()).hexdigest()[:16]}",
        "metric": "filing_long_term_debt_subtotal",
        "value": float(_decimal(current.value) + _decimal(noncurrent.value)),
        "unit": current.unit,
        "period_end": current.period_end,
        "published_at": _max_temporal(parents, "published_at"),
        "retrieved_at": _max_temporal(parents, "retrieved_at"),
        "source_url": source.source_url,
        "accession": current.accession,
        "source_tag": "research_engine:filing_context",
        "definition": "Filing-scoped LongTermDebtCurrent plus LongTermDebtNoncurrent; not complete total debt.",
        "adjustment_basis": "filing_context_reconciled_subtotal",
        "kind": "calculated",
        "formula": "filing_long_term_debt_current + filing_long_term_debt_noncurrent",
        "input_fact_ids": [current.fact_id, noncurrent.fact_id],
    })


def _binding_signature(binding: _Binding) -> str:
    return json.dumps({
        "fact_id": binding.fact.fact_id,
        "accession": binding.accession,
        "concept": binding.concept,
        "dimensions": binding.dimensions,
        "context_id": binding.candidate.get("context_id"),
        "source_node_id": binding.candidate.get("source_node_id"),
        "source_url": binding.candidate.get("source_url"),
    }, sort_keys=True, default=str)


def _is_class_member(dimensions: tuple[tuple[str, str], ...]) -> bool:
    return len(dimensions) == 1 and dimensions[0][0] == f"us-gaap:{_CLASS_AXIS}"


def _rounding_tolerance(bindings: list[_Binding]) -> Decimal:
    tolerance = Decimal(0)
    for binding in bindings:
        value = str(binding.candidate.get("decimals") or "").strip()
        try:
            decimals = int(value)
        except ValueError:
            continue
        tolerance += Decimal("0.5") * (Decimal(10) ** (-decimals))
    return tolerance


def _valid_decimals(value: Any) -> bool:
    text = str(value or "").strip()
    if not text or text.casefold() == "inf":
        return True
    try:
        decimals = int(text)
    except ValueError:
        return False
    return -18 <= decimals <= 18


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_cutoff(value: str) -> datetime:
    if "T" not in value and " " not in value:
        return datetime.combine(date.fromisoformat(value), time.max, timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("as_of datetime must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _temporal(value: str) -> datetime:
    if "T" not in value and " " not in value:
        return datetime.combine(date.fromisoformat(value), time.max, timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _after_cutoff(fact: EvidenceFact, cutoff: datetime) -> bool:
    return (
        date.fromisoformat(fact.period_end) > cutoff.date()
        or bool(fact.published_at and _temporal(fact.published_at) > cutoff)
        or _temporal(fact.retrieved_at) > cutoff
    )


def _max_temporal(facts: list[EvidenceFact], attribute: str) -> str | None:
    values = [getattr(fact, attribute) for fact in facts if getattr(fact, attribute)]
    return max(values, key=_temporal) if values else None


def _issue(code: str, message: str, severity: IssueSeverity, metric: str | None = None) -> ResearchIssue:
    return ResearchIssue(code=code, message=message, severity=severity, metric=metric)


def _dedupe_issues(issues: list[ResearchIssue]) -> list[ResearchIssue]:
    unique = {(issue.code, issue.message, issue.severity.value, issue.metric): issue for issue in issues}
    return sorted(unique.values(), key=lambda issue: (issue.severity.value, issue.code, issue.metric or "", issue.message))
