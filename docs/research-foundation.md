# Research foundation roadmap

Status: design and existing local customizations preserved; Phase 1 implementation has not started.

## Scope

Build a general-purpose, free-first equity research foundation inside TradingAgents.
The common workflow should accept any stock identifier and report actual coverage.
Start with US-listed operating companies with accessible public filings; support
additional markets through explicit provider, currency, and accounting adapters.
Select metrics and valuation methods by business model rather than by ticker.
Unsupported methods and unavailable data must be explicit, not filled with guesses.

## Development workflow

- Upstream project: https://github.com/TauricResearch/TradingAgents
- Development fork: https://github.com/sloop99/TradingAgents
- Development branch: `research-foundation`
- Keep the fork's `main` as the integration branch. Develop and validate on feature
  branches, then review a pull request into the fork's `main` when ready.
- Changes are based on the existing local checkout, initially upstream commit
  `a33fd4c`; upstream updates should be reviewed and integrated deliberately.
- The original local checkout remains separate from the development worktree.
- Do not commit credentials, local authentication state, personal research inputs,
  generated reports, or caches. Runtime configuration belongs in ignored files.

## Phase 1: sourced company research packet

Deliver a reusable command/workflow accepting ticker, research date, horizon, and
optional user thesis. Produce structured evidence plus a readable coverage report.

1. Resolve issuer identity, listing, reporting currency, fiscal calendar and business
   model. Preserve ambiguity instead of silently choosing an issuer.
2. Introduce shared evidence records: metric, value, units, period start/end,
   publication timestamp, retrieval timestamp, source URL/accession/section,
   accounting definition, adjustment basis, and reported/calculated/estimated status.
3. Implement replaceable provider interfaces. Begin with public SEC filings and
   current market-data access; evaluate EdgarTools as an optional filing adapter.
4. Cache raw sources and normalized facts locally, retaining versions and lineage.
   Reuse one evidence packet across agents rather than repeat retrieval.
5. Retrieve available annual/quarterly statements, recent filing/earnings material,
   price observations and corporate actions. Clearly identify unavailable coverage.
6. Add accounting and timing checks: units/currency, fiscal periods, duplicate or
   amended facts, split basis, current versus weighted-average shares, cash-flow
   definitions, and publication-date eligibility for historical research.
7. Add evidence states independent of investment ratings: sufficient, partial,
   material conflict, and unsupported. Missing evidence must not imply HOLD.
8. Feed the shared packet into the existing graph and retain source references
   through analysis and final synthesis. Preserve the current report interface.

### Acceptance criteria

- Each material reported fact has a source and period; each calculated fact has
  a formula and references to its input facts.
- Historical analysis excludes information published after its cutoff, including
  later amendments. Missing publication timing is visible.
- Conflicting cash-flow or capitalization inputs trigger reconciliation or an
  explicit unresolved result rather than a fabricated valuation.
- Repeated retrieval can use cached sources; missing feeds degrade transparently.
- Tests cover multiple business models, not only cybersecurity: mature operating
  company, cyclical company, bank, REIT and loss-making business.
- Specialized valuation may be marked unavailable in Phase 1; inappropriate
  generic valuation must not be substituted.
- Tests use fixed public/synthetic fixtures by default; live smoke checks are
  separate, bounded and do not require paid subscriptions.
- No change is merged into main until its review and validation are complete.

## Subsequent phases

2. Deterministic financial calculations: trailing metrics, cash-flow and share-count
   reconciliation, enterprise value, accounting checks and comparable periods.
3. Business-specific research modules: appropriate KPIs, peers, document extraction,
   customer evidence and accounting pitfalls.
4. Valuation and expectations: appropriate DCF/reverse DCF and comparable methods,
   explicit assumptions, scenario returns and guidance history. Historical consensus
   estimates remain optional until their cost and point-in-time quality are justified.
5. Market/portfolio context: relative performance, sector rotation, breadth,
   correlations and concentration. Do not infer fund flows from price changes.
6. Monitoring and evaluation: quarterly thesis changes, forecast accuracy, source
   accuracy, abstention quality and later bias-controlled historical evaluation.

## Cost controls

Prefer public filings and investor-relations sources; cache and refresh selectively.
Use Python for arithmetic and validation, focused extraction for documents, and
stronger reasoning for material disputes and final synthesis. Track provider calls,
model usage when available, elapsed time and unresolved coverage. Paid providers
are optional adapters, never silent dependencies. Do not assume an open-source
library includes free access to all underlying data.

## Initial baseline

The initial branch preserves pre-existing Codex subscription-provider integration
and FRED/Reddit retrieval fixes. These are baseline capabilities, not implementation
of the new evidence foundation. Personal holdings and generated research remain local.
