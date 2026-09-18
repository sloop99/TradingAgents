import json
from pathlib import Path

from tradingagents.dashboard.indexer import build_index, default_scan_roots


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


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
