# Reviewed capital inputs

The research harness can promote existing reported quantities into capital-input
calculations using an explicitly reviewed manifest. This is generic across
tickers, with supported source concepts checked individually. It is not an
automatic certification of capital-structure completeness.

## Workflow

Generate evidence and a pending draft (using the locally configured SEC contact):

```powershell
python -m tradingagents.research FTNT --as-of 2026-09-16 --filings --write-review-draft --output-dir .tradingagents/research/FTNT/review
```

The draft lists eligible source facts and required attestations. It has no rules
and cannot execute. Inspect the underlying filing, select exact fact IDs, record
the reviewer's actual identity, review timestamp and rationale, then set
`draft_only` to false and `status` to `reviewed`. A reviewer may be a human or an
explicitly identified agent; the resulting assertions remain analyst judgments.
Add rules such as:

```json
{
  "rule_id": "cover-common-shares",
  "output_metric": "current_shares",
  "operation": "copy",
  "input_fact_ids": ["<exact retained reported fact ID>"],
  "attestations": ["point_in_time_common_shares", "raw_as_reported_basis"],
  "rationale": "<source location and explanation supporting this interpretation>"
}
```

Retain the draft's schema version, ticker, exact `as_of` and evidence SHA-256.
The `reviewer` object requires `name`, `reviewed_at` and `rationale`. Review time
must be after the evidence became available and no later than the analysis
cutoff. Historical analyses cannot use a later review as contemporaneous evidence.

Apply the completed manifest against the same evidence snapshot:

```powershell
python -m tradingagents.research FTNT --as-of 2026-09-16 --filings --capital-review .tradingagents/research/FTNT/review/capital-review.json --output-dir .tradingagents/research/FTNT/reviewed
```

Use the same provider options and unexpired cached data, or replay preserved
provider responses with repeatable `--fixture` arguments. A packet JSON is not a
provider fixture. All original reported facts, including retrieval timestamps,
are hashed; refreshed market or filing evidence requires another review even if
the chosen quantity appears unchanged. Rejected or partially applied explicit
reviews return exit code 2, with the diagnostic packet preserved.

## Supported mappings and boundaries

- `split_ratio`: copy an existing positive standard filing split ratio or dated
  market split observation. The ratio means new shares per old share; a reverse
  split therefore has a ratio below one. Requires `effective_date_confirmed`,
  `new_shares_per_old_share` and `applies_to_all_counted_common_shares` attestations.
  Set `effective_date`, `effective_date_source_url` and
  `date_basis: "first_split_adjusted_trading_date"`. The date citation must point
  into the same source document as the ratio. Review the date on which quotes
  begin using the new share basis, not an announcement/record date or an after-
  close distribution date. Intraday ambiguities require further source review.
  Already dated market events cannot be moved. Different dates can have separate
  rules; duplicate dates reject every competing rule. One source ratio cannot be
  reused for multiple events. A filing's original reporting period remains in
  source lineage. These are reviewer assertions, not automatic date extraction.
- `current_share_price`: copy a positive reported `close`, `regular_market_price`
  or `market_price` with an explicit currency-per-share unit and recognized quote
  basis. Requires `listing_currency`, `non_dividend_adjusted_quote` and
  `quote_date_split_basis` attestations. The reviewer must establish that the
  value uses the quote date's split basis; later downloaded historical series
  may incorporate later splits. Dividend/total-return-adjusted prices and
  ambiguous adjustment labels are ineligible. This remains an analyst assertion,
  not independent vendor-price verification. Original source basis stays in
  source lineage; the reviewed input records the asserted current-quote basis.
- `current_shares`: copy a supported, reported point-in-time common-share count.
  Weighted-average shares and custom concepts are ineligible. This does not
  establish coverage of all classes, ADR conversion or complete split history.
- `total_debt`: copy or sum supported debt concepts with an explicit
  `complete_interest_bearing_debt` attestation. Sums also require
  `disjoint_scopes`. Review principal versus carrying value, borrowing scope and
  lease treatment before making this assertion. A note subtotal is not
  automatically complete company debt.

Dates, currencies, source IDs, quantities and lineage remain attached. Known
parent/component overlap, duplicate rules or output metrics, reused source nodes,
incompatible units and evidence changes cause rejection. Rules cannot introduce
numeric constants or turn missing data into zero. The layer does not promote
class-coverage, ADR or split-history proofs. Capitalization still applies
its independent readiness checks.

Reviewed split events enter the existing split arithmetic, where duplicate
same-date ratios are counted once and disagreements block adjustment. They do
not emit `split_history_complete`: independent complete coverage of the required
share-date-to-price-date interval is still mandatory. An event outside that
interval is not applied. No date or split ratio is inferred from missing records.

## Validation

Six cached issuer datasets produced pending drafts that all refused execution.
A source review of FTNT's filing cover promoted 733,713,653 common shares reported
at July 28, 2026. Changing the underlying quantity invalidated that review.
FTNT's debt schedule supported a scoped senior-note balance, but did not prove
complete interest-bearing debt, so no real-company total-debt promotion or
verified enterprise value was claimed. A synthetic pipeline test separately
exercises reviewed debt through net-debt arithmetic. Local artifacts are under
`.tradingagents/validation/reviewed-inputs/`.
