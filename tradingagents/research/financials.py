"""Deterministic period normalization and conservative financial analytics.

This module consumes facts that have already passed source reconciliation.  It
does not choose between competing disclosures.  Whenever the remaining facts
do not support one unambiguous calculation, the calculation is withheld and a
structured issue explains why.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from math import isclose
from numbers import Real
from typing import Any

from .checks import canonical_metric
from .models import BusinessModel, EvidenceFact, IssueSeverity, ResearchIssue

_ADDITIVE_METRICS = {
    "revenue",
    "net_income",
    "operating_income",
    "operating_cash_flow",
    "capital_expenditures",
    "stock_based_compensation",
}
_BANK_CASH_METRICS = {
    "operating_cash_flow",
    "capital_expenditures",
}
_POSITIVE_CAPEX_BASES = {
    "cash_outflow_positive",
    "outflow_positive",
    "positive_outflow",
}
_CONSISTENT_SHARE_BASES = {
    "split_adjusted",
    "split_adjusted_consistent",
    "stock_split_adjusted",
    "stock_split_adjusted_consistent",
}


@dataclass(frozen=True)
class FinancialResult:
    """Serializable output from :func:`analyze_financials`."""

    derived_facts: list[EvidenceFact]
    issues: list[ResearchIssue]
    summary: dict[str, Any]


def analyze_financials(
    selected_facts: list[EvidenceFact],
    business_model: BusinessModel,
    vintages: list[EvidenceFact] | None = None,
) -> FinancialResult:
    """Normalize duration facts and calculate only well-supported analytics.

    ``selected_facts`` must be the output of source reconciliation.  Defensive
    conflict checks remain here because period arithmetic must fail closed if a
    caller bypasses or weakens that earlier stage.  ``vintages`` are every
    reported edition of those facts (original and restated filings); they are
    used only to explain a direct-versus-derived quarter gap, never as inputs.
    """

    facts = sorted(selected_facts, key=_fact_sort_key)
    issues: list[ResearchIssue] = []
    derived: list[EvidenceFact] = []

    blocked = _find_ambiguous_periods(facts, issues)
    safe_facts = [
        fact
        for fact in facts
        if (_metric(fact), fact.period_start, fact.period_end) not in blocked
    ]
    additive = [
        fact
        for fact in safe_facts
        if _metric(fact) in _ADDITIVE_METRICS
        and fact.period_start is not None
        and not (_metric(fact) in _BANK_CASH_METRICS and business_model is BusinessModel.BANK)
    ]

    quarters = _derive_quarters(additive, safe_facts, issues, blocked, vintages or facts)
    derived.extend(quarters)

    # A direct-versus-derived quarter conflict discovered above invalidates the
    # direct slot for every later calculation.
    safe_facts = [
        fact
        for fact in safe_facts
        if (_metric(fact), fact.period_start, fact.period_end) not in blocked
    ]
    quarters = [
        fact
        for fact in quarters
        if (_metric(fact), fact.period_start, fact.period_end) not in blocked
    ]
    derived = list(quarters)
    normalized = safe_facts + quarters
    ttms = _derive_ttm(normalized, safe_facts, issues, business_model, vintages or facts)
    derived.extend(ttms)

    analytical_inputs = safe_facts + derived
    if business_model is BusinessModel.BANK:
        issues.append(
            _issue(
                "BANK_GENERIC_ANALYTICS_UNSUPPORTED",
                "Generic free-cash-flow and revenue-margin analytics are not applied to banks.",
                IssueSeverity.INFO,
            )
        )
    else:
        fcf = _derive_fcf(analytical_inputs, issues)
        derived.extend(fcf)
        analytical_inputs.extend(fcf)
        margins = _derive_margins(analytical_inputs, issues)
        derived.extend(margins)

    growth_inputs = safe_facts + derived
    growth = _derive_revenue_growth(growth_inputs, issues)
    derived.extend(growth)

    share_changes = _derive_share_changes(safe_facts, issues)
    derived.extend(share_changes)

    derived = sorted(_unique_facts(derived), key=_fact_sort_key)
    all_facts = safe_facts + derived
    summary = _build_summary(all_facts, safe_facts, derived, business_model, issues)
    return FinancialResult(
        derived_facts=derived,
        issues=_dedupe_issues(issues),
        summary=summary,
    )


def _derive_quarters(
    additive: list[EvidenceFact],
    all_reported: list[EvidenceFact],
    issues: list[ResearchIssue],
    blocked: set[tuple[str, str | None, str]],
    vintages: list[EvidenceFact],
) -> list[EvidenceFact]:
    by_signature: dict[tuple[str, str, str, str, str], list[EvidenceFact]] = defaultdict(list)
    for fact in additive:
        by_signature[_additive_signature(fact)].append(fact)

    existing = _unambiguous_by_period(all_reported)
    derived: list[EvidenceFact] = []
    for signature, peers in sorted(by_signature.items()):
        ordered = sorted(peers, key=lambda fact: (fact.period_start or "", fact.period_end, fact.fact_id))
        for later in ordered:
            later_days = _duration_days(later)
            if later_days is None or later_days < 140 or later_days > 385:
                continue
            candidates: list[tuple[EvidenceFact, date, float]] = []
            for earlier in ordered:
                if earlier.fact_id == later.fact_id or earlier.period_start != later.period_start:
                    continue
                earlier_end = date.fromisoformat(earlier.period_end)
                later_end = date.fromisoformat(later.period_end)
                if earlier_end >= later_end:
                    continue
                quarter_start = earlier_end + timedelta(days=1)
                quarter_days = (later_end - quarter_start).days + 1
                if not _is_quarter_days(quarter_days):
                    continue
                value = float(later.value) - float(earlier.value)
                candidates.append((earlier, quarter_start, value))
            if not candidates:
                continue
            # More than one subtraction path to the same quarter is evidence of
            # an upstream duplicate/period ambiguity; do not pick by accident.
            outputs = {(start.isoformat(), _number_key(value)) for _, start, value in candidates}
            if len(outputs) != 1 or len(candidates) != 1:
                issues.append(
                    _issue(
                        "QUARTER_DERIVATION_WITHHELD",
                        f"Multiple cumulative inputs could produce a quarter ending {later.period_end}.",
                        IssueSeverity.WARNING,
                        _metric(later),
                    )
                )
                continue
            earlier, quarter_start, value = candidates[0]
            metric = _metric(later)
            if metric in {"revenue", "capital_expenditures"} and value < 0:
                issues.append(
                    _issue(
                        "NEGATIVE_DERIVED_VALUE",
                        f"Withheld negative derived {metric} for {quarter_start.isoformat()} to {later.period_end}.",
                        IssueSeverity.WARNING,
                        metric,
                    )
                )
                continue
            period_key = (metric, quarter_start.isoformat(), later.period_end)
            direct = [fact for fact in existing.get(period_key, []) if _additive_signature(fact) == signature]
            if direct:
                if len(direct) == 1 and _same_number(direct[0].value, value):
                    continue
                explanation = _explained_quarter_gap(direct, later, earlier, value, vintages)
                if explanation:
                    issues.append(
                        _issue(
                            "QUARTER_DERIVATION_RECONCILED",
                            f"Reported and cumulative-derived {metric} for {quarter_start.isoformat()} to "
                            f"{later.period_end} differ only by {explanation}; the reported quarter is used.",
                            IssueSeverity.INFO,
                            metric,
                        )
                    )
                    continue
                issues.append(
                    _issue(
                        "QUARTER_DERIVATION_CONFLICT",
                        f"Reported and cumulative-derived {metric} disagree for {quarter_start.isoformat()} to {later.period_end}.",
                        IssueSeverity.ERROR,
                        metric,
                    )
                )
                blocked.add(period_key)
                continue
            derived.append(
                _calculated_fact(
                    fact_id=f"calc:quarter:{metric}:{later.fact_id}:{earlier.fact_id}",
                    metric=metric,
                    value=value,
                    unit=later.unit,
                    period_start=quarter_start.isoformat(),
                    period_end=later.period_end,
                    parents=[later, earlier],
                    definition=later.definition,
                    adjustment_basis=later.adjustment_basis,
                    formula=f"{metric}[{later.fact_id}] - {metric}[{earlier.fact_id}]",
                    source_tag=later.source_tag,
                )
            )
    return derived


def _rounding_unit(values: set[float]) -> float:
    """The reporting precision shared by every value (filings round to thousands or millions).

    Values not rounded to at least thousands are treated as exact: no slack.
    """
    for unit in (1_000_000, 100_000, 10_000, 1_000):
        if all(float(v).is_integer() and int(v) % unit == 0 for v in values if v):
            return float(unit)
    return 0.0


def _explained_quarter_gap(
    direct: list[EvidenceFact],
    later: EvidenceFact,
    earlier: EvidenceFact,
    derived_value: float,
    vintages: list[EvidenceFact],
) -> str | None:
    """Why a derived quarter differs from the reported one, if the gap is benign.

    Two benign causes are recognised: rounding in the filings' reporting unit,
    and restatements, where a later filing revised one of the figures, so some
    combination of filing editions reproduces the reported quarter.
    """

    def editions(fact: EvidenceFact) -> set[float]:
        return {
            float(item.value)
            for item in vintages
            if _metric(item) == _metric(fact)
            and item.unit.casefold() == fact.unit.casefold()
            and item.period_start == fact.period_start
            and item.period_end == fact.period_end
        } | {float(fact.value)}

    reported = set().union(*(editions(item) for item in direct))
    later_values, earlier_values = editions(later), editions(earlier)
    unit = _rounding_unit(reported | later_values | earlier_values)

    def close(left: float, right: float) -> bool:
        return _same_number(left, right) or abs(left - right) <= unit

    selected = {float(item.value) for item in direct}
    if any(_same_number(derived_value, value) for value in reported - selected):
        return "a restated reported quarter (an earlier filing edition matches exactly)"
    if any(close(derived_value, value) for value in reported):
        return f"rounding in the filings' reporting unit ({unit:,.0f})"
    for later_value in later_values:
        for earlier_value in earlier_values:
            if any(close(later_value - earlier_value, value) for value in reported):
                return "restated cumulative figures (a later filing revised an earlier period)"
    return None


def _restated_quarters_match(
    window: list[EvidenceFact],
    direct: list[EvidenceFact],
    vintages: list[EvidenceFact],
    allowance: float,
) -> bool:
    """Whether some combination of each quarter's filing editions sums to the stated annual figure."""
    from itertools import product

    options = []
    for quarter in window:
        editions = {
            float(item.value)
            for item in vintages
            if _metric(item) == _metric(quarter)
            and item.unit.casefold() == quarter.unit.casefold()
            and item.period_start == quarter.period_start
            and item.period_end == quarter.period_end
        } | {float(quarter.value)}
        options.append(sorted(editions))
    if all(len(values) == 1 for values in options):
        return False
    annual = {float(fact.value) for fact in direct}
    return any(
        any(_same_number(sum(combo), value) or abs(sum(combo) - value) <= allowance for value in annual)
        for combo in product(*options)
    )


def _derive_ttm(
    facts: list[EvidenceFact],
    reported: list[EvidenceFact],
    issues: list[ResearchIssue],
    business_model: BusinessModel,
    vintages: list[EvidenceFact] | None = None,
) -> list[EvidenceFact]:
    quarters = [
        fact
        for fact in facts
        if _metric(fact) in _ADDITIVE_METRICS
        and fact.period_start is not None
        and _is_quarter_days(_duration_days(fact))
        and not (_metric(fact) in _BANK_CASH_METRICS and business_model is BusinessModel.BANK)
    ]
    by_signature: dict[tuple[str, str, str, str, str], list[EvidenceFact]] = defaultdict(list)
    for fact in quarters:
        by_signature[_additive_signature(fact)].append(fact)

    reported_by_period = _unambiguous_by_period(reported)
    derived: list[EvidenceFact] = []
    for signature, peers in sorted(by_signature.items()):
        unique_periods: dict[tuple[str, str], list[EvidenceFact]] = defaultdict(list)
        for fact in peers:
            unique_periods[(fact.period_start or "", fact.period_end)].append(fact)
        if any(len(group) != 1 for group in unique_periods.values()):
            issues.append(
                _issue(
                    "TTM_DERIVATION_WITHHELD",
                    f"Duplicate quarter inputs prevent an unambiguous TTM {_metric(peers[0])} calculation.",
                    IssueSeverity.WARNING,
                    _metric(peers[0]),
                )
            )
            continue
        ordered = sorted((group[0] for group in unique_periods.values()), key=lambda f: (f.period_end, f.fact_id))
        for index in range(3, len(ordered)):
            window = ordered[index - 3 : index + 1]
            if not _contiguous(window):
                continue
            total_days = (
                date.fromisoformat(window[-1].period_end)
                - date.fromisoformat(window[0].period_start or window[0].period_end)
            ).days + 1
            if not 350 <= total_days <= 385:
                continue
            metric = _metric(window[-1])
            value = sum(float(fact.value) for fact in window)
            if metric in {"revenue", "capital_expenditures"} and value < 0:
                issues.append(
                    _issue(
                        "NEGATIVE_DERIVED_VALUE",
                        f"Withheld negative TTM {metric} ending {window[-1].period_end}.",
                        IssueSeverity.WARNING,
                        metric,
                    )
                )
                continue
            start = window[0].period_start
            end = window[-1].period_end
            direct = [
                fact
                for fact in reported_by_period.get((metric, start, end), [])
                if _additive_signature(fact) == signature and _is_annual_days(_duration_days(fact))
            ]
            if direct:
                if len(direct) == 1 and _same_number(direct[0].value, value):
                    # The filing already states the exact annual interval.  It
                    # is the clearer representation of the same measurement.
                    continue
                unit = _rounding_unit({float(fact.value) for fact in [*window, *direct]})
                # Each rounded input can be off by half a unit.
                allowance = unit * (len(window) + 1) / 2
                explanation = None
                if unit and all(abs(float(fact.value) - value) <= allowance for fact in direct):
                    explanation = f"rounding in the filings' reporting unit ({unit:,.0f})"
                elif _restated_quarters_match(window, direct, vintages or [], allowance):
                    explanation = "restated quarterly figures (a combination of filing editions matches)"
                if explanation:
                    issues.append(
                        _issue(
                            "TTM_ANNUAL_RECONCILED",
                            f"Summed quarters and the stated annual {metric} for {start} to {end} differ only by "
                            f"{explanation}; the stated annual figure is used.",
                            IssueSeverity.INFO,
                            metric,
                        )
                    )
                    continue
                issues.append(
                    _issue(
                        "TTM_ANNUAL_CONFLICT",
                        f"Four-quarter and reported annual {metric} disagree for {start} to {end}; TTM was withheld.",
                        IssueSeverity.WARNING,
                        metric,
                    )
                )
                continue
            input_ids = ":".join(fact.fact_id for fact in window)
            derived.append(
                _calculated_fact(
                    fact_id=f"calc:ttm:{metric}:{input_ids}",
                    metric=f"{metric}_ttm",
                    value=value,
                    unit=window[-1].unit,
                    period_start=start,
                    period_end=end,
                    parents=window,
                    definition=window[-1].definition,
                    adjustment_basis=window[-1].adjustment_basis,
                    formula=" + ".join(f"{metric}[{fact.fact_id}]" for fact in window),
                    source_tag=window[-1].source_tag,
                )
            )
    return derived


def _derive_fcf(facts: list[EvidenceFact], issues: list[ResearchIssue]) -> list[EvidenceFact]:
    derived: list[EvidenceFact] = []
    suffixes = ("", "_ttm")
    for suffix in suffixes:
        cfo_metric = f"operating_cash_flow{suffix}"
        capex_metric = f"capital_expenditures{suffix}"
        output_metric = f"free_cash_flow{suffix}"
        cfo_facts = [fact for fact in facts if _metric(fact) == cfo_metric]
        capex_facts = [fact for fact in facts if _metric(fact) == capex_metric]
        periods = sorted(
            {(f.period_start, f.period_end, f.unit.casefold()) for f in cfo_facts}
            & {(f.period_start, f.period_end, f.unit.casefold()) for f in capex_facts}
        )
        for start, end, unit in periods:
            cfos = [f for f in cfo_facts if (f.period_start, f.period_end, f.unit.casefold()) == (start, end, unit)]
            capexes = [f for f in capex_facts if (f.period_start, f.period_end, f.unit.casefold()) == (start, end, unit)]
            if len(cfos) != 1 or len(capexes) != 1:
                issues.append(
                    _issue(
                        "FCF_DERIVATION_WITHHELD",
                        f"Ambiguous CFO or capex inputs for {start or 'instant'} to {end}.",
                        IssueSeverity.WARNING,
                        output_metric,
                    )
                )
                continue
            cfo, capex = cfos[0], capexes[0]
            if _basis(capex) not in _POSITIVE_CAPEX_BASES or float(capex.value) < 0:
                issues.append(
                    _issue(
                        "FCF_DERIVATION_WITHHELD",
                        f"Capex for {start or 'instant'} to {end} lacks an explicit positive-outflow basis.",
                        IssueSeverity.WARNING,
                        output_metric,
                    )
                )
                continue
            if _narrow_capex_concept(capex):
                issues.append(
                    _issue(
                        "FCF_DEFINITION_LIMITED",
                        "Calculated free cash flow uses the selected PP&E capex concept; software, capitalized development, acquisitions, or other investing outflows may be excluded.",
                        IssueSeverity.WARNING,
                        output_metric,
                    )
                )
            derived.append(
                _calculated_fact(
                    fact_id=f"calc:fcf:{suffix or 'period'}:{cfo.fact_id}:{capex.fact_id}",
                    metric=output_metric,
                    value=float(cfo.value) - float(capex.value),
                    unit=cfo.unit,
                    period_start=start,
                    period_end=end,
                    parents=[cfo, capex],
                    definition=(
                        "Operating cash flow minus the selected outflow-positive capital "
                        "expenditures concept; not an issuer-defined free-cash-flow measure."
                    ),
                    adjustment_basis="derived_from_reported_inputs",
                    formula=f"{cfo_metric} - {capex_metric}",
                )
            )
    return derived


def _derive_margins(facts: list[EvidenceFact], issues: list[ResearchIssue]) -> list[EvidenceFact]:
    derived: list[EvidenceFact] = []
    pairs = (
        ("operating_income", "revenue", "operating_margin"),
        ("net_income", "revenue", "net_margin"),
        ("free_cash_flow", "revenue", "free_cash_flow_margin"),
        ("operating_income_ttm", "revenue_ttm", "operating_margin_ttm"),
        ("net_income_ttm", "revenue_ttm", "net_margin_ttm"),
        ("free_cash_flow_ttm", "revenue_ttm", "free_cash_flow_margin_ttm"),
    )
    for numerator_metric, revenue_metric, output_metric in pairs:
        numerators = [f for f in facts if _metric(f) == numerator_metric]
        revenues = [f for f in facts if _metric(f) == revenue_metric]
        periods = sorted(
            {(f.period_start, f.period_end, f.unit.casefold()) for f in numerators}
            & {(f.period_start, f.period_end, f.unit.casefold()) for f in revenues}
        )
        for start, end, unit in periods:
            nums = [f for f in numerators if (f.period_start, f.period_end, f.unit.casefold()) == (start, end, unit)]
            revs = [f for f in revenues if (f.period_start, f.period_end, f.unit.casefold()) == (start, end, unit)]
            if len(nums) != 1 or len(revs) != 1:
                issues.append(
                    _issue(
                        "MARGIN_DERIVATION_WITHHELD",
                        f"Ambiguous inputs prevent {output_metric} for {start or 'instant'} to {end}.",
                        IssueSeverity.WARNING,
                        output_metric,
                    )
                )
                continue
            numerator, revenue = nums[0], revs[0]
            if float(revenue.value) <= 0:
                issues.append(
                    _issue(
                        "MARGIN_DERIVATION_WITHHELD",
                        f"Non-positive revenue prevents {output_metric} for {start or 'instant'} to {end}.",
                        IssueSeverity.WARNING,
                        output_metric,
                    )
                )
                continue
            derived.append(
                _calculated_fact(
                    fact_id=f"calc:margin:{output_metric}:{numerator.fact_id}:{revenue.fact_id}",
                    metric=output_metric,
                    value=float(numerator.value) / float(revenue.value) * 100.0,
                    unit="percent",
                    period_start=start,
                    period_end=end,
                    parents=[numerator, revenue],
                    definition=f"{numerator_metric} as a percentage of {revenue_metric}.",
                    adjustment_basis="derived_ratio",
                    formula=f"{numerator_metric} / {revenue_metric} * 100",
                )
            )
    return derived


def _derive_revenue_growth(
    facts: list[EvidenceFact], issues: list[ResearchIssue]
) -> list[EvidenceFact]:
    revenues = [f for f in facts if _metric(f) in {"revenue", "revenue_ttm"} and f.period_start]
    by_signature: dict[tuple[str, str, str, str, str], list[EvidenceFact]] = defaultdict(list)
    for fact in revenues:
        period_type = "ttm" if _metric(fact) == "revenue_ttm" else _period_type(fact)
        if period_type not in {"quarter", "annual", "ttm"}:
            continue
        by_signature[(_metric(fact), fact.unit.casefold(), _norm(fact.definition), _basis(fact), period_type)].append(fact)

    derived: list[EvidenceFact] = []
    for signature, peers in sorted(by_signature.items()):
        ordered = sorted(peers, key=lambda f: (f.period_end, f.fact_id))
        for current in ordered:
            current_end = date.fromisoformat(current.period_end)
            current_days = _duration_days(current)
            candidates = []
            for prior in ordered:
                if prior.fact_id == current.fact_id:
                    continue
                prior_end = date.fromisoformat(prior.period_end)
                gap = (current_end - prior_end).days
                prior_days = _duration_days(prior)
                if 350 <= gap <= 385 and current_days is not None and prior_days is not None and abs(current_days - prior_days) <= 14:
                    candidates.append(prior)
            if len(candidates) != 1:
                if len(candidates) > 1:
                    issues.append(
                        _issue(
                            "YOY_GROWTH_WITHHELD",
                            f"Multiple comparable prior revenue periods exist for the period ending {current.period_end}.",
                            IssueSeverity.WARNING,
                            "revenue_growth_yoy",
                        )
                    )
                continue
            prior = candidates[0]
            if float(prior.value) <= 0:
                issues.append(
                    _issue(
                        "YOY_GROWTH_WITHHELD",
                        f"Prior revenue is non-positive for the comparison ending {current.period_end}.",
                        IssueSeverity.WARNING,
                        "revenue_growth_yoy",
                    )
                )
                continue
            output_metric = "revenue_ttm_growth_yoy" if signature[-1] == "ttm" else "revenue_growth_yoy"
            derived.append(
                _calculated_fact(
                    fact_id=f"calc:yoy:{output_metric}:{current.fact_id}:{prior.fact_id}",
                    metric=output_metric,
                    value=(float(current.value) / float(prior.value) - 1.0) * 100.0,
                    unit="percent",
                    period_start=current.period_start,
                    period_end=current.period_end,
                    parents=[current, prior],
                    definition=f"Year-over-year change in comparable {signature[-1]} revenue.",
                    adjustment_basis="derived_ratio",
                    formula="(current_revenue / prior_revenue - 1) * 100",
                )
            )
    return derived


def _derive_share_changes(
    facts: list[EvidenceFact], issues: list[ResearchIssue]
) -> list[EvidenceFact]:
    share_metrics = {
        "shares_outstanding",
        "weighted_average_shares",
        "weighted_average_shares_basic",
    }
    share_facts = [f for f in facts if _metric(f) in share_metrics]
    unresolved = [f for f in share_facts if _basis(f) not in _CONSISTENT_SHARE_BASES]
    if unresolved:
        issues.append(
            _issue(
                "SHARE_SPLIT_BASIS_UNRESOLVED",
                "Share levels are shown separately, but comparative growth is withheld unless a consistent split-adjusted basis is explicit.",
                IssueSeverity.WARNING,
                "shares",
            )
        )
    eligible = [f for f in share_facts if _basis(f) in _CONSISTENT_SHARE_BASES]
    groups: dict[tuple[str, str, str, str, str], list[EvidenceFact]] = defaultdict(list)
    for fact in eligible:
        period_type = "instant" if fact.period_start is None else _period_type(fact)
        if _metric(fact) in {"weighted_average_shares", "weighted_average_shares_basic"} and period_type not in {"quarter", "annual"}:
            continue
        groups[(_metric(fact), fact.unit.casefold(), _norm(fact.definition), _basis(fact), period_type)].append(fact)

    derived: list[EvidenceFact] = []
    for signature, peers in sorted(groups.items()):
        for current in sorted(peers, key=lambda f: (f.period_end, f.fact_id)):
            current_end = date.fromisoformat(current.period_end)
            candidates = [
                prior
                for prior in peers
                if prior.fact_id != current.fact_id
                and 350 <= (current_end - date.fromisoformat(prior.period_end)).days <= 385
            ]
            if len(candidates) != 1:
                continue
            prior = candidates[0]
            if float(prior.value) <= 0:
                continue
            metric = signature[0]
            output = f"{metric}_change_yoy"
            derived.append(
                _calculated_fact(
                    fact_id=f"calc:share_change:{metric}:{current.fact_id}:{prior.fact_id}",
                    metric=output,
                    value=(float(current.value) / float(prior.value) - 1.0) * 100.0,
                    unit="percent",
                    period_start=current.period_start,
                    period_end=current.period_end,
                    parents=[current, prior],
                    definition=f"Year-over-year change in split-adjusted {metric}.",
                    adjustment_basis=signature[3],
                    formula=f"(current_{metric} / prior_{metric} - 1) * 100",
                )
            )
    return derived


def _build_summary(
    all_facts: list[EvidenceFact],
    reported: list[EvidenceFact],
    derived: list[EvidenceFact],
    business_model: BusinessModel,
    issues: list[ResearchIssue],
) -> dict[str, Any]:
    metric_names = sorted({_metric(fact) for fact in all_facts})
    reporting_ends = [
        fact.period_end
        for fact in all_facts
        if _metric(fact) in {"revenue", "net_income"} and fact.period_start is not None
    ]
    latest_reporting_end = max(reporting_ends) if reporting_ends else None
    latest: dict[str, dict[str, Any]] = {}
    for metric in metric_names:
        peers = [fact for fact in all_facts if _metric(fact) == metric]
        chosen = max(
            peers,
            key=lambda f: (
                f.period_end,
                _period_preference(f),
                f.published_at or "",
                f.fact_id,
            ),
        )
        if (
            latest_reporting_end
            and metric in {"operating_cash_flow", "capital_expenditures", "free_cash_flow"}
            and chosen.period_end < latest_reporting_end
        ):
            continue
        latest[metric] = _summary_fact(chosen)

    latest_by_period: dict[str, dict[str, dict[str, Any]]] = {
        "quarter": {},
        "annual": {},
        "ytd": {},
        "ttm": {},
        "instant": {},
    }
    for fact in all_facts:
        panel = _summary_period_group(fact)
        if panel is None:
            continue
        metric = _metric(fact)
        current = latest_by_period[panel].get(metric)
        if current is None or (fact.period_end, fact.published_at or "", fact.fact_id) > (
            current["period_end"],
            current.get("published_at", ""),
            current["fact_id"],
        ):
            item = _summary_fact(fact)
            item["published_at"] = fact.published_at
            latest_by_period[panel][metric] = item

    current_shares = [fact for fact in reported if _metric(fact) == "shares_outstanding"]
    weighted_shares = [fact for fact in reported if _metric(fact) == "weighted_average_shares"]
    weighted_basic = [fact for fact in reported if _metric(fact) == "weighted_average_shares_basic"]
    share_changes = [
        fact
        for fact in derived
        if _metric(fact) in {
            "shares_outstanding_change_yoy",
            "weighted_average_shares_change_yoy",
            "weighted_average_shares_basic_change_yoy",
        }
    ]
    shares = {
        "current_shares": _summary_fact(max(current_shares, key=_latest_key)) if current_shares else None,
        "weighted_average_shares_diluted": _summary_fact(max(weighted_shares, key=_latest_key)) if weighted_shares else None,
        "weighted_average_shares_basic": _summary_fact(max(weighted_basic, key=_latest_key)) if weighted_basic else None,
        "comparative_changes": [_summary_fact(fact) for fact in sorted(share_changes, key=_fact_sort_key)],
        "split_basis_resolved": bool(current_shares or weighted_shares or weighted_basic)
        and all(
            _basis(fact) in _CONSISTENT_SHARE_BASES
            for fact in current_shares + weighted_shares + weighted_basic
        ),
    }

    expected = {"revenue", "net_income"}
    if business_model is not BusinessModel.BANK:
        expected |= {"operating_cash_flow", "capital_expenditures", "free_cash_flow"}
    missing = []
    stale_inputs: list[dict[str, str]] = []
    for metric in sorted(expected):
        peers = [fact for fact in all_facts if _metric(fact) == metric]
        if not peers:
            missing.append(metric)
            continue
        metric_end = max(fact.period_end for fact in peers)
        if latest_reporting_end and metric_end < latest_reporting_end:
            missing.append(f"{metric} for latest reporting window ending {latest_reporting_end}")
            stale_inputs.append(
                {
                    "metric": metric,
                    "latest_period_end": metric_end,
                    "latest_reporting_end": latest_reporting_end,
                }
            )
    periods = {
        "quarterly": sorted({_period_label(f) for f in all_facts if _period_type(f) == "quarter"}),
        "annual": sorted({_period_label(f) for f in reported if _period_type(f) == "annual"}),
        "ttm": sorted({_period_label(f) for f in derived if _metric(f).endswith("_ttm")}),
    }
    limitations = sorted({issue.message for issue in issues})
    return {
        "latest_metrics": latest,
        "latest_by_period": latest_by_period,
        "periods": periods,
        "shares": shares,
        "missing": missing,
        "stale_inputs": stale_inputs,
        "limitations": limitations,
    }


def _find_ambiguous_periods(
    facts: list[EvidenceFact], issues: list[ResearchIssue]
) -> set[tuple[str, str | None, str]]:
    groups: dict[tuple[str, str | None, str], list[EvidenceFact]] = defaultdict(list)
    for fact in facts:
        groups[(_metric(fact), fact.period_start, fact.period_end)].append(fact)
    blocked: set[tuple[str, str | None, str]] = set()
    for key, peers in sorted(groups.items()):
        if len(peers) < 2:
            continue
        signatures = {(_additive_signature(f), _number_key(float(f.value))) for f in peers}
        if len(signatures) > 1 or len({f.fact_id for f in peers}) > 1:
            blocked.add(key)
            units = {f.unit.casefold() for f in peers}
            definitions = {_norm(f.definition) for f in peers}
            code = "FINANCIAL_INPUT_CONFLICT"
            detail = "competing values or sources"
            if len(units) > 1:
                code, detail = "FINANCIAL_UNIT_CONFLICT", "mixed units"
            elif len(definitions) > 1:
                code, detail = "FINANCIAL_DEFINITION_CONFLICT", "inconsistent definitions"
            issues.append(
                _issue(
                    code,
                    f"Withheld period arithmetic for {key[0]} at {key[1] or 'instant'} to {key[2]} because of {detail}.",
                    IssueSeverity.WARNING,
                    key[0],
                )
            )
    return blocked


def _calculated_fact(
    *,
    fact_id: str,
    metric: str,
    value: float,
    unit: str,
    period_start: str | None,
    period_end: str,
    parents: list[EvidenceFact],
    definition: str,
    adjustment_basis: str,
    formula: str,
    source_tag: str | None = None,
) -> EvidenceFact:
    source_parent = max(parents, key=_source_key)
    stage = fact_id.split(":", 2)[1] if fact_id.startswith("calc:") else "derived"
    identity = "|".join(
        [fact_id, metric, period_start or "instant", period_end, formula]
        + [parent.fact_id for parent in parents]
    )
    compact_id = (
        f"calc:{stage}:{metric}:{period_start or 'instant'}:{period_end}:"
        f"{sha256(identity.encode('utf-8')).hexdigest()[:16]}"
    )
    return EvidenceFact.from_dict(
        {
            "fact_id": compact_id,
            "metric": metric,
            "value": value,
            "unit": unit,
            "period_start": period_start,
            "period_end": period_end,
            "published_at": _max_temporal(parents, "published_at"),
            "retrieved_at": _max_temporal(parents, "retrieved_at"),
            "source_url": source_parent.source_url,
            "accession": source_parent.accession,
            "source_tag": source_tag or "research_engine",
            "definition": definition,
            "adjustment_basis": adjustment_basis,
            "kind": "calculated",
            "formula": formula,
            "input_fact_ids": [parent.fact_id for parent in parents],
        }
    )


def _metric(fact: EvidenceFact) -> str:
    metric = canonical_metric(fact.metric)
    aliases = {
        "capital_expenditure": "capital_expenditures",
        "stock_based_compensation_expense": "stock_based_compensation",
        "share_based_compensation": "stock_based_compensation",
    }
    return aliases.get(metric, metric)


def _additive_signature(fact: EvidenceFact) -> tuple[str, str, str, str, str]:
    return (
        _metric(fact),
        fact.unit.casefold(),
        _norm(fact.definition),
        _basis(fact),
        _norm(fact.source_tag or ""),
    )


def _basis(fact: EvidenceFact) -> str:
    return "_".join(fact.adjustment_basis.casefold().replace("-", " ").split())


def _narrow_capex_concept(fact: EvidenceFact) -> bool:
    text = f"{fact.source_tag or ''} {fact.definition}".casefold().replace("_", " ")
    return any(
        marker in text
        for marker in (
            "property plant",
            "property, plant",
            "ppe",
            "payments to acquire property",
        )
    )


def _norm(value: str) -> str:
    return " ".join(value.casefold().split())


def _duration_days(fact: EvidenceFact) -> int | None:
    if fact.period_start is None:
        return None
    start = date.fromisoformat(fact.period_start)
    end = date.fromisoformat(fact.period_end)
    days = (end - start).days + 1
    return days if days > 0 else None


def _is_quarter_days(days: int | None) -> bool:
    return days is not None and 70 <= days <= 112


def _is_annual_days(days: int | None) -> bool:
    return days is not None and 330 <= days <= 385


def _period_type(fact: EvidenceFact) -> str:
    if fact.period_start is None:
        return "instant"
    days = _duration_days(fact)
    if _is_quarter_days(days):
        return "quarter"
    if _is_annual_days(days):
        return "annual"
    if days is not None and 140 <= days <= 215:
        return "year_to_date_6m"
    if days is not None and 216 <= days <= 320:
        return "year_to_date_9m"
    return "other_duration"


def _contiguous(facts: list[EvidenceFact]) -> bool:
    if len(facts) != 4:
        return False
    for prior, current in zip(facts, facts[1:], strict=False):
        if current.period_start is None:
            return False
        if date.fromisoformat(current.period_start) != date.fromisoformat(prior.period_end) + timedelta(days=1):
            return False
    return True


def _unambiguous_by_period(
    facts: Iterable[EvidenceFact],
) -> dict[tuple[str, str | None, str], list[EvidenceFact]]:
    result: dict[tuple[str, str | None, str], list[EvidenceFact]] = defaultdict(list)
    for fact in facts:
        result[(_metric(fact), fact.period_start, fact.period_end)].append(fact)
    return result


def _summary_fact(fact: EvidenceFact) -> dict[str, Any]:
    return {
        "fact_id": fact.fact_id,
        "value": fact.value,
        "unit": fact.unit,
        "period_start": fact.period_start,
        "period_end": fact.period_end,
        "period_type": "ttm" if _metric(fact).endswith("_ttm") else _period_type(fact),
        "kind": fact.kind.value,
    }


def _summary_period_group(fact: EvidenceFact) -> str | None:
    if _metric(fact).endswith("_ttm"):
        return "ttm"
    period_type = _period_type(fact)
    if period_type.startswith("year_to_date"):
        return "ytd"
    if period_type in {"quarter", "annual", "instant"}:
        return period_type
    return None


def _period_preference(fact: EvidenceFact) -> int:
    group = _summary_period_group(fact)
    return {"quarter": 5, "ttm": 4, "ytd": 3, "annual": 2, "instant": 1}.get(group or "", 0)


def _period_label(fact: EvidenceFact) -> str:
    return f"{fact.period_start or 'instant'} to {fact.period_end}"


def _max_temporal(parents: list[EvidenceFact], attribute: str) -> str | None:
    values = [getattr(parent, attribute) for parent in parents if getattr(parent, attribute)]
    if not values:
        return None
    return max(values, key=_temporal_key)


def _temporal_key(value: str) -> datetime:
    if "T" not in value and " " not in value:
        return datetime.combine(date.fromisoformat(value), time.max, timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _source_key(fact: EvidenceFact) -> tuple[datetime, datetime, str]:
    published = _temporal_key(fact.published_at) if fact.published_at else datetime.min.replace(tzinfo=timezone.utc)
    return (published, _temporal_key(fact.retrieved_at), fact.fact_id)


def _latest_key(fact: EvidenceFact) -> tuple[str, str, str]:
    return (fact.period_end, fact.published_at or "", fact.fact_id)


def _fact_sort_key(fact: EvidenceFact) -> tuple[str, str, str, str, str]:
    return (_metric(fact), fact.period_start or "", fact.period_end, fact.published_at or "", fact.fact_id)


def _same_number(left: Real, right: Real) -> bool:
    return isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-9)


def _number_key(value: float) -> str:
    return format(value, ".12g")


def _unique_facts(facts: list[EvidenceFact]) -> list[EvidenceFact]:
    by_id: dict[str, EvidenceFact] = {}
    for fact in facts:
        by_id.setdefault(fact.fact_id, fact)
    return list(by_id.values())


def _issue(
    code: str,
    message: str,
    severity: IssueSeverity,
    metric: str | None = None,
) -> ResearchIssue:
    return ResearchIssue(code=code, message=message, severity=severity, metric=metric)


def _dedupe_issues(issues: list[ResearchIssue]) -> list[ResearchIssue]:
    result: dict[tuple[str, str, str, str | None], ResearchIssue] = {}
    for issue in issues:
        key = (issue.code, issue.message, issue.severity.value, issue.metric)
        result.setdefault(key, issue)
    return sorted(result.values(), key=lambda i: (i.severity.value, i.code, i.metric or "", i.message))
