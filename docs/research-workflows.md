# Research workflow runner

Prepare an evidence packet first. This default route has no graph or LLM call,
so it does not add model cost. Each invocation creates a fresh timestamped run
under `.tradingagents/research-runs`; its manifest records the packet hash,
coverage status, limitations, and that costs are not quantified.

```powershell
. .tradingagents/sec-env.ps1
python -m tradingagents.research.workflow MSFT --filings --analyst-targets --thesis "Durable cloud demand" --horizon 12_months
```

FTNT is the same workflow, not a recommendation:

```powershell
python -m tradingagents.research.workflow FTNT --filings --analyst-targets --thesis "AI threats create durable demand for security spending" --horizon long_term
```

For an already reviewed packet, provide `--packet path\to\packet.json`. The
runner copies its exact bytes and records the hash; it does not silently replace
the packet's thesis or horizon with command-line values.

Use `--fixture response.json` (repeatable) for offline evidence replay. Fixture
mode cannot run agents. To opt into the normal TradingAgents graph and its LLM
and provider calls, add `--run-agents`; identity-less packets and identity/fact
collisions are blocked. Partial evidence and ordinary financial limitations are
retained as limitations for review rather than being relabeled as a rating.

```powershell
python -m tradingagents.research.workflow FTNT --as-of 2026-09-16 --packet .tradingagents\research\FTNT\2026-09-16\packet.json --run-agents --llm-provider openai --quick-model gpt-5.4-mini --deep-model gpt-5.5
```

Model flags are optional overrides. When omitted, the repository's configured
provider and models are used; the workflow does not select a paid model for you.
To reduce an opted-in graph's scope, pass `--analysts fundamentals,news`; this
selects only those two graph analysts. `--analyst-targets` adds the optional
Yahoo aggregate analyst-target evidence provider during a live packet build.

Omitting `--as-of` uses today's UTC date. A historical cutoff will not fetch
today's analyst targets. `--filings` retrieves one primary SEC filing by default;
`--max-filings 2` or `3` expands that bounded collection. Source availability
and valuation coverage vary by issuer; unsupported issuers remain explicit.

Every completed evidence run contains `research-brief.md`, `packet.json`,
`coverage.md` and `manifest.json`. Agent runs also contain the consolidated
report and retain the chosen provider, models and analysts in the manifest.
An evidence-only completion is not an AI thesis evaluation. The full graph
can use newer legacy tool data; it is not a historical backtest guarantee.

## Next research and build steps

1. Run one current thesis through `--run-agents --analysts fundamentals,news`
   using the configured provider, review source quality and the final report,
   then expand to CRWD, FTNT and PANW with the same thesis and cutoff.
2. Add firm-attributed analyst targets and estimate revisions, retaining dates,
   share basis and deduplicated firm counts separately from vendor aggregates.
3. Add explicit scenario valuation and issuer-specific operating metrics;
   unresolved share/debt inputs must remain visible.
4. Add sector-relative performance/rotation and a comparable-ideas report.
   Expand provider coverage based on observed gaps before buying data feeds.
