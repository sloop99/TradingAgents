"""Bounded, fail-closed extraction of capital-structure Inline XBRL candidates.

The observations produced here deliberately use ``filing_`` metric names.  A
filing tag and its context are useful evidence, but they do not by themselves
establish consolidated scope, debt completeness, an ADR ratio, or the absence
of stock splits.  A later reconciliation step must promote any candidate used
in a valuation.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

from lxml import etree

from .models import EvidenceFact

_MAX_SOURCE_BYTES = 16 * 1024 * 1024
_MAX_ELEMENTS = 250_000
_MAX_CANDIDATES = 5_000
_MAX_LISTING_CANDIDATES = 1_000
_MAX_LISTING_TEXT = 500
_MAX_SCALE = 18
_SNIPPET_LIMIT = 600

_TRANSFORMATION_URIS = (
    "http://www.xbrl.org/inlineXBRL/transformation/",
    "http://www.sec.gov/inlineXBRL/transformation/",
)

# metric suffix -> required unit family.  These names describe the reported
# concept narrowly and never imply that a component is a complete total.
_CONCEPTS: dict[str, tuple[str, str]] = {
    # Shares and common equity.
    "EntityCommonStockSharesOutstanding": ("shares_outstanding", "shares"),
    "CommonStockSharesOutstanding": ("common_stock_shares_outstanding", "shares"),
    "CommonStockSharesIssued": ("common_stock_shares_issued", "shares"),
    "CommonStockSharesAuthorized": ("common_stock_shares_authorized", "shares"),
    "TreasuryStockShares": ("treasury_stock_shares", "shares"),
    "WeightedAverageNumberOfSharesOutstandingBasic": (
        "weighted_average_shares_basic",
        "shares",
    ),
    "WeightedAverageNumberOfDilutedSharesOutstanding": (
        "weighted_average_shares_diluted",
        "shares",
    ),
    "CommonStocksIncludingAdditionalPaidInCapital": (
        "common_stock_and_additional_paid_in_capital",
        "currency",
    ),
    "CommonStockValue": ("common_stock_carrying_value", "currency"),
    # Borrowings and leases remain separate reported scopes.
    "ShortTermBorrowings": ("short_term_borrowings", "currency"),
    "ShortTermDebtCurrent": ("short_term_debt_current", "currency"),
    "DebtCurrent": ("debt_current", "currency"),
    "CommercialPaper": ("commercial_paper", "currency"),
    "LongTermDebt": ("long_term_debt_reported", "currency"),
    "LongTermDebtCurrent": ("long_term_debt_current", "currency"),
    "LongTermDebtNoncurrent": ("long_term_debt_noncurrent", "currency"),
    "LongTermDebtAndFinanceLeaseObligations": (
        "long_term_debt_and_finance_leases_reported",
        "currency",
    ),
    "LongTermDebtAndFinanceLeaseObligationsCurrent": (
        "long_term_debt_and_finance_leases_current",
        "currency",
    ),
    "LongTermDebtAndFinanceLeaseObligationsNoncurrent": (
        "long_term_debt_and_finance_leases_noncurrent",
        "currency",
    ),
    "FinanceLeaseLiability": ("finance_lease_liability_reported", "currency"),
    "FinanceLeaseLiabilityCurrent": ("finance_lease_liability_current", "currency"),
    "FinanceLeaseLiabilityNoncurrent": (
        "finance_lease_liability_noncurrent",
        "currency",
    ),
    "OperatingLeaseLiability": ("operating_lease_liability_reported", "currency"),
    "OperatingLeaseLiabilityCurrent": ("operating_lease_liability_current", "currency"),
    "OperatingLeaseLiabilityNoncurrent": (
        "operating_lease_liability_noncurrent",
        "currency",
    ),
    "ConvertibleNotesPayable": ("convertible_notes_payable_reported", "currency"),
    # Cash scopes stay distinct.
    "CashAndCashEquivalentsAtCarryingValue": ("cash_and_cash_equivalents", "currency"),
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents": (
        "cash_and_restricted_cash",
        "currency",
    ),
    "CashAndDueFromBanks": ("cash_and_due_from_banks", "currency"),
    # Preferred interests and NCI.
    "PreferredStockValue": ("preferred_stock_carrying_value", "currency"),
    "PreferredStockLiquidationPreferenceValue": (
        "preferred_stock_liquidation_preference",
        "currency",
    ),
    "PreferredStockSharesOutstanding": ("preferred_stock_shares_outstanding", "shares"),
    "PreferredStockSharesIssued": ("preferred_stock_shares_issued", "shares"),
    "PreferredStockSharesAuthorized": ("preferred_stock_shares_authorized", "shares"),
    "RedeemablePreferredStockCarryingAmount": (
        "redeemable_preferred_stock_carrying_amount",
        "currency",
    ),
    "MinorityInterest": ("noncontrolling_interest_carrying_amount", "currency"),
    "NoncontrollingInterestInConsolidatedEntity": (
        "noncontrolling_interest_in_consolidated_entity",
        "currency",
    ),
    "RedeemableNoncontrollingInterestEquityCarryingAmount": (
        "redeemable_noncontrolling_interest_carrying_amount",
        "currency",
    ),
    # Split ratios and common-holder income.
    "StockSplitConversionRatio1": ("stock_split_conversion_ratio", "pure"),
    "StockSplitConversionRatio": ("stock_split_conversion_ratio", "pure"),
    "NetIncomeLossAvailableToCommonStockholdersBasic": (
        "net_income_available_to_common_basic",
        "currency",
    ),
    "NetIncomeLossAvailableToCommonStockholdersDiluted": (
        "net_income_available_to_common_diluted",
        "currency",
    ),
    "IncomeLossFromContinuingOperationsAvailableToCommonStockholdersBasic": (
        "continuing_income_available_to_common_basic",
        "currency",
    ),
    # IFRS concepts encountered in SEC foreign-private-issuer filings.
    "CashAndCashEquivalents": ("cash_and_cash_equivalents", "currency"),
    "BorrowingsCurrent": ("borrowings_current", "currency"),
    "BorrowingsNoncurrent": ("borrowings_noncurrent", "currency"),
    "NoncontrollingInterests": ("noncontrolling_interests", "currency"),
}

_STANDARD_PREFIXES = {"dei", "us-gaap", "ifrs-full"}
_CUSTOM_MARKERS = (
    "commonstockshares",
    "preferredstock",
    "sharesoutstanding",
    "sharesissued",
    "sharesauthorized",
    "weightedaverageshares",
    "treasurystockshares",
    "borrowings",
    "notespayable",
    "debtdue",
    "debtoutstanding",
    "debtobligation",
    "debtliability",
    "cashandcashequivalent",
    "noncontrollinginterest",
    "minorityinterest",
    "stocksplit",
    "incomeavailabletocommon",
    "incomeattributabletocommon",
)

_LISTING_CONCEPTS = {
    "Security12bTitle": "security_title",
    "TradingSymbol": "trading_symbol",
    "SecurityExchangeName": "exchange_name",
}


def extract_filing_evidence(
    html: str,
    *,
    ticker: str,
    cik: str,
    accession: str,
    source_url: str,
    published_at: str,
    retrieved_at: str,
) -> dict[str, Any]:
    """Extract narrow Inline XBRL candidates from one SEC filing document.

    Parsing is bounded and uses an XML parser with DTD loading, entity
    substitution, and network access disabled.  If strict XHTML parsing fails,
    a no-network HTML parser is used; all contexts, units, transforms, and CIKs
    still have to pass the same explicit safety checks.
    """

    normalized_ticker = str(ticker).strip().upper()
    supplied_cik = str(cik).strip()
    identity = {"ticker": normalized_ticker, "cik": supplied_cik}
    source_text = html if isinstance(html, str) else ""
    source_bytes = source_text.encode("utf-8")
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    metadata: dict[str, Any] = {
        "parser": "inline_xbrl_capital_candidates_v1",
        "accession": str(accession).strip(),
        "source_url": str(source_url).strip(),
        "published_at": str(published_at).strip(),
        "retrieved_at": str(retrieved_at).strip(),
        "source_sha256": source_hash,
        "source_bytes": len(source_bytes),
        "element_count": 0,
        "candidates": [],
        "listing_candidates": [],
        "contexts": {},
    }
    result: dict[str, Any] = {
        "identity": identity,
        "facts": [],
        "documents": [],
        "issues": [],
        "coverage": {
            "identity": "partial",
            "documents": "unsupported",
            "facts": "unsupported",
            "point_in_time": "partial",
        },
        "metadata": metadata,
    }

    def issue(code: str, message: str, severity: str = "warning", metric: str | None = None) -> None:
        record: dict[str, Any] = {"code": code, "message": message, "severity": severity}
        if metric:
            record["metric"] = metric
        # One issue per code/metric keeps hostile documents from inflating output.
        if not any(
            item["code"] == code and item.get("metric") == metric for item in result["issues"]
        ):
            result["issues"].append(record)

    if not all((normalized_ticker, supplied_cik, str(accession).strip(), str(source_url).strip())):
        issue("filing_input_invalid", "Ticker, CIK, accession, and source URL are required.", "error")
        return result
    expected_cik = _canonical_cik(supplied_cik)
    if expected_cik is None:
        issue("filing_cik_invalid", "The supplied CIK is not a decimal SEC identifier.", "error")
        return result
    if not source_text.strip():
        issue("filing_empty", "The filing document is empty.", "error")
        return result
    if len(source_bytes) > _MAX_SOURCE_BYTES:
        issue(
            "filing_too_large",
            f"The filing is {len(source_bytes)} bytes; the parser limit is {_MAX_SOURCE_BYTES}.",
            "error",
        )
        return result
    if re.search(r"<!\s*ENTITY\b", source_text, flags=re.IGNORECASE):
        issue("filing_entity_declaration", "Inline XBRL with entity declarations is not parsed.", "error")
        return result

    root, parse_mode = _parse_document(source_bytes)
    if root is None:
        issue("filing_parse_failed", "The filing is not parseable XHTML or HTML.", "error")
        return result
    metadata["parse_mode"] = parse_mode
    elements = list(root.iter())
    metadata["element_count"] = len(elements)
    if len(elements) > _MAX_ELEMENTS:
        issue(
            "filing_element_limit",
            f"The filing has {len(elements)} elements; the parser limit is {_MAX_ELEMENTS}.",
            "error",
        )
        return result

    namespace_uris = _namespace_declarations(root, source_text)
    contexts = _read_contexts(elements, expected_cik, namespace_uris)
    metadata["contexts"] = contexts
    units = _read_units(elements, namespace_uris)
    if not contexts:
        issue("filing_contexts_missing", "No Inline XBRL contexts were found.", "error")

    mismatched = [key for key, value in contexts.items() if value.get("reason") == "cik_mismatch"]
    if mismatched:
        issue(
            "filing_entity_mismatch",
            f"Context entity CIK does not match supplied CIK {supplied_cik}; affected contexts were excluded.",
            "error",
        )
    if any(value.get("safe") for value in contexts.values()):
        result["coverage"]["identity"] = "sufficient"

    document_id = f"sec-inline:{str(accession).strip()}"
    period_ends = sorted(
        {str(value["period_end"]) for value in contexts.values() if value.get("safe")}
    )
    document: dict[str, Any] = {
        "document_id": document_id,
        "form": "INLINE_XBRL",
        "published_at": str(published_at).strip(),
        "source_url": str(source_url).strip(),
        "accession": str(accession).strip(),
        "title": _document_title(elements) or str(accession).strip(),
    }
    if period_ends:
        document["period_end"] = period_ends[-1]
    result["documents"].append(document)
    result["coverage"]["documents"] = "partial"

    _extract_listing_candidates(
        elements=elements,
        contexts=contexts,
        namespace_uris=namespace_uris,
        source_url=str(source_url).strip(),
        source_sha256=source_hash,
        output=metadata["listing_candidates"],
        issue=issue,
    )

    candidate_count = 0
    used_node_ids: set[str] = set()
    for element in elements:
        if not _is_inline_element(element, "nonfraction", namespace_uris):
            continue
        concept = _attribute(element, "name")
        classification = _classify_concept(concept, element, namespace_uris)
        if classification is None:
            continue
        semantic, expected_unit, custom = classification
        metric = f"filing_{semantic}"
        context_ref = _attribute(element, "contextref")
        unit_ref = _attribute(element, "unitref")
        path = element.getroottree().getpath(element)
        source_node_id, source_locator, anchored_url = _source_locator(
            element, str(source_url).strip(), path, used_node_ids
        )
        candidate: dict[str, Any] = {
            "status": "rejected",
            "metric": metric,
            "concept": concept,
            "custom": custom,
            "context_id": context_ref,
            "unit_ref": unit_ref,
            "source_node_id": source_node_id,
            "source_locator": source_locator,
            "source_url": anchored_url,
            "snippet": _snippet(element),
        }
        metadata["candidates"].append(candidate)
        candidate_count += 1
        if candidate_count > _MAX_CANDIDATES:
            metadata["candidates"].pop()
            issue(
                "filing_candidate_limit",
                f"More than {_MAX_CANDIDATES} capital-structure candidates were present; the remainder were excluded.",
                "error",
            )
            break

        if _is_nil(element):
            candidate["reason"] = "nil"
            issue("filing_nil_fact", "Nil Inline XBRL candidates were excluded.", metric=metric)
            continue
        if _attribute(element, "continuedat"):
            candidate["reason"] = "continuation_unsupported"
            issue(
                "filing_continuation_unsupported",
                "Continued numeric candidates were excluded rather than partially parsed.",
                metric=metric,
            )
            continue
        context = contexts.get(context_ref)
        if context is None:
            candidate["reason"] = "missing_context"
            issue(
                "filing_context_missing",
                f"Candidate references missing context {context_ref!r}.",
                "error",
                metric,
            )
            continue
        if not context.get("safe"):
            candidate["reason"] = str(context.get("reason") or "unsafe_context")
            code = (
                "filing_typed_dimension"
                if context.get("reason") == "typed_member"
                else "filing_context_unsafe"
            )
            issue(code, "Unsafe Inline XBRL contexts were excluded.", "error", metric)
            continue
        unit = units.get(unit_ref)
        if unit is None or not _unit_matches(unit, expected_unit):
            candidate["reason"] = "invalid_unit"
            issue(
                "filing_unit_invalid",
                f"Candidate has a missing, unsupported, or incompatible unit {unit_ref!r}.",
                "error",
                metric,
            )
            continue
        numeric = _parse_numeric(element, namespace_uris)
        if numeric["value"] is None:
            candidate["reason"] = str(numeric["reason"])
            issue(
                "filing_numeric_unsafe",
                f"Candidate numeric value was excluded: {numeric['reason']}.",
                "error",
                metric,
            )
            continue

        candidate.update(
            {
                "status": "accepted",
                "raw_text": numeric["raw_text"],
                "format": numeric["format"],
                "scale": numeric["scale"],
                "sign": numeric["sign"],
                "decimals": _attribute(element, "decimals") or None,
                "value": numeric["value"],
                "unit": unit["canonical"],
                "unit_measures": unit["measures"],
                "source_sha256": source_hash,
                "context_signature": context["signature"],
                "period_start": context.get("period_start"),
                "period_end": context["period_end"],
                "period_type": context["period_type"],
                "dimensions": context["dimensions"],
            }
        )
        dimensions_text = json.dumps(
            context["dimensions"], sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        definition = (
            "Inline XBRL candidate only; "
            f"concept={concept}; context={context_ref}; period_type={context['period_type']}; "
            f"period_start={context.get('period_start') or ''}; period_end={context['period_end']}; "
            f"unit_ref={unit_ref}; unit={unit['canonical']}; scale={numeric['scale']}; "
            f"sign={numeric['sign']}; dimensions={dimensions_text}"
        )
        id_material = json.dumps(
            {
                "document": document_id,
                "source_sha256": source_hash,
                "element_path": path,
                "source_node_id": source_node_id,
                "concept": concept,
                "context": context_ref,
                "context_signature": context["signature"],
                "unit": unit["canonical"],
                "value": numeric["decimal_text"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        fact_id = f"sec-inline:{hashlib.sha256(id_material.encode('utf-8')).hexdigest()}"
        fact_data = {
            "fact_id": fact_id,
            "metric": metric,
            "value": numeric["value"],
            "unit": unit["canonical"],
            "period_start": context.get("period_start"),
            "period_end": context["period_end"],
            "published_at": str(published_at).strip(),
            "retrieved_at": str(retrieved_at).strip(),
            "source_url": candidate["source_url"],
            "accession": str(accession).strip(),
            "source_tag": concept,
            "definition": definition,
            "adjustment_basis": "filing_context_unreconciled",
            "kind": "reported",
        }
        # Validate against the public model before returning provider data.
        fact = EvidenceFact.from_dict(fact_data)
        result["facts"].append(fact.to_dict())
        candidate["fact_id"] = fact_id

    if result["facts"]:
        result["coverage"]["facts"] = "partial"
    return result


def _extract_listing_candidates(
    *,
    elements: list[etree._Element],
    contexts: dict[str, dict[str, Any]],
    namespace_uris: dict[str, str],
    source_url: str,
    source_sha256: str,
    output: list[dict[str, Any]],
    issue: Any,
) -> None:
    """Surface bounded, context-paired DEI listing text for later review."""

    listing_nodes: list[tuple[etree._Element, str, str]] = []
    id_counts: dict[str, int] = {}
    for element in elements:
        node_id = _attribute(element, "id")
        if node_id:
            id_counts[node_id] = id_counts.get(node_id, 0) + 1
    for element in elements:
        if not _is_inline_element(element, "nonnumeric", namespace_uris):
            continue
        concept = _attribute(element, "name")
        field = _listing_field(concept, element, namespace_uris)
        if field is None:
            continue
        listing_nodes.append((element, concept, field))

    used_node_ids: set[str] = set()
    for element, concept, field in listing_nodes[:_MAX_LISTING_CANDIDATES]:
        context_ref = _attribute(element, "contextref")
        path = element.getroottree().getpath(element)
        source_node_id, source_locator, anchored_url = _source_locator(
            element, source_url, path, used_node_ids
        )
        candidate: dict[str, Any] = {
            "status": "rejected",
            "concept": concept,
            "field": field,
            "context_id": context_ref,
            "source_node_id": source_node_id,
            "source_locator": source_locator,
            "source_url": anchored_url,
            "source_sha256": source_sha256,
            "snippet": _listing_snippet(element),
        }
        output.append(candidate)

        supplied_id = _attribute(element, "id")
        if supplied_id and id_counts.get(supplied_id, 0) != 1:
            candidate.update(
                {
                    "reason": "duplicate_node_id",
                    "source_node_id": None,
                    "source_locator": f"xpath:{path}",
                    "source_url": source_url,
                }
            )
            issue(
                "filing_listing_duplicate_node_id",
                "Listing text with an ambiguous source node ID was excluded.",
                "error",
            )
            continue
        if _is_nil(element):
            candidate["reason"] = "nil"
            issue("filing_listing_nil", "Nil listing-text candidates were excluded.")
            continue
        if _attribute(element, "continuedat"):
            candidate["reason"] = "continuation_unsupported"
            issue(
                "filing_listing_continuation_unsupported",
                "Continued listing text was excluded rather than partially resolved.",
            )
            continue
        format_name = _attribute(element, "format")
        display_text_format = False
        if format_name:
            display_text_format = _is_sec_exchange_display_format(
                format_name, field, element, namespace_uris
            )
            if not display_text_format:
                candidate["reason"] = "transform_unsupported"
                issue(
                    "filing_listing_transform_unsupported",
                    "Unsupported or untrusted transformed listing text was excluded.",
                )
                continue
        context = contexts.get(context_ref)
        if context is None:
            candidate["reason"] = "missing_context"
            issue(
                "filing_listing_context_missing",
                f"Listing text references missing context {context_ref!r}.",
                "error",
            )
            continue
        if not context.get("safe"):
            candidate["reason"] = str(context.get("reason") or "unsafe_context")
            issue(
                "filing_listing_context_unsafe",
                "Listing text with an unsafe context was excluded.",
                "error",
            )
            continue
        raw_text = _visible_numeric_text(element)
        value = " ".join(raw_text.split())
        if not value:
            candidate["reason"] = "empty_text"
            issue("filing_listing_text_invalid", "Empty listing text was excluded.")
            continue
        if len(value) > _MAX_LISTING_TEXT:
            candidate["reason"] = "text_too_long"
            issue(
                "filing_listing_text_limit",
                f"Listing text longer than {_MAX_LISTING_TEXT} characters was excluded.",
                "error",
            )
            continue
        candidate.update(
            {
                "status": "accepted",
                "value": value,
                "period_start": context.get("period_start"),
                "period_end": context["period_end"],
                "period_type": context["period_type"],
                "dimensions": context["dimensions"],
                "context_signature": context["signature"],
            }
        )
        if display_text_format:
            candidate.update(
                {
                    "value_kind": "display_text",
                    "format": format_name,
                    "transformed_value": None,
                }
            )

    if len(listing_nodes) > _MAX_LISTING_CANDIDATES:
        issue(
            "filing_listing_candidate_limit",
            f"More than {_MAX_LISTING_CANDIDATES} listing-text candidates were present; the remainder were excluded.",
            "error",
        )


def _listing_field(
    concept: str, element: etree._Element, namespace_uris: dict[str, str]
) -> str | None:
    if not concept or not _qname_resolves(concept, element, namespace_uris):
        return None
    prefix, local = concept.split(":", 1)
    if local not in _LISTING_CONCEPTS:
        return None
    uri = (element.nsmap or {}).get(prefix) or namespace_uris.get(prefix, "")
    if not uri.startswith("http://xbrl.sec.gov/dei/"):
        return None
    return _LISTING_CONCEPTS[local]


def _is_sec_exchange_display_format(
    format_name: str,
    field: str,
    element: etree._Element,
    namespace_uris: dict[str, str],
) -> bool:
    if field != "exchange_name" or not _qname_resolves(
        format_name, element, namespace_uris
    ):
        return False
    prefix, local = format_name.split(":", 1)
    uri = (element.nsmap or {}).get(prefix) or namespace_uris.get(prefix, "")
    return (
        re.fullmatch(
            r"http://www\.sec\.gov/inlineXBRL/transformation/\d{4}-\d{2}-\d{2}", uri
        )
        is not None
        and local.casefold() == "exchnameen"
    )


def _listing_snippet(element: etree._Element) -> str:
    attributes = []
    for name in ("id", "name", "contextref", "format", "continuedat"):
        value = _attribute(element, name)
        if value:
            attributes.append(f'{name}="{value}"')
    text = " ".join(_visible_numeric_text(element).split())
    compact = f"<ix:nonNumeric {' '.join(attributes)}>{text}</ix:nonNumeric>"
    return compact[:_SNIPPET_LIMIT]


def _parse_document(source: bytes) -> tuple[etree._Element | None, str | None]:
    try:
        parser = etree.XMLParser(
            resolve_entities=False,
            no_network=True,
            load_dtd=False,
            recover=False,
            huge_tree=False,
        )
        return etree.fromstring(source, parser=parser), "xml"
    except (etree.XMLSyntaxError, ValueError):
        try:
            parser = etree.HTMLParser(no_network=True, recover=True, huge_tree=False)
            return etree.fromstring(source, parser=parser), "html"
        except (etree.ParserError, etree.XMLSyntaxError, ValueError):
            return None, None


def _local_name(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.rsplit("}", 1)[-1].rsplit(":", 1)[-1].casefold()


def _is_inline_element(
    element: etree._Element, local_name: str, namespace_uris: dict[str, str]
) -> bool:
    tag = element.tag
    if not isinstance(tag, str) or _local_name(tag) != local_name.casefold():
        return False
    if tag.startswith("{"):
        uri = tag[1:].split("}", 1)[0]
        return uri == "http://www.xbrl.org/2013/inlineXBRL"
    if ":" not in tag:
        return False
    prefix = tag.split(":", 1)[0]
    uri = (element.nsmap or {}).get(prefix) or namespace_uris.get(prefix, "")
    return uri == "http://www.xbrl.org/2013/inlineXBRL"


def _attribute(element: etree._Element, local_name: str) -> str:
    target = local_name.casefold()
    for key, value in element.attrib.items():
        if _local_name(key) == target:
            return str(value).strip()
    return ""


def _namespace_declarations(root: etree._Element, source: str) -> dict[str, str]:
    output = {str(prefix): uri for prefix, uri in (root.nsmap or {}).items() if prefix and uri}
    # lxml's HTML parser treats namespace declarations as ordinary attributes.
    for prefix, uri in re.findall(
        r"\bxmlns:([A-Za-z_][\w.-]*)\s*=\s*['\"]([^'\"]+)['\"]", source[:100_000]
    ):
        output.setdefault(prefix, uri)
    return output


def _canonical_cik(value: str) -> str | None:
    text = str(value).strip()
    if not re.fullmatch(r"\d{1,10}", text):
        return None
    return text.lstrip("0") or "0"


def _descendants(element: etree._Element, name: str) -> list[etree._Element]:
    target = name.casefold()
    return [child for child in element.iterdescendants() if _local_name(child.tag) == target]


def _node_text(element: etree._Element) -> str:
    return "".join(element.itertext()).strip()


def _read_contexts(
    elements: list[etree._Element], expected_cik: str, namespace_uris: dict[str, str]
) -> dict[str, dict[str, Any]]:
    contexts: dict[str, dict[str, Any]] = {}
    for element in elements:
        if not _is_taxonomy_element(
            element, "context", "http://www.xbrl.org/2003/instance", namespace_uris
        ):
            continue
        context_id = _attribute(element, "id")
        if not context_id:
            continue
        if context_id in contexts:
            contexts[context_id] = {
                "entity_cik": None,
                "period_type": None,
                "period_start": None,
                "period_end": None,
                "dimensions": {},
                "safe": False,
                "reason": "duplicate_context_id",
            }
            continue
        record: dict[str, Any] = {
            "entity_cik": None,
            "period_type": None,
            "period_start": None,
            "period_end": None,
            "dimensions": {},
            "safe": False,
            "reason": None,
        }
        identifiers = _descendants(element, "identifier")
        if len(identifiers) != 1 or not _is_taxonomy_element(
            identifiers[0], "identifier", "http://www.xbrl.org/2003/instance", namespace_uris
        ):
            record["reason"] = "missing_or_ambiguous_identifier"
            contexts[context_id] = record
            continue
        identifier_scheme = _attribute(identifiers[0], "scheme").casefold().rstrip("/")
        if identifier_scheme not in {"http://www.sec.gov/cik", "https://www.sec.gov/cik"}:
            record["reason"] = "untrusted_identifier_scheme"
            contexts[context_id] = record
            continue
        entity_cik = _canonical_cik(_node_text(identifiers[0]))
        record["entity_cik"] = _node_text(identifiers[0])
        if entity_cik != expected_cik:
            record["reason"] = "cik_mismatch"
            contexts[context_id] = record
            continue
        typed_members = _descendants(element, "typedmember")
        if typed_members:
            record["reason"] = (
                "typed_member"
                if all(
                    _is_taxonomy_element(
                        member,
                        "typedmember",
                        "http://xbrl.org/2006/xbrldi",
                        namespace_uris,
                    )
                    for member in typed_members
                )
                else "untrusted_context_namespace"
            )
            contexts[context_id] = record
            continue
        dimensions: dict[str, str] = {}
        unsafe_dimension = False
        for member in _descendants(element, "explicitmember"):
            dimension = _attribute(member, "dimension")
            member_name = _node_text(member)
            if (
                not _is_taxonomy_element(
                    member, "explicitmember", "http://xbrl.org/2006/xbrldi", namespace_uris
                )
                or not dimension
                or not member_name
                or not _qname_resolves(dimension, member, namespace_uris)
                or not _qname_resolves(member_name, member, namespace_uris)
                or dimension in dimensions
            ):
                unsafe_dimension = True
                break
            dimensions[dimension] = member_name
        if unsafe_dimension:
            record["reason"] = "unsafe_explicit_dimension"
            contexts[context_id] = record
            continue
        instants = _descendants(element, "instant")
        starts = _descendants(element, "startdate")
        ends = _descendants(element, "enddate")
        period_nodes = [*instants, *starts, *ends]
        if any(
            not _is_taxonomy_element(
                node, _local_name(node.tag), "http://www.xbrl.org/2003/instance", namespace_uris
            )
            for node in period_nodes
        ):
            record["reason"] = "untrusted_context_namespace"
            contexts[context_id] = record
            continue
        if len(instants) == 1 and not starts and not ends:
            period_end = _iso_date(_node_text(instants[0]))
            period_start = None
            period_type = "instant"
        elif not instants and len(starts) == 1 and len(ends) == 1:
            period_start = _iso_date(_node_text(starts[0]))
            period_end = _iso_date(_node_text(ends[0]))
            period_type = "duration"
            if not period_start or not period_end or period_start > period_end:
                period_end = None
        else:
            period_start = period_end = period_type = None
        if not period_end:
            record["reason"] = "invalid_period"
            contexts[context_id] = record
            continue
        record.update(
            {
                "period_type": period_type,
                "period_start": period_start,
                "period_end": period_end,
                "dimensions": dict(sorted(dimensions.items())),
                "safe": True,
            }
        )
        signature = json.dumps(
            {
                "entity_cik": entity_cik,
                "period_type": period_type,
                "period_start": period_start,
                "period_end": period_end,
                "dimensions": record["dimensions"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        record["signature"] = hashlib.sha256(signature.encode("utf-8")).hexdigest()
        contexts[context_id] = record
    return contexts


def _is_taxonomy_element(
    element: etree._Element,
    local_name: str,
    namespace_uri: str,
    namespace_uris: dict[str, str],
) -> bool:
    tag = element.tag
    if not isinstance(tag, str) or _local_name(tag) != local_name.casefold():
        return False
    if tag.startswith("{"):
        return tag[1:].split("}", 1)[0] == namespace_uri
    if ":" not in tag:
        return False
    prefix = tag.split(":", 1)[0]
    uri = (element.nsmap or {}).get(prefix) or namespace_uris.get(prefix, "")
    return uri == namespace_uri


def _iso_date(value: str) -> str | None:
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except (TypeError, ValueError):
        return None


def _qname_resolves(
    value: str, element: etree._Element, namespace_uris: dict[str, str]
) -> bool:
    if ":" not in value:
        return False
    prefix, local = value.split(":", 1)
    if not re.fullmatch(r"[A-Za-z_][\w.-]*", prefix) or not re.fullmatch(
        r"[A-Za-z_][\w.-]*", local
    ):
        return False
    return prefix in (element.nsmap or {}) or prefix in namespace_uris


def _read_units(
    elements: list[etree._Element], namespace_uris: dict[str, str]
) -> dict[str, dict[str, Any]]:
    units: dict[str, dict[str, Any]] = {}
    for element in elements:
        if _local_name(element.tag) != "unit":
            continue
        unit_id = _attribute(element, "id")
        if not unit_id:
            continue
        if unit_id in units:
            units[unit_id] = {}
            continue
        direct_measure_nodes = [
            child for child in element if _local_name(child.tag) == "measure"
        ]
        divide_nodes = [child for child in element if _local_name(child.tag) == "divide"]
        canonical = None
        family = None
        measures: dict[str, Any] = {}
        if len(direct_measure_nodes) == 1 and not divide_nodes:
            measure = _node_text(direct_measure_nodes[0])
            parsed = _measure(measure, direct_measure_nodes[0], namespace_uris)
            if parsed:
                canonical, family = parsed
                measures = {"measure": measure}
        elif len(divide_nodes) == 1 and not direct_measure_nodes:
            numerators = _descendants(divide_nodes[0], "unitnumerator")
            denominators = _descendants(divide_nodes[0], "unitdenominator")
            if len(numerators) == 1 and len(denominators) == 1:
                numerator_measures = _descendants(numerators[0], "measure")
                denominator_measures = _descendants(denominators[0], "measure")
                if len(numerator_measures) == 1 and len(denominator_measures) == 1:
                    numerator_text = _node_text(numerator_measures[0])
                    denominator_text = _node_text(denominator_measures[0])
                    numerator = _measure(numerator_text, numerator_measures[0], namespace_uris)
                    denominator = _measure(
                        denominator_text, denominator_measures[0], namespace_uris
                    )
                    if numerator and denominator and numerator[1] == "currency" and denominator[1] == "shares":
                        canonical = f"{numerator[0]}/shares"
                        family = "currency_per_share"
                        measures = {
                            "numerator": numerator_text,
                            "denominator": denominator_text,
                        }
        if canonical and family:
            units[unit_id] = {
                "canonical": canonical,
                "family": family,
                "measures": measures,
            }
    return units


def _measure(
    value: str, element: etree._Element, namespace_uris: dict[str, str]
) -> tuple[str, str] | None:
    if not _qname_resolves(value, element, namespace_uris):
        return None
    prefix, local = value.split(":", 1)
    uri = (element.nsmap or {}).get(prefix) or namespace_uris.get(prefix, "")
    if local.casefold() == "shares" and "xbrl.org/2003/instance" in uri:
        return "shares", "shares"
    if local.casefold() == "pure" and "xbrl.org/2003/instance" in uri:
        return "pure", "pure"
    if re.fullmatch(r"[A-Z]{3}", local) and "xbrl.org/2003/iso4217" in uri:
        return local, "currency"
    return None


def _unit_matches(unit: dict[str, Any], expected: str) -> bool:
    return expected == "any" or unit.get("family") == expected


def _classify_concept(
    concept: str, element: etree._Element, namespace_uris: dict[str, str]
) -> tuple[str, str, bool] | None:
    if not concept or not _qname_resolves(concept, element, namespace_uris):
        return None
    prefix, local = concept.split(":", 1)
    known = _CONCEPTS.get(local)
    if prefix in _STANDARD_PREFIXES:
        uri = (element.nsmap or {}).get(prefix) or namespace_uris.get(prefix, "")
        expected_uri = {
            "dei": "http://xbrl.sec.gov/dei/",
            "us-gaap": "http://fasb.org/us-gaap/",
            "ifrs-full": "http://xbrl.ifrs.org/taxonomy/",
        }[prefix]
        if not uri.startswith(expected_uri):
            return None
        if known is None:
            return None
        return known[0], known[1], False
    # Issuer extensions are surfaced only as visibly custom candidates.  The
    # heuristic deliberately excludes generic debt-security/investment tags,
    # which often describe assets rather than issuer obligations.
    folded = re.sub(r"[^a-z0-9]", "", local.casefold())
    if "debtsecurit" in folded or "debtinvestment" in folded:
        return None
    if not any(marker in folded for marker in _CUSTOM_MARKERS):
        return None
    slug = _camel_to_snake(local)[:100].strip("_") or "capital_concept"
    return f"custom_{slug}", "any", True


def _camel_to_snake(value: str) -> str:
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"[^A-Za-z0-9]+", "_", value).casefold()


def _source_locator(
    element: etree._Element,
    source_url: str,
    path: str,
    used: set[str],
) -> tuple[str | None, str, str]:
    supplied = _attribute(element, "id")
    if supplied and re.fullmatch(r"[A-Za-z_][\w.:-]{0,199}", supplied) and supplied not in used:
        used.add(supplied)
        return supplied, f"fragment:{supplied}", f"{source_url}#{quote(supplied, safe='-._~')}"
    # An XPath is an auditable locator in the parsed source.  It is explicitly
    # not presented as a fragment that the original document does not contain.
    return None, f"xpath:{path}", source_url


def _is_nil(element: etree._Element) -> bool:
    return _attribute(element, "nil").casefold() in {"true", "1"}


def _parse_numeric(element: etree._Element, namespace_uris: dict[str, str]) -> dict[str, Any]:
    raw_text = _visible_numeric_text(element)
    format_name = _attribute(element, "format")
    transform = ""
    if format_name:
        if not _qname_resolves(format_name, element, namespace_uris):
            return _numeric_error(raw_text, format_name, "unresolved_transform")
        prefix, transform_name = format_name.split(":", 1)
        uri = (element.nsmap or {}).get(prefix) or namespace_uris.get(prefix, "")
        if not any(uri.startswith(allowed) for allowed in _TRANSFORMATION_URIS):
            return _numeric_error(raw_text, format_name, "untrusted_transform_namespace")
        transform = re.sub(r"[-_]", "", transform_name.casefold())
        if transform not in {"numdotdecimal", "numcommadecimal", "zerodash", "fixedzero"}:
            return _numeric_error(raw_text, format_name, "unsupported_transform")
    sign = _attribute(element, "sign") or "+"
    if sign not in {"+", "-"}:
        return _numeric_error(raw_text, format_name, "invalid_sign")
    scale_text = _attribute(element, "scale") or "0"
    if not re.fullmatch(r"[+-]?\d+", scale_text):
        return _numeric_error(raw_text, format_name, "invalid_scale")
    scale = int(scale_text)
    if abs(scale) > _MAX_SCALE:
        return _numeric_error(raw_text, format_name, "scale_out_of_bounds")
    try:
        if transform == "fixedzero":
            number = Decimal(0)
        elif transform == "zerodash":
            if raw_text.strip() not in {"-", "–", "—"}:
                return _numeric_error(raw_text, format_name, "invalid_zero_dash")
            number = Decimal(0)
        elif transform == "numdotdecimal":
            lexical = re.sub(r"[\s\u00a0]", "", raw_text)
            if not re.fullmatch(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", lexical):
                return _numeric_error(raw_text, format_name, "invalid_num_dot_decimal")
            number = Decimal(lexical.replace(",", ""))
        elif transform == "numcommadecimal":
            lexical = re.sub(r"[\s\u00a0]", "", raw_text)
            if not re.fullmatch(r"[+-]?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?", lexical):
                return _numeric_error(raw_text, format_name, "invalid_num_comma_decimal")
            number = Decimal(lexical.replace(".", "").replace(",", "."))
        else:
            lexical = raw_text.strip()
            if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", lexical):
                return _numeric_error(raw_text, format_name, "invalid_untransformed_decimal")
            number = Decimal(lexical)
        number = number.scaleb(scale)
        if sign == "-":
            number = -number
        if not number.is_finite() or number.adjusted() > 307:
            return _numeric_error(raw_text, format_name, "numeric_out_of_bounds")
    except (InvalidOperation, ValueError):
        return _numeric_error(raw_text, format_name, "invalid_numeric")
    normalized = format(number, "f")
    value: int | float = int(number) if number == number.to_integral_value() else float(number)
    return {
        "value": value,
        "decimal_text": normalized,
        "raw_text": raw_text,
        "format": format_name or None,
        "scale": scale,
        "sign": sign,
        "reason": None,
    }


def _numeric_error(raw_text: str, format_name: str, reason: str) -> dict[str, Any]:
    return {
        "value": None,
        "decimal_text": None,
        "raw_text": raw_text,
        "format": format_name or None,
        "scale": None,
        "sign": None,
        "reason": reason,
    }


def _visible_numeric_text(element: etree._Element) -> str:
    parts: list[str] = []

    def visit(node: etree._Element) -> None:
        if node.text:
            parts.append(node.text)
        for child in node:
            if _local_name(child.tag) != "exclude":
                visit(child)
            if child.tail:
                parts.append(child.tail)

    visit(element)
    return "".join(parts).strip()


def _snippet(element: etree._Element) -> str:
    # Serializing an XHTML node repeats every inherited namespace declaration
    # and can consume the bound before the useful value.  Store a compact,
    # synthetic review snippet with only the source attributes and visible text.
    attributes = []
    for name in ("id", "name", "contextref", "unitref", "format", "scale", "sign"):
        value = _attribute(element, name)
        if value:
            attributes.append(f'{name}="{value}"')
    text = " ".join(_visible_numeric_text(element).split())
    compact = f"<ix:nonFraction {' '.join(attributes)}>{text}</ix:nonFraction>"
    return compact[:_SNIPPET_LIMIT]


def _document_title(elements: list[etree._Element]) -> str | None:
    for element in elements:
        if _local_name(element.tag) == "title":
            title = " ".join(_node_text(element).split())
            if title:
                return title[:300]
    return None
