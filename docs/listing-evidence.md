# Listed-security evidence

The optional `--filings` pass extracts displayed Inline XBRL listing text in
addition to numeric filing candidates. It recognizes DEI `Security12bTitle`,
`TradingSymbol` and `SecurityExchangeName`, including namespace aliases bound to
the trusted DEI namespace. No ticker allowlist is used.

Each observation retains its source URL and node locator, filing SHA-256, exact
context, period, dimensions and bounded displayed text. Unsafe CIK/context or
namespace bindings, ambiguous source IDs, nil values, unsupported continuations
and transformations are rejected. Text is capped at 500 characters and each
filing at 1,000 listing candidates.

The SEC `exchnameen` transformation on an exchange-name node is narrowly retained
as **displayed text only**. Its format is recorded and `transformed_value` remains
null. The harness does not evaluate the transformation or infer an exchange tier
from its rendered name. Other transformations remain unsupported.

The packet's `financial_analysis.listing_evidence` groups accepted text within
the same filing and semantic context. It checks that the source document was
retained, publication/retrieval times are no later than the analysis cutoff, the
context matches the issuer, and candidate fields and source hashes agree with
their enclosing metadata. These are consistency checks, not independent source
authentication. Ambiguous multiple field values stay visible; fields from
different contexts are never cross-joined into a listing.

Reports show source-linked titles, symbols and exchange display names. A complete
three-field listing does **not** prove all common share classes are covered:
filing covers can also list preferred securities, notes, debentures and guarantees.
Duration-context dates describe the filing context, not the date of a quote or
current share count. No class-coverage, ADR or split-completeness ratios are
generated from these observations.

## Cached-source validation

Six preserved primary filings yielded 72 accepted text observations in 24 context
groups: CRWD 1, FTNT 1, PANW 1, JPM 10, WMT 9 and CAT 2. CRWD's listing title
explicitly identifies Class A common stock. Several other filings contain debt
and preferred-security listings alongside common stock. All 388 previously
retained numeric facts matched exactly after re-extraction.

This validation used cached sources, not a refresh of current listings or prices.
Artifacts are under `.tradingagents/validation/listing-evidence/`. Equity-note
class inventories and complete corporate-action coverage remain separate work
before this evidence can support complete market-cap readiness.
