# Research foundation roadmap

Status: the evidence foundation and conservative financial-reconciliation layer are implemented on `research-foundation`. Main remains the integration branch; no merge is implied.

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
   The requested [analyst expectations module](analyst-expectations-roadmap.md)
   will separate firm-attributed targets from vendor consensus, with mean, median,
   range, coverage count, age and horizon checks. Prefer free sources and cached
   evidence; do not assume a fixed top-ten firm universe.
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

## Phase 1 usage

Generate a packet without an LLM call or paid data subscription:

```powershell
$env:SEC_USER_AGENT = "YourOrganization your-contact@example.com"
python -m tradingagents.research AAPL --as-of 2026-09-16
```

Replace the example SEC contact with your own identifying contact. Missing SEC
configuration produces an explicit coverage issue and skips SEC requests. Yahoo
is optional (`--no-market`). Output defaults to
`.tradingagents/research/AAPL/2026-09-16/packet.json` and `coverage.md`.
Use `--output-dir` to preserve separate runs on the same date. Raw response
snapshots are content-addressed under `.tradingagents/evidence-cache`.
A date means end of day UTC; an aware ISO timestamp selects an exact cutoff.
`--horizon` and `--thesis` preserve research intent without creating forecasts.
`--require-sufficient` writes the packet and exits 2 when coverage is insufficient.
`--fixture provider-response.json` disables all network providers for offline replay.

Attach the same packet to every agent in a programmatic stock run:

```python
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

config = DEFAULT_CONFIG.copy()
config["research_packet_path"] = ".tradingagents/research/AAPL/2026-09-16/packet.json"
graph = TradingAgentsGraph(config=config)
state, decision = graph.propagate("AAPL", "2026-09-16")
graph.save_reports(state, "AAPL")
```

The graph rejects mismatched ticker/date packets before analysis. Packet content
changes invalidate checkpoint reuse. Reports retain the full packet and coverage
under `0_evidence/`. The bounded shared context links facts to their sources;
evidence status is separate from BUY/HOLD/SELL. Existing behavior remains the
default when `research_packet_path` is unset. The interactive CLI does not yet
expose a packet-selection option; use the programmatic `propagate` route above.

### Actual coverage and limits

- SEC: current issuer lookup, recent filing metadata, and selected standardized
  companyfacts concepts with exact periods, units, tags and accessions. This is
  not full filing-text or earnings-call extraction. Custom company KPIs, analyst
  consensus, customer research and valuation models remain later phases.
- Current ticker identity is not a historical security master. Historical
  eligibility uses acceptance times where available; a filing-date fallback is
  conservatively end of day and flagged. Amendments are excluded. Current SEC
  responses are not a complete archive of historically retrieved data vintages.
- Yahoo: recent completed daily closes and available corporate actions. Retrieval
  time is the earliest verified availability proxy, not an exchange publication
  time. Retrospective cutoffs exclude newly retrieved prices. The history-only
  feed does not resolve quote currency; it never silently assumes USD.
- SIC-based business classification selects basic coverage checks for banks,
  REITs, retail, industrial, software and general companies. It is coarse and
  does not establish a company's actual economics or specialized valuation.
- Compatible operating cash flow less explicitly positive-outflow capex can be
  calculated with input lineage. No TTM stitching, enterprise value, split-adjusted
  cost-basis return, target price, DCF or sector rotation is claimed in this phase.
- The packet's cutoff constrains its evidence only. Legacy tools may retrieve
  newer data during an agent run; the combined report is not a historical
  backtesting guarantee. Agent calls still carry their normal provider costs.

The next build should reconcile fiscal periods and share/capitalization inputs,
then add deterministic TTM metrics and business-appropriate valuation methods.

### Validation of the initial implementation

- Full local suite: 633 passed, 2 skipped, 70 subtests passed. Skips are the
  optional Bedrock dependency and a live DeepSeek API test without credentials.
- Ruff passes for all Phase 1 source/tests and modified integration files.
- Offline fixtures cover multiple business types, date eligibility, invalid and
  conflicting facts, cache corruption recovery, provider integration, report
  persistence, and packet-aware checkpoint signatures.
- Normal and debug graph integration are exercised without paid model calls;
  debug streams now retain node output even when no message accompanies it.
- A live SEC request with a truthful public-project User-Agent returned HTTP 403
  at issuer mapping. Live SEC data retrieval therefore remains unverified in
  this environment. No private contact was invented or access block bypassed.
- The standalone CLI was smoke-tested with missing SEC configuration and emits
  an unsupported evidence packet successfully. Live Yahoo retrieval and a full
  LLM-backed research run were not exercised as part of this build.

### Live validation follow-up (2026-09-16)

Market retrieval passed for CRWD, FTNT, PANW, JPM, WMT and CAT: each returned
30 completed daily bars through September 15. Market-only evidence stays partial.
A real CLI run retained 60 facts and replayed identical facts/timestamps from cache
without network access. The CLI now places yfinance's separate SQLite cache under
`--cache-dir/yfinance`; a writable cache and permitted network access are required.

SEC ticker mapping and a submissions diagnostic both returned HTTP 403. The test
stopped; full SEC statements and cross-source financial comparisons remain blocked.
A user-approved identifying contact is needed for the next bounded SEC check;
a contact change alone is not guaranteed to resolve the access restriction.

The validation fixed permanent-error retries, 403 map fallback, cached SEC fetch
timestamps, and an invalid failure coverage value. Tests now freeze market retrieval
time. Final regression suite: 639 passed, 2 skipped, 70 subtests; Ruff passes.
Local raw diagnostics and the detailed report are under `.tradingagents/validation/`
and remain excluded from Git. This validates ingestion and failure behavior, not
valuation accuracy or production readiness.

### Financial reconciliation implementation (2026-09-16)

The initial calculation layer is implemented; see
[financial reconciliation](financial-reconciliation.md) for behavior and boundaries.
SEC access subsequently succeeded with the user-approved local identifying contact.
Six previously retrieved live SEC datasets were replayed offline through concept
selection and period calculations. Nine issuer benchmark comparisons passed across
revenue, quarterly CFO, operating margin and revenue growth. The full suite passed
674 tests and 70 subtests, with 2 optional skips; Ruff passed.

Reconciliation retains source versions and records choices, while quarter/TTM
calculations preserve formula lineage. Equipment-code business classification,
explicit stale-input warnings, PP&E/productive-assets distinctions and share-basis
limits prevent unsupported shortcuts. Remaining conflicts and missing inputs are
still reported. Net debt/enterprise value, comprehensive share reconciliation and
valuation models were not implemented in that phase. Local validation artifacts remain under
`.tradingagents/validation/phase2/`; personal configuration stays outside Git.

### Capitalization implementation (2026-09-16)

[Capitalization and multiples](capitalization.md) now provides a deterministic
calculator, split reconciliation, explicit readiness requirements and source
lineage. Yahoo vendor market cap and shares remain separate observations. SEC debt,
lease, preferred and minority-interest concepts retain their distinct scopes.
Live snapshots were retrieved for CRWD, FTNT, PANW, JPM, WMT and CAT and combined
with cached SEC evidence. All six still withhold verified capitalization multiples:
the built-in providers do not yet supply complete share-class/split/capital-structure
proofs. The positive arithmetic path is covered by controlled numerical tests.
Local combined packets are under `.tradingagents/validation/capitalization/`.
Final regression: 708 passed, 2 optional skips and 70 subtests passed; Ruff passed.

### Filing-level evidence implementation (2026-09-16)

The optional [filing evidence pass](filing-evidence.md) retrieves primary SEC
filings and retains share-class and debt-instrument contexts, numeric scale/sign,
source locations and review snippets. Run with `--filings`; one filing is fetched
by default, with a maximum of three per invocation. Cached snapshots support
offline replay and retain original retrieval times.

Filing candidates are explicitly excluded from consolidated calculations, even
when their source tag matches a familiar financial metric. This prevents an
individual class or debt instrument from replacing a company total. The phase
adds source extraction and review evidence; it does not complete automatic
capital-structure reconciliation or unlock verified live multiples by itself.

Live validation retrieved all six sample filings and retained 388 candidates.
127 matched aggregate SEC observations with no disagreements; three direct source
checks passed. Network-disabled replay and packet roundtrips passed. Final suite:
725 passed, 2 optional skips, 70 subtests passed. Detailed local artifacts are in
`.tradingagents/validation/filings/`.

### Filing context reconciliation (2026-09-16)

Filing metadata now binds to retained source facts before share-class comparisons
or debt subtotals are attempted. Repeated equivalent observations are removed,
while mismatched dates, units, contexts or values remain separate or are rejected.
The six-company replay bound 388 source observations, removed 89 repetitions and
produced five scoped long-term-debt subtotals. It did not infer complete total debt
or complete share-class coverage.

Filing eligibility now includes 20-F and 40-F alongside 10-K and 10-Q. The provider
has no ticker allowlist. A separate live TSM test retrieved a 20-F and extracted
two supported numeric candidates; this demonstrates foreign-filing retrieval,
not comprehensive IFRS or investment coverage. Detailed artifacts are under
`.tradingagents/validation/filing-contexts/`.
Regression: 739 passed, 2 optional skips, 70 subtests passed; focused rendering
checks and Ruff also passed.

### Reviewed capital inputs (2026-09-16)

The [reviewed-input layer](reviewed-inputs.md) can copy supported common-share
counts or copy/sum supported debt quantities under explicit analyst assertions.
Manifests bind to the exact ticker, cutoff and reported-evidence hash, preserve
reviewer rationale and source lineage, and reject pending drafts or changed
evidence. CLI flags generate drafts and apply completed reviews without an LLM
call or paid feed.

All six cached issuer drafts refused automatic execution. A real FTNT cover-page
review promoted 733,713,653 common shares; mutating its evidence invalidated the
review. A synthetic test exercises reviewed debt through net-debt calculation.
Complete debt scope and class/ADR/split proofs remain separate requirements;
verified live enterprise values remain unavailable for this sample.
Regression: 755 passed, 2 optional skips, 70 subtests passed; Ruff passed.

### Price review and actionable valuation readiness (2026-09-16)

Review manifests now support sourced price copies with explicit currency,
non-dividend-adjusted quote and quote-date split-basis assertions. Original
observations remain available, and no class, ADR or split-completeness proof is
inferred. Historical vendor prices cannot become current-basis inputs merely
because their metric is named `close`.

Capitalization reports now list specific outstanding evidence requests and the
exact shares-to-price split interval, including observed in-window split IDs.
Freshness and positivity are explicit prerequisites; stale price and share
inputs are both diagnosed rather than short-circuiting after the first failure.
An evidence plan marked ready applies only to market cap, not all valuation
metrics. Cached six-issuer replay artifacts are in
`.tradingagents/validation/valuation-readiness/`.
Six cached packets passed replay and roundtrip checks, with 30 eligible price
candidates each and no automatic price promotion. A synthetic complete-proof
pipeline calculates market cap from reviewed price and shares. Regression:
775 passed, 2 optional skips and 70 subtests passed; Ruff passed.

### Listed-security evidence (2026-09-16)

The [listing evidence pass](listing-evidence.md) adds cover-page security titles,
symbols and exchange display text with source locations and context binding.
Text is grouped within a filing context; multiple listed instruments are not
treated as common-equity classes. Safe parsing and retained-document/cutoff
checks preserve rejected cases as diagnostics. No capitalization proof ratios
are generated from listing text.

Six cached filings yielded 72 accepted text observations in 24 context groups,
while all 388 existing numeric facts were reproduced exactly. This supplies
reviewable listing evidence for generic stock inputs; equity-note class coverage
and complete split-history evidence still need separate reconciliation.
Regression: 792 passed, 2 optional skips and 70 subtests passed; Ruff passed.

### Share inventory and analyst-expectations roadmap

The [share inventory](share-inventory.md) separates outstanding, issued,
authorized and treasury quantities, with strict source/context binding. Listing
associations require exact supported class dimensions; empty or ambiguous scopes
remain unallocated. Split ratios are candidates only until effective dates and
complete event coverage are established.

The six cached filings supplied 60 point-in-time share observations, with all
388 numeric source facts unchanged. No automatic listing-to-share association
was justified in this real sample; a controlled fixture tests that path.
The [analyst expectations roadmap](analyst-expectations-roadmap.md) records the
requested named-firm average, median, range and coverage count, separately from
vendor consensus. Analyst data collection is not implemented in this phase.
Regression: 805 passed, 2 optional skips and 70 subtests passed; Ruff passed.

### Reviewed split-event dates

Evidence-bound review manifests now support multiple dated split events. A
reviewer must identify the first split-adjusted trading date, cite its source,
confirm the unchanged ratio's direction and its applicability to counted shares.
Announcement dates, record dates and filing periods are not inferred as event
dates. Existing dated market events cannot be moved, and competing rules for the
same date are rejected.

The calculator can consume these events while continuing to require independent
complete interval coverage. A controlled test verifies 100 shares through a
four-for-one split at a USD 10 quote yields USD 4,000 market cap only when class,
ADR and complete split-history evidence are also supplied. Real-company split
history completeness is not established by this change.
Six cached packets produced review drafts; CRWD had one eligible dated market
split candidate, and the other five had none in retained evidence. All pending
drafts refused execution. Regression: 817 passed, 2 optional skips and 70 subtests
passed; Ruff passed.
