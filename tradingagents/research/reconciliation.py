"""Conservative selection of comparable reported evidence.

Providers may return several observations for the same reported period.  This
module deliberately keeps the raw observations outside its result: it selects
only one eligible observation for a precisely defined concept and records the
candidate lineage needed to audit that choice.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timezone

from .models import EvidenceFact, IssueSeverity, ResearchIssue

# These are semantic metric names, not aliases for arithmetic.  In particular,
# the debt concepts are intentionally separate because a current component, a
# non-current component, and a total may overlap.
_SEC_TAG_METRICS = {
    "us-gaap:cashandcashequivalentsatcarryingvalue": "cash",
    "us-gaap:cashcashequivalentsrestrictedcashandrestrictedcashequivalents": "cash_including_restricted",
    "us-gaap:shorttermborrowings": "short_term_borrowings",
    "us-gaap:commercialpaper": "commercial_paper",
    # Keep this deliberately cautious: the tag's embedded description does
    # not establish that current maturities are included.
    "us-gaap:longtermdebt": "long_term_debt_reported",
    "us-gaap:longtermdebtcurrent": "long_term_debt_current",
    "us-gaap:longtermdebtnoncurrent": "long_term_debt_noncurrent",
    "us-gaap:longtermdebtandfinanceleaseobligations": "long_term_debt_including_finance_leases_total",
    "us-gaap:longtermdebtandfinanceleaseobligationscurrent": "long_term_debt_including_finance_leases_current",
    "us-gaap:longtermdebtandfinanceleaseobligationsnoncurrent": "long_term_debt_including_finance_leases_noncurrent",
    "us-gaap:financeleaseliability": "finance_lease_liability",
    "us-gaap:financeleaseliabilitycurrent": "finance_lease_liability_current",
    "us-gaap:financeleaseliabilitynoncurrent": "finance_lease_liability_noncurrent",
    "us-gaap:stockholdersequity": "equity",
    "us-gaap:stockholdersequityincludingportionattributabletononcontrollinginterest": "equity_including_noncontrolling",
    "us-gaap:preferredstockvalue": "preferred_stock_value",
    "us-gaap:preferredstockliquidationpreferencevalue": "preferred_stock_liquidation_preference",
    "us-gaap:minorityinterest": "noncontrolling_interest_carrying",
    "us-gaap:redeemablenoncontrollinginterestequitycarryingamount": "redeemable_nci_equity_carrying_amount",
    "us-gaap:netincomelossattributabletoparent": "net_income_attributable_to_parent",
    "us-gaap:weightedaveragenumberofsharesoutstandingbasic": "weighted_average_shares_basic",
    "us-gaap:paymentstoacquireproductiveassets": "capital_expenditures_productive_assets",
}

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
    "weighted_average_number_of_shares_outstanding_basic": "weighted_average_shares_basic",
    "cash_and_cash_equivalents_at_carrying_value": "cash",
}


@dataclass(frozen=True)
class ReconciliationResult:
    selected_facts: list[EvidenceFact]
    issues: list[ResearchIssue]
    decisions: list[dict]


def normalize_concept(fact: EvidenceFact) -> EvidenceFact:
    """Return a view of ``fact`` with a safe, normalized metric name.

    The original fact remains untouched.  This also makes SEC packets cached
    before the provider metric split usable by the reconciliation pass.
    """
    tag = _normalized_tag(fact.source_tag)
    metric = _SEC_TAG_METRICS.get(tag, _canonical_metric(fact.metric))
    # This is the documented standard US-GAAP cash-payment concept.  It is the
    # sole legacy-basis remap; no vendor label or generic capex metric is given
    # an assumed sign convention.
    basis = (
        "cash_outflow_positive"
        if tag in {
            "us-gaap:paymentstoacquirepropertyplantandequipment",
            "us-gaap:paymentstoacquireproductiveassets",
        }
        else fact.adjustment_basis
    )
    return fact if metric == fact.metric and basis == fact.adjustment_basis else replace(fact, metric=metric, adjustment_basis=basis)


def reconcile_facts(facts: list[EvidenceFact]) -> ReconciliationResult:
    """Select comparable facts and make conflicts/restatements explicit.

    A selected fact is the latest publication for one exact concept, unit,
    start/end period, and adjustment basis.  This function never derives a
    period value and never sums components.
    """
    normalized = [(fact, normalize_concept(fact)) for fact in facts]
    issues: list[ResearchIssue] = []
    excluded: set[str] = set()

    # A unit/basis conflict makes all candidates in that semantic slot unsafe.
    semantic_groups: dict[tuple[str, str | None, str], list[tuple[EvidenceFact, EvidenceFact]]] = defaultdict(list)
    for original, view in normalized:
        semantic_groups[(view.metric, view.period_start, view.period_end)].append((original, view))
    for (metric, start, end), peers in semantic_groups.items():
        units = {_normalized_unit(view.unit) for _, view in peers}
        if len(units) > 1:
            excluded.update(original.fact_id for original, _ in peers)
            issues.append(_issue("UNIT_CONFLICT", metric, start, end, f"use units {sorted(units)}"))

    # Different definitions for one generic metric are not interchangeable.
    # SEC tags remain distinct concepts; no tag-to-tag aliases are declared here.
    definition_groups: dict[tuple[str, str | None, str, str], list[tuple[EvidenceFact, EvidenceFact]]] = defaultdict(list)
    for original, view in normalized:
        definition_groups[(view.metric, view.period_start, view.period_end, _normalized_unit(view.unit))].append((original, view))
    for (metric, start, end, _unit), peers in definition_groups.items():
        concepts = {_concept_key(original, view) for original, view in peers}
        definitions = {_normalized_definition(view.definition) for _, view in peers}
        has_untrusted_concept = any(not _is_sec_or_dei_tag(original.source_tag) for original, _ in peers)
        if len(definitions) > 1 and (len(concepts) > 1 or has_untrusted_concept):
            excluded.update(original.fact_id for original, _ in peers)
            issues.append(_issue("DEFINITION_CONFLICT", metric, start, end, "use different accounting definitions"))

    exact_groups: dict[tuple[str, str, str | None, str, str], list[tuple[EvidenceFact, EvidenceFact]]] = defaultdict(list)
    for original, view in normalized:
        exact_groups[
            (
                _concept_key(original, view),
                _normalized_unit(view.unit),
                view.period_start,
                view.period_end,
                _normalized_basis(view.adjustment_basis),
            )
        ].append((original, view))

    # A concept reported under incompatible adjustment bases cannot be compared.
    basis_groups: dict[tuple[str, str, str | None, str], list[tuple[EvidenceFact, EvidenceFact]]] = defaultdict(list)
    for original, view in normalized:
        basis_groups[(_concept_key(original, view), _normalized_unit(view.unit), view.period_start, view.period_end)].append((original, view))
    for (_concept, _unit, start, end), peers in basis_groups.items():
        bases = {_normalized_basis(view.adjustment_basis) for _, view in peers}
        if len(bases) > 1:
            excluded.update(original.fact_id for original, _ in peers)
            issues.append(_issue("ADJUSTMENT_BASIS_CONFLICT", peers[0][1].metric, start, end, f"use bases {sorted(bases)}"))

    selected: list[EvidenceFact] = []
    decisions: list[dict] = []
    for key in sorted(exact_groups, key=_exact_group_sort_key):
        peers = exact_groups[key]
        originals = [original for original, _ in peers]
        views = [view for _, view in peers]
        concept, unit, start, end, basis = key
        decision = {
            "concept": concept,
            "metric": views[0].metric,
            "unit": unit,
            "period_start": start,
            "period_end": end,
            "adjustment_basis": basis,
            "candidate_fact_ids": sorted(fact.fact_id for fact in originals),
            "selected_fact_id": None,
            "outcome": "excluded",
        }
        if any(fact.fact_id in excluded for fact in originals):
            decision["reason"] = "semantic_conflict"
            decisions.append(decision)
            continue

        latest_key = max(_publication_key(fact.published_at) for fact in originals)
        latest = [(original, view) for original, view in peers if _publication_key(original.published_at) == latest_key]
        latest_time = max((original.published_at or "") for original, _ in latest)
        latest_values = {_value_key(original.value) for original, _ in latest}
        if len(latest_values) > 1:
            ids = sorted(original.fact_id for original, _ in latest)
            issues.append(
                ResearchIssue(
                    code="FACT_CONFLICT",
                    message=(f"Latest {views[0].metric} facts for {start or 'instant'} to {end} "
                             f"({unit}) disagree at {latest_time}: {', '.join(ids)}."),
                    severity=IssueSeverity.ERROR,
                    metric=views[0].metric,
                )
            )
            decision["reason"] = "latest_timestamp_value_conflict"
            decisions.append(decision)
            continue

        chosen_original, chosen_view = sorted(latest, key=lambda pair: pair[0].fact_id)[0]
        values = {_value_key(fact.value) for fact in originals}
        if len(values) > 1:
            issues.append(
                ResearchIssue(
                    code="RESTATED_OR_REVISED_FACT",
                    message=(f"{chosen_view.metric} for {start or 'instant'} to {end} ({unit}) was "
                             f"revised across filings; selected {chosen_original.fact_id} published "
                             f"{latest_time}; candidates: {', '.join(sorted(fact.fact_id for fact in originals))}."),
                    severity=IssueSeverity.WARNING,
                    metric=chosen_view.metric,
                )
            )
        selected.append(chosen_view)
        decision.update({
            "selected_fact_id": chosen_original.fact_id,
            "outcome": "selected",
            "reason": "latest_published" if len(originals) > 1 else "only_candidate",
        })
        decisions.append(decision)

    selected.sort(key=lambda fact: (fact.metric, fact.period_end, fact.period_start or "", fact.published_at or "", fact.fact_id))
    return ReconciliationResult(selected_facts=selected, issues=_dedupe_issues(issues), decisions=decisions)


def _canonical_metric(metric: str) -> str:
    key = "_".join(str(metric).strip().casefold().replace("-", " ").split())
    return _METRIC_ALIASES.get(key, key)


def _normalized_tag(value: str | None) -> str:
    return str(value or "").strip().casefold()


def _normalized_definition(value: str) -> str:
    return " ".join(str(value).casefold().split())


def _normalized_unit(value: str) -> str:
    return str(value).strip().casefold()


def _normalized_basis(value: str) -> str:
    return "_".join(str(value or "unknown").strip().casefold().replace("-", " ").split())


def _concept_key(original: EvidenceFact, view: EvidenceFact) -> str:
    tag = _normalized_tag(original.source_tag)
    if _is_sec_or_dei_tag(original.source_tag):
        return tag
    # Unmapped provider tags do not become cross-provider aliases.  The
    # definition is included for tagless data so exact duplicate observations
    # can still coalesce, while differing definitions are withheld above.
    if tag:
        return f"{view.metric}:{tag}"
    return f"{view.metric}:definition:{_normalized_definition(view.definition)}"


def _value_key(value: object) -> str:
    return format(value, ".12g") if isinstance(value, float) else repr(value)


def _publication_key(value: str | None) -> datetime:
    """Compare publication values as UTC instants; a date means UTC day end."""
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    if "T" not in value and " " not in value:
        return datetime.combine(date.fromisoformat(value), time.max, tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc)


def _exact_group_sort_key(key: tuple[str, str, str | None, str, str]) -> tuple[str, str, str, str, str]:
    concept, unit, start, end, basis = key
    return (concept, unit, start or "", end, basis)


def _is_sec_or_dei_tag(value: str | None) -> bool:
    tag = _normalized_tag(value)
    return tag.startswith("us-gaap:") or tag.startswith("dei:")


def _issue(code: str, metric: str, start: str | None, end: str, detail: str) -> ResearchIssue:
    return ResearchIssue(
        code=code,
        message=f"Competing {metric} facts for {start or 'instant'} to {end} {detail}.",
        severity=IssueSeverity.ERROR,
        metric=metric,
    )


def _dedupe_issues(issues: Iterable[ResearchIssue]) -> list[ResearchIssue]:
    unique: dict[tuple[str, str, str, str | None], ResearchIssue] = {}
    for issue in issues:
        unique[(issue.code, issue.message, issue.severity.value, issue.metric)] = issue
    return [unique[key] for key in sorted(unique)]
