# Share-class inventory

`financial_analysis.share_inventory` organizes standard filing observations by
their reported scope: outstanding, issued, authorized and treasury shares. It
does not sum or promote these quantities. Weighted-average and duration share
facts, unsupported custom concepts and negative quantities cannot enter the
point-in-time inventory. Repeated source nodes remain source observations, not
independent estimates to average.

Each accepted numeric candidate must match the retained fact's ID, tag, metric,
value, unit, exact source URL, accession, dates and evidence timestamps. Its source
hash and context signature must match the enclosing parsed filing. Older cached
parser metadata without these fields must be reparsed before it can bind; numeric
fact values and IDs remain unchanged. Future or mismatched evidence is rejected.

Candidate listing associations require the same accession, source hash and exact
supported share-class dimension. Empty dimensions remain unallocated issuer
evidence, even if only one common stock is listed. Geographic, product and other
dimensions cannot stand in for share classes. Ambiguous listing groups stay
unallocated. Both the listing-context date and the numeric observation date are
retained; a date difference is not silently reconciled.

Recognized reported split ratios are retained separately. Their filing period is
not treated as the event-effective date, and metadata cannot supply an unchecked
effective-date override. The inventory makes no split adjustments and establishes
neither complete class coverage nor complete split history.

## Validation

Reparsing six cached filings preserved all 388 numeric facts exactly and bound
60 point-in-time share observations: 34 outstanding, 12 issued and 14 authorized.
No treasury quantity was substituted where none was retained. No automatic
listing association satisfied the strict dimension matching in this sample;
the positive association path is tested with a controlled fixture. No supported
numeric split candidate was retained in these six filings, which does not mean
no stock splits occurred. Narrative disclosures still require event-date review.

The existing `.tradingagents/validation/listing-evidence/` packets now include the
inventory and its source-linked Markdown section. This is a review aid rather
than a completed security master or an unlocked valuation.
