"""Filing-bound share observations for capital-structure review.

This module deliberately inventories individual reported observations.  It does
not select, aggregate, promote, or use them as a valuation input.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urldefrag

from .models import EvidenceFact, FactKind

_KINDS = ("outstanding", "issued", "authorized", "treasury")
_SHARE_CLASS_AXES = {"us-gaap:StatementClassOfStockAxis", "dei:SecurityClassOfStockAxis"}
_SHARE_ROLES = {
    "dei:EntityCommonStockSharesOutstanding": "outstanding",
    "us-gaap:CommonStockSharesOutstanding": "outstanding",
    "us-gaap:CommonStockSharesIssued": "issued",
    "us-gaap:CommonStockSharesAuthorized": "authorized",
    "us-gaap:TreasuryStockShares": "treasury",
}
_SPLIT_TAGS = {"us-gaap:StockSplitConversionRatio", "us-gaap:StockSplitConversionRatio1"}


def analyze_share_inventory(
    facts: list[EvidenceFact], filing_metadata: list[dict], listing_summary: dict, as_of: str
) -> dict:
    """Return provenance-preserving, point-in-time share observations.

    An accepted parser candidate is usable only when it binds exactly to one
    retained reported fact and its filing context.  The returned records are
    evidence for review, never a complete share-class inventory or numeric
    conclusion.
    """
    cutoff = _cutoff(as_of)
    by_id: dict[str, EvidenceFact] = {}
    duplicate_ids: set[str] = set()
    for fact in facts:
        if fact.kind is not FactKind.REPORTED:
            continue
        if fact.fact_id in by_id and by_id[fact.fact_id].to_dict() != fact.to_dict():
            duplicate_ids.add(fact.fact_id)
        else:
            by_id.setdefault(fact.fact_id, fact)
    for fact_id in duplicate_ids:
        by_id.pop(fact_id, None)

    observations: dict[str, list[dict]] = {kind: [] for kind in _KINDS}
    splits: list[dict] = []
    rejected: Counter[str] = Counter()
    bound = 0
    accepted = 0
    seen: set[tuple[str, str, str]] = set()
    for filing in filing_metadata:
        if not isinstance(filing, dict):
            rejected["invalid_filing_metadata"] += 1
            continue
        if not _filing_is_current(filing, cutoff):
            rejected["filing_after_cutoff_or_invalid"] += 1
            continue
        candidates = filing.get("candidates", [])
        if not isinstance(candidates, list):
            rejected["invalid_candidates"] += 1
            continue
        if len(candidates) > 5000:
            rejected["candidate_limit"] += len(candidates) - 5000
        for candidate in candidates[:5000]:
            if not isinstance(candidate, dict) or candidate.get("status") != "accepted":
                continue
            accepted += 1
            fact = by_id.get(str(candidate.get("fact_id") or ""))
            record = _bind(fact, filing, candidate, cutoff)
            if record is None:
                rejected["binding_invalid"] += 1
                continue
            identity = (record["fact_id"], record["source_sha256"], record["context_id"])
            if identity in seen:
                continue
            seen.add(identity)
            bound += 1
            kind = _share_kind(record)
            if kind:
                observations[kind].append(record)
            elif _is_split(record):
                splits.append({
                    **record,
                    "event_effective_date": None,
                    "warning": "Reported period is not evidence of a split effective date; no split adjustment is applied.",
                })

    for values in observations.values():
        values.sort(key=_record_key)
    splits.sort(key=_record_key)
    links, unallocated, ambiguous, unresolved_dimensions = _link_listings(observations, listing_summary)
    status = "partial" if any(observations.values()) or splits else "unsupported"
    return {
        "status": status,
        "as_of": cutoff.isoformat().replace("+00:00", "Z"),
        "counts": {
            "input_reported_facts": len(by_id), "accepted_numeric_candidates": accepted,
            "bound_numeric_candidates": bound,
            "bound_observations": sum(len(rows) for rows in observations.values()),
            "rejected_bindings": sum(rejected.values()),
            "listing_links": len(links), "unallocated_observations": unallocated,
            "ambiguous_listing_observations": ambiguous, "split_candidates": len(splits),
            "unresolved_dimension_observations": unresolved_dimensions,
        },
        "observations": observations,
        "split_candidates": splits,
        "listing_links": links,
        "class_completeness_established": False,
        "split_completeness_established": False,
        "rejected_counts": dict(sorted(rejected.items())),
        "limitations": [
            "No numeric share observation is promoted, selected, summed, or used for valuation.",
            "Weighted-average and duration share facts are excluded from point-in-time observations.",
            "Empty dimensions are unallocated issuer share evidence and do not prove one listed common class is complete.",
            "Listing evidence does not establish share-class completeness, ADR conversion, or split history.",
        ],
    }


def _bind(fact: EvidenceFact | None, filing: dict, candidate: dict, cutoff: datetime) -> dict | None:
    if fact is None or fact.kind is not FactKind.REPORTED or _fact_after(fact, cutoff):
        return None
    digest = filing.get("source_sha256")
    contexts = filing.get("contexts")
    context_id = str(candidate.get("context_id") or "").strip()
    context = contexts.get(context_id) if isinstance(contexts, dict) else None
    if not isinstance(context, dict) or context.get("safe") is not True:
        return None
    try:
        signature = context.get("signature")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            return None
        if not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{64}", signature):
            return None
        if candidate.get("source_sha256") != digest:
            return None
        dimensions = candidate.get("dimensions")
        if not isinstance(dimensions, dict) or not all(isinstance(k, str) and isinstance(v, str) and k and v for k, v in dimensions.items()):
            return None
        checks = (
            bool(fact.accession) and str(candidate.get("accession") or filing.get("accession") or "") == str(fact.accession) == filing.get("accession"),
            candidate.get("metric") == fact.metric,
            filing.get("published_at") == fact.published_at,
            filing.get("retrieved_at") == fact.retrieved_at,
            str(candidate.get("concept") or "") == str(fact.source_tag or ""),
            _decimal(candidate.get("value")) == _decimal(fact.value),
            str(candidate.get("unit") or "").casefold() == fact.unit.casefold(),
            candidate.get("period_start") == fact.period_start == context.get("period_start"),
            str(candidate.get("period_end") or "") == fact.period_end == str(context.get("period_end") or ""),
            urldefrag(str(candidate.get("source_url") or ""))[0] == urldefrag(fact.source_url)[0] == str(filing.get("source_url") or ""),
            candidate.get("source_url") == fact.source_url,
            candidate.get("dimensions") == context.get("dimensions"),
            candidate.get("context_signature") == signature,
        )
        if not all(checks):
            return None
    except (InvalidOperation, TypeError, ValueError):
        return None
    return {
        "fact_id": fact.fact_id, "metric": fact.metric, "value": fact.value, "unit": fact.unit,
        "source_url": fact.source_url, "accession": fact.accession, "source_tag": fact.source_tag,
        "period_start": fact.period_start, "period_end": fact.period_end,
        "published_at": fact.published_at, "retrieved_at": fact.retrieved_at,
        "source_sha256": digest, "context_id": context_id,
        "context_signature": context.get("signature"), "dimensions": dict(sorted(candidate["dimensions"].items())),
    }


def _share_kind(record: dict) -> str | None:
    tag = str(record["source_tag"] or "").casefold()
    metric = str(record["metric"]).casefold()
    if record["unit"].casefold() != "shares" or record["period_start"] is not None or "weighted" in tag or "weighted" in metric:
        return None
    if float(record["value"]) < 0:
        return None
    return _SHARE_ROLES.get(record["source_tag"])


def _is_split(record: dict) -> bool:
    return record["source_tag"] in _SPLIT_TAGS and record["unit"] in {"pure", "ratio"} and float(record["value"]) > 0


def _link_listings(observations: dict[str, list[dict]], summary: dict) -> tuple[list[dict], int, int, int]:
    groups = summary.get("groups", []) if isinstance(summary, dict) else []
    index: dict[tuple[str, str, tuple], list[dict]] = defaultdict(list)
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict):
            continue
        dims = group.get("dimensions")
        digest, accession = group.get("source_sha256"), group.get("accession")
        if isinstance(dims, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in dims.items()) and isinstance(digest, str) and isinstance(accession, str):
            index[(accession, digest, tuple(sorted(dims.items())))].append(group)
    links: list[dict] = []
    unallocated = ambiguous = unresolved_dimensions = 0
    for record in [r for values in observations.values() for r in values]:
        dims = tuple(sorted(record["dimensions"].items()))
        if not dims:
            unallocated += 1
            continue
        if not _supported_share_class_dimensions(dims):
            # Dimensions such as geography, product, debt instrument, or a
            # custom axis do not identify a listed share class.
            unallocated += 1
            unresolved_dimensions += 1
            continue
        matches = index.get((str(record["accession"] or ""), record["source_sha256"], dims), [])
        if len(matches) != 1:
            unallocated += 1
            if matches:
                ambiguous += 1
            continue
        group = matches[0]
        if group.get("ambiguous_fields"):
            unallocated += 1
            ambiguous += 1
            continue
        links.append({"fact_id": record["fact_id"], "listing_accession": group["accession"],
                      "listing_context_signature": group.get("context_signature"),
                      "listing_period_start": group.get("period_start"),
                      "listing_period_end": group.get("period_end"),
                      "share_period_end": record["period_end"],
                      "dimensions": record["dimensions"], "period_caveat":
                      "Listing and numeric observation share source hash and dimensions; their reporting dates may differ and are retained without equivalence inference."})
    return sorted(links, key=lambda x: (x["fact_id"], str(x["listing_context_signature"]))), unallocated, ambiguous, unresolved_dimensions


def _supported_share_class_dimensions(dimensions: tuple[tuple[str, str], ...]) -> bool:
    return len(dimensions) == 1 and dimensions[0][0] in _SHARE_CLASS_AXES


def _filing_is_current(filing: dict, cutoff: datetime) -> bool:
    try:
        return _temporal(str(filing["published_at"])) <= cutoff and _temporal(str(filing["retrieved_at"])) <= cutoff
    except (KeyError, ValueError):
        return False


def _fact_after(fact: EvidenceFact, cutoff: datetime) -> bool:
    try:
        return date.fromisoformat(fact.period_end) > cutoff.date() or _temporal(fact.retrieved_at) > cutoff or bool(fact.published_at and _temporal(fact.published_at) > cutoff)
    except ValueError:
        return True


def _cutoff(value: str) -> datetime:
    if "T" not in value and " " not in value:
        return datetime.combine(date.fromisoformat(value), time.max, timezone.utc)
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("as_of datetime must be timezone-aware")
    return result.astimezone(timezone.utc)


def _temporal(value: str) -> datetime:
    if "T" not in value and " " not in value:
        return datetime.combine(date.fromisoformat(value), time.max, timezone.utc)
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Evidence timestamp must include timezone")
    return result.astimezone(timezone.utc)


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _record_key(value: dict) -> tuple:
    return (value["period_end"], value["fact_id"], value["source_sha256"], value["context_id"])
