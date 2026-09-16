# Capitalization and multiples

The deterministic evidence layer can calculate split-reconciled common shares,
equity market capitalization, net debt, enterprise value, EV/revenue, P/E and
price/free cash flow when the required evidence is present. It does not issue
target prices or investment ratings. No LLM or paid data subscription is required.

## Evidence contract

Every input is an `EvidenceFact` with a source, publication/retrieval timestamps,
period, units and definition. Calculations preserve their formula and input IDs.
The packet stores readiness and unmet requirements under
`financial_analysis.capitalization`, and reports them in Markdown.

| Calculation | Required evidence |
|---|---|
| Common shares on quote date | As-reported `current_shares`, dated `split_ratio` events, and a sourced `split_history_complete` assertion covering the entire shares-to-quote interval |
| Equity market cap | `current_share_price` in explicit currency/share, reconciled shares, `share_class_coverage_ratio=1`, and `adr_ratio=1` |
| Net debt | Complete interest-bearing `total_debt` and cash, in the same currency and on the same balance-sheet date |
| Enterprise value | Verified equity cap, debt, cash, preferred stock and noncontrolling interest; balance-sheet items share a date and monetary inputs share a currency |
| EV/revenue | Enterprise value and positive recent annual/TTM revenue |
| P/E | Verified equity cap and positive recent annual/TTM earnings explicitly attributable to common holders |
| Price/free cash flow | Verified equity cap and positive recent annual/TTM free cash flow; inspect the linked capex definition |

Missing preferred stock or noncontrolling interest is not treated as zero. An
explicit sourced zero is acceptable. Carrying amounts and liquidation preferences
remain separate candidate concepts; they are not automatically promoted into EV.
The initial contract supports a verified 1:1 listing/share relationship; non-unit
ADR conversion and multi-class valuation need additional implementation.

Quotes expire after 7 days, shares after 130 days, and balance-sheet/denominator
period ends after 150 days. Annual denominators must span 330–400 days. The calculator
rejects nonpositive denominators, conflicting inputs, mismatched currencies,
dividend-adjusted prices and incomplete split histories. Banks do not receive
generic EV or free-cash-flow multiples. These are explicit initial policies,
not universal financial conventions.

## Provider coverage and limitations

Yahoo adds `market_cap_reported` and `shares_outstanding_market` snapshots when
metadata matches the requested ticker. These remain vendor observations, distinct
from independently calculated market cap. Currency is used only with matching
metadata. Metadata failures preserve usable price history. The adapter retains
30 daily price rows and corporate actions returned by a 400-day request; that
window does not establish complete lifetime split history. Its cache is versioned.

SEC concepts keep long-term debt, short-term borrowing, commercial paper and lease
liabilities separate. `LongTermDebt` is not complete total debt. Preferred carrying
value, liquidation preference, ordinary NCI carrying amount and redeemable NCI
carrying amount also retain separate meanings. No arbitrary sum is promoted to a
complete capital structure.

Consequently, successful ingestion does not guarantee a verified live multiple.
The next data task is to extract and reconcile share classes, split coverage and
capital-structure completeness from filings with source-linked evidence. Existing
financial-statement conflicts and stale inputs still require resolution. Forward
estimates, growth scenarios, DCF and sector-relative valuation remain later phases.
