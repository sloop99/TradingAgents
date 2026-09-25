"""When a ticker's research is due for a rerun.

Pure rules over run dates and an earnings-calendar snapshot. The dashboard
shows the resulting alerts; an automated rerun job can act on the same list.

Market holidays are not modeled: a session is any weekday, closing at 16:00
New York time. Around a holiday an alert can therefore appear a session early.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)

PRE_EARNINGS_WINDOW_DAYS = 7    # warn this many days before a report...
PRE_EARNINGS_STALE_DAYS = 30    # ...when the research is older than this
WEEKLY_SLOT_WEEKDAY = 4         # Friday: every holding is rerun after the close
WEEKLY_GRACE_DAYS = 2           # a run this many days before the slot (Wed/Thu) still counts

_SEVERITY_ORDER = {"rerun": 0, "due": 1, "pending": 2}


@dataclass(frozen=True)
class TickerState:
    ticker: str
    group: str                  # "holding" or "watching"
    research_date: date | None  # newest full report; None when only evidence packets exist
    run_id: str | None          # the report an alert links to


def evaluate(tickers: Iterable[TickerState], calendar: Mapping[str, Mapping[str, Any]], now: datetime) -> list[dict]:
    """At most one alert per ticker, most urgent first.

    ``calendar`` maps a ticker to its earnings-calendar snapshot entry
    (``next_earnings`` and ``last_reported``); missing tickers have no events.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    local = now.astimezone(MARKET_TZ)
    ranked = []
    for state in tickers:
        found = _alert(state, calendar.get(state.ticker) or {}, local)
        if found is not None:
            alert, when = found
            ranked.append(((_SEVERITY_ORDER[alert["severity"]], when, alert["ticker"]), alert))
    return [alert for _, alert in sorted(ranked, key=lambda item: item[0])]


def _alert(state: TickerState, entry: Mapping[str, Any], local: datetime) -> tuple[dict, date] | None:
    """The ticker's highest-priority alert and the date it sorts by, or None."""
    today = local.date()
    research = state.research_date
    next_event = _event(entry.get("next_earnings"))
    dates = {"reported": None, "slot": None, "next": _iso(next_event and next_event[0]), "research": _iso(research)}

    def build(kind, severity, message, when):
        return {
            "ticker": state.ticker, "group": state.group, "kind": kind, "severity": severity,
            "message": message, "run_id": state.run_id, "dates": dates,
        }, when

    pending = None
    # Earnings alerts need a verdict to go stale: a ticker with no full report gets none.
    if research is not None:
        released = [event for event in (_event(entry.get("last_reported")), next_event)
                    if event is not None and _released(*event, local)]
        if released:
            reported, timing = max(released)
            slot = _post_earnings_slot(reported, timing)
            if research < slot:
                dates.update(reported=reported.isoformat(), slot=slot.isoformat())
                if _session_closed(slot, local):
                    severity = "rerun" if state.group == "holding" else "due"
                    return build("post_earnings", severity,
                                 f"Reported {_day(reported)} · research from {_day(research)}", slot)
                pending = build("post_earnings_pending", "pending",
                                f"Reported {_day(reported)} · re-run after {_day(slot)} close", slot)

        if next_event is not None and not _released(*next_event, local):
            upcoming = next_event[0]
            age = (today - research).days
            if 0 <= (upcoming - today).days <= PRE_EARNINGS_WINDOW_DAYS and age > PRE_EARNINGS_STALE_DAYS:
                return build("pre_earnings", "due",
                             f"Reports {upcoming:%a} {_day(upcoming)} · research {age} days old", upcoming)

    if state.group == "holding":
        slot = _weekly_slot(local)
        if research is None or research < slot - timedelta(days=WEEKLY_GRACE_DAYS):
            detail = "no full report yet" if research is None else f"research from {_day(research)}"
            return build("weekly_due", "due", f"Weekly run due · {detail}", research or date.min)

    return pending


def _event(raw: Any) -> tuple[date, str] | None:
    if not isinstance(raw, Mapping) or not raw.get("date"):
        return None
    return date.fromisoformat(str(raw["date"])[:10]), str(raw.get("timing") or "unknown")


def _released(day: date, timing: str, local: datetime) -> bool:
    """Whether a report on ``day`` is out by ``local``."""
    if local.date() != day:
        return local.date() > day
    return local.time() >= (MARKET_OPEN if timing == "before_open" else MARKET_CLOSE)


def _post_earnings_slot(reported: date, timing: str) -> date:
    """The first full session after the release; unknown timing counts as after the close."""
    return reported if timing == "before_open" else _next_weekday(reported)


def _session_closed(day: date, local: datetime) -> bool:
    return local.date() > day or (local.date() == day and local.time() >= MARKET_CLOSE)


def _weekly_slot(local: datetime) -> date:
    """The most recent Friday whose close has passed."""
    today = local.date()
    friday = today - timedelta(days=(today.weekday() - WEEKLY_SLOT_WEEKDAY) % 7)
    if friday == today and local.time() < MARKET_CLOSE:
        friday -= timedelta(days=7)
    return friday


def _next_weekday(day: date) -> date:
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _day(value: date) -> str:
    return f"{value:%b} {value.day}"


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None
