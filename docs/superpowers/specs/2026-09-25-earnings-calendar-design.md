# Earnings calendar and rerun alerts

**Status:** design approved in brainstorming. Waiting for spec review.
**Date:** 2026-09-25
**Branch:** `codex/research-dashboard`
**Scope:**
- `tradingagents/research/` (a new calendar collector and rerun rules)
- `tradingagents/dashboard/` (a loader, one API route and the Positions and Report views)
- the tests for all of the above

## Problem

The Positions page shows what each holding's latest report says. It doesn't show when that report stops being current. Earnings are the main reason research goes stale: the company's guidance, margins and estimates change in one afternoon. Right now the only way to know when that happens is to look up each ticker's date by hand.

## Decisions (made with the user)

| Topic | Decision |
|---|---|
| Events | Earnings for every ticker, plus ex-dividend and pay dates for holdings. Macro releases (CPI, jobs, FOMC) are out of scope. |
| Placement | An **agenda list** at the top of the Positions page's right column, grouped by week (mockup option A). |
| Alerts | Flag research that predates a report, **and** warn before a report when the research is old. |
| Weekly rerun | All holdings every **Friday after the close**. |
| Earnings reruns | After the report, once the **first full session after the release** has closed. No routine run before earnings. |
| Watchlist | Alerts only. No weekly rerun. |
| Automation | Out of scope here. This spec shows what is due; running it on a schedule gets its own follow-up spec. |

The mockups are in `.tradingagents/.superpowers/brainstorm/1655-1790363203/content/calendar-placement.html` (git-ignored).

## Data source: Yahoo Finance through yfinance

Yahoo is already a dependency and needs no API key. Each ticker takes three requests:

| Call | What it provides |
|---|---|
| `Ticker.info` | `earningsTimestampStart` / `earningsTimestampEnd` (the next report window as UTC epochs), `isEarningsDateEstimate` and `quoteType` |
| `Ticker.calendar` | The EPS estimate (average, low, high), the revenue estimate (average, low, high), `Ex-Dividend Date` and `Dividend Date` |
| `Ticker.get_earnings_dates(limit=8)` | The most recent past row with a reported EPS, which gives the **last reported** date and time |

The last reported date comes only from `get_earnings_dates`. In `info`, `earningsTimestamp` sometimes holds the *upcoming* date (it did for MU on 2026-09-25), so it can't be treated as the last one.

If `info` has no next-report window, `calendar["Earnings Date"]` is the fallback. One date there means a single day. Two dates mean an estimated window, and the event is marked estimated.

### Timing: before the open or after the close

Yahoo stores report times as fixed UTC instants, all year: around 12:00 UTC for before the open (LUNR's is 12:30) and 20:00 UTC for after the close. November reports therefore read 15:00 EST, so converting to Eastern time and comparing against 4pm would call them mid-session. Timing is classified from the **UTC** clock time:

| UTC time | `timing` |
|---|---|
| 09:00 up to 14:30 | `before_open` |
| 20:00 up to midnight | `after_close` |
| anything else, or no time | `unknown` |

### Dividends

`calendar` returns only the last **declared** ex-dividend and pay dates, and most of them are in the past. Only dates on or after today appear on the calendar. Future dividends are never estimated from past ones.

## Collector: `tradingagents/research/earnings_calendar.py`

This follows `sector_rotation.py`: a pure builder, a thin fetch layer and a CLI.

- **`build_earnings_calendar(raw_by_ticker, *, retrieved_at) -> dict`**
  - A pure function. The caller owns retrieval.
  - Takes the three raw responses per ticker and returns the JSON-safe snapshot.
  - It must not treat NaN or None as a value. A missing number becomes `null`.
- **`_fetch(ticker)`** makes the three calls. Each call is wrapped separately: one failed call adds a warning to that ticker and leaves its fields `null`.
- **`main(argv)`** does the following:
  1. Builds the research index from `--scan-root` (default: the canonical `.tradingagents`, the same as the dashboard).
  2. Takes every ticker that has a completed non-test run.
  3. Fetches them one at a time and writes `--output` atomically, using the same writer as `sector_rotation.py`.
  4. Prints `Wrote <path> (N dated, N undated, N failed)`.
  5. **If every ticker fails, it exits non-zero and leaves the existing file as it was.**
- There is no `--as-of`. Yahoo's calendar is live only, so a snapshot can't be replayed to a past date.

### Snapshot: `.tradingagents/earnings-calendar.json`

```json
{
  "retrieved_at": "2026-09-25T20:05:00Z",
  "source": "Yahoo Finance",
  "tickers": [
    {
      "ticker": "AAPL",
      "quote_type": "EQUITY",
      "next_earnings": {
        "date": "2026-10-29", "window_end": null, "timing": "after_close", "estimated": false,
        "eps": {"avg": 1.98, "low": 1.93, "high": 2.07},
        "revenue": {"avg": 113624521680, "low": 112248100000, "high": 117219700000}
      },
      "last_reported": {"date": "2026-07-30", "timing": "after_close"},
      "dividend": {"ex_date": "2026-08-09", "pay_date": "2026-08-12"},
      "warnings": []
    },
    { "ticker": "DXYZ", "quote_type": "EQUITY", "next_earnings": null, "last_reported": null, "dividend": null, "warnings": [] }
  ],
  "warnings": []
}
```

## Rerun rules: `tradingagents/research/rerun_policy.py`

These are pure functions with no I/O: `evaluate(tickers, calendar, now) -> list[Alert]`. They live in `research/` rather than `dashboard/` because the automation follow-up will run the same rules to decide what to rerun.

### Terms

- **Today** is the date in `America/New_York` at `now`.
- **A session has closed** once it is 16:00 New York time on a weekday. Weekends are skipped. *Market holidays are not modeled*, so around a holiday an alert can appear one session early. The automation follow-up adds a trading calendar.
- **Research date:**
  - The `analysis_date` of the ticker's newest completed, non-test, non-evidence-only run.
  - It is **none** when only evidence packets exist (GSAT and CAT today). None counts as older than any threshold, and the alert message says "no full report yet".
- **Reported date R:** the latest of these two:
  - `last_reported.date`
  - `next_earnings.date`, once that report is out: its date is before today, or it is today and either it is a before-open report or it is 16:00 or later
  - This rule keeps alerts correct when the calendar snapshot is older than the report.
- **Post-earnings slot S:**
  - For a `before_open` report, S is R itself.
  - Otherwise S is the next weekday after R. `unknown` timing counts as after the close.
- **Weekly slot F:** today if it is Friday and 16:00 or later, otherwise the most recent earlier Friday.

### Rules

Each ticker gets **at most one** alert, taking the highest-priority rule that matches.

| Priority | Kind | Applies to | Fires when | Severity |
|---|---|---|---|---|
| 1 | `post_earnings` | all | S's session has closed, and the research date is before S | holding: `rerun` (red) · watching: `due` (amber) |
| 2 | `pre_earnings` | all | the next report is 0–7 days after today, and the research is more than 30 days old | `due` (amber) |
| 3 | `weekly_due` | holdings | the research date is before F − 2 days, so a run on the Wednesday, Thursday or Friday of that week counts | `due` (amber) |
| 4 | `post_earnings_pending` | all | the report is out but S hasn't closed yet, and the research date is before S | `pending` (muted) |

The constants `PRE_EARNINGS_WINDOW_DAYS = 7`, `PRE_EARNINGS_STALE_DAYS = 30`, `WEEKLY_SLOT_WEEKDAY = 4` (Friday) and `WEEKLY_GRACE_DAYS = 2` are named at the top of the module.

**Alert shape:** `{ticker, group, kind, severity, message, run_id, dates: {reported, slot, next, research}}`. `run_id` is the ticker's row run from `tickers`, so clicking an alert opens that ticker's report.

### Worked examples (now = Fri 2026-09-25, 15:49 ET)

- **MU (watching):**
  - It reports on Wed Sep 30, which is 5 days away. Its research is from Aug 18, 38 days ago.
  - Result: `pre_earnings`, with the message "Reports Wed Sep 30 · research 38 days old".
- **The other holdings, all researched on Sep 23 or 24:**
  - F is Sep 18, because 16:00 hasn't come yet today, so F − 2 is Sep 16.
  - Result: no alert. They stay clear after 16:00 too, because F becomes Sep 25 and F − 2 becomes Sep 23.
- **GOOGL after Oct 28:**
  - It reports after the close on Wed Oct 28, so S is Thu Oct 29.
  - From Oct 28 after the close until 16:00 on Oct 29: `post_earnings_pending`, with the message "Reported Oct 28 · re-run after Oct 29 close".
  - After that: `post_earnings` (red) until there's a run dated Oct 29 or later.
- **GSAT (holding):** it only has an evidence packet, so it shows `weekly_due` with the message "no full report yet" until it gets a full run.

## Dashboard

### Loader: `tradingagents/dashboard/earnings.py`

This follows `dashboard/sectors.py`: it reads the precomputed file and never makes a network call.

- **Validation:**
  - The file size is at most 2 MB.
  - The top level must be an object, and `retrieved_at` must carry a timezone and not be in the future (with 5 minutes of tolerance).
  - Dates must be ISO dates, and `timing` must be one of the three values.
  - Numbers must be finite or `null`, and strings are truncated.
  - Any failure returns `status: "unavailable"` with the reason as a warning, never partial data.
- **`status` values:**
  - `available`
  - `stale`, when `retrieved_at` is more than 3 days old. Dates move, especially estimated ones.
  - `unavailable`, when the file is missing or invalid.
- **`build_earnings_view(snapshot, index, now)`** assembles the API payload.

### API: `GET /api/earnings`

Each request re-reads the file and recomputes the alerts against the current time, so they move forward even while the page stays open. It works like `/api/sectors`.

```json
{
  "status": "available",
  "retrieved_at": "…", "source": "Yahoo Finance",
  "today": "2026-09-25",
  "events": [
    {"date": "2026-09-30", "ticker": "MU", "group": "watching", "kind": "earnings",
     "timing": "after_close", "estimated": false, "eps": {…}, "revenue": {…}},
    {"date": "2026-09-30", "ticker": "NVDA", "group": "holding", "kind": "dividend_paid"}
  ],
  "next_earnings": {"AAPL": {"date": "2026-10-29", "timing": "after_close", "estimated": false}},
  "alerts": [ … ],
  "undated": ["DXYZ"],
  "missing": [],
  "warnings": []
}
```

- **`events`:** earnings for every ticker, plus `ex_dividend` and `dividend_paid` for holdings. It covers today through today + 60 days. Events are sorted by date, then earnings before dividends, then holdings before watching, then by ticker.
- **`next_earnings`:** every dated ticker, including ones past the 60-day window (CRWD's Dec 1 report is 67 days out).
- **`missing`:** tickers in the archive that aren't in the snapshot, for example one researched after the last calendar refresh.
- **Server and CLI changes:**
  - `DashboardState` gains `earnings_file`, defaulting to `roots[0] / "earnings-calendar.json"`.
  - `tradingagents.dashboard` gains `--earnings-file`.

### Agenda panel (Positions, right column, first panel)

The right column becomes **Agenda**, then **Latest report**, then **Sector chart**. The agenda lives in a new module, `static/agenda.js`, because `positions.js` is already long.

1. **Eyebrow:** `UPCOMING · EARNINGS & DIVIDENDS`, with `yahoo · <retrieved date>` on the right.
2. **Rerun queue:** shown only when there are alerts.
   - Rows are ordered `rerun`, then `due`, then `pending`, then by date. Each row has a severity dot, the ticker and the message, and clicking it opens `#/run/<run_id>`.
   - When two or more holdings are `weekly_due`, they collapse into one row: `WEEKLY RUN DUE · AAPL AMZN …`.
   - With no alerts, one muted line reads "Research is current."
3. **Agenda:**
   - Rows are grouped under `THIS WEEK`, `NEXT WEEK` or `WEEK OF OCT 26 · IN 31D` (weeks run Monday to Sunday).
   - An earnings row shows `WED SEP 30 · ticker · verdict tag · after close / before open / — · EPS 1.98`.
   - An estimated date gets a `~` prefix.
   - Watching tickers are muted, the same as in the mockup.
   - A dividend row shows `$ ex-dividend` or `$ dividend paid` in the accent color, with no verdict or EPS.
   - Each row's `title` tooltip gives the EPS range, the average revenue estimate, and "estimated date" when that applies.
4. **Cap:** 6 agenda rows, then a `+ N more through <last date>` button that expands the list inline. `Show fewer` collapses it. The state lasts for the session only. Queue rows don't count toward the cap.
5. **Footer:** `No date published: DXYZ` and `Not in the calendar yet: X — refresh it`. Each appears only when it has entries.
6. **Status:**
   - `stale` shows an amber banner inside the panel: "Earnings dates are N days old; refresh before relying on them". Alerts are still shown.
   - `unavailable` or a fetch error shows the reason and the command `.\.venv\Scripts\python.exe scripts\research.py earnings`, the same pattern as `sectorMissing`.

### Positions tables

- **New `Next` column.** It goes before `Run` in both tables.
  - It shows the days until the next report (`5d`, `34d`) or `—`, with the date and timing in the tooltip.
  - It's optional at ≤520px, like the other `col-opt` columns.
  - `0d` reads `today`.
- **Alert dot.** A red or amber dot after the ticker, with the alert message as its tooltip. There's no dot for `pending`.

### Report page

- **Facts row:** a `Next earnings` fact (`Thu Oct 29 · after close`, or `~Nov 10 · estimated`), left out when there's no date.
- **Alert banner:** when the ticker has a `post_earnings` or `pre_earnings` alert, a one-line banner under the facts row shows its message. When you're reading an old report, you can see that it predates a report.

### Front-end plumbing

- **`api.js`:** adds `getEarnings()`.
- **`app.js`:**
  - Adds `state.earnings` and `state.earningsError`.
  - `loadEarnings()` runs at boot and on Refresh, the same way as `loadSectors()`.
  - The ctx gains `earnings`.
- **`format.js`:** helpers for timing labels, countdowns and the estimated-date prefix.
- **`shared.js`:** adds `EARNINGS_COMMAND`.
- **The CSP stays `'self'`.** There are no inline styles, and colors come from the existing tokens (`--bad`, `--warn`, `--mu`, `--acc`).

### Launcher (`scripts/research.py`, untracked in the canonical checkout)

This file is outside this branch, so it's changed by hand:
- Add `"earnings": "tradingagents.research.earnings_calendar"`, with defaults `--output <artifacts>/earnings-calendar.json` and `--scan-root <artifacts>`.
- Add `--earnings-file <artifacts>/earnings-calendar.json` to the dashboard defaults.

## Error handling

| Situation | Behavior |
|---|---|
| No snapshot file | The panel explains and shows the `research.py earnings` command. The tables show `—` under Next. There are no alerts. |
| Stale snapshot (more than 3 days) | An amber banner in the panel. Alerts are still computed. |
| Invalid snapshot | `unavailable`, with the reason from the loader. |
| `/api/earnings` fails | The panel shows an error with Retry. The rest of the page works. |
| One ticker fails during collection | Its entry keeps `null` fields and a warning. It is listed under "Data notes" in the panel. |
| Every ticker fails | The CLI exits non-zero, and the existing file is kept. |
| Crypto, ETF or fund with no dates | The ticker is listed as `undated`. It isn't an error. |

## Testing

**Python (pytest, runs in CI):**
- **`tests/test_research_earnings_calendar.py`**, using fake raw responses and no network:
  - 12:00 UTC is before the open, 20:00 UTC is after the close, and November's 20:00 UTC stays after the close
  - midnight or no time is `unknown`
  - the estimated flag
  - a two-date calendar window is treated as estimated
  - NaN estimates become `null`
  - the last reported date comes from `get_earnings_dates` and not from `info.earningsTimestamp`
  - an undated ticker
  - one failed call becomes a warning
  - when every ticker fails, the CLI exits non-zero and the output is untouched
- **`tests/test_rerun_policy.py`**, with `now` injected:
  - the MU pre-earnings case, and the boundaries at 7 and 8 days and at 30 and 31 days
  - a before-open report has S = R, an after-close report has S on the next weekday, and a Friday after-close report's S lands on Monday
  - pending versus due at exactly 15:59 and 16:00 New York time
  - research on or after S clears the alert
  - the weekly slot at Friday 15:59 and 16:00, and research on the Wednesday or Thursday of that week counts
  - `weekly_due` applies only to holdings
  - evidence-only research counts as none
  - one alert per ticker, by priority
  - the reported date is still found when the snapshot's `next_earnings` date has passed
- **`tests/test_research_dashboard.py`** (the loader and the server):
  - a missing file gives `unavailable` with instructions
  - stale after 3 days
  - a future `retrieved_at` is rejected
  - an oversized or malformed file is rejected
  - `/api/earnings` returns events within the 60-day window, `next_earnings` beyond it, `undated` and `missing`
- `ruff check .` passes.

**Front end (Playwright, run locally against the real archive, not in CI):**
- Positions at 1440px and 390px wide, in light and dark themes.
- The queue shows MU's pre-earnings warning, and `+ N more` expands and collapses.
- The Next column and the alert dots are present, and the report header shows Next earnings.
- There are no console errors and no horizontal overflow at 390px.

## Acceptance criteria

1. `scripts\research.py earnings` writes the snapshot for every archived ticker and reports how many are dated, undated and failed.
2. On today's data, the collapsed agenda shows MU's earnings and NVDA's dividend payment (both Sep 30), then BRO, GOOGL, AAPL and AMZN in the week of Oct 26. `+ 9 more through Nov 19` reveals CAT and the rest. CRWD's Dec 1 report is past the 60-day window, so it appears only in the Next column (`67d`). The footer names DXYZ as undated.
3. The queue shows MU as pre-earnings and GSAT as weekly-due. On a date after Oct 29 with no newer run, GOOGL shows as a red post-earnings rerun.
4. The timing labels match Yahoo's before-open and after-close times for November reports, which fall after daylight saving ends.
5. At 1440×900, the tables, the agenda panel and the latest-report card are visible without scrolling. *This replaces criterion 1 of the Research Ledger spec: the sector chart may now start below the fold.*
6. The dashboard still makes no network calls.
7. The existing tests and the new ones pass, and `ruff check .` is clean.

## Out of scope (follow-up spec: automatic reruns)

- **A `research.py rerun` command.** It builds the evidence packets and runs the agents for whatever `rerun_policy.evaluate` says is due. It generalizes `.tradingagents/run_holdings_2026_09_23.py`, which is hard-coded to one date.
- **A Windows scheduled task at 15:00 Mountain on weekdays**, plus a notification when it finishes.
- **A trading-holiday calendar**, so no runs are scheduled on holidays and post-earnings slots are exact.
- **Labeling runs with the New York trading date.** The "2026-09-24" runs were built at 19:16 Mountain on Sep 23, which was already Sep 24 in UTC, so a Friday-evening run would otherwise be labeled Saturday.
- Macro releases, beat/miss history, and editing the watchlist without a research run.
