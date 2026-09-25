# Research Ledger dashboard redesign

**Status:** approved design, ready for implementation plan
**Date:** 2026-09-22
**Branch:** `codex/research-dashboard` (checkpoint `930f90a` holds the pre-redesign state)
**Scope:** `tradingagents/dashboard/` (static front end, `indexer.py`, `server.py`) and its tests

## Problem

The dashboard at `scripts/research.py dashboard` works, but using it is slow. An audit of the live page with the real archive (22 runs, 12 tickers) found seven problems, and the user confirmed all seven:

1. **Research is buried.** A marketing hero ("The work behind every position.") fills the first screen. The first run card is about 2,050px down a 3,462px page.
2. **The sector chart is unreadable.** Dot color alternates by array index and carries no meaning. Dots have no labels, and 11 dashed trails tangle.
3. **The run chronology is low-signal.** It repeats the cards in a sideways scroller that is cut off at the right edge.
4. **The cards don't scan.** Every verdict appears in the same black serif, evidence shows only as a 3px edge, and placeholder text about hidden position data repeats on several cards.
5. **There's no view of change over time.** Nothing shows a rating moving (for example, OUST going from Hold to Sell).
6. **The report reader is a modal.** It has no URL and no previous/next run, and its 14 tabs all get equal weight. The markdown renderer drops nested lists, blockquotes and h4 and deeper headings.
7. **There's no dark mode.** The page is hard-set to `color-scheme: light`.

When the user opens the dashboard, they are doing three things: **checking their positions**, **reading the latest report**, and **seeing market context**.

## Decisions (made with the user)

| Topic | Decision |
|---|---|
| Layout | **Cockpit**: all three jobs on the first screen. Reports open as their own page. |
| Visual style | **Terminal**: dense and monospace, with square corners and solid verdict tags. The user said "for now", so every color, font and spacing value lives in a single token block that can be swapped. |
| Positions grouping | **Holdings** (the ticker's current run is "Existing holder") and **Watching** (everything else). |
| Cost basis | Always shown. The "Show position data" toggle is removed. |
| Sector views | **Names first.** Sector names replace fund tickers everywhere, and tickers become secondary gray text. Axes and quadrants are labeled in plain English. |
| Sector panel on Positions | A **mini chart** with labeled dots, using the same component as the Market page. |
| Implementation | **Plain JavaScript ES modules.** No build step, no dependencies, and the CSP stays `'self'`. |

The mockups are in `.tradingagents/.superpowers/brainstorm/77-1790135073/content/` (git-ignored): `screens.html` shows all three pages, and `market-v2.html` shows the names-first sector views.

## Pages and routes

This is a single-page app with hash routes. Reloading a route or bookmarking it restores the same view.

| Route | Page |
|---|---|
| `#/` (default) | Positions |
| `#/run/<run-id>` | Report, opened at its default section |
| `#/run/<run-id>/<section-slug>` | Report, opened at that section |
| `#/market` | Market |

An unknown route falls back to Positions.

The shell is shared by all pages:
- **Top bar:** the `RESEARCH LEDGER` wordmark, `POSITIONS` / `MARKET` navigation, a search box, a refresh button and a theme toggle.
- **Footer:** archive counts and the index time, an index-warning count (expandable), a footnote for carried costs (shown only when a carried cost appears), a "Show test runs" switch and keyboard hints.

### Positions (`#/`)

This is a two-column grid (about 1.8fr / 1fr). The right column stacks below the tables when the window is narrower than 900px.

**Left column: two tables, Holdings then Watching.** Each has one row per ticker, with these columns:

| Column | Content |
|---|---|
| Ticker | Monospace, bold |
| Verdict | A tag for the row's run (see Verdict tags) |
| Since last run | `unchanged`, `▲ from HOLD · SEP 3`, `▼ from HOLD · SEP 3`, `first run` or `no rated run` |
| Evidence | Colored dot and label (see Evidence display) |
| Valuation | Capitalized status, or `—` when unknown |
| Cost | Holdings only: the USD cost to 2 decimals, with `†` when carried from an earlier record, or `—` |
| Run | The row run's analysis date as `SEP 17` |

- Rows are sorted by run date descending, then by ticker.
- Clicking a row or pressing Enter opens `#/run/<row-run-id>`.
- A table with no rows shows a single muted line instead of the table.

**Right column:**
1. **Latest report card.** It shows the most recent rated run across all tickers, ordered by `(analysis_date, completed_at)`: ticker, verdict tag, date, and an excerpt of up to about 200 characters. The excerpt comes from `executive_summary`, then `decision_interpretation`, then `thesis`, and is left out if all three are empty. A `READ REPORT →` link opens that run.
2. **Sector rotation mini chart.** This is the compact variant of the shared chart. It fills the column width, with its height at 0.75 × width, capped at 300px. Clicking it or `MARKET DETAIL →` goes to `#/market`.

**Search:**
- Case-insensitive substring match over ticker, verdict, perspective and thesis. It filters both tables live.
- `/` focuses the search box and Esc clears it.
- When nothing matches, the page shows "No tickers match '<query>'".

**Test runs:** hidden by default. The footer switch reveals a third group, "Test runs", with one row per test run, listed separately and not merged into ticker rows. These rows use the same columns, with "Since last run" showing `test run` and Cost blank. The switch state lasts only for the session.

**Row selection:** no row is selected on load. The first ↓ (or `j`) selects the first visible row.

**Removed:** the hero, the summary strip, the run chronology tape, the run cards, and the "Latest run only" and "Show position data" toggles.

### Report (`#/run/<id>/<section>`)

**Header:**
- A breadcrumb: `‹ POSITIONS / PANW / 2026-09-17`.
- The title row: ticker, a large verdict tag, and the change text (for example "unchanged from Sep 16").
- A facts row: Perspective, Cost (holdings only, with `†` when carried), Horizon, Evidence and Valuation. A fact with no value is left out.
- A collapsed `Notes (N)` disclosure under the facts row, shown only when the run's `warnings` list is non-empty. It lists the warnings, for example "Legacy run: metadata inferred from report and path." or OUST's "Ignored run_manifest.json: JSONDecodeError: Unexpected UTF-8 BOM…".

**Rating history strip.** This shows every non-test run for the ticker, oldest to newest:
- Rated runs appear as verdict tags with their date.
- Evidence-only runs appear as small `○` markers on the connecting line, with a date tooltip.
- The run being viewed is outlined, and clicking any run navigates to it.
- `[ OLDER` and `NEWER ]` buttons (and the `[` and `]` keys) step through the strip. At either end the button is disabled.
- When the runs don't fit, the strip scrolls horizontally inside itself and starts scrolled to the run being viewed. The page itself never scrolls sideways.

**Body:** a left section nav (about 170px) and a reading column (max width about 72ch).
- **Section nav.** The run's sections, grouped under headings. A section the run doesn't have is hidden, and so is a group left with nothing in it.

| Group | Sections (the display label is shown in brackets where it differs from the indexer's section name) |
|---|---|
| DECISION | Final decision, Trader |
| DEBATE | Bull case, Bear case, Research manager |
| RISK | Aggressive risk [Aggressive], Conservative risk [Conservative], Neutral risk [Neutral] |
| ANALYSTS | Market, Fundamentals, News, Sentiment |
| EVIDENCE | Evidence coverage [Coverage], Analyst expectations, Research brief |
| FULL | Complete report |
| MORE | Any section name not listed above, in the indexer's order |

- **Default section:** Final decision if present, otherwise the first section in the nav.
- **Section slugs:** the indexer section name lowercased, with runs of non-alphanumeric characters replaced by `-` (for example `final-decision`). An unknown slug falls back to the default section.
- **Analyst expectations** appears when the run has structured analyst data (`analyst_consensus` or `vendor_analyst_targets`), and uses the existing structured renderer (`renderAnalyst`). Otherwise it appears when a raw "Analyst expectations" section exists, which is rendered as markdown. If neither exists it is hidden. *(This intentionally changes the old behavior, which always showed the tab.)*
- **Reading column.** The section heading is monospace amber. Body text is sans-serif at about 13–14px with a line-height of about 1.65. Content comes from `/api/report`, rendered by `markdown.js`.
- **Keyboard:** Esc returns to Positions. `[` and `]` move to the older and newer run.

### Market (`#/market`)

This is a two-column grid (about 1.45fr / 1fr) that stacks below 900px.
- **Left:** the full-size sector chart, with a one-line caption explaining the axes in plain English.
- **Right: a quadrant summary** in the order Leading, Weakening, Improving, Lagging.
  - Each group has a colored heading and a plain-English description: "ahead of the market and gaining", "ahead, but losing ground", "behind, but catching up", "behind the market and fading".
  - Each row shows the full sector name, the fund symbol (small, muted), the 13-week relative value (`+18.2 pts`) and a bar whose length is proportional to |value|.
  - Rows are sorted by 13-week relative return, descending.
  - Clicking a row focuses that sector on the chart and highlights the row.
- **Below the summary:** a source line (source, benchmark, retrieved date).
- **"Exact values & data notes"** is a collapsed `<details>` block containing:
  - the existing exact table: sector / fund, 13-week relative, 4-week change, 13-week absolute, as of
  - the weekly plotted values
  - warnings and exclusions
- **Snapshot status.** `stale` shows the existing amber banner and `unavailable` the red banner. If the snapshot is missing, the page shows the existing instruction to run `scripts\research.py sectors`.

## Shared components

### Sector chart (`sector-chart.js`)

The same function renders both sizes: `renderSectorChart(points, { size: "full" | "compact", focus, onFocus })`.

**Axes.**
- x is the 13-week relative return (`point.x`) and y is the 4-week change (`point.y`).
- The scale is symmetric around 0. Each extent is `max(2, max |value| over the current points) × 1.15`.
- When a sector is focused (full size only), its trail's values are added to the extent calculation.

**Quadrants and colors.** A value of exactly 0 counts as ≥ 0.

| Quadrant | Rule | Color |
|---|---|---|
| Leading | x ≥ 0, y ≥ 0 | green |
| Improving | x < 0, y ≥ 0 | blue |
| Weakening | x ≥ 0, y < 0 | amber |
| Lagging | x < 0, y < 0 | red |

Colors come from the theme tokens `--q-lead`, `--q-impr`, `--q-weak` and `--q-lag`.

**Labels.** Labels use short display names (see Sector names). Placement is greedy, in order of distance from the chart center:
- Try candidate positions right, left, above, below, then the four diagonals.
- Accept the first candidate that stays inside the plot and doesn't overlap an already-placed label or any dot.
- Measure label boxes with `getBBox()` after the text is appended.
- **Full size:** if no candidate fits, retry the ring at twice and then three times the offset, drawing a thin leader line to the dot. If even that fails, place the label at the three-times offset on the right with a leader line and accept the overlap. A full-size chart never drops a label.
- **Compact:** if no candidate fits, leave the label off. The dot's `<title>` tooltip still names it.

**Full-size extras:**
- Each quadrant corner shows its name and plain-English description.
- Axis captions read "← UNDERPERFORMING S&P 500 / OUTPERFORMING S&P 500 →" and "GAINING ↑ / ↓ FADING".
- A dotted trail with small point markers is drawn only for the focused or hovered sector, and its oldest point is labeled with its date.

**Compact extras:** quadrant names only, with no descriptions, trails or axis captions.

**Accessibility:** `role="img"` and an `aria-label` that counts sectors per quadrant. The Market summary list is the text alternative.

**Resizing:** the chart re-renders when its container width changes by 2px or more (the existing `ResizeObserver` approach).

### Sector names

| Fund | Short name (chart labels) | Full name (lists) |
|---|---|---|
| XLK | Technology | the `sector` field |
| XLC | Comm. Services | the `sector` field |
| XLY | Consumer Disc. | the `sector` field |
| Any other | the `sector` field | the `sector` field |

If the `sector` field is missing, the fund symbol is used.

### Markdown renderer (`markdown.js`)

- It builds DOM nodes with `createElement` and `textContent`. **It never uses `innerHTML` on report text**, because reports are model-written.
- It keeps what works today (h1–h3, paragraphs, flat lists, pipe tables, `**bold**`, inline code, links). Links open only `http` and `https` in a new tab with `rel="noopener noreferrer"`; any other link target renders as inert text.
- It adds:
  - h4–h6
  - nested lists, where indentation of 2 or more spaces nests
  - `>` blockquotes
  - fenced code blocks (```` ``` ````)
  - `---` horizontal rules
  - `*italic*` / `_italic_`

### Verdict tags

A tag's color comes from the normalized rating, not the raw string.

| Normalized | Raw decisions (case-insensitive) | Tag |
|---|---|---|
| buy (5) | Buy, Strong Buy | green (`--buy` / `--buy-bg`) |
| overweight (4) | Overweight | green |
| hold (3) | Hold, Neutral | neutral |
| underweight (2) | Underweight | red (`--sell` / `--sell-bg`) |
| sell (1) | Sell, Strong Sell | red |
| evidence | Evidence only | dashed outline, text `EVIDENCE` |
| unrated | anything else (including Unresolved) | muted neutral, raw text shown |

Tag text is the decision in uppercase.

### Evidence display

| `evidence_status` | Display |
|---|---|
| sufficient | ok-colored `●` Sufficient |
| partial, unsupported | warn-colored `●` Partial / Unsupported |
| material_conflict | bad-colored `●` Conflict |
| legacy | muted `○` Legacy |
| other | muted `○` + the capitalized value |

### Theme (`styles.css`)

- **One token block.** A single `:root` block defines every color, font and spacing value. `[data-theme="dark"]` overrides the dark values, and `@media (prefers-color-scheme: dark)` applies them under `:root:not([data-theme="light"])`.
- **Fonts:** Cascadia Mono / Consolas for tickers, numbers, headings and labels, and Segoe UI for body and reading text. Web fonts are not allowed: the CSP blocks them.
- **Theme toggle.** It flips between light and dark and stores the choice in `localStorage` (`ledger-theme`). Every storage call is wrapped in try/catch. With nothing stored, the theme follows the system.
- The `color-scheme` meta tag becomes `light dark`.
- **Palettes.**
  - Dark: `#07090b` background, `#0e1216` surfaces, `#f0b429` amber headings, `#3ddc84` buy, `#ff6b6b` sell.
  - Light: the Terminal light palette from the mockup (`#eeeeea` background, `#fbfbf8` surfaces, solid `#0a8f4e` buy and `#c93434` sell tags with white text).
- **Contrast.** Tag text must meet 4.5:1 against its tag background in both themes. Muted text must be at least 4.5:1 against the surface it sits on. Two mockup values fall short and must be adjusted:
  - The dark muted gray `#6f7b88` measures 4.36:1 on `#0e1216`. Replace it with `#7d8896`, which measures 5.22:1.
  - White on the light-mode green `#0a8f4e` measures 4.16:1. Replace the green with `#087a43`, which measures 5.42:1.
- **CSP.** The CSP forbids inline `style="…"` attributes and `setAttribute("style", …)`. Use classes, or `element.style.prop = …` (CSSOM), which is allowed.

### Responsive behavior

| Width | Positions | Report | Market |
|---|---|---|---|
| ≥ 900px | Two columns | Section nav on the left | Two columns |
| < 900px | The right column stacks below the tables | The section nav becomes a horizontally scrolling strip above the reader | Stacked |
| ≤ 520px | Tables show only Ticker, Verdict, Since last run and Run; Evidence moves into the verdict cell as a dot | The facts row wraps | The chart height is capped at 360px |

The page never scrolls horizontally at 390px. Wide tables inside Exact values scroll within their own container.

### Keyboard

| Where | Keys |
|---|---|
| Anywhere | `/` focuses search (and goes to Positions if not already there) |
| Positions | ↑ ↓ (also `j` `k`) move the row selection · Enter opens · Esc clears search |
| Report | `[` older run · `]` newer run · Esc back to Positions |

Keys are ignored while focus is in a text input, except Esc.

## Data changes (Python)

### `indexer.py`

**Per-run public dict: one new field**
- `executive_summary: str | None`. Taken from `decision_text` (the `5_portfolio/decision.md` the indexer already reads): the text after `**Executive Summary**:` up to the end of that paragraph, whitespace-collapsed and cut to 400 characters. Falls back to `None`.

**Index payload: new `tickers` list** (one entry per ticker, test runs excluded)

```json
{
  "ticker": "OUST",
  "group": "watching",
  "row_run_id": "…",
  "rating": "sell",
  "decision": "Sell",
  "change": "down",
  "previous": { "run_id": "…", "decision": "Hold", "rating": "hold", "analysis_date": "2026-09-03" },
  "history": [
    { "run_id": "…", "analysis_date": "2026-09-03", "decision": "Hold", "rating": "hold", "evidence_only": false },
    { "run_id": "…", "analysis_date": "2026-09-08", "decision": "Sell", "rating": "sell", "evidence_only": false }
  ]
}
```

The rules:
- **Eligible runs:** `status == "completed"` and not a test run.
- **Rated run:** an eligible run whose normalized rating is one of sell (1) through buy (5). Normalization follows the Verdict tags table and is implemented once in Python. The front end uses the `rating` values it is sent.
- **Row run:** the newest rated run, ordered by `(analysis_date, completed_at)`. If the ticker has no rated run, the newest eligible run (for example CAT's evidence-only run).
- **`group`:** `"holding"` if the row run's `perspective == "Existing holder"`, otherwise `"watching"`.
- **`change`:**
  - `"none"` if the row run is unrated
  - `"first"` if there is no earlier rated run
  - otherwise `"up"`, `"down"` or `"unchanged"`, comparing the rating number of the row run against the newest rated run strictly older than it
- **`previous`:** that older rated run, or `null`.
- **`history`:** all eligible runs, oldest first, with evidence-only runs flagged.

The existing `runs` list, `is_latest` and the cost carry-forward logic are unchanged.

### `server.py`

- **Explicit content types.** Serve static files using an explicit extension map (`.html` → `text/html`, `.js` → `text/javascript`, `.css` → `text/css`, `.svg` → `image/svg+xml`, `.json` → `application/json`), falling back to `mimetypes`. A registry-provided `text/plain` for `.js` would stop ES modules from loading.
- **Nested files.** Paths inside `static/` (for example `/views/positions.js`) must be served. The existing containment check already allows them, and it must keep rejecting paths outside the folder.
- No new API routes.

## Front-end file layout (`tradingagents/dashboard/static/`)

| File | Responsibility |
|---|---|
| `index.html` | The shell (top bar, `<main id="view">`, footer) and a single `<script type="module" src="/app.js">` |
| `app.js` | Boot, hash router, keyboard handling, theme toggle, search box, footer |
| `api.js` | `getRuns()`, `getSectors()`, `getReport(id, section)`, `refresh()`. It throws a typed error carrying the HTTP status. |
| `format.js` | Dates, numbers, money, verdict, evidence and section-slug helpers, and sector display names |
| `markdown.js` | The safe DOM markdown renderer |
| `sector-chart.js` | The shared chart and label placement |
| `views/positions.js` | The Positions page |
| `views/report.js` | The Report page (header, history strip, section nav, reader) |
| `views/market.js` | The Market page |
| `research.js` | The existing analyst-expectations renderer, converted to an ES module that exports `renderAnalyst`. The sector code moves to `sector-chart.js` and `views/market.js`. |
| `styles.css` | Theme tokens and components |

## Error handling

| Situation | Behavior |
|---|---|
| `/api/runs` fails | Positions shows an inline error panel with the message and a Retry button. The shell stays usable. |
| `/api/sectors` fails | The chart area shows the error and the `research.py sectors` instruction. The rest of the page works. |
| `/api/report` fails | The reader shows the error with Retry. The header and navigation stay usable. |
| Unknown run id | "Run not found. It may have been removed or re-indexed," with a link back to Positions. |
| Index warnings | Index-level warnings (the payload's `warnings`, such as skipped reports or roots that couldn't be scanned) show as "N index warnings" in the footer, expandable, and only when N > 0. Per-run warnings appear in that run's report header (see Notes). |
| Refresh | The button is disabled while running. On success the current route re-renders and a short status message appears. On failure the inline error shows. |
| Late responses | A response that arrives after the user has moved to another route or section is dropped (the existing `activeRun`/`activeSection` guard, generalized to routes). |

## Testing

**Python (`pytest`, runs in CI):**
- **`tests/test_research_dashboard.py`: ticker summaries**
  - holding vs watching grouping
  - the row run prefers the newest rated run over a newer evidence-only run
  - a ticker with only evidence-only runs has `change == "none"`
  - `first`, `up`, `down` and `unchanged` changes
  - unrated decisions (for example Unresolved) are ignored when comparing
  - test runs are excluded
  - history is ordered oldest first, with evidence-only runs flagged
- **`executive_summary`:** extraction, truncation, and the missing case.
- **Server:** content types for `.js` and `.css`; a nested static path is served; path traversal is still blocked.
- **`tests/test_investment_features_integration.py`:** repoint the `renderAnalyst` assertion to the module that exports it.
- `ruff check .` passes.

**Front end (Playwright, run locally against the real archive, not in CI):**
- Screenshots of Positions, Report and Market at 1440px and 390px wide, in light and dark themes.
- No console errors, and no horizontal page overflow at 390px.
- Reloading `#/run/<id>/bull-case` restores that view, and an unknown id shows "Run not found".
- A keyboard pass: `/`, ↑↓ plus Enter, `[` `]`, and Esc.
- The markdown fixture renders nested lists, a blockquote, a code block and h4, and no raw HTML from the report text is ever interpreted.

## Acceptance criteria

1. On a 1440×900 window, both position tables, the latest-report card and the sector mini chart are visible without scrolling on the current archive (12 tickers).
2. Every sector dot's color reflects its quadrant. The full chart names every sector, using leader lines where needed. The Market summary lists all 11 sectors by name, grouped by quadrant.
3. The chronology tape and the run cards are gone. A ticker's history lives in the Report page's rating-history strip.
4. Verdicts are colored by normalized rating, and "Since last run" shows OUST as `▼ from HOLD · SEP 3`.
5. Every report has a URL that can be reloaded. Sections are grouped. Previous/next run navigation works.
6. Dark mode follows the system setting, the manual toggle persists, and the look is controlled from one token block.
7. Cost basis appears on Holdings rows and report headers without any toggle, with `†` marking carried costs.
8. The existing tests plus the new ones pass, and `ruff check .` is clean.

## Out of scope

- Linking holdings to their sector on the chart. The data has only SEC industry (SIC) codes, and only on newer runs, so this would need a ticker→sector map.
- Live prices or P/L (the dashboard makes no network calls).
- Starting or editing research runs from the UI.
- A side-by-side bull/bear view, or run-to-run diffing of report text.
- Adding a JavaScript test runner to CI.
