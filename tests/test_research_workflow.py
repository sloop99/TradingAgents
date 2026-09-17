import json
from pathlib import Path

import pytest

from tradingagents.research.workflow import main, parser, run_workflow


def _identity_fixture(ticker="TEST"):
    return {
        "identity": {
            "ticker": ticker,
            "name": "Test Corporation",
            "cik": "0000000001",
            "exchange": "NYSE",
            "currency": "USD",
            "fiscal_year_end": "1231",
            "sic": "3570",
            "business_model": None,
        },
        "facts": [],
        "issues": [],
        "coverage": {},
    }


def test_fixture_evidence_run_is_completed_without_constructing_a_graph(tmp_path):
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(_identity_fixture()), encoding="utf-8")
    args = parser().parse_args([
        "TEST", "--as-of", "2026-01-01", "--fixture", str(fixture),
        "--output-dir", str(tmp_path / "runs"), "--thesis", "Check demand", "--horizon", "six_months",
    ])
    code, run_dir = run_workflow(args, graph_factory=lambda **_: pytest.fail("graph must not be made"))
    assert code == 0
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert manifest["costs"] == "not quantified"
    assert manifest["fixture_replay"] is True
    assert manifest["packet_thesis"] == "Check demand"
    assert (run_dir / "packet.json").exists()
    assert (run_dir / "coverage.md").exists()


def test_existing_packet_is_copied_exactly_and_command_intent_is_not_substituted(tmp_path):
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(_identity_fixture()), encoding="utf-8")
    evidence_args = parser().parse_args([
        "TEST", "--as-of", "2026-01-01", "--fixture", str(fixture),
        "--output-dir", str(tmp_path / "evidence"), "--thesis", "Original", "--horizon", "one_year",
    ])
    _, evidence_dir = run_workflow(evidence_args)
    source = evidence_dir / "packet.json"
    reuse_args = parser().parse_args([
        "TEST", "--as-of", "2026-01-01", "--packet", str(source),
        "--output-dir", str(tmp_path / "reuse"), "--thesis", "Replacement", "--horizon", "one_day",
    ])
    _, run_dir = run_workflow(reuse_args)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert (run_dir / "packet.json").read_bytes() == source.read_bytes()
    assert manifest["packet_thesis"] == "Original"
    assert manifest["requested_thesis"] == "Replacement"


def test_agent_run_passes_packet_and_selected_analysts_to_graph(tmp_path):
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(_identity_fixture()), encoding="utf-8")
    captured = {}

    class FakeGraph:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def propagate(self, ticker, as_of):
            captured["propagate"] = (ticker, as_of)
            return {"final_trade_decision": "synthetic"}, "synthetic"

        def save_reports(self, state, ticker, save_path=None):
            del state, ticker
            report = Path(save_path) / "report.md"
            report.parent.mkdir(parents=True)
            report.write_text("report", encoding="utf-8")
            return report

    args = parser().parse_args([
        "TEST", "--as-of", "2026-01-01", "--fixture", str(fixture), "--run-agents",
        "--output-dir", str(tmp_path / "runs"), "--analysts", "fundamentals,news",
        "--llm-provider", "test", "--quick-model", "quick", "--deep-model", "deep",
    ])
    with pytest.raises(ValueError, match="cannot be combined"):
        run_workflow(args, graph_factory=FakeGraph)

    args.fixture = []
    # Reuse the established packet so no live provider is needed for this graph test.
    # A valid packet needs the builder's normalized fields; make it in offline mode first.
    prep = parser().parse_args([
        "TEST", "--as-of", "2026-01-01", "--fixture", str(fixture), "--output-dir", str(tmp_path / "prep"),
    ])
    _, prepared_dir = run_workflow(prep)
    args.packet = prepared_dir / "packet.json"
    code, run_dir = run_workflow(args, graph_factory=FakeGraph)
    assert code == 0
    assert captured["selected_analysts"] == ("fundamentals", "news")
    assert captured["config"]["research_packet_path"] == str((run_dir / "packet.json").resolve())
    assert (run_dir / "reports" / "report.md").read_text(encoding="utf-8") == "report"


def test_cli_returns_error_for_fixture_agent_combination(tmp_path, capsys):
    fixture = tmp_path / "fixture.json"
    fixture.write_text("{}", encoding="utf-8")
    assert main(["TEST", "--fixture", str(fixture), "--run-agents", "--output-dir", str(tmp_path)]) == 2
    assert "cannot be combined" in capsys.readouterr().err


def test_parser_rejects_invalid_cutoff_and_conflicting_packet_sources(tmp_path):
    fixture = tmp_path / "fixture.json"
    packet = tmp_path / "packet.json"
    with pytest.raises(SystemExit):
        parser().parse_args(["TEST", "--as-of", "../../outside"])
    with pytest.raises(SystemExit):
        parser().parse_args(["TEST", "--packet", str(packet), "--fixture", str(fixture)])


def test_parser_exposes_live_filing_and_analyst_target_switches():
    args = parser().parse_args([
        "FTNT", "--as-of", "2026-09-16", "--filings", "--max-filings", "2", "--analyst-targets",
    ])
    assert args.filings is True
    assert args.max_filings == 2
    assert args.analyst_targets is True
