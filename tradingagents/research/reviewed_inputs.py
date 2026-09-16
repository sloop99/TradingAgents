"""Deterministic analyst-reviewed promotions of existing reported evidence.

Review rules can copy or sum retained quantities, but cannot introduce numeric
constants or turn an assertion into machine-proven class, ADR, or split scope.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Any

from .models import EvidenceFact, IssueSeverity, ResearchIssue

_SCHEMA_VERSION = 1
_OUTPUTS = {"current_shares", "total_debt"}
_SHARE_ATTESTATIONS = {"point_in_time_common_shares", "raw_as_reported_basis"}
_DEBT_ATTESTATIONS = {"complete_interest_bearing_debt"}
_DISJOINT = "disjoint_scopes"

_SHARE_CONCEPTS = {
    "dei:EntityCommonStockSharesOutstanding",
    "us-gaap:CommonStockSharesOutstanding",
}
_DEBT_CONCEPTS = {
    "us-gaap:ShortTermBorrowings",
    "us-gaap:ShortTermDebtCurrent",
    "us-gaap:DebtCurrent",
    "us-gaap:CommercialPaper",
    "us-gaap:LongTermDebt",
    "us-gaap:LongTermDebtCurrent",
    "us-gaap:LongTermDebtNoncurrent",
    "us-gaap:LongTermDebtAndFinanceLeaseObligations",
    "us-gaap:LongTermDebtAndFinanceLeaseObligationsCurrent",
    "us-gaap:LongTermDebtAndFinanceLeaseObligationsNoncurrent",
    "us-gaap:FinanceLeaseLiability",
    "us-gaap:FinanceLeaseLiabilityCurrent",
    "us-gaap:FinanceLeaseLiabilityNoncurrent",
    "ifrs-full:BorrowingsCurrent",
    "ifrs-full:BorrowingsNoncurrent",
}


@dataclass(frozen=True)
class ReviewedInputsResult:
    derived_facts: list[EvidenceFact]
    issues: list[ResearchIssue]
    summary: dict[str, Any]


def evidence_sha256(facts: list[EvidenceFact]) -> str:
    """Hash exact original reported evidence, independent of input ordering."""

    payload = [
        fact.to_dict()
        for fact in sorted(_reported(facts), key=lambda item: item.fact_id)
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(encoded.encode("utf-8")).hexdigest()


def apply_reviewed_inputs(
    facts: list[EvidenceFact],
    manifest: dict[str, Any] | None,
    ticker: str,
    as_of: str,
) -> ReviewedInputsResult:
    """Apply valid reviewed copy/sum rules while preserving full lineage."""

    base_hash = evidence_sha256(facts)
    if manifest is None:
        return ReviewedInputsResult([], [], _summary("not_provided", ticker, as_of, base_hash))

    issues: list[ResearchIssue] = []
    if (
        not isinstance(manifest, dict)
        or manifest.get("draft_only") is True
        or str(manifest.get("status") or "").strip().casefold() in {"pending", "draft"}
    ):
        issues.append(_issue("REVIEW_MANIFEST_NOT_APPLICABLE", "A pending draft is not an applicable review manifest.", IssueSeverity.WARNING))
        return ReviewedInputsResult([], issues, _summary("rejected", ticker, as_of, base_hash))

    cutoff = _parse_cutoff(as_of)
    reported = _reported(facts)
    by_id = {fact.fact_id: fact for fact in reported}
    manifest_errors = _validate_manifest_binding(manifest, ticker, as_of, base_hash, reported, cutoff)
    issues.extend(manifest_errors)
    if manifest_errors:
        summary = _summary("rejected", ticker, as_of, base_hash)
        summary["rule_decisions"] = []
        return ReviewedInputsResult([], _dedupe_issues(issues), summary)

    reviewer = manifest["reviewer"]
    reviewed_at = str(reviewer["reviewed_at"]).strip()
    rules = manifest.get("rules", [])
    reused_ids, reused_urls = _reused_sources(rules, by_id)
    rule_id_counts = Counter(
        str(rule.get("rule_id") or "").strip()
        for rule in rules if isinstance(rule, dict)
    )
    output_counts = Counter(
        str(rule.get("output_metric") or "").strip()
        for rule in rules if isinstance(rule, dict)
    )
    derived: list[EvidenceFact] = []
    decisions: list[dict[str, Any]] = []
    seen_outputs: set[str] = set()
    for raw_rule in rules:
        rule_id = str(raw_rule.get("rule_id") or "").strip() if isinstance(raw_rule, dict) else ""
        output = str(raw_rule.get("output_metric") or "").strip() if isinstance(raw_rule, dict) else ""
        inputs = [str(value).strip() for value in raw_rule.get("input_fact_ids", [])] if isinstance(raw_rule, dict) and isinstance(raw_rule.get("input_fact_ids"), list) else []
        operation = str(raw_rule.get("operation") or "").strip() if isinstance(raw_rule, dict) else ""
        rationale = str(raw_rule.get("rationale") or "").strip() if isinstance(raw_rule, dict) else ""
        attestations = sorted(
            str(value).strip()
            for value in raw_rule.get("attestations", [])
            if str(value).strip()
        ) if isinstance(raw_rule, dict) and isinstance(raw_rule.get("attestations"), list) else []
        reasons = _validate_rule(raw_rule, output, inputs, by_id, reviewed_at)
        if rule_id and rule_id_counts[rule_id] > 1:
            reasons.append("duplicate rule_id makes every matching rule ambiguous")
        if output and output_counts[output] > 1:
            reasons.append("duplicate output_metric makes every matching rule ambiguous")
        if output in seen_outputs:
            reasons.append("output_metric was already applied")
        if set(inputs) & reused_ids:
            reasons.append("an input fact is reused across review rules")
        if any(by_id.get(fact_id) and by_id[fact_id].source_url in reused_urls for fact_id in inputs):
            reasons.append("an evidence source node is reused across review rules")
        if reasons:
            issues.append(_issue(
                "REVIEW_RULE_REJECTED",
                f"Review rule {rule_id or '<missing>'} was rejected: {'; '.join(sorted(set(reasons)))}.",
                IssueSeverity.WARNING,
                output or None,
            ))
            decisions.append({
                "rule_id": rule_id,
                "output_metric": output,
                "operation": operation,
                "input_fact_ids": inputs,
                "attestations": attestations,
                "rationale": rationale,
                "status": "rejected",
                "reasons": sorted(set(reasons)),
            })
            continue
        selected = [by_id[fact_id] for fact_id in inputs]
        promoted = _promote(raw_rule, selected, reviewer, base_hash)
        derived.append(promoted)
        seen_outputs.add(output)
        decisions.append({
            "rule_id": rule_id,
            "output_metric": output,
            "operation": operation,
            "status": "applied",
            "input_fact_ids": inputs,
            "attestations": attestations,
            "rationale": rationale,
            "derived_fact_id": promoted.fact_id,
        })

    applied = sum(decision["status"] == "applied" for decision in decisions)
    rejected = sum(decision["status"] == "rejected" for decision in decisions)
    status = "applied" if applied and not rejected else ("partial" if applied else "rejected")
    summary = _summary(status, ticker, as_of, base_hash)
    summary.update({
        "reviewer": {
            "name": reviewer["name"],
            "reviewed_at": reviewed_at,
            "rationale": reviewer["rationale"],
        },
        "rule_decisions": decisions,
        "applied_metrics": sorted(fact.metric for fact in derived),
        "unresolved_proofs": ["share_class_completeness", "adr_ratio", "split_history"],
    })
    return ReviewedInputsResult(
        sorted(derived, key=lambda fact: (fact.metric, fact.fact_id)),
        _dedupe_issues(issues),
        summary,
    )


def draft_review_manifest(
    facts: list[EvidenceFact], ticker: str, as_of: str
) -> dict[str, Any]:
    """Return a bound pending-review draft; it never contains applied rules."""

    reported = _reported(facts)
    share_candidates = [fact for fact in reported if _share_source_allowed(fact)]
    debt_candidates = [fact for fact in reported if _debt_source_allowed(fact)]
    return {
        "schema_version": _SCHEMA_VERSION,
        "draft_only": True,
        "status": "pending",
        "ticker": ticker.strip().upper(),
        "as_of": as_of,
        "base_evidence_sha256": evidence_sha256(facts),
        "reviewer": {"name": "", "reviewed_at": "", "rationale": ""},
        "rules": [],
        "candidates": {
            "current_shares": [_candidate_summary(fact) for fact in share_candidates],
            "total_debt": [_candidate_summary(fact) for fact in debt_candidates],
        },
        "required_attestations": {
            "current_shares": sorted(_SHARE_ATTESTATIONS),
            "total_debt": sorted(_DEBT_ATTESTATIONS),
            "sum_operation_additional": [_DISJOINT],
        },
        "unresolved_gaps": [
            "Analyst review and rationale are required; no candidate is selected automatically.",
            "Current-share promotion does not prove all-class coverage, ADR conversion, or split history.",
            "Missing debt or share quantities are never filled with zero.",
        ],
    }


def _validate_manifest_binding(
    manifest: dict[str, Any],
    ticker: str,
    as_of: str,
    base_hash: str,
    reported: list[EvidenceFact],
    cutoff: datetime,
) -> list[ResearchIssue]:
    problems: list[str] = []
    if manifest.get("schema_version") != _SCHEMA_VERSION:
        problems.append("schema_version must be 1")
    if str(manifest.get("ticker") or "").strip().upper() != ticker.strip().upper():
        problems.append("ticker binding does not match")
    if str(manifest.get("as_of") or "").strip() != as_of:
        problems.append("as_of binding does not match exactly")
    if str(manifest.get("base_evidence_sha256") or "").strip() != base_hash:
        problems.append("base evidence SHA256 does not match")
    reviewer = manifest.get("reviewer")
    if not isinstance(reviewer, dict):
        problems.append("reviewer is required")
    else:
        for field in ("name", "reviewed_at", "rationale"):
            if not str(reviewer.get(field) or "").strip():
                problems.append(f"reviewer.{field} is required")
        reviewed_at = str(reviewer.get("reviewed_at") or "").strip()
        if reviewed_at:
            try:
                review_time = _parse_cutoff(reviewed_at)
            except ValueError:
                problems.append("reviewer.reviewed_at is invalid")
            else:
                if review_time > cutoff:
                    problems.append("reviewer.reviewed_at is after as_of")
                evidence_times = [
                    _temporal(value)
                    for fact in reported
                    for value in (fact.published_at, fact.retrieved_at)
                    if value
                ]
                if evidence_times and review_time < max(evidence_times):
                    problems.append("reviewer.reviewed_at predates base evidence availability")
    rules = manifest.get("rules")
    if not isinstance(rules, list) or not rules:
        problems.append("at least one review rule is required")
    if not problems:
        return []
    return [_issue(
        "REVIEW_MANIFEST_REJECTED",
        f"Review manifest was rejected: {'; '.join(sorted(set(problems)))}.",
        IssueSeverity.ERROR,
    )]


def _validate_rule(
    rule: Any,
    output: str,
    inputs: list[str],
    by_id: dict[str, EvidenceFact],
    reviewed_at: str,
) -> list[str]:
    if not isinstance(rule, dict):
        return ["rule must be an object"]
    reasons: list[str] = []
    rule_id = str(rule.get("rule_id") or "").strip()
    operation = str(rule.get("operation") or "").strip()
    rationale = str(rule.get("rationale") or "").strip()
    attestations = {
        str(value).strip()
        for value in rule.get("attestations", [])
        if str(value).strip()
    } if isinstance(rule.get("attestations"), list) else set()
    if not rule_id:
        reasons.append("rule_id is required")
    if output not in _OUTPUTS:
        reasons.append("output_metric is not allowed")
    if operation not in {"copy", "sum"}:
        reasons.append("operation must be copy or sum")
    if operation == "copy" and len(inputs) != 1:
        reasons.append("copy requires exactly one input")
    if operation == "sum" and len(inputs) < 2:
        reasons.append("sum requires at least two inputs")
    if len(inputs) != len(set(inputs)):
        reasons.append("input_fact_ids must be unique")
    if not rationale:
        reasons.append("rule rationale is required")
    missing = [fact_id for fact_id in inputs if fact_id not in by_id]
    if missing:
        reasons.append("every input must reference existing original reported evidence")
        return reasons
    selected = [by_id[fact_id] for fact_id in inputs]
    if operation == "sum":
        if len({(fact.unit, fact.period_start, fact.period_end) for fact in selected}) != 1:
            reasons.append("summed inputs must have identical units and periods")
        if _DISJOINT not in attestations:
            reasons.append("sum requires disjoint_scopes attestation")
    required = _SHARE_ATTESTATIONS if output == "current_shares" else _DEBT_ATTESTATIONS
    if not required <= attestations:
        reasons.append(f"missing attestations: {', '.join(sorted(required - attestations))}")
    review_time = _parse_cutoff(reviewed_at)
    if any(
        (fact.published_at and _temporal(fact.published_at) > review_time)
        or _temporal(fact.retrieved_at) > review_time
        for fact in selected
    ):
        reasons.append("review predates an input's evidence availability")
    if output == "current_shares":
        if operation != "copy":
            reasons.append("current_shares initially supports copy only")
        if any(not _share_source_allowed(fact) for fact in selected):
            reasons.append("current_shares inputs must be standard point-in-time common-share facts")
        if any(float(fact.value) <= 0 for fact in selected):
            reasons.append("current_shares inputs must be positive")
    elif output == "total_debt":
        if any(not _debt_source_allowed(fact) for fact in selected):
            reasons.append("total_debt inputs must be allowlisted standard debt or finance-lease concepts")
        if any(float(fact.value) < 0 for fact in selected):
            reasons.append("total_debt inputs must be nonnegative")
        if len({_debt_scope(fact) for fact in selected}) != len(selected):
            reasons.append("duplicate conceptual debt scopes are not disjoint")
        if _known_debt_overlap(selected):
            reasons.append("known parent/component debt scopes overlap")
    return reasons


def _promote(
    rule: dict[str, Any],
    inputs: list[EvidenceFact],
    reviewer: dict[str, Any],
    base_hash: str,
) -> EvidenceFact:
    output = str(rule["output_metric"])
    operation = str(rule["operation"])
    if operation == "copy":
        value = inputs[0].value
    elif all(isinstance(fact.value, int) and not isinstance(fact.value, bool) for fact in inputs):
        value = sum(fact.value for fact in inputs)
    else:
        value = float(sum((Decimal(str(fact.value)) for fact in inputs), Decimal(0)))
    reviewed_at = str(reviewer["reviewed_at"]).strip()
    rule_material = json.dumps(rule, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = sha256(f"{base_hash}|{rule_material}".encode()).hexdigest()[:16]
    source = max(inputs, key=lambda fact: (_temporal(fact.published_at or fact.retrieved_at), fact.fact_id))
    return EvidenceFact.from_dict({
        "fact_id": f"calc:reviewed-input:{output}:{inputs[0].period_end}:{digest}",
        "metric": output,
        "value": value,
        "unit": inputs[0].unit,
        "period_start": inputs[0].period_start,
        "period_end": inputs[0].period_end,
        "published_at": reviewed_at,
        "retrieved_at": reviewed_at,
        "source_url": source.source_url,
        "accession": source.accession,
        "source_tag": f"reviewed-input:{rule['rule_id']}",
        "definition": _reviewed_definition(output, operation, reviewer, rule),
        "adjustment_basis": "as_reported" if output == "current_shares" else "reviewed_analyst_assertion",
        "kind": "calculated",
        "formula": f"reviewed_{operation}({', '.join(fact.fact_id for fact in inputs)})",
        "input_fact_ids": [fact.fact_id for fact in inputs],
    })


def _share_source_allowed(fact: EvidenceFact) -> bool:
    tag = fact.source_tag or ""
    return (
        tag in _SHARE_CONCEPTS
        and fact.unit.casefold() == "shares"
        and fact.period_start is None
        and "weighted_average" not in fact.metric.casefold()
        and "WeightedAverage" not in tag
    )


def _debt_source_allowed(fact: EvidenceFact) -> bool:
    return (
        (fact.source_tag or "") in _DEBT_CONCEPTS
        and bool(re.fullmatch(r"[A-Z]{3}", fact.unit))
        and fact.period_start is None
    )


def _debt_scope(fact: EvidenceFact) -> str:
    return (fact.source_tag or "").split(":", 1)[-1]


def _known_debt_overlap(facts: list[EvidenceFact]) -> bool:
    scopes = {_debt_scope(fact) for fact in facts}
    parent_components = (
        ({"LongTermDebt"}, {"LongTermDebtCurrent", "LongTermDebtNoncurrent"}),
        ({"LongTermDebtAndFinanceLeaseObligations"}, {
            "LongTermDebtAndFinanceLeaseObligationsCurrent",
            "LongTermDebtAndFinanceLeaseObligationsNoncurrent",
            "LongTermDebt", "LongTermDebtCurrent", "LongTermDebtNoncurrent",
            "FinanceLeaseLiability", "FinanceLeaseLiabilityCurrent", "FinanceLeaseLiabilityNoncurrent",
        }),
        ({"FinanceLeaseLiability"}, {"FinanceLeaseLiabilityCurrent", "FinanceLeaseLiabilityNoncurrent"}),
        ({"DebtCurrent"}, {"ShortTermBorrowings", "ShortTermDebtCurrent", "CommercialPaper", "LongTermDebtCurrent"}),
        ({"LongTermDebtAndFinanceLeaseObligationsCurrent"}, {
            "LongTermDebtCurrent", "FinanceLeaseLiabilityCurrent",
        }),
        ({"LongTermDebtAndFinanceLeaseObligationsNoncurrent"}, {
            "LongTermDebtNoncurrent", "FinanceLeaseLiabilityNoncurrent",
        }),
        ({"ShortTermBorrowings"}, {"CommercialPaper"}),
    )
    return any(bool(scopes & parents) and bool(scopes & components) for parents, components in parent_components)


def _reviewed_definition(
    output: str,
    operation: str,
    reviewer: dict[str, Any],
    rule: dict[str, Any],
) -> str:
    scope = (
        "The reviewer attests this is complete interest-bearing debt including all obligations. "
        if output == "total_debt"
        else "The reviewer attests this is a raw point-in-time common-share quantity; class, ADR, and split proofs remain separate. "
    )
    return (
        f"Reviewed analyst assertion by {reviewer['name']}; {operation} promotion to {output}. "
        f"{scope}Rule rationale: {rule['rationale']} Review rationale: {reviewer['rationale']}"
    )


def _reused_sources(
    rules: Any, by_id: dict[str, EvidenceFact]
) -> tuple[set[str], set[str]]:
    if not isinstance(rules, list):
        return set(), set()
    ids: list[str] = []
    urls: list[str] = []
    for rule in rules:
        if not isinstance(rule, dict) or not isinstance(rule.get("input_fact_ids"), list):
            continue
        for value in rule["input_fact_ids"]:
            fact_id = str(value).strip()
            ids.append(fact_id)
            if fact_id in by_id:
                urls.append(by_id[fact_id].source_url)
    return (
        {value for value, count in Counter(ids).items() if count > 1},
        {value for value, count in Counter(urls).items() if count > 1},
    )


def _reported(facts: list[EvidenceFact]) -> list[EvidenceFact]:
    return [fact for fact in facts if fact.kind.value == "reported"]


def _candidate_summary(fact: EvidenceFact) -> dict[str, Any]:
    return {
        "fact_id": fact.fact_id,
        "metric": fact.metric,
        "source_tag": fact.source_tag,
        "value": fact.value,
        "unit": fact.unit,
        "period_start": fact.period_start,
        "period_end": fact.period_end,
    }


def _summary(status: str, ticker: str, as_of: str, base_hash: str) -> dict[str, Any]:
    return {
        "status": status,
        "ticker": ticker.strip().upper(),
        "as_of": as_of,
        "base_evidence_sha256": base_hash,
        "rule_decisions": [],
        "applied_metrics": [],
        "assertion_notice": "Reviewed promotions are analyst assertions, not machine-proven accounting scope.",
        "unresolved_proofs": ["share_class_completeness", "adr_ratio", "split_history"],
    }


def _parse_cutoff(value: str) -> datetime:
    if "T" not in value and " " not in value:
        return datetime.combine(date.fromisoformat(value), time.max, timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _temporal(value: str) -> datetime:
    return _parse_cutoff(value)


def _issue(code: str, message: str, severity: IssueSeverity, metric: str | None = None) -> ResearchIssue:
    return ResearchIssue(code=code, message=message, severity=severity, metric=metric)


def _dedupe_issues(issues: list[ResearchIssue]) -> list[ResearchIssue]:
    unique = {(issue.code, issue.message, issue.severity.value, issue.metric): issue for issue in issues}
    return sorted(unique.values(), key=lambda issue: (issue.severity.value, issue.code, issue.metric or "", issue.message))
