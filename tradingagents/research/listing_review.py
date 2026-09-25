"""Bind listing text to retained filings for review, never valuation proofs."""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, time, timezone
from urllib.parse import urldefrag

from .models import EvidenceDocument

_FIELDS = {"security_title", "trading_symbol", "exchange_name"}


def summarize_listing_evidence(filings: list[dict], documents: list[EvidenceDocument],
                               ticker: str, cik: str | None, as_of: str) -> dict:
    """Group source candidates only within one filing and semantic context.

    Document/time/hash/context checks protect review packets from accidental
    metadata mismatches. They do not independently authenticate filing content.
    """
    cutoff = _instant(as_of)
    retained = {(d.accession, d.source_url, _instant(d.published_at)) for d in documents}
    groups: dict[tuple, dict] = {}
    rejected: Counter = Counter()
    seen: set[tuple] = set()
    for filing in filings:
        candidates = filing.get("listing_candidates", [])
        if not isinstance(candidates, list):
            rejected["invalid_listing_candidates"] += 1
            continue
        if not candidates:
            continue
        try:
            published = _instant(filing["published_at"])
            retrieved = _instant(filing["retrieved_at"])
            source = str(filing["source_url"])
            accession = str(filing["accession"])
            digest = str(filing["source_sha256"])
            if published > cutoff or retrieved > cutoff:
                raise ValueError("filing_after_cutoff")
            if (accession, source, published) not in retained:
                raise ValueError("filing_document_not_retained")
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("invalid_source_hash")
        except (ValueError, KeyError, TypeError) as exc:
            rejected[str(exc) or "invalid_filing_metadata"] += 1
            continue
        contexts = filing.get("contexts", {})
        if not isinstance(contexts, dict):
            rejected["invalid_contexts"] += 1
            continue
        if len(candidates) > 1000:
            rejected["listing_candidate_limit"] += len(candidates) - 1000
        for candidate in candidates[:1000]:
            if not isinstance(candidate, dict) or candidate.get("status") != "accepted":
                rejected["parser_rejected_candidate"] += 1
                continue
            try:
                context = contexts.get(candidate.get("context_id"))
                if not isinstance(context, dict) or context.get("safe") is not True:
                    raise ValueError("unsafe_context")
                if not cik or int(context.get("entity_cik", "")) != int(cik):
                    raise ValueError("issuer_mismatch")
                if not isinstance(context.get("signature"), str) or not re.fullmatch(r"[0-9a-f]{64}", context["signature"]) or candidate.get("context_signature") != context["signature"]:
                    raise ValueError("context_signature_mismatch")
                for key in ("period_start", "period_end", "period_type", "dimensions"):
                    if candidate.get(key) != context.get(key):
                        raise ValueError("context_fields_mismatch")
                if date.fromisoformat(context["period_end"]) > cutoff.date():
                    raise ValueError("context_after_cutoff")
                if candidate.get("source_sha256") != digest or urldefrag(candidate["source_url"])[0] != source:
                    raise ValueError("source_binding_mismatch")
                field, value = candidate["field"], candidate["value"]
                if field not in _FIELDS or not isinstance(value, str) or not value.strip() or len(value) > 500:
                    raise ValueError("invalid_listing_text")
                locator = candidate.get("source_locator")
                if not isinstance(locator, str) or not locator:
                    raise ValueError("missing_source_locator")
            except (ValueError, KeyError, TypeError) as exc:
                rejected[str(exc) or "invalid_candidate"] += 1
                continue
            signature = (accession, digest, context["signature"])
            observation = (signature, field, value, locator)
            if observation in seen:
                continue
            seen.add(observation)
            group = groups.setdefault(signature, {
                "accession": accession, "source_url": source, "source_sha256": digest,
                "context_signature": context["signature"], "period_start": context.get("period_start"),
                "period_end": context["period_end"], "period_type": context.get("period_type"),
                "dimensions": context.get("dimensions", {}),
                "fields": {key: [] for key in sorted(_FIELDS)},
            })
            group["fields"][field].append({"value": value, "source_url": candidate["source_url"],
                                           "source_locator": locator,
                                           "value_kind": candidate.get("value_kind", "display_text"),
                                           "format": candidate.get("format"),
                                           "transformed_value": None})
    output = []
    for _, group in sorted(groups.items()):
        values = {key: sorted({item["value"] for item in records}) for key, records in group["fields"].items()}
        group["ambiguous_fields"] = [key for key, items in values.items() if len(items) > 1]
        group["requested_symbol_observed"] = ticker.upper() in {v.upper() for v in values["trading_symbol"]}
        group["complete_listing_triplet"] = all(len(items) == 1 for items in values.values())
        output.append(group)
    return {
        "status": "partial" if output else "unsupported", "groups": output,
        "rejected_counts": dict(sorted(rejected.items())),
        "class_completeness_established": False, "adr_conversion_established": False,
        "split_completeness_established": False,
        "limitations": "Listed securities are not a complete inventory of common equity. Text is source evidence for review, not a capital-structure proof.",
    }


def _instant(value: str) -> datetime:
    if "T" not in value and " " not in value:
        return datetime.combine(date.fromisoformat(value), time.max, timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp_timezone_missing")
    return parsed.astimezone(timezone.utc)
