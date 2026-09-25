# Financial reconciliation and period calculations

The research command now preserves source observations and selects comparable
financial inputs before calculating period metrics. It remains a research-data
layer: it does not calculate a target price or investment rating.

## Processing

1. Providers retrieve reported facts. Publication cutoffs exclude information
   unavailable at the requested date before reconciliation begins.
2. Reconciliation separates accounting concepts, units and adjustment bases. It
   selects the latest eligible disclosure for the same concept and exact period.
   A changed value creates a revision warning; conflicting latest disclosures
   remain unresolved. Every selection records its candidate and selected fact IDs.
3. Period calculations derive standalone quarters from compatible cumulative
   periods and trailing totals from four contiguous quarters. Quarter and fiscal
   calendar lengths allow 52/53-week reporting; missing periods are not guessed.
4. Compatible inputs support revenue growth, operating/net margins and a defined
   CFO-minus-positive-outflow-PP&E-capex measure of free cash flow. These are not
   substitutes for an issuer's adjusted cash-flow measures.
5. The complete packet retains original source versions plus calculated facts.
   `financial_analysis` contains selected input views, decisions and the summary.
   Calculated facts carry formulas and input IDs. Agent context uses selected
   input views; the full report preserves the audit trail.

## Accounting boundaries

- Cash and cash including restricted balances are distinct.
- Current and noncurrent long-term debt, totals, and debt including finance
  leases remain separate; no overlapping debt components are summed.
- Equity excluding and including noncontrolling interests are distinct.
- Basic/diluted weighted-average shares are distinct from each other and from
  point-in-time shares. EPS and weighted-average shares are not subtracted to
  manufacture standalone quarters. Share growth requires a consistent known
  split basis; otherwise it is withheld.
- Productive-assets cash outflows can include software and other intangibles.
  They are retained separately from PP&E purchases rather than silently used
  as an equivalent capex input.
- Financial periods and currency units must match for margins and cash flow.
  Revenue growth with a nonpositive prior denominator is withheld.
- A generic operating-company FCF/margin template is not applied to banks.
  SIC equipment categories remain general with an explicit mixed-business
  caveat; a ticker or company name does not override the classification.

## What still needs work

This is a conservative calculation foundation, not a full accounting model.
The latest filing can revise an earlier period without explaining why; revision
warnings retain that uncertainty. Full restatement narratives, reporting-currency
conversion, security-level split histories, share-class aggregation, comprehensive
debt reconciliation, enterprise value, net debt, and business-specific valuation
remain outside this implementation. Missing or stale inputs and unsupported
methods must be reviewed before making comparisons.

The model's evidence coverage status measures available inputs, not whether a
company is attractive or every valuation input is ready. An annual total is not
interchangeable with a quarter or year-to-date total merely because their end
dates match. Read the period fields and calculation lineage.

## Validation

Unit tests cover revisions, unresolved conflicts, cumulative-period subtraction,
gaps, mixed units, inconsistent definitions, non-additive share measures, fiscal
calendar differences, negative earnings and bank exclusions. Integration tests
verify publication cutoffs, source preservation, packet round trips and report
context. Cached live SEC packets can be replayed without fresh network or model
calls; generated packets and local contact configuration stay outside Git.
