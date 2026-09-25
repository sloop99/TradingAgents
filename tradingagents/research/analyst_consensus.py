"""Deterministic reconstruction of sourced, firm-attributed price targets.

This module never fetches research or treats vendor aggregate snapshots as firm
constituents. The input envelope asserts ticker/security identity; callers must
retain and review the underlying publications separately.
"""

from __future__ import annotations

import math
import re
import statistics
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from html import escape
from urllib.parse import urlparse


def _instant(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        raw = value.strip()
        if "T" not in raw and " " not in raw:
            return datetime.combine(date.fromisoformat(raw), time.max, timezone.utc)
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _text(value: object, limit: int = 500) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned[:limit] if cleaned else None


def _url(value: object) -> str | None:
    cleaned = _text(value, 2048)
    if not cleaned or re.search(r"[\s<>\[\]()]", cleaned):
        return None
    try:
        parsed = urlparse(cleaned)
        return cleaned if parsed.scheme in {"http", "https"} and parsed.hostname else None
    except ValueError:
        return None


def _positive(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    # Bound otherwise finite inputs so aggregate/quote ratios stay finite too.
    return number if math.isfinite(number) and 1e-9 <= number <= 1e12 else None


def _integer(value: object, minimum: int = 1, maximum: int = 100000) -> int | None:
    return value if type(value) is int and minimum <= value <= maximum else None


def _currency(value: object) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[A-Z]{3}", value) else None


def _end_date(published: datetime, months: int | None) -> str | None:
    if months is None:
        return None
    year, month = divmod(published.month - 1 + months, 12)
    target_year, target_month = published.year + year, month + 1
    if target_year > 9999:
        return None
    if target_month == 12:
        last_day = 31
    else:
        next_year, next_month = (target_year + 1, 1) if target_month == 12 else (target_year, target_month + 1)
        last_day = (date(next_year, next_month, 1) - timedelta(days=1)).day
    return date(target_year, target_month, min(published.day, last_day)).isoformat()


def _ranking(raw: object, cutoff: datetime, max_age_days: int) -> dict | None:
    if not isinstance(raw, dict):
        return None
    rank = _integer(raw.get("rank"))
    size = _integer(raw.get("universe_size"))
    observed = _instant(raw.get("as_of"))
    provider = _text(raw.get("provider"), 120)
    method = _text(raw.get("method"), 240)
    source = _url(raw.get("source_url"))
    if not all((rank, size, observed, provider, method, source)) or rank > size:
        return None
    if observed > cutoff or cutoff - observed > timedelta(days=max_age_days):
        return None
    return {"rank": rank, "universe_size": size, "provider": provider,
            "method": method, "as_of": _iso(observed), "source_url": source}


def _summary(records: list[dict]) -> dict:
    values = [item["target"] for item in records]
    ages = [item["age_days"] for item in records]
    ends = sorted(item["target_end_date"] for item in records if item["target_end_date"])
    return {"count": len(records), "mean": statistics.mean(values) if values else None,
            "median": statistics.median(values) if values else None,
            "min": min(values) if values else None,
            "max": max(values) if values else None,
            "median_age_days": statistics.median(ages) if ages else None,
            "target_end_date_min": ends[0] if ends else None,
            "target_end_date_max": ends[-1] if ends else None}


def _exclusion(index: int, raw: object, reason: str) -> dict:
    item = raw if isinstance(raw, dict) else {}
    return {"record_index": index, "firm_id": _text(item.get("firm_id"), 120),
            "reason": reason, "source_url": _url(item.get("source_url")),
            "published_at": _text(item.get("published_at"), 50)}


def analyze_targets(payload: dict, *, ticker: str, as_of: str) -> dict:
    """Validate sourced records and aggregate one latest eligible target per firm.

    Input requires ticker, security_id, records, and explicit record provenance.
    Date-only values mean end of that UTC day. Exact time values need a timezone.
    A historical as-of requires both publication and retrieval by that cutoff.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be a mapping")
    cutoff = _instant(as_of)
    if cutoff is None:
        raise ValueError("as_of must be an ISO date or timezone-aware timestamp")
    symbol = _text(ticker, 40)
    if not symbol or not re.fullmatch(r"[A-Za-z0-9.^_-]+", symbol):
        raise ValueError("invalid ticker")
    symbol = symbol.upper()
    if payload.get("ticker") != symbol:
        raise ValueError("payload ticker does not match requested ticker")
    security = _text(payload.get("security_id"), 120)
    if not security:
        raise ValueError("security_id is required")
    raw_records = payload.get("records")
    if not isinstance(raw_records, list):
        raise ValueError("records must be a list")
    max_age = _integer(payload.get("max_age_days", 90), 1, 3650)
    percentile = _integer(payload.get("top_percentile", 20), 1, 100)
    quote_age = _integer(payload.get("max_quote_age_days", 7), 1, 365)
    if max_age is None or percentile is None or quote_age is None:
        raise ValueError("invalid policy parameter")
    requested = payload.get("requested_firms")
    if requested is not None:
        if not isinstance(requested, list) or any(not isinstance(item, str) or
           not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", item) for item in requested):
            raise ValueError("requested_firms must be canonical firm IDs")
        requested = sorted(set(requested))

    exclusions: list[dict] = []
    events: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for index, raw in enumerate(raw_records):
        if not isinstance(raw, dict):
            exclusions.append(_exclusion(index, raw, "not_a_record"))
            continue
        firm_id = _text(raw.get("firm_id"), 120)
        firm = _text(raw.get("firm"), 160)
        if not firm_id or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", firm_id) or not firm:
            exclusions.append(_exclusion(index, raw, "firm_identity_missing_or_invalid"))
            continue
        if raw.get("security_id") != security:
            exclusions.append(_exclusion(index, raw, "security_mismatch"))
            continue
        published, retrieved = _instant(raw.get("published_at")), _instant(raw.get("retrieved_at"))
        source = _url(raw.get("source_url"))
        if not published or not retrieved or not source or retrieved < published:
            exclusions.append(_exclusion(index, raw, "provenance_missing_or_invalid"))
            continue
        if published > cutoff or retrieved > cutoff:
            exclusions.append(_exclusion(index, raw, "after_as_of_cutoff"))
            continue
        status = raw.get("status", "active")
        if not isinstance(status, str) or status not in {"active", "rating_only", "withdrawn"}:
            exclusions.append(_exclusion(index, raw, "invalid_status"))
            continue
        events[(firm_id, security)].append({"index": index, "raw": raw, "firm_id": firm_id,
                                            "firm": firm, "published": published,
                                            "retrieved": retrieved, "source_url": source,
                                            "status": status})

    selected: list[dict] = []
    for (_firm, _security), group in sorted(events.items()):
        if requested is not None and _firm not in requested:
            for event in group:
                exclusions.append(_exclusion(event["index"], event["raw"], "not_requested_firm"))
            continue
        group.sort(key=lambda event: (event["published"], event["index"]))
        latest_target_time = max((event["published"] for event in group if event["status"] == "active"), default=None)
        withdrawal_time = max((event["published"] for event in group if event["status"] == "withdrawn"), default=None)
        if latest_target_time is None:
            for event in group:
                exclusions.append(_exclusion(event["index"], event["raw"], "no_target_action"))
            continue
        if withdrawal_time is not None and withdrawal_time >= latest_target_time:
            for event in group:
                exclusions.append(_exclusion(event["index"], event["raw"], "coverage_withdrawn" if event["published"] == withdrawal_time else "superseded_by_withdrawal"))
            continue
        latest = [event for event in group if event["published"] == latest_target_time and event["status"] == "active"]
        # A simultaneous withdrawal or rating-only record makes ordering unknowable.
        simultaneous = [event for event in group if event["published"] == latest_target_time and event["status"] != "active"]
        signatures = {(_positive(event["raw"].get("target")), _currency(event["raw"].get("currency")),
                       _text(event["raw"].get("share_basis"), 120),
                       event["raw"].get("horizon_months") if type(event["raw"].get("horizon_months")) is int else None)
                      for event in latest}
        if simultaneous or len(signatures) != 1:
            for event in group:
                exclusions.append(_exclusion(event["index"], event["raw"], "conflicting_latest" if event["published"] == latest_target_time else "superseded"))
            continue
        chosen = min(latest, key=lambda event: (event["retrieved"], event["index"]))
        attribution_or_ranking_conflict = (
            len({_text(event["raw"].get("analyst"), 120) for event in latest}) > 1
            or len({str(_ranking(event["raw"].get("ranking"), cutoff, max_age)) for event in latest}) > 1
        )
        for event in group:
            if event is not chosen:
                reason = "duplicate_reprint" if event in latest else "rating_only_no_refresh" if event["status"] == "rating_only" else "superseded"
                exclusions.append(_exclusion(event["index"], event["raw"], reason))
        raw = chosen["raw"]
        target = _positive(raw.get("target"))
        currency = _currency(raw.get("currency"))
        basis = _text(raw.get("share_basis"), 120)
        horizon = raw.get("horizon_months")
        if horizon is not None:
            horizon = _integer(horizon, 1, 120)
        if target is None or currency is None or not basis or (raw.get("horizon_months") is not None and horizon is None):
            exclusions.append(_exclusion(chosen["index"], raw, "target_or_basis_invalid"))
            continue
        age = (cutoff - chosen["published"]).total_seconds() / 86400
        if age > max_age:
            exclusions.append(_exclusion(chosen["index"], raw, "target_too_old"))
            continue
        target_end = _end_date(chosen["published"], horizon)
        if target_end is not None and _instant(target_end) < cutoff:
            exclusions.append(_exclusion(chosen["index"], raw, "target_horizon_expired"))
            continue
        selected.append({"firm_id": chosen["firm_id"], "firm": chosen["firm"],
                         "analyst": _text(raw.get("analyst"), 120), "target": target,
                         "currency": currency, "security_id": security, "share_basis": basis,
                         "horizon": {"months": horizon, "label": f"{horizon} months" if horizon is not None else "unknown"},
                         "published_at": _iso(chosen["published"]), "retrieved_at": _iso(chosen["retrieved"]),
                         "target_end_date": target_end,
                         "age_days": round(age, 6), "source_url": chosen["source_url"],
                         "rating": _text(raw.get("rating"), 80), "action": _text(raw.get("action"), 80),
                         "thesis": _text(raw.get("thesis"), 500),
                         "ranking": None if attribution_or_ranking_conflict else _ranking(raw.get("ranking"), cutoff, max_age),
                         "ranking_conflict": attribution_or_ranking_conflict})

    raw_quote = payload.get("quote")
    quote = None
    quote_issue = None
    if raw_quote is not None:
        if not isinstance(raw_quote, dict):
            quote_issue = "invalid_quote"
        else:
            observed = _instant(raw_quote.get("observed_at"))
            price = _positive(raw_quote.get("price"))
            currency = _currency(raw_quote.get("currency"))
            basis = _text(raw_quote.get("share_basis"), 120)
            source = _url(raw_quote.get("source_url"))
            if not all((observed, price, currency, basis, source)) or raw_quote.get("security_id") != security:
                quote_issue = "invalid_or_mismatched_quote"
            elif observed > cutoff or cutoff - observed > timedelta(days=quote_age):
                quote_issue = "quote_outside_as_of_or_age_limit"
            else:
                quote = {"price": price, "currency": currency, "security_id": security,
                         "share_basis": basis, "observed_at": _iso(observed), "source_url": source}

    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for record in selected:
        grouped[(record["security_id"], record["currency"], record["share_basis"],
                 record["horizon"]["months"])].append(record)
    cohorts = []
    for (sec, currency, basis, months), records in sorted(grouped.items(), key=lambda pair: tuple(str(x) for x in pair[0])):
        records.sort(key=lambda record: record["firm_id"])
        ranks = [record["ranking"] for record in records]
        top_group: dict
        if any(rank is None for rank in ranks):
            reason = ("conflicting_duplicate_attribution_or_ranking" if any(item["ranking_conflict"] for item in records)
                      else "ranking_metadata_missing_or_invalid")
            top_group = {"status": "unavailable", "reason": reason, "summary": None, "targets": []}
        else:
            rank_keys = {(rank["provider"], rank["method"], rank["as_of"], rank["universe_size"]) for rank in ranks}
            if len(rank_keys) != 1:
                top_group = {"status": "unavailable", "reason": "ranking_universes_incompatible", "summary": None, "targets": []}
            else:
                boundary = max(1, math.ceil(ranks[0]["universe_size"] * percentile / 100))
                ranked = [record for record in records if record["ranking"]["rank"] <= boundary]
                top_group = {"status": "available" if ranked else "unavailable",
                             "reason": None if ranked else "no_eligible_firm_in_top_percentile",
                             "selection": {"provider": ranks[0]["provider"], "method": ranks[0]["method"],
                                           "as_of": ranks[0]["as_of"], "universe_size": ranks[0]["universe_size"],
                                           "top_percentile": percentile, "max_rank": boundary},
                             "summary": _summary(ranked) if ranked else None, "targets": ranked}
        implied = None
        if quote and months is not None and quote["currency"] == currency and quote["share_basis"] == basis:
            implied = {"quote": quote, "mean_percent": (_summary(records)["mean"] / quote["price"] - 1) * 100,
                       "median_percent": (_summary(records)["median"] / quote["price"] - 1) * 100}
            if top_group["status"] == "available":
                implied["top_mean_percent"] = (top_group["summary"]["mean"] / quote["price"] - 1) * 100
                implied["top_median_percent"] = (top_group["summary"]["median"] / quote["price"] - 1) * 100
        cohorts.append({"key": {"security_id": sec, "currency": currency, "share_basis": basis,
                                "horizon": {"months": months, "label": f"{months} months" if months is not None else "unknown"}},
                        "summary": _summary(records), "targets": records, "top_group": top_group,
                        "implied_price_return": implied})
    exclusions.sort(key=lambda item: (item["record_index"], item["reason"]))
    universe = None
    if requested is not None:
        observed = {key[0] for key in events}
        eligible = {item["firm_id"] for item in selected}
        universe = {"requested": requested, "observed": sorted(observed.intersection(requested)),
                    "eligible": sorted(eligible.intersection(requested)),
                    "missing": sorted(set(requested) - observed)}
    return {"ticker": symbol, "security_id": security, "as_of": _iso(cutoff),
            "policy": {"max_age_days": max_age, "max_quote_age_days": quote_age, "top_percentile": percentile,
                       "cutoff_requires_retrieval_by_as_of": True},
            "cohorts": cohorts, "exclusions": exclusions, "quote": quote, "quote_issue": quote_issue,
            "firm_universe": universe,
            "method": "unweighted_latest_eligible_target_per_canonical_firm; sourced opinions, not valuation"}


def render_consensus_markdown(result: dict) -> str:
    """Render a compact report from the sanitized derived result."""
    lines = ["## Firm-level analyst targets", "",
             f"As of {result['as_of']}; source-attributed analyst opinions, not a valuation.", ""]
    if not result["cohorts"]:
        lines.append("No eligible firm-level targets.")
    for cohort in result["cohorts"]:
        key, summary = cohort["key"], cohort["summary"]
        lines += [f"### {key['security_id']} · {key['currency']} · {key['share_basis']} · {key['horizon']['label']}", "",
                  f"{summary['count']} firms; mean {summary['mean']:.2f}, median {summary['median']:.2f}, range {summary['min']:.2f}–{summary['max']:.2f}. Median target age {summary['median_age_days']:.1f} days.", "",
                  "| Firm | Analyst | Target | Published | End date | Rating/action | Source |", "| --- | --- | ---: | --- | --- | --- | --- |"]
        def esc(value):
            return escape(str(value or "—")).replace("|", "\\|").replace("\n", " ")

        for item in cohort["targets"]:
            lines.append(f"| {esc(item['firm'])} | {esc(item['analyst'])} | {item['target']:.2f} {item['currency']} | {item['published_at']} | {item['target_end_date'] or 'unknown'} | {esc(item['rating'])} / {esc(item['action'])} | [Source]({item['source_url']}) |")
        lines.append("")
        for item in cohort["targets"]:
            if item["thesis"]:
                lines.append(f"- {esc(item['firm'])}: {esc(item['thesis'])}")
        if summary["target_end_date_min"]:
            lines.append(f"\nTarget end dates range from {summary['target_end_date_min']} to {summary['target_end_date_max']}; rolling targets do not share a common endpoint.")
        top = cohort["top_group"]
        if top["status"] == "available":
            lines.append(f"\nProvider-ranked top {top['selection']['top_percentile']}% subset: {top['summary']['count']} observed firms; mean {top['summary']['mean']:.2f}. Ranking does not establish target accuracy.")
        else:
            lines.append(f"\nProvider-ranked subset unavailable ({top['reason']}).")
        implied = cohort["implied_price_return"]
        if implied:
            lines.append(f"Mean target versus eligible {implied['quote']['observed_at']} quote: {implied['mean_percent']:+.1f}% opinion-implied price change.")
        lines.append("")
    if result["exclusions"]:
        lines.append(f"Excluded input records: {len(result['exclusions'])}. See structured exclusions for reasons and provenance.")
    return "\n".join(lines).strip() + "\n"
