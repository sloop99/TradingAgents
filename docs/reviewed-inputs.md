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
price, class-coverage, ADR or split-history proofs. Capitalization still applies
its independent readiness checks.

## Validation

Six cached issuer datasets produced pending drafts that all refused execution.
A source review of FTNT's filing cover promoted 733,713,653 common shares reported
at July 28, 2026. Changing the underlying quantity invalidated that review.
FTNT's debt schedule supported a scoped senior-note balance, but did not prove
complete interest-bearing debt, so no real-company total-debt promotion or
verified enterprise value was claimed. A synthetic pipeline test separately
exercises reviewed debt through net-debt arithmetic. Local artifacts are under
`.tradingagents/validation/reviewed-inputs/`.
