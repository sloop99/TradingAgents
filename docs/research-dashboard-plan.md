# Research dashboard plan

## Product

Build a local, read-only research ledger for one investor. Its single job is to
show what was researched, when it was researched, what the conclusion meant,
and how complete the supporting evidence was.

The dashboard must keep these concepts separate:

- the model decision and its plain-language interpretation;
- existing-holder, prospective-buyer, and general-research perspectives;
- evidence sufficiency and valuation readiness;
- archived analysis dates and optional current market observations;
- real research runs and smoke/test runs.

## Basic version

1. Discover legacy and evidence-aware runs beneath approved local roots.
2. Normalize manifests, decisions, dates, models, evidence status, and artifacts.
3. Group runs by ticker and identify the most recent run.
4. Show a searchable latest-research view and a chronological research tape.
5. Provide a safe, local report reader for the available report sections.
6. Hide smoke runs and personal cost data by default.
7. Bind only to `127.0.0.1` and never edit source reports.

## Run the first version

From the repository, start the local dashboard with:

```powershell
python -m tradingagents.dashboard
```

It opens on `http://127.0.0.1:8791` and automatically finds the canonical
`.tradingagents/runs` archive when launched from a research worktree. Use
`--scan-root PATH` to add or replace scan locations, and `--no-open` when a
browser should not open automatically.

## Design system

- Paper `#f4f7fb`, white `#ffffff`, ink `#172033`, navy `#263d5a`, research
  blue `#2f67ad`, amber `#b67718`, conflict red `#a34444`.
- Charter/Georgia for restrained editorial headings, Aptos/Segoe UI for body
  text, and Cascadia Mono for dates and evidence labels.
- The signature element is the research tape: a ticker-specific chronology in
  which each run is a dated marker carrying decision and evidence state.
- Motion is limited to the initial reveal and the report drawer. Reduced-motion
  preferences disable both.

## Later phases

- Compare two runs and highlight changed conclusions, risks, and evidence.
- Compare companies within a thesis basket.
- Add thesis checkpoints, filing freshness, and earnings reminders.
- Add separately timestamped current-market snapshots without rewriting history.

## Acceptance checks

- Existing run directories remain byte-for-byte unchanged.
- A malformed manifest cannot prevent other runs from appearing.
- Report requests can only read files discovered by the indexer.
- Decision meaning is not inferred from the rating alone when context exists.
- The page works on desktop and mobile, by keyboard, and with reduced motion.
