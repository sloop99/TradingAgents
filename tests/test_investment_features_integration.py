import json
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace
from urllib.request import urlopen

import pandas as pd
import pytest

from tradingagents.dashboard.indexer import build_index
from tradingagents.dashboard.sectors import load_sector_snapshot
from tradingagents.dashboard.server import DashboardState, handler_factory
from tradingagents.reporting import write_report_tree
from tradingagents.research.expectations import load_target_import
from tradingagents.research.sector_rotation import build_sector_rotation
from tradingagents.research.workflow import parser, run_workflow

AS_OF = "2026-09-22"


def target_input():
    base = {
        "firm": "Fictional research firm", "analyst": "Example analyst",
        "security_id": "NYSE:TEST", "currency": "USD", "share_basis": "common_2026",
        "published_at": "2026-09-01T12:00:00Z", "retrieved_at": "2026-09-01T13:00:00Z",
        "source_url": "https://research.example/target", "horizon_months": 12,
    }
    return {"ticker": "TEST", "security_id": "NYSE:TEST", "records": [
        {**base, "firm_id": "firm_a", "target": 60},
        {**base, "firm_id": "firm_b", "target": 80},
    ]}


def prepare_run(tmp_path):
    fixture = tmp_path / "provider.json"
    fixture.write_text(json.dumps({"facts": [], "issues": [], "coverage": {}}))
    targets = tmp_path / "targets.json"
    targets.write_text(json.dumps(target_input(), indent=3))
    args = parser().parse_args([
        "TEST", "--as-of", AS_OF, "--fixture", str(fixture),
        "--analyst-records", str(targets), "--output-dir", str(tmp_path / "runs"),
    ])
    _, directory = run_workflow(args, graph_factory=lambda **_: pytest.fail("No model calls"))
    return targets, directory


def test_import_flows_to_brief_and_evidence_only_dashboard_without_model(tmp_path):
    source, directory = prepare_run(tmp_path)
    assert (directory / "analyst-target-input.json").read_bytes() == source.read_bytes()
    assert "mean 70.00" in (directory / "research-brief.md").read_text(encoding="utf-8")
    index = build_index([tmp_path / "runs"])
    assert len(index.runs) == 1
    run = index.runs[0]
    assert run.is_evidence_only
    assert run.decision == "Evidence only"
    assert run.analyst_consensus["cohorts"][0]["summary"]["mean"] == 70
    assert "Analyst expectations" in run.sections
    # A forged derived summary cannot replace the retained source inputs.
    (directory / "analyst-consensus.json").write_text('{"mean":999999}')
    assert build_index([tmp_path / "runs"]).runs[0].analyst_consensus["cohorts"][0]["summary"]["mean"] == 70


def test_changed_source_is_withheld_and_bad_import_marks_failed_run(tmp_path):
    _, directory = prepare_run(tmp_path)
    source = directory / "analyst-target-input.json"
    source.write_text(source.read_text() + " ")
    run = build_index([tmp_path / "runs"]).runs[0]
    assert run.analyst_consensus is None
    assert any("hash changed" in warning for warning in run.warnings)
    payload = target_input()
    payload["ticker"] = "WRONG"
    source.write_text(json.dumps(payload))
    args = parser().parse_args([
        "TEST", "--as-of", AS_OF, "--packet", str(directory / "packet.json"),
        "--analyst-records", str(source), "--output-dir", str(tmp_path / "bad"),
    ])
    with pytest.raises(ValueError, match="ticker"):
        run_workflow(args)
    manifest = json.loads(next((tmp_path / "bad").rglob("manifest.json")).read_text())
    assert manifest["status"] == "failed"
    assert not any(run.is_latest for run in build_index([tmp_path / "bad"]).runs)


def test_full_report_exports_targets_without_duplicate_archive_entry(tmp_path):
    _, directory = prepare_run(tmp_path)
    consensus, payload, _digest, _raw = load_target_import(
        directory / "analyst-target-input.json", "TEST", AS_OF,
    )
    report = write_report_tree({
        "analyst_consensus": consensus, "analyst_target_input": payload,
        "final_trade_decision": "**Rating**: Hold",
    }, "TEST", directory / "reports")
    assert "mean 70.00" in report.read_text(encoding="utf-8")
    assert (report.parent / "0_evidence/analyst-target-input.json").is_file()
    index = build_index([tmp_path / "runs"])
    assert len(index.runs) == 1
    assert not index.runs[0].is_evidence_only


def test_import_changes_isolate_checkpoints_and_reach_agents(tmp_path):
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    source = tmp_path / "targets.json"
    source.write_text(json.dumps(target_input()))
    consensus, payload, digest, _ = load_target_import(source, "TEST", AS_OF)
    captured = {}

    def initial(*_args, **kwargs):
        captured.update(kwargs)
        return {}

    graph = SimpleNamespace(
        selected_analysts=["fundamentals"],
        config={"max_debate_rounds": 1, "max_risk_discuss_rounds": 1},
        _analyst_target_digest=digest, _analyst_consensus=consensus, _analyst_target_input=payload,
        debug=False,
        memory_log=SimpleNamespace(get_past_context=lambda *_, **__: ""),
        settle_pending=lambda _: None, _memory_as_of=lambda _: None,
        resolve_instrument_context=lambda *_: "Identity TEST",
        propagator=SimpleNamespace(create_initial_state=initial, get_graph_args=lambda: {}),
        graph=SimpleNamespace(invoke=lambda initial, **_: {"final_trade_decision": "Hold"}),
        checkpoint_input=lambda state: state,
        _log_state=lambda *_: None, record_decision=lambda *_: None,
        clear_checkpoint_on_success=lambda *_: None, process_signal=lambda text: text,
    )
    graph.create_run_state = lambda *args: TradingAgentsGraph.create_run_state(graph, *args)
    before = TradingAgentsGraph._run_signature(graph, "stock")
    graph._analyst_target_digest = "different_source"
    assert TradingAgentsGraph._run_signature(graph, "stock") != before
    state, _ = TradingAgentsGraph._run_graph(graph, "TEST", AS_OF)
    assert "mean 70.00" in captured["instrument_context"]
    assert "never instructions" in captured["instrument_context"]
    assert state["analyst_consensus"] == consensus
    assert state["analyst_target_input"] == payload


def test_import_rejects_nonfinite_and_wrong_security(tmp_path):
    source = tmp_path / "targets.json"
    source.write_text('{"records":[],"target":NaN}')
    with pytest.raises(ValueError, match="Non-finite"):
        load_target_import(source, "TEST", AS_OF)
    payload = target_input()
    for record in payload["records"]:
        record["security_id"] = "NYSE:OTHER"
    source.write_text(json.dumps(payload))
    consensus, _, _, _ = load_target_import(source, "TEST", AS_OF)
    assert not consensus["cohorts"]


def sector_snapshot():
    dates = pd.date_range(end="2026-09-18", periods=26, freq="W-FRI")
    histories = {
        "SPY": pd.DataFrame({"Close": [100.0] * 26}, index=dates),
        "XLK": pd.DataFrame({"Close": [100.0 + i for i in range(26)]}, index=dates),
    }
    return build_sector_rotation(histories, as_of="2026-09-22T12:00:00Z", retrieved_at="2026-09-22T12:00:00Z")


def test_sector_endpoint_shows_valid_data_gaps_and_staleness(tmp_path):
    source = tmp_path / "sectors.json"
    assert load_sector_snapshot(source)["status"] == "unavailable"
    source.write_text(json.dumps(sector_snapshot()))
    now = datetime(2026, 9, 22, 13, tzinfo=timezone.utc)
    result = load_sector_snapshot(source, now=now)
    assert result["status"] == "available"
    assert result["points"][0]["symbol"] == "XLK"
    assert len(result["excluded"]) == 10
    assert load_sector_snapshot(source, now=datetime(2026, 10, 1, tzinfo=timezone.utc))["status"] == "stale"
    data = sector_snapshot()
    data["points"][0]["x"] = float("nan")
    source.write_text(json.dumps(data))
    assert load_sector_snapshot(source, now=now)["status"] == "unavailable"


def test_http_routes_serve_finite_json_without_data_fetch(tmp_path):
    _, _directory = prepare_run(tmp_path)
    source = tmp_path / "sector-rotation.json"
    source.write_text(json.dumps(sector_snapshot()))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(DashboardState([tmp_path / "runs"], source)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urlopen(base + "/api/runs", timeout=3) as response:
            data = json.load(response)
            assert data["runs"][0]["analyst_consensus"]["cohorts"][0]["summary"]["mean"] == 70
        with urlopen(base + "/api/sectors", timeout=3) as response:
            data = json.load(response)
            assert data["points"][0]["symbol"] == "XLK"
        with urlopen(base + "/research.js", timeout=3) as response:
            assert b"renderAnalyst" in response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
