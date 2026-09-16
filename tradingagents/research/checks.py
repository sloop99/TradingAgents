"""Deterministic accounting, dimension, and coverage checks."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from numbers import Real

from .models import (
    BusinessModel,
    CoverageStatus,
    EvidenceFact,
    IssuerIdentity,
    IssueSeverity,
    ResearchIssue,
)

_METRIC_ALIASES = {
    "revenues": "revenue",
    "sales": "revenue",
    "sales_revenue": "revenue",
    "total_revenue": "revenue",
    "profit_loss": "net_income",
    "net_income_loss": "net_income",
    "net_cash_provided_by_operating_activities": "operating_cash_flow",
    "cash_from_operations": "operating_cash_flow",
    "cfo": "operating_cash_flow",
    "payments_to_acquire_property_plant_and_equipment": "capital_expenditures",
    "capex": "capital_expenditures",
    "stockholders_equity": "equity",
    "shareholders_equity": "equity",
    "common_stock_shares_outstanding": "shares_outstanding",
    "weighted_average_number_of_diluted_shares_outstanding": "weighted_average_shares",
    "weighted_average_number_of_shares_outstanding_basic": "weighted_average_shares",
    "cash_and_cash_equivalents_at_carrying_value": "cash",
}


def canonical_metric(metric: str) -> str:
    key = "_".join(metric.strip().casefold().replace("-", " ").split())
    return _METRIC_ALIASES.get(key, key)


def classify_business(identity: IssuerIdentity | None) -> tuple[BusinessModel, list[ResearchIssue]]:
    """Classify from SIC only; never infer a model from the ticker."""
    if identity is None or not identity.sic:
        supplied = identity.business_model.casefold() if identity and identity.business_model else None
        if supplied in {item.value for item in BusinessModel}:
            return BusinessModel(supplied), [
                ResearchIssue(
                    code="BUSINESS_CLASSIFICATION_LIMITED",
                    message=(
                        f"No SIC was available; the provider-supplied {supplied!r} business "
                        "model is provisional."
                    ),
                    severity=IssueSeverity.WARNING,
                )
            ]
        return BusinessModel.GENERAL, [
            ResearchIssue(
                code="BUSINESS_CLASSIFICATION_LIMITED",
                message="No SIC was available; general-company checks are a conservative fallback.",
                severity=IssueSeverity.WARNING,
            )
        ]
    try:
        sic = int(identity.sic)
    except ValueError:
        return BusinessModel.GENERAL, [
            ResearchIssue(
                code="BUSINESS_CLASSIFICATION_LIMITED",
                message=f"SIC {identity.sic!r} is not numeric; general-company checks were used.",
                severity=IssueSeverity.WARNING,
            )
        ]
    if 6000 <= sic <= 6199:
        model = BusinessModel.BANK
    elif sic == 6798:
        model = BusinessModel.REIT
    elif 5200 <= sic <= 5999:
        model = BusinessModel.RETAIL
    elif 7370 <= sic <= 7379:
        model = BusinessModel.SOFTWARE
    elif 2000 <= sic <= 3999:
        model = BusinessModel.INDUSTRIAL
    else:
        model = BusinessModel.GENERAL
    issues: list[ResearchIssue] = []
    if model is BusinessModel.BANK and sic >= 6100:
        issues.append(
            ResearchIssue(
                code="BUSINESS_CLASSIFICATION_LIMITED",
                message=(
                    f"SIC {sic} is in the broad credit-institution range; Phase 1 applies "
                    "bank checks, but the issuer may be a nonbank lender."
                ),
                severity=IssueSeverity.INFO,
            )
        )
    if model is BusinessModel.GENERAL:
        issues.append(
            ResearchIssue(
                code="BUSINESS_CLASSIFICATION_GENERAL",
                message=(
                    f"SIC {sic} does not map to a Phase 1 specialized model; "
                    "general-company checks were used."
                ),
                severity=IssueSeverity.INFO,
            )
        )
    return model, issues


@dataclass(frozen=True)
class CheckResult:
    business_model: BusinessModel
    facts: list[EvidenceFact]
    issues: list[ResearchIssue]
    coverage: dict[str, str]
    status: CoverageStatus


def run_checks(
    identity: IssuerIdentity | None,
    facts: Iterable[EvidenceFact],
    *,
    has_documents: bool = False,
    prior_issues: Iterable[ResearchIssue] = (),
) -> CheckResult:
    facts_list = list(facts)
    business_model, issues = classify_business(identity)
    issues.extend(_dimension_issues(facts_list))
    issues.extend(_competition_issues(facts_list))
    derived, derivation_issues = derive_free_cash_flow(facts_list)
    facts_list.extend(derived)
    issues.extend(derivation_issues)
    issues.extend(prior_issues)

    coverage = _coverage(identity, facts_list, business_model, has_documents)
    if any(
        issue.code in {"FACT_CONFLICT", "DEFINITION_CONFLICT", "UNIT_CONFLICT", "INVALID_PERIOD"}
        for issue in issues
    ):
        status = CoverageStatus.MATERIAL_CONFLICT
    elif (
        coverage["identity"] == CoverageStatus.UNSUPPORTED.value
        and not facts_list
        and not has_documents
    ):
        status = CoverageStatus.UNSUPPORTED
    else:
        required = _required_areas(business_model)
        status = (
            CoverageStatus.SUFFICIENT
            if all(coverage.get(area) == CoverageStatus.SUFFICIENT.value for area in required)
            else CoverageStatus.PARTIAL
        )
        if status is CoverageStatus.SUFFICIENT and any(
            issue.severity is IssueSeverity.ERROR for issue in issues
        ):
            status = CoverageStatus.PARTIAL
    coverage["valuation"] = CoverageStatus.UNSUPPORTED.value
    return CheckResult(
        business_model=business_model,
        facts=sorted(facts_list, key=_fact_sort_key),
        issues=_dedupe_issues(issues),
        coverage=dict(sorted(coverage.items())),
        status=status,
    )


def _dimension_issues(facts: list[EvidenceFact]) -> list[ResearchIssue]:
    issues: list[ResearchIssue] = []
    by_metric_period: dict[tuple[str, str | None, str], list[EvidenceFact]] = {}
    for fact in facts:
        if fact.period_start and date.fromisoformat(fact.period_start) > date.fromisoformat(fact.period_end):
            issues.append(
                ResearchIssue(
                    code="INVALID_PERIOD",
                    message=f"Fact {fact.fact_id} starts after it ends.",
                    severity=IssueSeverity.ERROR,
                    metric=fact.metric,
                )
            )
        by_metric_period.setdefault(
            (canonical_metric(fact.metric), fact.period_start, fact.period_end), []
        ).append(fact)
    for (metric, period_start, period_end), peers in by_metric_period.items():
        units = {fact.unit.casefold() for fact in peers}
        if len(units) > 1:
            period = f"{period_start or 'instant'} to {period_end}"
            issues.append(
                ResearchIssue(
                    code="UNIT_CONFLICT",
                    message=f"Competing {metric} facts for {period} use units {sorted(units)}.",
                    severity=IssueSeverity.ERROR,
                    metric=metric,
                )
            )
    return issues


def _competition_issues(facts: list[EvidenceFact]) -> list[ResearchIssue]:
    groups: dict[tuple[str, str | None, str, str], list[EvidenceFact]] = {}
    for fact in facts:
        # Exact start and end dates keep quarter-only and year-to-date values separate.
        groups.setdefault(
            (
                canonical_metric(fact.metric),
                fact.period_start,
                fact.period_end,
                fact.unit.casefold(),
            ),
            [],
        ).append(fact)
    issues: list[ResearchIssue] = []
    for (metric, start, end, unit), peers in groups.items():
        if len(peers) < 2:
            continue
        definitions = {" ".join(fact.definition.casefold().split()) for fact in peers}
        values = {_comparable_value(fact.value) for fact in peers}
        period = f"{start or 'instant'} to {end}"
        if len(definitions) > 1:
            issues.append(
                ResearchIssue(
                    code="DEFINITION_CONFLICT",
                    message=(
                        f"Competing {metric} facts for {period} ({unit}) use "
                        "different accounting definitions."
                    ),
                    severity=IssueSeverity.ERROR,
                    metric=metric,
                )
            )
        if len(values) > 1:
            issues.append(
                ResearchIssue(
                    code="FACT_CONFLICT",
                    message=(
                        f"Competing {metric} facts for {period} ({unit}) disagree: "
                        f"{', '.join(sorted(values))}."
                    ),
                    severity=IssueSeverity.ERROR,
                    metric=metric,
                )
            )
    return issues


def derive_free_cash_flow(
    facts: Iterable[EvidenceFact],
) -> tuple[list[EvidenceFact], list[ResearchIssue]]:
    """Derive CFO minus capex only when period, unit, and publication align.

    Capex signs vary across APIs.  A derivation is allowed only when the provider
    explicitly labels capex as an outflow represented by a positive magnitude.
    """
    facts_list = list(facts)
    cfo_facts = [fact for fact in facts_list if canonical_metric(fact.metric) == "operating_cash_flow"]
    capex_facts = [fact for fact in facts_list if canonical_metric(fact.metric) == "capital_expenditures"]
    existing_periods = {
        (fact.period_start, fact.period_end)
        for fact in facts_list
        if canonical_metric(fact.metric) == "free_cash_flow"
    }
    derived: list[EvidenceFact] = []
    issues: list[ResearchIssue] = []
    periods = {
        (fact.period_start, fact.period_end) for fact in cfo_facts + capex_facts
    }
    for period_start, period_end in periods:
        if (period_start, period_end) in existing_periods:
            continue
        period_cfo = [
            fact
            for fact in cfo_facts
            if (fact.period_start, fact.period_end) == (period_start, period_end)
        ]
        period_capex = [
            fact
            for fact in capex_facts
            if (fact.period_start, fact.period_end) == (period_start, period_end)
        ]
        if not period_cfo or not period_capex:
            continue
        if len(period_cfo) != 1 or len(period_capex) != 1:
            issues.append(
                ResearchIssue(
                    code="FCF_DERIVATION_WITHHELD",
                    message=(
                        f"Competing CFO or capex inputs exist for {period_start or 'instant'} "
                        f"to {period_end}; free cash flow was not calculated."
                    ),
                    severity=IssueSeverity.WARNING,
                    metric="free_cash_flow",
                )
            )
            continue
        cfo = period_cfo[0]
        capex = period_capex[0]
        if capex.unit.casefold() != cfo.unit.casefold() or capex.published_at != cfo.published_at:
            continue
        safe_basis = capex.adjustment_basis.casefold().replace("-", "_").replace(" ", "_")
        if safe_basis not in {"cash_outflow_positive", "outflow_positive", "positive_outflow"}:
            issues.append(
                ResearchIssue(
                    code="FCF_DERIVATION_WITHHELD",
                    message=(
                        "CFO and capex align, but capex sign convention is not explicitly "
                        "outflow-positive; free cash flow was not calculated."
                    ),
                    severity=IssueSeverity.WARNING,
                    metric="free_cash_flow",
                )
            )
            continue
        if (
            not isinstance(cfo.value, Real)
            or isinstance(cfo.value, bool)
            or not isinstance(capex.value, Real)
            or isinstance(capex.value, bool)
            or capex.value < 0
        ):
            issues.append(
                ResearchIssue(
                    code="FCF_DERIVATION_WITHHELD",
                    message="CFO/capex inputs are nonnumeric or conflict with the declared sign basis.",
                    severity=IssueSeverity.WARNING,
                    metric="free_cash_flow",
                )
            )
            continue
        derived.append(
            EvidenceFact.from_dict(
                {
                    "fact_id": f"calc:free_cash_flow:{cfo.fact_id}:{capex.fact_id}",
                    "metric": "free_cash_flow",
                    "value": cfo.value - capex.value,
                    "unit": cfo.unit,
                    "period_start": cfo.period_start,
                    "period_end": cfo.period_end,
                    "published_at": cfo.published_at,
                    "retrieved_at": max(cfo.retrieved_at, capex.retrieved_at),
                    "source_url": cfo.source_url,
                    "accession": cfo.accession if cfo.accession == capex.accession else None,
                    "source_tag": "research_engine",
                    "definition": "Operating cash flow minus outflow-positive capital expenditures.",
                    "adjustment_basis": "derived_from_reported_inputs",
                    "kind": "calculated",
                    "formula": "operating_cash_flow - capital_expenditures",
                    "input_fact_ids": [cfo.fact_id, capex.fact_id],
                }
            )
        )
    return derived, issues


def _required_areas(model: BusinessModel) -> tuple[str, ...]:
    if model is BusinessModel.BANK:
        return ("identity", "earnings", "financial_position", "capital")
    if model is BusinessModel.REIT:
        return ("identity", "earnings", "financial_position", "cash_flow", "shares")
    return ("identity", "earnings", "financial_position", "cash_flow", "shares")


def _coverage(
    identity: IssuerIdentity | None,
    facts: list[EvidenceFact],
    model: BusinessModel,
    has_documents: bool,
) -> dict[str, str]:
    metrics = {canonical_metric(fact.metric) for fact in facts}
    coverage = {
        "identity": _identity_coverage(identity),
        "filings": (
            CoverageStatus.SUFFICIENT.value if has_documents else CoverageStatus.UNSUPPORTED.value
        ),
    }
    if model is BusinessModel.BANK:
        coverage.update(
            {
                "earnings": _all_metrics(metrics, {"net_income"}, {"net_interest_income", "revenue"}),
                "financial_position": _all_metrics(metrics, {"total_assets"}, {"deposits", "total_liabilities"}),
                "capital": _all_metrics(metrics, {"equity"}),
                "cash_flow": CoverageStatus.UNSUPPORTED.value,
                "shares": _all_metrics(metrics, set(), {"shares_outstanding", "weighted_average_shares"}),
            }
        )
    else:
        earnings_any = {"funds_from_operations", "net_income"} if model is BusinessModel.REIT else {"net_income"}
        coverage.update(
            {
                "earnings": _all_metrics(metrics, {"revenue"}, earnings_any),
                "financial_position": _all_metrics(metrics, {"total_assets"}, {"equity", "total_liabilities"}),
                "cash_flow": _all_metrics(metrics, {"operating_cash_flow", "capital_expenditures"}),
                "shares": _all_metrics(metrics, set(), {"shares_outstanding", "weighted_average_shares"}),
            }
        )
    return coverage


def _identity_coverage(identity: IssuerIdentity | None) -> str:
    if identity is None:
        return CoverageStatus.UNSUPPORTED.value
    present = sum(bool(value) for value in (identity.name, identity.cik, identity.currency, identity.sic))
    if present == 0:
        return CoverageStatus.UNSUPPORTED.value
    return CoverageStatus.SUFFICIENT.value if present >= 3 else CoverageStatus.PARTIAL.value


def _all_metrics(metrics: set[str], required: set[str], alternatives: set[str] = frozenset()) -> str:
    required_ok = required.issubset(metrics)
    alternatives_ok = not alternatives or bool(metrics & alternatives)
    if required_ok and alternatives_ok:
        return CoverageStatus.SUFFICIENT.value
    if metrics & (required | alternatives):
        return CoverageStatus.PARTIAL.value
    return CoverageStatus.UNSUPPORTED.value


def _comparable_value(value: object) -> str:
    if isinstance(value, float):
        return format(value, ".12g")
    return repr(value)


def _fact_sort_key(fact: EvidenceFact) -> tuple[str, str, str, str]:
    return (canonical_metric(fact.metric), fact.period_end, fact.published_at or "", fact.fact_id)


def _dedupe_issues(issues: Iterable[ResearchIssue]) -> list[ResearchIssue]:
    seen: set[tuple[str, str, str, str | None]] = set()
    result: list[ResearchIssue] = []
    for issue in issues:
        key = (issue.code, issue.message, issue.severity.value, issue.metric)
        if key not in seen:
            seen.add(key)
            result.append(issue)
    return sorted(result, key=lambda issue: (issue.severity.value, issue.code, issue.metric or ""))
