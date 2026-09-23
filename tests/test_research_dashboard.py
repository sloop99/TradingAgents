import http.client
import json
import mimetypes
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from tradingagents.dashboard.indexer import build_index, default_scan_roots, normalize_rating
from tradingagents.dashboard.server import DashboardState, handler_factory


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _rated_run(root: Path, ticker: str, date: str, decision: str, *, completed: str = "", context: str = "",
               summary: str = "", stamp: str = "full", extra: str = "") -> Path:
    run = root / "runs" / f"{ticker}_{date}_{stamp}"
    _write(run / "report" / "complete_report.md", f"# Trading Analysis Report: {ticker}")
    body = f"**Rating**: {decision}"
    if summary:
        body += f"\n\n**Executive Summary**: {summary}\n\n**Investment Thesis**: Not part of the summary."
    if extra:
        body += "\n\n" + extra
    _write(run / "report" / "5_portfolio" / "decision.md", body)
    manifest = {"ticker": ticker, "analysis_date": date, "status": "completed", "final_rating": decision}
    if completed:
        manifest["completed_at"] = completed
    if context:
        manifest["decision_context"] = context
    _write(run / "run_manifest.json", json.dumps(manifest))
    return run


def _evidence_run(root: Path, ticker: str, date: str, *, completed: str = "") -> Path:
    run = root / "research" / ticker / date / f"evidence-{completed or 'x'}".replace(":", "")
    _write(run / "research-brief.md", f"# {ticker} evidence brief")
    manifest = {"ticker": ticker, "as_of": date, "status": "completed"}
    if completed:
        manifest["completed_at"] = completed
    _write(run / "manifest.json", json.dumps(manifest))
    return run


def _summaries(root: Path) -> dict[str, dict]:
    return {entry["ticker"]: entry for entry in build_index([root]).public_payload()["tickers"]}


def test_indexes_legacy_and_evidence_aware_runs(tmp_path):
    legacy = tmp_path / "runs" / "OUST_2026-09-08_full"
    _write(legacy / "report" / "complete_report.md", "# Trading Analysis Report: OUST")
    _write(
        legacy / "report" / "5_portfolio" / "decision.md",
        "**Rating**: Sell\n\nAvoid initiating a new position.",
    )
    (legacy / "run_manifest.json").write_text(
        json.dumps({
            "ticker": "OUST",
            "analysis_date": "2026-09-08",
            "status": "complete",
            "final_rating": "Sell",
            "decision_context": "Prospective new buyer",
            "action_interpretation": "Avoid initiating; not a short.",
            "model": "gpt-test",
        }),
        encoding="utf-8",
    )

    modern = tmp_path / "research" / "CRWD" / "2026-09-17" / "stamp"
    _write(modern / "reports" / "complete_report.md", "# CRWD report")
    _write(modern / "reports" / "5_portfolio" / "decision.md", "**Rating**: Hold")
    _write(modern / "reports" / "0_evidence" / "coverage.md", "# Evidence")
    (modern / "manifest.json").write_text(
        json.dumps({
            "ticker": "CRWD",
            "as_of": "2026-09-17",
            "status": "completed",
            "evidence_status": "material_conflict",
            "packet_thesis": "Existing CRWD holding with average broker cost USD 240.54 per share.",
            "packet_horizon": "long_term",
            "agent_configuration": {"provider": "codex_subscription", "analysts": ["fundamentals"]},
        }),
        encoding="utf-8",
    )
    (modern / "packet.json").write_text(
        json.dumps({"ticker": "CRWD", "as_of": "2026-09-17", "coverage": {"valuation": "unsupported"}}),
        encoding="utf-8",
    )

    index = build_index([tmp_path])
    assert len(index.runs) == 2
    latest = index.runs[0]
    assert latest.ticker == "CRWD"
    assert latest.is_latest is True
    assert latest.perspective == "Existing holder"
    assert latest.average_cost_usd == 240.54
    assert latest.average_cost_as_of == "2026-09-17"
    assert latest.evidence_status == "material_conflict"
    assert latest.valuation_status == "unsupported"
    assert "Evidence coverage" in latest.sections
    oust = index.runs[1]
    assert oust.status == "completed"
    assert oust.perspective == "Prospective buyer"
    assert oust.decision_interpretation == "Avoid initiating; not a short."


def test_smoke_runs_are_labeled_and_excluded_from_public_summary(tmp_path):
    report = tmp_path / "codex_smoke" / "AAPL_2026-08-15" / "complete_report.md"
    _write(report, "# Trading Analysis Report: AAPL\n\nFINAL TRANSACTION PROPOSAL: **HOLD**")
    index = build_index([tmp_path])
    assert index.runs[0].is_smoke is True
    assert index.public_payload()["summary"] == {
        "runs": 0,
        "tickers": 0,
        "smoke_runs": 1,
        "latest_date": None,
    }


def test_malformed_manifest_does_not_block_other_runs(tmp_path):
    run = tmp_path / "runs" / "AMZN_2026-09-01_full"
    _write(run / "report" / "complete_report.md", "# Trading Analysis Report: AMZN")
    _write(run / "report" / "5_portfolio" / "decision.md", "**Rating**: Overweight")
    _write(run / "run_manifest.json", "{not json")
    index = build_index([tmp_path])
    assert len(index.runs) == 1
    assert index.runs[0].ticker == "AMZN"
    assert index.runs[0].decision == "Overweight"
    assert any("Ignored run_manifest.json" in warning for warning in index.runs[0].warnings)


def test_default_root_reaches_canonical_archive_from_nested_worktree(tmp_path):
    worktree = tmp_path / ".tradingagents" / "worktrees" / "dashboard"
    worktree.mkdir(parents=True)
    assert default_scan_roots(worktree) == [(tmp_path / ".tradingagents").resolve()]


def test_last_recorded_position_cost_is_carried_forward_with_provenance(tmp_path):
    older = tmp_path / "runs" / "FTNT_2026-09-16_holder"
    _write(older / "report" / "complete_report.md", "# FTNT report")
    _write(
        older / "run_manifest.json",
        json.dumps({
            "ticker": "FTNT",
            "analysis_date": "2026-09-16",
            "average_cost_usd": 172.33,
        }),
    )
    newer = tmp_path / "runs" / "FTNT_2026-09-17_refresh"
    _write(newer / "report" / "complete_report.md", "# FTNT refresh")
    _write(
        newer / "run_manifest.json",
        json.dumps({"ticker": "FTNT", "analysis_date": "2026-09-17"}),
    )

    latest = build_index([tmp_path]).runs[0]
    assert latest.average_cost_usd == 172.33
    assert latest.average_cost_as_of == "2026-09-16"
    assert any("carried from" in warning for warning in latest.warnings)


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        ("Buy", "buy"), ("Strong Buy", "buy"), ("Overweight", "overweight"), ("HOLD", "hold"),
        ("Neutral", "hold"), ("Underweight", "underweight"), ("Sell", "sell"), ("strong  sell", "sell"),
        ("Evidence only", "evidence"), ("Unresolved", "unrated"), ("", "unrated"), (None, "unrated"),
    ],
)
def test_normalize_rating_maps_decisions_onto_the_five_tier_scale(decision, expected):
    assert normalize_rating(decision) == expected


def test_ticker_summary_groups_by_the_row_runs_perspective(tmp_path):
    _rated_run(tmp_path, "PANW", "2026-09-17", "Hold", context="Existing PANW holding")
    _rated_run(tmp_path, "AMZN", "2026-09-01", "Overweight")
    summaries = _summaries(tmp_path)
    assert summaries["PANW"]["group"] == "holding"
    assert summaries["AMZN"]["group"] == "watching"


def test_ticker_summary_prefers_newest_rated_run_over_newer_evidence_only_run(tmp_path):
    rated = _rated_run(tmp_path, "PANW", "2026-09-17", "Hold", completed="2026-09-17T10:00:00Z")
    _evidence_run(tmp_path, "PANW", "2026-09-17", completed="2026-09-17T11:00:00Z")
    index = build_index([tmp_path])
    rated_id = next(run.id for run in index.runs if run.report_path.is_relative_to(rated.resolve()))
    summary = index.public_payload()["tickers"][0]
    assert summary["row_run_id"] == rated_id
    assert summary["rating"] == "hold"
    assert summary["decision"] == "Hold"


def test_ticker_with_only_evidence_runs_has_no_rating_change(tmp_path):
    _evidence_run(tmp_path, "CAT", "2026-09-16", completed="2026-09-16T09:00:00Z")
    summary = _summaries(tmp_path)["CAT"]
    assert summary["rating"] == "evidence"
    assert summary["decision"] == "Evidence only"
    assert summary["change"] == "none"
    assert summary["previous"] is None


def test_rating_change_compares_against_the_previous_rated_run(tmp_path):
    _rated_run(tmp_path, "OUST", "2026-09-03", "Hold")
    _rated_run(tmp_path, "OUST", "2026-09-08", "Sell")
    _rated_run(tmp_path, "LUNR", "2026-08-01", "Underweight")
    _rated_run(tmp_path, "LUNR", "2026-08-24", "Overweight")
    _rated_run(tmp_path, "CRWD", "2026-09-16", "Hold")
    _rated_run(tmp_path, "CRWD", "2026-09-17", "Hold")
    _rated_run(tmp_path, "AMZN", "2026-09-01", "Overweight")
    summaries = _summaries(tmp_path)
    assert summaries["OUST"]["change"] == "down"
    assert summaries["OUST"]["previous"]["decision"] == "Hold"
    assert summaries["OUST"]["previous"]["rating"] == "hold"
    assert summaries["OUST"]["previous"]["analysis_date"] == "2026-09-03"
    assert summaries["LUNR"]["change"] == "up"
    assert summaries["CRWD"]["change"] == "unchanged"
    assert summaries["AMZN"]["change"] == "first"
    assert summaries["AMZN"]["previous"] is None


def test_unrated_runs_are_skipped_when_comparing_ratings(tmp_path):
    _rated_run(tmp_path, "MU", "2026-08-01", "Hold")
    _rated_run(tmp_path, "MU", "2026-08-10", "Unresolved")
    _rated_run(tmp_path, "MU", "2026-08-18", "Buy")
    summary = _summaries(tmp_path)["MU"]
    assert summary["decision"] == "Buy"
    assert summary["change"] == "up"
    assert summary["previous"]["analysis_date"] == "2026-08-01"


def test_ticker_summaries_exclude_smoke_and_incomplete_runs(tmp_path):
    _write(tmp_path / "codex_smoke" / "AAPL_2026-08-15" / "complete_report.md", "# Trading Analysis Report: AAPL")
    failed = _rated_run(tmp_path, "BRO", "2026-08-24", "Underweight")
    manifest = json.loads((failed / "run_manifest.json").read_text(encoding="utf-8"))
    manifest["status"] = "failed"
    _write(failed / "run_manifest.json", json.dumps(manifest))
    assert _summaries(tmp_path) == {}


def test_ticker_history_is_oldest_first_and_flags_evidence_only_runs(tmp_path):
    _rated_run(tmp_path, "FTNT", "2026-09-16", "Hold", completed="2026-09-16T10:00:00Z")
    _evidence_run(tmp_path, "FTNT", "2026-09-17", completed="2026-09-17T09:00:00Z")
    _rated_run(tmp_path, "FTNT", "2026-09-17", "Hold", completed="2026-09-17T10:00:00Z", stamp="refresh")
    history = _summaries(tmp_path)["FTNT"]["history"]
    assert [(entry["analysis_date"], entry["evidence_only"]) for entry in history] == [
        ("2026-09-16", False), ("2026-09-17", True), ("2026-09-17", False),
    ]
    assert [entry["rating"] for entry in history] == ["hold", "evidence", "hold"]
    assert all(entry["run_id"] for entry in history)


def test_same_day_evidence_packet_precedes_the_rated_run_when_untimed(tmp_path):
    _rated_run(tmp_path, "PANW", "2026-09-17", "Hold")
    _evidence_run(tmp_path, "PANW", "2026-09-17")
    history = _summaries(tmp_path)["PANW"]["history"]
    assert [entry["evidence_only"] for entry in history] == [True, False]


def test_executive_summary_is_extracted_from_the_final_decision(tmp_path):
    _rated_run(tmp_path, "PANW", "2026-09-17", "Hold", summary="Maintain the position.\nReassess   quarterly.")
    run = build_index([tmp_path]).public_payload()["runs"][0]
    assert run["executive_summary"] == "Maintain the position. Reassess quarterly."
    assert run["rating"] == "hold"


def test_long_executive_summary_is_truncated_with_an_ellipsis(tmp_path):
    _rated_run(tmp_path, "PANW", "2026-09-17", "Hold", summary="word " * 200)
    summary = build_index([tmp_path]).public_payload()["runs"][0]["executive_summary"]
    assert len(summary) == 400
    assert summary.endswith("…")


def test_executive_summary_is_none_without_one(tmp_path):
    _rated_run(tmp_path, "AMZN", "2026-09-01", "Overweight")
    assert build_index([tmp_path]).public_payload()["runs"][0]["executive_summary"] is None


@pytest.fixture
def dashboard_server(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(DashboardState([tmp_path])))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _get(port: int, path: str) -> tuple[int, str]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        response.read()
        return response.status, response.getheader("Content-Type", "")
    finally:
        connection.close()


def test_scripts_are_served_as_javascript_even_when_the_registry_says_text_plain(dashboard_server, monkeypatch):
    monkeypatch.setattr(mimetypes, "guess_type", lambda *_args, **_kwargs: ("text/plain", None))
    status, content_type = _get(dashboard_server, "/app.js")
    assert status == 200
    assert content_type.startswith("text/javascript")


def test_nested_static_modules_are_served(dashboard_server):
    status, content_type = _get(dashboard_server, "/views/positions.js")
    assert status == 200
    assert content_type.startswith("text/javascript")


def test_static_paths_cannot_escape_the_static_folder(dashboard_server):
    status, _ = _get(dashboard_server, "/../server.py")
    assert status == 404


def test_model_price_targets_are_read_from_the_final_decision(tmp_path):
    _rated_run(tmp_path, "PANW", "2026-09-23", "Hold", extra=(
        "**Price Target**: 264.5\n\n**Bear Case Target**: 210.0\n\n**Bull Case Target**: 310.0\n\n"
        "**Target Basis**: 22x forward FCF;\nconsensus mean is 270.\n\n**Time Horizon**: 12 months"
    ))
    run = build_index([tmp_path]).public_payload()["runs"][0]
    assert run["model_target"] == {
        "base": 264.5, "bear": 210.0, "bull": 310.0, "basis": "22x forward FCF; consensus mean is 270.",
    }


def test_single_legacy_price_target_is_read(tmp_path):
    _rated_run(tmp_path, "LUNR", "2026-08-24", "Underweight", extra="**Price Target**: $1,019.52")
    run = build_index([tmp_path]).public_payload()["runs"][0]
    assert run["model_target"] == {"base": 1019.52, "bear": None, "bull": None, "basis": None}


def test_runs_without_targets_have_no_model_target(tmp_path):
    _rated_run(tmp_path, "AMZN", "2026-09-01", "Overweight")
    assert build_index([tmp_path]).public_payload()["runs"][0]["model_target"] is None


def test_last_close_on_or_before_the_as_of_date_comes_from_the_evidence_packet(tmp_path):
    run = _rated_run(tmp_path, "AAPL", "2026-09-23", "Hold")

    def close(date, value, metric="close"):
        return {"metric": metric, "value": value, "unit": "USD/share", "period_end": date}

    _write(run / "packet.json", json.dumps({"ticker": "AAPL", "as_of": "2026-09-23", "facts": [
        close("2026-09-19", 330.0), close("2026-09-22", 339.75),
        close("2026-09-23", 1.0, metric="close_adjusted"), close("2026-09-24", 999.0),
    ]}))
    assert build_index([tmp_path]).public_payload()["runs"][0]["evidence_close"] == {
        "value": 339.75, "date": "2026-09-22",
    }
