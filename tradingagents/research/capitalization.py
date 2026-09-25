"""Conservative, source-linked capitalization and valuation calculations.

The calculator deliberately distinguishes market-vendor observations from a
capitalization that can be reproduced from verified point-in-time facts.  It
never fills an absent capital-structure item with zero and never substitutes
period-average shares for current shares.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from hashlib import sha256
from typing import Any

from .checks import canonical_metric
from .models import BusinessModel, EvidenceFact, IssueSeverity, ResearchIssue

PRICE_MAX_AGE_DAYS = 7
SHARES_MAX_AGE_DAYS = 130
BALANCE_SHEET_MAX_AGE_DAYS = 150
DENOMINATOR_MAX_AGE_DAYS = 150
ANNUAL_MIN_DAYS = 330
ANNUAL_MAX_DAYS = 400

_PRICE_METRICS = {"current_share_price"}
_SHARE_METRICS = {"current_shares"}
_OBSERVED_PRICE_METRICS = {"close", "regular_market_price", "market_price"}
_OBSERVED_SHARE_METRICS = {"shares_outstanding_market", "shares_outstanding"}
_REPORTED_CAP_METRICS = {"market_cap", "market_cap_reported", "reported_market_cap"}
_CASH_METRICS = {"cash", "cash_and_cash_equivalents", "cash_and_short_term_investments"}
_PREFERRED_METRICS = {"preferred_stock", "preferred_equity"}
_NCI_METRICS = {"noncontrolling_interest", "minority_interest"}
_PREFERRED_CANDIDATE_METRICS = {
    "preferred_stock_value",
    "preferred_stock_liquidation_preference",
}
_NCI_CANDIDATE_METRICS = {
    "noncontrolling_interest_carrying",
    "redeemable_nci_equity_carrying_amount",
}
_DEBT_COMPONENTS = {
    "short_term_debt",
    "current_debt",
    "debt_current",
    "short_term_borrowings",
    "long_term_debt",
    "long_term_debt_reported",
    "long_term_debt_current",
    "long_term_debt_noncurrent",
    "long_term_debt_including_finance_leases_total",
    "long_term_debt_including_finance_leases_current",
    "long_term_debt_including_finance_leases_noncurrent",
    "debt_noncurrent",
    "finance_lease_liabilities",
    "finance_lease_liability_current",
    "finance_lease_liability_noncurrent",
    "operating_lease_liabilities",
}
_GOOD_BASIS = {
    "current": "current_split_basis",
    "current quote": "current_split_basis",
    "point in time": "current_split_basis",
    "split adjusted": "current_split_basis",
    "stock split adjusted": "current_split_basis",
    "vendor split adjusted": "current_split_basis",
    "split adjusted consistent": "current_split_basis",
    "stock split adjusted consistent": "current_split_basis",
}


@dataclass(frozen=True)
class CapitalizationResult:
    """Serializable output from :func:`analyze_capitalization`."""

    derived_facts: list[EvidenceFact]
    issues: list[ResearchIssue]
    summary: dict[str, Any]


def analyze_capitalization(
    selected_facts: list[EvidenceFact],
    business_model: BusinessModel,
    as_of: str,
) -> CapitalizationResult:
    """Calculate only valuation measures whose full formula is evidenced.

    Supported point-in-time facts are ``current_share_price`` (``USD/share``
    style unit), ``current_shares`` (``shares``),
    ``share_class_coverage_ratio`` and ``adr_ratio`` (both exactly one),
    ``total_debt``, cash, ``preferred_stock``, and
    ``noncontrolling_interest``.  Market-vendor ``close``,
    ``shares_outstanding_market``, and ``market_cap_reported`` remain visible
    observations but do not independently verify equity capitalization.
    """

    cutoff = _parse_cutoff(as_of)
    issues: list[ResearchIssue] = []
    eligible: list[EvidenceFact] = []
    for fact in sorted(selected_facts, key=_fact_sort_key):
        reason = _ineligible_reason(fact, cutoff)
        if reason:
            issues.append(_issue("CAP_FACT_AFTER_CUTOFF", reason, IssueSeverity.WARNING, _metric(fact)))
        else:
            eligible.append(fact)

    observations = _observation_summary(eligible)
    derived: list[EvidenceFact] = []
    selected: dict[str, EvidenceFact] = {}
    conflicts = False

    price, conflict = _select_role(eligible, _PRICE_METRICS, "share price", issues)
    conflicts |= conflict
    shares, conflict = _select_role(eligible, _SHARE_METRICS, "current shares", issues)
    conflicts |= conflict
    class_ratio, conflict = _select_role(eligible, {"share_class_coverage_ratio"}, "share-class coverage", issues)
    conflicts |= conflict
    adr_ratio, conflict = _select_role(eligible, {"adr_ratio"}, "ADR ratio", issues)
    conflicts |= conflict
    split_complete, conflict = _select_role(eligible, {"split_history_complete"}, "split-history completeness", issues)
    conflicts |= conflict

    market_cap: EvidenceFact | None = None
    cap_prerequisites = {
        "current_share_price": price is not None,
        "current_shares": shares is not None,
        "share_class_coverage_ratio_equal_1": False,
        "adr_ratio_equal_1": False,
        "split_history_complete": False,
        "matching_currency_and_split_basis": False,
        "fresh_positive_price_and_shares": False,
    }
    if price is None:
        _missing(issues, "current_share_price", "a verified current_share_price fact")
    if shares is None:
        _missing(issues, "current_shares", "a verified current_shares fact; weighted-average shares are not current shares")
    if class_ratio is None:
        _missing(issues, "market_cap", "numeric share_class_coverage_ratio evidence equal to 1")
    elif not _ratio_is_one(class_ratio):
        issues.append(_issue("INCOMPLETE_SHARE_CLASS_COVERAGE", "Market capitalization was withheld because share_class_coverage_ratio is not 1.", IssueSeverity.WARNING, "market_cap"))
    elif _fresh(class_ratio, cutoff.date(), SHARES_MAX_AGE_DAYS, "share-class coverage", issues):
        cap_prerequisites["share_class_coverage_ratio_equal_1"] = True
    if adr_ratio is None:
        _missing(issues, "market_cap", "numeric adr_ratio evidence equal to 1, or an equivalent traceable listing/share conversion fact")
    elif not _ratio_is_one(adr_ratio):
        issues.append(_issue("ADR_CONVERSION_UNSUPPORTED", "Market capitalization was withheld because the supplied ADR ratio is not 1 and no verified conversion path is supported.", IssueSeverity.WARNING, "market_cap"))
    elif _fresh(adr_ratio, cutoff.date(), SHARES_MAX_AGE_DAYS, "ADR ratio", issues):
        cap_prerequisites["adr_ratio_equal_1"] = True
    if split_complete is None:
        _missing(issues, "market_cap", "a sourced numeric split_history_complete fact spanning the shares-to-price interval")

    if price and shares:
        price_currency = _price_currency(price.unit)
        share_unit_ok = _norm(shares.unit) in {"share", "shares"}
        price_basis = _basis_group(price)
        price_fresh = _fresh(price, cutoff.date(), PRICE_MAX_AGE_DAYS, "share price", issues)
        shares_fresh = _fresh(shares, cutoff.date(), SHARES_MAX_AGE_DAYS, "current shares", issues)
        fresh = price_fresh and shares_fresh
        positive = True
        if float(price.value) <= 0 or float(shares.value) <= 0:
            positive = False
            issues.append(_issue("NONPOSITIVE_CAPITALIZATION_INPUT", "current_share_price and current_shares must both be positive.", IssueSeverity.ERROR, "market_cap"))
        if price_currency is None:
            issues.append(_issue("PRICE_CURRENCY_UNRESOLVED", "current_share_price must use an explicit ISO currency-per-share unit such as USD/share.", IssueSeverity.WARNING, "market_cap"))
        if not share_unit_ok:
            issues.append(_issue("SHARE_UNIT_INVALID", "current_shares must use the unit shares.", IssueSeverity.WARNING, "market_cap"))
        cap_prerequisites["fresh_positive_price_and_shares"] = fresh and positive
        reconciled_shares = _reconcile_splits(shares, price, split_complete, eligible, issues)
        if reconciled_shares is not None:
            cap_prerequisites["split_history_complete"] = True
            if reconciled_shares.fact_id != shares.fact_id:
                derived.append(reconciled_shares)
        if price_basis is None or reconciled_shares is None:
            issues.append(_issue("SHARE_PRICE_BASIS_UNRESOLVED", "Market capitalization requires an explicit current split basis for price and complete sourced split reconciliation for shares.", IssueSeverity.WARNING, "market_cap"))
        elif _is_dividend_adjusted(price):
            issues.append(_issue("DIVIDEND_ADJUSTED_PRICE_REJECTED", "A dividend-adjusted close cannot be used to calculate current equity capitalization.", IssueSeverity.WARNING, "market_cap"))
        else:
            cap_prerequisites["matching_currency_and_split_basis"] = bool(price_currency and share_unit_ok)
        if all(cap_prerequisites.values()) and fresh and positive and not _is_dividend_adjusted(price) and reconciled_shares is not None:
            selected.update(price=price, shares=shares, share_class_coverage=class_ratio, adr_ratio=adr_ratio)  # type: ignore[arg-type]
            market_cap = _calculated(
                "market_cap", float(price.value) * float(reconciled_shares.value), price_currency or "", None,
                max(price.period_end, reconciled_shares.period_end), [price, reconciled_shares, class_ratio, adr_ratio],  # type: ignore[list-item]
                "Verified equity market capitalization from current share price and all covered current common shares.",
                "current split basis", "current_share_price * current_shares_split_adjusted",
            )
            derived.append(market_cap)

    cash, conflict = _select_role(eligible, _CASH_METRICS, "cash", issues)
    conflicts |= conflict
    debt, conflict = _select_role(eligible, {"total_debt"}, "total debt", issues)
    conflicts |= conflict
    preferred, conflict = _select_role(eligible, _PREFERRED_METRICS, "preferred stock", issues)
    conflicts |= conflict
    nci, conflict = _select_role(eligible, _NCI_METRICS, "noncontrolling interest", issues)
    conflicts |= conflict

    debt_components = [f for f in eligible if _metric(f) in _DEBT_COMPONENTS]
    if debt is None and debt_components:
        issues.append(_issue("DEBT_COMPONENTS_INCOMPLETE", "Debt components are reported as candidates, but total debt is withheld because completeness cannot be established.", IssueSeverity.WARNING, "total_debt"))
    if debt is not None and not _complete_debt_definition(debt):
        issues.append(_issue("TOTAL_DEBT_DEFINITION_UNRESOLVED", "total_debt was withheld because its definition does not claim complete interest-bearing obligations.", IssueSeverity.WARNING, "total_debt"))
        debt = None

    for fact, metric in ((cash, "cash"), (debt, "total_debt"), (preferred, "preferred_stock"), (nci, "noncontrolling_interest")):
        if fact is not None and float(fact.value) < 0:
            issues.append(_issue("NEGATIVE_CAPITAL_STRUCTURE_INPUT", f"{metric} must be nonnegative for capitalization arithmetic.", IssueSeverity.ERROR, metric))
            if metric == "cash":
                cash = None
            elif metric == "total_debt":
                debt = None
            elif metric == "preferred_stock":
                preferred = None
            else:
                nci = None

    for role, fact in (("cash", cash), ("total debt", debt), ("preferred stock", preferred), ("noncontrolling interest", nci)):
        if fact is not None and not _fresh(fact, cutoff.date(), BALANCE_SHEET_MAX_AGE_DAYS, role, issues):
            if role == "cash":
                cash = None
            elif role == "total debt":
                debt = None
            elif role == "preferred stock":
                preferred = None
            else:
                nci = None

    net_debt: EvidenceFact | None = None
    net_requirements = {
        "complete_current_debt": debt is not None,
        "current_cash": cash is not None,
        "matching_balance_dates": bool(debt and cash) and _same_balance_date([debt, cash], issues, "net_debt"),
        "matching_currency": bool(debt and cash) and _same_currency([debt, cash], issues, "net_debt"),
    }
    if all(net_requirements.values()):
        net_debt = _calculated(
            "net_debt", float(debt.value) - float(cash.value), _money_currency(debt.unit) or "", None,
            max(debt.period_end, cash.period_end), [debt, cash],
            "Total interest-bearing debt less cash included by the selected cash definition.",
            "as reported", "total_debt - cash",
        )
        derived.append(net_debt)

    enterprise_value: EvidenceFact | None = None
    ev_balance = [debt, preferred, nci, cash]
    ev_inputs = [market_cap] + ev_balance
    ev_requirements = {
        "verified_market_cap": market_cap is not None,
        "complete_current_debt": debt is not None,
        "current_cash": cash is not None,
        "explicit_preferred_equity": preferred is not None,
        "explicit_noncontrolling_interest": nci is not None,
        "matching_balance_dates": business_model is not BusinessModel.BANK and all(ev_balance) and _same_balance_date(ev_balance, issues, "enterprise_value"),
        "matching_currency": business_model is not BusinessModel.BANK and all(ev_inputs) and _same_currency(ev_inputs, issues, "enterprise_value"),
    }
    valuation_readiness = {
        "market_cap": _metric_readiness(cap_prerequisites),
        "net_debt": _metric_readiness(net_requirements),
        "enterprise_value": _metric_readiness(ev_requirements, business_model is not BusinessModel.BANK),
    }
    if business_model is BusinessModel.BANK:
        issues.append(_issue("BANK_EV_UNSUPPORTED", "Generic enterprise value and free-cash-flow multiples are not applied to banks.", IssueSeverity.INFO, "enterprise_value"))
    else:
        for fact, metric, requirement in (
            (debt, "enterprise_value", "complete total_debt"),
            (cash, "enterprise_value", "cash"),
            (preferred, "enterprise_value", "preferred_stock, including an explicit zero"),
            (nci, "enterprise_value", "noncontrolling_interest, including an explicit zero"),
        ):
            if fact is None:
                _missing(issues, metric, requirement)
        if all(ev_requirements.values()):
            enterprise_value = _calculated(
                "enterprise_value",
                float(market_cap.value) + float(debt.value) + float(preferred.value) + float(nci.value) - float(cash.value),  # type: ignore[union-attr]
                market_cap.unit, None, max(f.period_end for f in ev_inputs if f), ev_inputs,  # type: ignore[arg-type]
                "Enterprise value including explicitly reported debt, preferred stock, noncontrolling interest, and cash.",
                "as reported", "market_cap + total_debt + preferred_stock + noncontrolling_interest - cash",
            )
            derived.append(enterprise_value)

    multiple_specs: list[tuple[str, EvidenceFact | None, tuple[str, ...]]] = [
        ("price_to_earnings", market_cap, (
            "net_income_attributable_to_common_ttm", "net_income_common_ttm",
            "net_income_attributable_to_common", "net_income_common", "net_income_ttm", "net_income",
        )),
    ]
    if business_model is not BusinessModel.BANK:
        multiple_specs.extend([
            ("enterprise_value_to_revenue", enterprise_value, ("revenue_ttm", "revenue")),
            ("price_to_free_cash_flow", market_cap, ("free_cash_flow_ttm", "free_cash_flow")),
        ])
    for output_metric, numerator, denominator_metrics in multiple_specs:
        denominator, conflict = _select_denominator(eligible, denominator_metrics, output_metric, cutoff.date(), issues)
        conflicts |= conflict
        requirements = {
            "verified_numerator": numerator is not None,
            "eligible_annual_or_ttm_denominator": denominator is not None,
            "positive_denominator": denominator is not None and float(denominator.value) > 0,
            "denominator_currency_identified": denominator is not None and _money_currency(denominator.unit) is not None,
            "matching_currency": bool(numerator and denominator) and _same_currency([numerator, denominator], issues, output_metric),
        }
        valuation_readiness[output_metric] = {
            **_metric_readiness(requirements),
            "denominator": _summary_fact(denominator) if denominator else None,
        }
        if denominator is None:
            continue
        if float(denominator.value) <= 0:
            issues.append(_issue("NONPOSITIVE_MULTIPLE_DENOMINATOR", f"{output_metric} was withheld because {denominator.metric} is nonpositive.", IssueSeverity.INFO, output_metric))
            continue
        if not all(requirements.values()):
            continue
        derived.append(_calculated(
            output_metric, float(numerator.value) / float(denominator.value), "x",
            denominator.period_start, numerator.period_end, [numerator, denominator],
            f"{output_metric.replace('_', ' ')} using a verified capitalization numerator.",
            "not applicable", f"{numerator.metric} / {denominator.metric}",
        ))
    if business_model is BusinessModel.BANK:
        for metric in ("enterprise_value_to_revenue", "price_to_free_cash_flow"):
            valuation_readiness[metric] = _metric_readiness({}, False)

    intended = {"market_cap", "price_to_earnings"}
    if business_model is not BusinessModel.BANK:
        intended |= {"net_debt", "enterprise_value", "enterprise_value_to_revenue", "price_to_free_cash_flow"}
    produced = {_metric(f) for f in derived}
    status = "material_conflict" if conflicts or any(i.severity is IssueSeverity.ERROR for i in issues) else ("sufficient" if intended <= produced else ("partial" if produced else "unsupported"))
    summary: dict[str, Any] = {
        "status": status,
        "as_of": cutoff.isoformat().replace("+00:00", "Z"),
        "metrics": {f.metric: _summary_fact(f) for f in derived},
        "provider_observations": observations,
        "capital_structure_inputs": {
            "cash": _summary_fact(cash) if cash else None,
            "total_debt": _summary_fact(debt) if debt else None,
            "preferred_stock": _summary_fact(preferred) if preferred else None,
            "noncontrolling_interest": _summary_fact(nci) if nci else None,
        },
        "debt_component_candidates": [_summary_fact(f) for f in sorted(debt_components, key=_fact_sort_key)],
        "preferred_component_candidates": [
            _summary_fact(f) for f in eligible if _metric(f) in _PREFERRED_CANDIDATE_METRICS
        ],
        "noncontrolling_interest_component_candidates": [
            _summary_fact(f) for f in eligible if _metric(f) in _NCI_CANDIDATE_METRICS
        ],
        "market_cap_prerequisites": cap_prerequisites,
        "valuation_readiness": valuation_readiness,
        "market_cap_evidence_plan": _evidence_plan(cap_prerequisites, price, shares, eligible),
        "missing_metrics": sorted(intended - produced),
        "assumptions": {
            "price_max_age_days": PRICE_MAX_AGE_DAYS,
            "shares_max_age_days": SHARES_MAX_AGE_DAYS,
            "balance_sheet_max_age_days": BALANCE_SHEET_MAX_AGE_DAYS,
            "denominator_max_age_days": DENOMINATOR_MAX_AGE_DAYS,
            "annual_duration_days": [ANNUAL_MIN_DAYS, ANNUAL_MAX_DAYS],
            "missing_debt_preferred_or_noncontrolling_interest_assumed_zero": False,
            "weighted_average_shares_used_for_current_capitalization": False,
            "dividend_adjusted_close_allowed": False,
            "provider_reported_market_cap_independently_verified": False,
        },
    }
    return CapitalizationResult(sorted(derived, key=_fact_sort_key), _dedupe_issues(issues), summary)


def _metric_readiness(requirements: dict[str, bool], applicable: bool = True) -> dict[str, Any]:
    return {
        "status": ("ready" if all(requirements.values()) else "blocked") if applicable else "not_applicable",
        "requirements": requirements if applicable else {},
        "unresolved": [key for key, value in requirements.items() if not value] if applicable else [],
    }


def _select_role(facts: list[EvidenceFact], metrics: set[str], label: str, issues: list[ResearchIssue]) -> tuple[EvidenceFact | None, bool]:
    candidates = [f for f in facts if _metric(f) in metrics]
    if not candidates:
        return None, False
    latest_end = max(f.period_end for f in candidates)
    latest = [f for f in candidates if f.period_end == latest_end]
    signatures = {(float(f.value), _norm(f.unit), _norm(f.definition), _norm(f.adjustment_basis)) for f in latest}
    if len(signatures) != 1:
        issues.append(_issue("CAPITALIZATION_INPUT_CONFLICT", f"{label} was withheld because latest facts conflict.", IssueSeverity.ERROR, next(iter(metrics))))
        return None, True
    return max(latest, key=lambda f: (f.published_at or "", f.retrieved_at, f.fact_id)), False


def _select_denominator(facts: list[EvidenceFact], metrics: tuple[str, ...], output: str, cutoff: date, issues: list[ResearchIssue]) -> tuple[EvidenceFact | None, bool]:
    # TTM wins over annual, but only exact TTM/near-year durations qualify.
    for metric in metrics:
        candidates = [f for f in facts if _metric(f) == metric and f.period_start and _annual_duration(f)]
        if output == "price_to_earnings" and candidates:
            scoped = [f for f in candidates if _earnings_common_scope(f)]
            if not scoped:
                issues.append(_issue("EARNINGS_SCOPE_UNRESOLVED", "P/E was withheld because net income is not explicitly attributable or available to common shareholders.", IssueSeverity.WARNING, output))
                continue
            candidates = scoped
        if not candidates:
            continue
        latest_end = max(f.period_end for f in candidates)
        latest = [f for f in candidates if f.period_end == latest_end]
        signatures = {(float(f.value), _norm(f.unit), _norm(f.definition)) for f in latest}
        if len(signatures) != 1:
            issues.append(_issue("MULTIPLE_DENOMINATOR_CONFLICT", f"{output} was withheld because latest {metric} facts conflict.", IssueSeverity.ERROR, output))
            return None, True
        chosen = max(latest, key=lambda f: (f.published_at or "", f.fact_id))
        if not _fresh(chosen, cutoff, DENOMINATOR_MAX_AGE_DAYS, metric, issues):
            return None, False
        return chosen, False
    _missing(issues, output, f"a positive, current exact-year {' or '.join(metrics)} denominator")
    return None, False


def _observation_summary(facts: list[EvidenceFact]) -> dict[str, list[dict[str, Any]]]:
    groups = {
        "reported_market_cap": _REPORTED_CAP_METRICS,
        "market_prices": _OBSERVED_PRICE_METRICS,
        "market_shares": _OBSERVED_SHARE_METRICS,
    }
    return {name: [_summary_fact(f) for f in facts if _metric(f) in metrics] for name, metrics in groups.items()}


def _evidence_plan(prerequisites, price, shares, facts):
    """Explain outstanding gates without converting observations into proof."""
    actions = {
        "current_share_price": "Review a sourced, currency-identified quote on the price date's split basis; exclude dividend-adjusted prices.",
        "current_shares": "Review a sourced point-in-time common-share count, not weighted-average shares.",
        "share_class_coverage_ratio_equal_1": "Establish all outstanding common classes and their economic conversion; a single listed class does not prove complete coverage.",
        "adr_ratio_equal_1": "Establish that the quoted security and counted shares have a one-to-one conversion; do not infer non-ADR status from ticker alone.",
        "split_history_complete": "Obtain sourced complete split coverage from the share-count date through the quote date; an empty vendor event list is insufficient.",
        "matching_currency_and_split_basis": "Reconcile raw reported shares through the complete split interval onto the quote basis and verify currency per share.",
        "fresh_positive_price_and_shares": "Use positive price and share quantities within the configured freshness limits.",
    }
    interval = None
    if price and shares:
        interval = {
            "start_exclusive": shares.period_end,
            "end_inclusive": price.period_end,
            "valid_order": shares.period_end <= price.period_end,
            "share_fact_id": shares.fact_id,
            "price_fact_id": price.fact_id,
            "observed_split_fact_ids": sorted(
                f.fact_id for f in facts
                if _metric(f) == "split_ratio" and shares.period_end < f.period_end <= price.period_end
            ),
            "absence_of_events_proves_no_split": False,
        }
    return {
        "ready": all(prerequisites.values()),
        "outstanding": [{"requirement": key, "action": actions[key]}
                        for key, satisfied in prerequisites.items() if not satisfied],
        "split_interval": interval,
    }


def _reconcile_splits(
    shares: EvidenceFact,
    price: EvidenceFact,
    completeness: EvidenceFact | None,
    facts: list[EvidenceFact],
    issues: list[ResearchIssue],
) -> EvidenceFact | None:
    """Move a point-in-time share count onto the price date's split basis."""

    share_date = date.fromisoformat(shares.period_end)
    price_date = date.fromisoformat(price.period_end)
    if _norm(shares.adjustment_basis) != "as reported":
        issues.append(_issue("SHARE_SPLIT_BASIS_UNRESOLVED", "Split-event reconciliation requires current_shares on a raw as_reported basis; pre-adjusted shares could be adjusted twice.", IssueSeverity.WARNING, "market_cap"))
        return None
    if share_date > price_date:
        issues.append(_issue("SPLIT_RECONCILIATION_INVALID_INTERVAL", "current_shares is later than current_share_price; the split interval cannot be reconciled.", IssueSeverity.WARNING, "market_cap"))
        return None
    if completeness is None or not _ratio_is_one(completeness) or completeness.period_start is None:
        if completeness is not None:
            issues.append(_issue("SPLIT_HISTORY_UNVERIFIED", "split_history_complete must be a numeric unitless fact equal to 1 with an explicit coverage start.", IssueSeverity.WARNING, "market_cap"))
        return None
    coverage_start = date.fromisoformat(completeness.period_start)
    coverage_end = date.fromisoformat(completeness.period_end)
    if coverage_start > share_date or coverage_end < price_date:
        issues.append(_issue("SPLIT_HISTORY_COVERAGE_GAP", "split_history_complete does not span the full shares-to-price interval.", IssueSeverity.WARNING, "market_cap"))
        return None

    event_candidates = [
        fact for fact in facts
        if _metric(fact) == "split_ratio" and share_date < date.fromisoformat(fact.period_end) <= price_date
    ]
    ratio = 1.0
    events: list[EvidenceFact] = []
    by_date: dict[str, list[EvidenceFact]] = {}
    for event in event_candidates:
        by_date.setdefault(event.period_end, []).append(event)
    for event_date, peers in sorted(by_date.items()):
        signatures = {(float(event.value), _norm(event.unit)) for event in peers}
        if len(signatures) != 1:
            issues.append(_issue("SPLIT_RATIO_CONFLICT", f"Split events on {event_date} disagree and cannot be multiplied independently.", IssueSeverity.ERROR, "split_ratio"))
            return None
        event = max(peers, key=lambda item: (item.published_at or "", item.fact_id))
        if _norm(event.unit) not in {"ratio", "x", "1", "unitless"} or float(event.value) <= 0:
            issues.append(_issue("INVALID_SPLIT_RATIO", f"Split event {event.fact_id} must have a positive unitless ratio.", IssueSeverity.ERROR, "split_ratio"))
            return None
        events.append(event)
        ratio *= float(event.value)
    parents = [shares, completeness] + sorted(events, key=_fact_sort_key)
    return _calculated(
        "current_shares_split_adjusted",
        float(shares.value) * ratio,
        "shares",
        None,
        price.period_end,
        parents,
        "Current shares reconciled through every sourced split event in the declared complete interval.",
        "current split basis",
        "current_shares * product(split_ratio events after shares date through price date)",
    )


def _calculated(metric: str, value: float, unit: str, period_start: str | None, period_end: str, parents: Iterable[EvidenceFact | None], definition: str, adjustment_basis: str, formula: str) -> EvidenceFact:
    source_parents = [p for p in parents if p is not None]
    identity = "|".join([metric, period_start or "instant", period_end, formula] + [p.fact_id for p in source_parents])
    fact_id = f"calc:capitalization:{metric}:{period_start or 'instant'}:{period_end}:{sha256(identity.encode()).hexdigest()[:16]}"
    source = max(source_parents, key=lambda f: (_temporal(f.published_at or f.retrieved_at), f.fact_id))
    return EvidenceFact.from_dict({
        "fact_id": fact_id, "metric": metric, "value": value, "unit": unit,
        "period_start": period_start, "period_end": period_end,
        "published_at": _max_temporal(source_parents, "published_at"),
        "retrieved_at": _max_temporal(source_parents, "retrieved_at"),
        "source_url": source.source_url, "accession": source.accession,
        "source_tag": "research_engine:capitalization", "definition": definition,
        "adjustment_basis": adjustment_basis, "kind": "calculated", "formula": formula,
        "input_fact_ids": [p.fact_id for p in source_parents],
    })


def _parse_cutoff(value: str) -> datetime:
    if "T" not in value and " " not in value:
        return datetime.combine(date.fromisoformat(value), time.max, timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("as_of datetime must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _ineligible_reason(fact: EvidenceFact, cutoff: datetime) -> str | None:
    if date.fromisoformat(fact.period_end) > cutoff.date():
        return f"Fact {fact.fact_id} ends after the as-of cutoff."
    if fact.published_at and _temporal(fact.published_at) > cutoff:
        return f"Fact {fact.fact_id} was published after the as-of cutoff."
    if _temporal(fact.retrieved_at) > cutoff:
        return f"Fact {fact.fact_id} was retrieved after the as-of cutoff."
    return None


def _temporal(value: str) -> datetime:
    if "T" not in value and " " not in value:
        return datetime.combine(date.fromisoformat(value), time.max, timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _fresh(fact: EvidenceFact, cutoff: date, max_days: int, label: str, issues: list[ResearchIssue]) -> bool:
    age = (cutoff - date.fromisoformat(fact.period_end)).days
    if age < 0 or age > max_days:
        issues.append(_issue("STALE_CAPITALIZATION_INPUT", f"{label} is {age} days old; maximum is {max_days} days.", IssueSeverity.WARNING, _metric(fact)))
        return False
    return True


def _annual_duration(fact: EvidenceFact) -> bool:
    if not fact.period_start:
        return False
    days = (date.fromisoformat(fact.period_end) - date.fromisoformat(fact.period_start)).days + 1
    return ANNUAL_MIN_DAYS <= days <= ANNUAL_MAX_DAYS


def _complete_debt_definition(fact: EvidenceFact) -> bool:
    text = _norm(fact.definition)
    if any(token in text for token in ("exclude", "excluding", "partial", "subset")):
        return False
    has_scope = any(token in text.split() for token in ("all", "complete", "total"))
    return has_scope and "interest bearing" in text and any(token in text.split() for token in ("debt", "obligations"))


def _ratio_is_one(fact: EvidenceFact | None) -> bool:
    return fact is not None and abs(float(fact.value) - 1.0) <= 1e-12 and _norm(fact.unit) in {"ratio", "x", "1", "unitless"}


def _price_currency(unit: str) -> str | None:
    match = re.fullmatch(r"([A-Za-z]{3})(?:/|_per_| per )(?:share|shares)", unit.strip(), re.IGNORECASE)
    if not match:
        return None
    currency = match.group(1).upper()
    return None if currency in {"NAT", "UNK"} else currency


def _money_currency(unit: str) -> str | None:
    value = unit.strip().upper()
    return value if re.fullmatch(r"[A-Z]{3}", value) and value not in {"NAT", "UNK"} else None


def _same_currency(facts: list[EvidenceFact], issues: list[ResearchIssue], metric: str) -> bool:
    currencies = [_money_currency(f.unit) for f in facts]
    if any(c is None for c in currencies) or len(set(currencies)) != 1:
        issues.append(_issue("CAPITALIZATION_CURRENCY_CONFLICT", f"{metric} was withheld because every monetary input must use one explicit ISO currency.", IssueSeverity.WARNING, metric))
        return False
    return True


def _same_balance_date(facts: list[EvidenceFact], issues: list[ResearchIssue], metric: str) -> bool:
    dates = {fact.period_end for fact in facts}
    if len(dates) != 1:
        issues.append(_issue("BALANCE_DATE_MISMATCH", f"{metric} was withheld because balance-sheet inputs do not share one period end.", IssueSeverity.WARNING, metric))
        return False
    return True


def _earnings_common_scope(fact: EvidenceFact) -> bool:
    metric = _metric(fact)
    definition = _norm(fact.definition)
    return "common" in metric or "attributable to common" in definition or "available to common" in definition


def _basis_group(fact: EvidenceFact) -> str | None:
    return _GOOD_BASIS.get(_norm(fact.adjustment_basis))


def _is_dividend_adjusted(fact: EvidenceFact) -> bool:
    basis = _norm(fact.adjustment_basis)
    definition = _norm(fact.definition)
    return "dividend adjusted" in basis or "dividend adjusted" in definition or basis in {"adjusted_close", "total_return_adjusted"}


def _max_temporal(facts: list[EvidenceFact], attribute: str) -> str | None:
    values = [getattr(f, attribute) for f in facts if getattr(f, attribute)]
    return max(values, key=_temporal) if values else None


def _summary_fact(fact: EvidenceFact) -> dict[str, Any]:
    return {"fact_id": fact.fact_id, "metric": fact.metric, "value": fact.value, "unit": fact.unit, "period_start": fact.period_start, "period_end": fact.period_end, "kind": fact.kind.value}


def _missing(issues: list[ResearchIssue], metric: str, requirement: str) -> None:
    issues.append(_issue("CAPITALIZATION_PREREQUISITE_MISSING", f"{metric} requires {requirement}.", IssueSeverity.INFO, metric))


def _metric(fact: EvidenceFact) -> str:
    return canonical_metric(fact.metric)


def _norm(value: str) -> str:
    return " ".join(value.strip().casefold().replace("-", " ").replace("_", " ").split())


def _fact_sort_key(fact: EvidenceFact) -> tuple[str, str, str, str]:
    return (_metric(fact), fact.period_end, fact.published_at or "", fact.fact_id)


def _issue(code: str, message: str, severity: IssueSeverity, metric: str | None = None) -> ResearchIssue:
    return ResearchIssue(code=code, message=message, severity=severity, metric=metric)


def _dedupe_issues(issues: list[ResearchIssue]) -> list[ResearchIssue]:
    unique = {(i.code, i.message, i.severity.value, i.metric): i for i in issues}
    return sorted(unique.values(), key=lambda i: (i.severity.value, i.code, i.metric or "", i.message))
