from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from tradingagents.research.rerun_policy import TickerState, evaluate

NY = ZoneInfo("America/New_York")


def at(year, month, day, hour=12, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=NY)


def holding(ticker="AAPL", research=date(2026, 9, 23)):
    return TickerState(ticker=ticker, group="holding", research_date=research, run_id=f"run-{ticker}")


def watching(ticker="MU", research=date(2026, 8, 18)):
    return TickerState(ticker=ticker, group="watching", research_date=research, run_id=f"run-{ticker}")


def entry(next_date=None, timing="after_close", last=None, last_timing="after_close"):
    return {
        "next_earnings": {"date": next_date, "timing": timing, "estimated": False} if next_date else None,
        "last_reported": {"date": last, "timing": last_timing} if last else None,
    }


def kinds(alerts):
    return [(alert["ticker"], alert["kind"], alert["severity"]) for alert in alerts]


# ---------- before earnings ----------

def test_old_research_meeting_a_report_within_a_week_is_flagged_before_earnings():
    alerts = evaluate([watching("MU", date(2026, 8, 18))], {"MU": entry("2026-09-30")}, at(2026, 9, 25, 15, 49))
    assert kinds(alerts) == [("MU", "pre_earnings", "due")]
    assert alerts[0]["message"] == "Reports Wed Sep 30 · research 38 days old"
    assert alerts[0]["run_id"] == "run-MU"
    assert alerts[0]["dates"] == {"reported": None, "slot": None, "next": "2026-09-30", "research": "2026-08-18"}


@pytest.mark.parametrize(("next_date", "research", "flagged"), [
    ("2026-10-02", date(2026, 8, 18), True),    # 7 days out
    ("2026-10-03", date(2026, 8, 18), False),   # 8 days out
    ("2026-09-30", date(2026, 8, 25), True),    # research 31 days old
    ("2026-09-30", date(2026, 8, 26), False),   # research 30 days old
])
def test_before_earnings_window_boundaries(next_date, research, flagged):
    alerts = evaluate([watching("MU", research)], {"MU": entry(next_date)}, at(2026, 9, 25))
    assert (kinds(alerts) == [("MU", "pre_earnings", "due")]) is flagged


def test_a_report_already_out_today_is_not_flagged_as_upcoming():
    now = at(2026, 9, 30, 16, 30)
    alerts = evaluate([watching("MU", date(2026, 8, 18))], {"MU": entry("2026-09-30")}, now)
    assert kinds(alerts) == [("MU", "post_earnings_pending", "pending")]


# ---------- after earnings ----------

def test_after_close_report_waits_for_the_next_sessions_close():
    state = [holding("GOOGL", date(2026, 10, 23))]
    calendar = {"GOOGL": entry("2026-10-28")}
    pending = evaluate(state, calendar, at(2026, 10, 29, 15, 59))
    assert kinds(pending) == [("GOOGL", "post_earnings_pending", "pending")]
    assert pending[0]["message"] == "Reported Oct 28 · re-run after Oct 29 close"
    due = evaluate(state, calendar, at(2026, 10, 29, 16, 0))
    assert kinds(due) == [("GOOGL", "post_earnings", "rerun")]
    assert due[0]["message"] == "Reported Oct 28 · research from Oct 23"
    assert due[0]["dates"]["reported"] == "2026-10-28"
    assert due[0]["dates"]["slot"] == "2026-10-29"


def test_friday_after_close_report_is_rerun_after_mondays_close():
    # Research from Wednesday of the report week, so the weekly rule stays quiet.
    state = [holding("AAPL", date(2026, 10, 28))]
    calendar = {"AAPL": entry("2026-10-30")}
    assert kinds(evaluate(state, calendar, at(2026, 10, 31))) == [("AAPL", "post_earnings_pending", "pending")]
    due = evaluate(state, calendar, at(2026, 11, 2, 16, 0))
    assert kinds(due) == [("AAPL", "post_earnings", "rerun")]
    assert due[0]["dates"]["slot"] == "2026-11-02"


def test_before_open_report_is_rerun_after_the_same_days_close():
    state = [holding("GSAT", date(2026, 10, 30))]
    calendar = {"GSAT": entry("2026-11-05", timing="before_open")}
    assert kinds(evaluate(state, calendar, at(2026, 11, 5, 9, 29))) == []
    assert kinds(evaluate(state, calendar, at(2026, 11, 5, 12))) == [("GSAT", "post_earnings_pending", "pending")]
    due = evaluate(state, calendar, at(2026, 11, 5, 16))
    assert kinds(due) == [("GSAT", "post_earnings", "rerun")]
    assert due[0]["dates"]["slot"] == "2026-11-05"


def test_unknown_timing_counts_as_after_close():
    state = [holding("OUST", date(2026, 10, 30))]
    calendar = {"OUST": entry("2026-11-03", timing="unknown")}
    assert kinds(evaluate(state, calendar, at(2026, 11, 3, 17))) == [("OUST", "post_earnings_pending", "pending")]
    assert kinds(evaluate(state, calendar, at(2026, 11, 4, 16))) == [("OUST", "post_earnings", "rerun")]


def test_research_on_or_after_the_slot_clears_the_alert():
    calendar = {"GOOGL": entry("2026-10-28")}
    assert evaluate([holding("GOOGL", date(2026, 10, 29))], calendar, at(2026, 10, 30, 9)) == []


def test_research_between_the_report_and_its_slot_is_still_stale():
    alerts = evaluate([holding("GOOGL", date(2026, 10, 28))], {"GOOGL": entry("2026-10-28")}, at(2026, 10, 30, 9))
    assert kinds(alerts) == [("GOOGL", "post_earnings", "rerun")]


def test_watching_ticker_after_earnings_is_amber_not_red():
    alerts = evaluate([watching("BRO", date(2026, 8, 24))], {"BRO": entry("2026-10-26")}, at(2026, 10, 28))
    assert kinds(alerts) == [("BRO", "post_earnings", "due")]


def test_last_reported_date_is_used_once_the_calendar_moves_on():
    calendar = {"GOOGL": entry("2027-02-03", last="2026-10-28")}
    alerts = evaluate([holding("GOOGL", date(2026, 10, 23))], calendar, at(2026, 11, 2))
    assert kinds(alerts) == [("GOOGL", "post_earnings", "rerun")]
    assert alerts[0]["dates"]["reported"] == "2026-10-28"


def test_a_passed_next_date_counts_as_reported_when_the_snapshot_is_old():
    calendar = {"GOOGL": entry("2026-10-28", last="2026-07-29")}
    alerts = evaluate([holding("GOOGL", date(2026, 10, 23))], calendar, at(2026, 11, 2))
    assert alerts[0]["dates"]["reported"] == "2026-10-28"


# ---------- weekly run ----------

@pytest.mark.parametrize(("now", "research", "due"), [
    (at(2026, 9, 25, 15, 59), date(2026, 9, 16), False),  # slot is still Sep 18; Wed Sep 16 counts
    (at(2026, 9, 25, 15, 59), date(2026, 9, 15), True),
    (at(2026, 9, 25, 16, 0), date(2026, 9, 23), False),   # slot is now Sep 25; Wed Sep 23 counts
    (at(2026, 9, 25, 16, 0), date(2026, 9, 22), True),
    (at(2026, 9, 27, 10, 0), date(2026, 9, 22), True),    # Sunday: slot is Fri Sep 25
])
def test_weekly_run_is_due_after_fridays_close(now, research, due):
    alerts = evaluate([holding("AAPL", research)], {}, now)
    assert (kinds(alerts) == [("AAPL", "weekly_due", "due")]) is due


def test_weekly_run_applies_only_to_holdings():
    assert evaluate([watching("TPR", date(2026, 8, 18))], {}, at(2026, 9, 26)) == []


def test_holding_without_a_full_report_is_due_for_its_weekly_run():
    alerts = evaluate([holding("GSAT", None)], {"GSAT": entry("2026-11-05", timing="before_open")}, at(2026, 9, 25))
    assert kinds(alerts) == [("GSAT", "weekly_due", "due")]
    assert alerts[0]["message"] == "Weekly run due · no full report yet"
    assert alerts[0]["dates"]["research"] is None


def test_ticker_without_a_full_report_gets_no_earnings_alerts():
    calendar = {"CAT": entry("2026-10-29", timing="before_open", last="2026-07-29")}
    assert evaluate([watching("CAT", None)], calendar, at(2026, 10, 26)) == []


def test_weekly_message_names_the_research_date():
    alerts = evaluate([holding("AAPL", date(2026, 9, 16))], {}, at(2026, 9, 26))
    assert alerts[0]["message"] == "Weekly run due · research from Sep 16"


# ---------- combining ----------

def test_each_ticker_gets_only_its_highest_priority_alert():
    # Both the post-earnings rule and the weekly rule match; post-earnings wins.
    alerts = evaluate([holding("AAPL", date(2026, 10, 16))], {"AAPL": entry("2026-10-29")}, at(2026, 10, 31))
    assert kinds(alerts) == [("AAPL", "post_earnings", "rerun")]


def test_alerts_are_ordered_red_then_amber_then_pending():
    state = [
        holding("GSAT", date(2026, 10, 30)),         # pending
        watching("MU", date(2026, 8, 18)),           # amber, before earnings
        holding("GOOGL", date(2026, 10, 23)),        # red
    ]
    calendar = {
        "GSAT": entry("2026-11-05", timing="before_open"),
        "MU": entry("2026-11-10"),
        "GOOGL": entry("2026-10-28"),
    }
    alerts = evaluate(state, calendar, at(2026, 11, 5, 12))
    assert [alert["ticker"] for alert in alerts] == ["GOOGL", "MU", "GSAT"]


def test_now_must_carry_a_timezone():
    with pytest.raises(ValueError):
        evaluate([], {}, datetime(2026, 9, 25, 12))


def test_utc_now_is_read_in_new_york_time():
    # 20:30 UTC on Oct 29 is 16:30 in New York: GOOGL's slot has closed.
    now = datetime(2026, 10, 29, 20, 30, tzinfo=timezone.utc)
    alerts = evaluate([holding("GOOGL", date(2026, 10, 23))], {"GOOGL": entry("2026-10-28")}, now)
    assert kinds(alerts) == [("GOOGL", "post_earnings", "rerun")]
