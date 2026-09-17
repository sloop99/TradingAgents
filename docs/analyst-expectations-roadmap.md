# Analyst targets and expectations

Requested addition: compare what identifiable brokerage research firms expect
for any input stock, including large firms such as Morgan Stanley where their
published targets are accessible. This is a planned research module; it does not
claim that ten firms cover every company or that premium research is available.

## Output

Show one current, eligible price target per research firm in a source-linked
table: firm, analyst if available, target, currency, publication date, stated
horizon, rating/action and relevant thesis or outlook excerpt. Display the
unweighted mean, median, minimum, maximum and distinct-firm count for comparable
observations. Let the user optionally select a named firm universe; report which
firms were requested, observed, eligible or missing. Do not invent a ranking of
the "top ten" firms or fill missing coverage with estimates.

Keep provider-published consensus snapshots separate from a reconstructed
firm-level average. A vendor mean without its constituents is an observation,
not evidence that Morgan Stanley or another requested firm was included.

## Evidence and aggregation contract

- Identify issuer and quoted security; preserve source URL, original text,
  publication and retrieval timestamps, original target/currency, target horizon,
  firm spelling and canonical firm identifier.
- Count only the latest eligible target per canonical firm. Reprinted stories
  and multiple analysts from the same firm must not overweight that firm. Equal-
  time conflicting targets remain unresolved instead of selecting arbitrarily.
- Use an explicit as-of cutoff and configurable age limit. A recent rating-only
  action must not refresh an older target's publication date. Withdrawn coverage
  should not leave an old target counted as current.
- Aggregate matching currency, security and target horizon/share basis only.
  Unknown horizons stay in a separate bucket. Never silently treat an unspecified
  target as a 12-month forecast. Preserve split adjustments and conversion lineage.
- Expose all exclusions and the age distribution. Small coverage counts and
  dispersion are information, not a reason to suppress inconvenient targets.
- Calculate implied upside/downside only against an eligible dated quote on the
  same basis. Distinguish analyst opinion from company guidance and our scenarios.
- For EPS/revenue outlooks, require matching fiscal period and accounting basis;
  do not average annual and quarterly forecasts or GAAP and adjusted EPS together.

## Low-cost build order

1. Add independent target-record validation and deterministic aggregation with
   offline fixtures before depending on a live vendor.
2. Add a cached adapter for available free aggregate snapshots, labeled as vendor
   consensus with unknown constituents when necessary. Missing fields stay missing.
3. Collect firm-attributed targets from accessible, dated source publications,
   retaining provenance. Add manual source import when automatic extraction is
   unavailable. This may produce partial coverage without any paid subscription.
4. Add revisions, dispersion and disagreement with company guidance to the report.
   Keep long-term fundamental valuation separate from shorter-horizon sell-side
   targets; agreement among analysts is not independent proof of future returns.
5. Evaluate paid constituent/history feeds only if their coverage and publication
   timestamps justify the cost. No paid subscription or feed is enabled by this plan.

The optional aggregate adapter is implemented. Firm-level collection and
aggregation remain future work. Share-class and corporate-action reconciliation
remain necessary before computing target-implied returns.

## Source capability audit

The research packet can now call yfinance's analyst-target endpoint with
`--analyst-targets`. The library's
[analysis API](https://ranaroussi.github.io/yfinance/reference/yfinance.analysis.html)
and [price-target API](https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.get_analyst_price_targets.html)
document aggregate target and estimate capabilities. These do not establish
complete firm-level constituents or historical publication vintages. The adapter
stores mean, median, low, high and count when supplied as vendor observations,
with retrieval time and unknown publication time/horizon. Invalid observations
are withheld; historical cutoffs cannot trigger a current snapshot fetch.
See [workflow commands](research-workflows.md) for evidence-only and agent runs.
