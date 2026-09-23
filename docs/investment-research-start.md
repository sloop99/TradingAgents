# Investment research dashboard and inputs

Run the launcher from the repository root. It routes dashboard and research commands to the dashboard worktree while keeping file arguments relative to the directory where you ran it:

```powershell
cd <repo>
.\.venv\Scripts\python.exe scripts\research.py dashboard --no-open --port 8792
.\.venv\Scripts\python.exe scripts\research.py sectors
```

`<repo>` is the TradingAgents repository root. The shared virtual environment is at that root; the worktree has no separate environment. Open `http://127.0.0.1:8792` for the read-only dashboard. The sectors command writes the default snapshot to `<repo>\.tradingagents\sector-rotation.json`.

```powershell
cd <repo>\.tradingagents\worktrees\research-dashboard
& ..\..\..\.venv\Scripts\python.exe -m tradingagents.research.sector_rotation --output ..\..\sector-rotation.json
```

The direct module command above runs from the worktree and writes the same canonical snapshot path.

The sector command requests `auto_adjust=True` daily closes. It computes each ETF's 13-week simple return minus SPY's return (x), and the four-calendar-week change in that relative return (y), in percentage points. It uses SPY's last session in each completed Friday-ending week and requires each sector close on that same session date. Its trail shows up to eight dated weeks; missing weeks are withheld rather than compressed. The snapshot is a transparent relative-performance calculation, not proprietary RRG math, money-flow estimates, or a buy/sell signal. At least 25 complete weekly observations are needed for the current coordinates and trail. A historical `--as-of` with later-retrieved prices is marked as a replay, not a contemporaneous snapshot.

## Analyst target inputs

`docs\examples\analyst-target-input.json` is a small fictional input example. Its issuer, firms, rankings, quote, and source pages are invented; the `.example` URLs are placeholders, not a live feed. Review the underlying publications before using real records. The importer checks format, dates, identifiers, and arithmetic but does not verify source assertions.

Create a new run from an existing packet and the example records without invoking research agents or a model:

```powershell
cd <repo>
.\.venv\Scripts\python.exe scripts\research.py analyze DEMO --as-of 2026-09-22 --packet <existing-packet.json> --analyst-records .tradingagents\worktrees\research-dashboard\docs\examples\analyst-target-input.json
```

`--packet` reuses the packet's exact bytes in the new run. For an offline packet build, use `--fixture <provider-fixture.json>` instead; `--packet` and `--fixture` are mutually exclusive. The workflow saves the input, a deterministic per-firm consensus, and a markdown summary under `.tradingagents\research-runs`. It selects at most one latest eligible target per firm, keeps horizon/currency/share-basis cohorts separate, and lists excluded records with reasons. Date-only `--as-of` means the end of that UTC date; a historical cutoff also requires retrieval by that date.

Agent work is opt-in. Add `--run-agents` only when you want the normal graph, providers, and configured model calls (and their existing costs). `--analyst-targets` is a separate option that requests Yahoo's current aggregate analyst-target snapshot; it does not create firm-attributed source records.

The current feature imports sourced target records; it does not provide a live top-analyst feed, DCF valuation, or deeper sector drilldowns. Ranking provider, method, universe, and date are retained as input assertions and require source review. A later rating-only update does not refresh a firm's older target. A sector snapshot describes observed relative returns and their recent change, not inferred flows.
