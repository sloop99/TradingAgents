"""Estimated, explicitly unverified valuation from the facts already in a packet.

The capitalization module only calculates multiples whose every input is proven
(share-class coverage, ADR ratio, split history, complete debt). Until those
proofs exist, agents and readers still need a consistent valuation anchor. This
module derives one from the same packet facts and labels it as an estimate:

* price: the latest daily close on or before the analysis date;
* shares: the latest SEC cover-page count when it is recent and agrees with the
  vendor snapshot, otherwise the vendor count (multi-class issuers, splits);
* TTM revenue, earnings and free cash flow only when their period is recent;
* debt: interest-bearing components and finance leases on the latest balance
  sheet date (operating leases excluded), less cash on that date.

It works on plain fact dictionaries so the dashboard can recompute it for older
packets. Nothing here is written back into the verified capitalization audit.
"""

from __future__ import annotations

from datetime import date
from typing import Any

SHARES_MAX_AGE_DAYS = 130
TTM_MAX_AGE_DAYS = 150
BALANCE_MAX_AGE_DAYS = 150
SHARE_COUNT_TOLERANCE = 0.10
# Price dates can differ by a session or two from the vendor snapshot; larger
# gaps mean the share count and the vendor cap describe different share sets.
MARKET_CAP_TOLERANCE = 0.10
# Above this, earnings are too close to zero for P/E to describe the valuation.
MEANINGFUL_PE_LIMIT = 150

_SEC_SHARE_TAGS = {"dei:EntityCommonStockSharesOutstanding", "us-gaap:CommonStockSharesOutstanding"}
# Components that do not overlap one another; a reported total is used only when
# its current/noncurrent parts are absent.
_DEBT_PARTS = ("long_term_debt_current", "long_term_debt_noncurrent")
_DEBT_TOTAL = "long_term_debt_reported"
_SHORT_TERM_DEBT = ("commercial_paper", "short_term_borrowings", "short_term_debt")
_LEASE_PARTS = ("finance_lease_liability_current", "finance_lease_liability_noncurrent")
_LEASE_TOTAL = "finance_lease_liability"


def estimate_valuation(facts: list[dict[str, Any]], as_of: str) -> dict[str, Any] | None:
    """Return the estimate, or None when no price on or before ``as_of`` exists."""
    cutoff = date.fromisoformat(str(as_of)[:10])
    usable = [f for f in facts if _period_end(f) is not None and _period_end(f) <= cutoff and _number(f) is not None]
    notes: list[str] = []

    price = _latest(usable, {"close"})
    if price is None:
        return None
    shares, shares_source = _shares(usable, cutoff, notes)
    market_cap = _number(price) * shares if shares else None
    vendor_cap = _latest(usable, {"market_cap_reported"})
    if market_cap is not None and vendor_cap is not None and _number(vendor_cap) > 0:
        gap = market_cap / _number(vendor_cap) - 1
        if abs(gap) > MARKET_CAP_TOLERANCE:
            notes.append(
                f"Price x shares ({market_cap / 1e9:,.1f}B) differs from the vendor market cap "
                f"({_number(vendor_cap) / 1e9:,.1f}B) by {gap:+.0%}, typically because the share count covers only "
                "some share classes; using the vendor market cap."
            )
            market_cap = _number(vendor_cap)
            shares_source = "vendor_market_cap"
    if market_cap is None and vendor_cap is not None:
        market_cap = _number(vendor_cap)
        shares_source = "vendor_market_cap"
        notes.append("No usable share count; market cap is the vendor's reported figure.")

    ttm = {
        name: _fresh_ttm(usable, metric, cutoff, label, notes)
        for name, metric, label in (
            ("revenue", "revenue_ttm", "revenue"),
            ("net_income", "net_income_ttm", "earnings"),
            ("free_cash_flow", "free_cash_flow_ttm", "free cash flow"),
            ("operating_income", "operating_income_ttm", "operating income"),
        )
    }
    debt, cash, balance_date = _balance_sheet(usable, cutoff, notes)
    enterprise_value = market_cap + debt - cash if market_cap is not None and debt is not None and cash is not None else None

    def ratio(numerator, denominator, label):
        if numerator is None or denominator is None:
            return None
        if denominator <= 0:
            notes.append(f"TTM {label} is negative or zero, so the multiple is not meaningful.")
            return None
        return numerator / denominator

    multiples = {
        "price_to_earnings": ratio(market_cap, _value(ttm["net_income"]), "earnings"),
        "price_to_sales": ratio(market_cap, _value(ttm["revenue"]), "revenue"),
        "ev_to_revenue": ratio(enterprise_value, _value(ttm["revenue"]), "revenue") if enterprise_value is not None else None,
        "price_to_free_cash_flow": ratio(market_cap, _value(ttm["free_cash_flow"]), "free cash flow"),
    }
    pe = multiples["price_to_earnings"]
    if pe is not None and pe > MEANINGFUL_PE_LIMIT:
        notes.append(
            f"GAAP earnings are near breakeven, so the P/E of {pe:,.0f}x says little; "
            "sales and free-cash-flow multiples are more useful here."
        )
    return {
        "status": "estimated",
        "as_of": str(as_of)[:10],
        "price": {"value": _number(price), "date": str(price["period_end"])[:10], "source": price.get("source_tag")},
        "shares": {"value": shares, "source": shares_source},
        "market_cap": market_cap,
        "vendor_market_cap": _number(vendor_cap) if vendor_cap else None,
        "total_debt": debt,
        "cash": cash,
        "balance_date": balance_date,
        "enterprise_value": enterprise_value,
        "ttm": ttm,
        "multiples": multiples,
        "notes": list(dict.fromkeys(notes)),
    }


def _shares(facts, cutoff, notes) -> tuple[float | None, str | None]:
    sec = _latest([f for f in facts if f.get("source_tag") in _SEC_SHARE_TAGS], {"shares_outstanding"})
    vendor = _latest(facts, {"shares_outstanding_market"})
    sec_ok = sec is not None and (cutoff - _period_end(sec)).days <= SHARES_MAX_AGE_DAYS
    if sec_ok and vendor is not None:
        gap = abs(_number(sec) - _number(vendor)) / _number(vendor)
        if gap > SHARE_COUNT_TOLERANCE:
            notes.append(
                f"SEC cover-page shares ({_number(sec):,.0f} on {str(sec['period_end'])[:10]}) disagree with the "
                f"vendor count ({_number(vendor):,.0f}) by {gap:.0%}; using the vendor count (possible split or share class gap)."
            )
            return _number(vendor), "vendor"
    if sec_ok:
        return _number(sec), "sec_cover"
    if vendor is not None:
        if sec is not None:
            notes.append(f"Latest SEC share count ({str(sec['period_end'])[:10]}) is too old; using the vendor count.")
        return _number(vendor), "vendor"
    return None, None


def _fresh_ttm(facts, metric, cutoff, label, notes) -> dict[str, Any] | None:
    latest = _latest(facts, {metric})
    if latest is None:
        notes.append(f"TTM {label} is not available in the evidence packet.")
        return None
    age = (cutoff - _period_end(latest)).days
    if age > TTM_MAX_AGE_DAYS:
        notes.append(f"Latest TTM {label} ends {str(latest['period_end'])[:10]} and is stale; it is not used.")
        return None
    return {"value": _number(latest), "period_start": latest.get("period_start"), "period_end": str(latest["period_end"])[:10]}


def _balance_sheet(facts, cutoff, notes) -> tuple[float | None, float | None, str | None]:
    """Debt and cash on the latest balance sheet, dated by its cash figure."""
    cash_fact = _latest(facts, {"cash"})
    if cash_fact is None:
        notes.append("No cash figure was found, so enterprise value is not estimated.")
        return None, None, None
    balance = _period_end(cash_fact)
    if (cutoff - balance).days > BALANCE_MAX_AGE_DAYS:
        notes.append(f"Latest balance sheet ({balance.isoformat()}) is stale, so enterprise value is not estimated.")
        return None, None, None
    on_date = [f for f in facts if _period_end(f) == balance]

    def value(metric):
        found = [f for f in on_date if f.get("metric") == metric]
        return _number(found[-1]) if found else None

    parts = [value(metric) for metric in _DEBT_PARTS]
    borrowings = sum(p for p in parts if p is not None) if any(p is not None for p in parts) else value(_DEBT_TOTAL)
    short_term = [value(metric) for metric in _SHORT_TERM_DEBT]
    lease_parts = [value(metric) for metric in _LEASE_PARTS]
    leases = sum(p for p in lease_parts if p is not None) if any(p is not None for p in lease_parts) else value(_LEASE_TOTAL)
    reported = [amount for amount in (borrowings, leases, *short_term) if amount is not None]
    debt = sum(reported) if reported else 0.0
    if not reported:
        earlier = [f for f in facts if f.get("metric") in (*_DEBT_PARTS, _DEBT_TOTAL)]
        last = f" (last reported {max(_period_end(f) for f in earlier).isoformat()})" if earlier else ""
        notes.append(f"No debt is reported on the {balance.isoformat()} balance sheet{last}; treated as zero, which should be verified.")
    else:
        notes.append("Debt is an estimate from reported borrowings and finance leases; completeness is not verified.")
    return debt, value("cash"), balance.isoformat()


def _latest(facts, metrics) -> dict[str, Any] | None:
    matching = [f for f in facts if f.get("metric") in metrics]
    return max(matching, key=lambda f: (str(f.get("period_end")), str(f.get("published_at") or ""))) if matching else None


def _period_end(fact) -> date | None:
    try:
        return date.fromisoformat(str(fact.get("period_end"))[:10])
    except (TypeError, ValueError):
        return None


def _number(fact) -> float | None:
    if fact is None:
        return None
    value = fact.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _value(entry: dict[str, Any] | None) -> float | None:
    return entry["value"] if entry else None


_SHARE_SOURCES = {
    "sec_cover": "SEC cover-page share count",
    "vendor": "vendor share count",
    "vendor_market_cap": "vendor market cap",
}
_MULTIPLE_LABELS = (
    ("price_to_earnings", "P/E"),
    ("price_to_sales", "P/S"),
    ("ev_to_revenue", "EV/revenue"),
    ("price_to_free_cash_flow", "P/FCF"),
)


def _money(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value / 1e9:,.1f}B" if abs(value) >= 1e9 else f"${value / 1e6:,.1f}M"


def describe_estimate(estimate: dict[str, Any], max_notes: int = 3) -> str:
    """One labelled line for agent context and briefs."""
    price = estimate["price"]
    source = _SHARE_SOURCES.get(estimate["shares"].get("source") or "", "unknown share basis")
    parts = [f"market cap {_money(estimate['market_cap'])}"]
    if estimate.get("enterprise_value") is not None:
        parts.append(f"enterprise value {_money(estimate['enterprise_value'])}")
    for key, label in _MULTIPLE_LABELS:
        value = estimate["multiples"].get(key)
        parts.append(f"{label} {value:.1f}x" if value is not None else f"{label} n/a")
    text = (
        f"Estimated valuation (unverified; {price['date']} close {price['value']:.2f} with the {source}): "
        + ", ".join(parts) + "."
    )
    notes = [note for note in estimate.get("notes", []) if "completeness is not verified" not in note][:max_notes]
    if notes:
        text += " Caveats: " + " ".join(notes)
    return text
