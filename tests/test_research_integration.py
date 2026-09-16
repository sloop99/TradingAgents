import json
from types import SimpleNamespace

import pytest

from tradingagents.reporting import write_report_tree
from tradingagents.research import build_packet
from tradingagents.research.__main__ import main
from tradingagents.research.integration import load_packet


def test_offline_cli_and_report_preserve_unsupported_evidence(tmp_path, capsys):
    fixture = tmp_path / 'fixture.json'
    fixture.write_text(json.dumps({'facts': [], 'issues': [], 'coverage': {}}))
    output = tmp_path / 'output'
    code = main(['TEST', '--as-of', '2026-01-01', '--fixture', str(fixture),
                 '--output-dir', str(output), '--require-sufficient'])
    assert code == 2
    assert 'unsupported (not an investment rating)' in capsys.readouterr().out
    packet, digest = load_packet(output / 'packet.json', 'test', '2026-01-01')
    assert packet.status.value == 'unsupported'
    assert len(digest) == 64
    report = write_report_tree({'research_packet': packet.to_dict()}, 'TEST', tmp_path / 'report')
    assert '**unsupported**' in report.read_text(encoding='utf-8')
    assert json.loads((report.parent / '0_evidence/packet.json').read_text()) == packet.to_dict()
    assert (report.parent / '0_evidence/coverage.md').exists()


def test_packet_identity_cutoff_and_checkpoint_isolation(tmp_path):
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    path = tmp_path / 'packet.json'
    data = build_packet('TEST', '2026-01-01', []).to_dict()
    path.write_text(json.dumps(data))
    _, digest = load_packet(path, 'TEST', '2026-01-01')
    with pytest.raises(ValueError, match='ticker'):
        load_packet(path, 'OTHER', '2026-01-01')
    with pytest.raises(ValueError, match='cutoff'):
        load_packet(path, 'TEST', '2026-01-02')
    with pytest.raises(ValueError):
        load_packet(path, 'TEST', '2026-01-01T12:00:00')
    graph = SimpleNamespace(selected_analysts=['market'], config={
        'max_debate_rounds': 1, 'max_risk_discuss_rounds': 1})
    baseline = TradingAgentsGraph._run_signature(graph, 'stock')
    assert baseline == 'analysts=market|debate=1|risk=1|asset=stock'
    graph._research_packet_digest = digest
    first = TradingAgentsGraph._run_signature(graph, 'stock')
    data['thesis'] = 'A different unverified thesis'
    path.write_text(json.dumps(data))
    _, graph._research_packet_digest = load_packet(path, 'TEST', '2026-01-01')
    assert TradingAgentsGraph._run_signature(graph, 'stock') != first


def test_cli_rejects_path_tickers_before_fetch():
    with pytest.raises(SystemExit) as error:
        main(['../TEST'])
    assert error.value.code == 2


def test_cli_places_yahoo_sqlite_cache_in_selected_directory(tmp_path, monkeypatch):
    import yfinance as yf

    from tradingagents.research.providers.market import YahooMarketProvider
    from tradingagents.research.providers.sec import SecEdgarProvider

    configured = []
    monkeypatch.setattr(yf, 'set_tz_cache_location', configured.append)
    monkeypatch.setattr(SecEdgarProvider, 'fetch', lambda *args: {})
    monkeypatch.setattr(YahooMarketProvider, 'fetch', lambda *args: {})
    cache = tmp_path / 'cache'
    assert main(['TEST', '--as-of', '2026-09-16', '--cache-dir', str(cache),
                 '--output-dir', str(tmp_path / 'out')]) == 0
    assert configured == [str((cache / 'yfinance').resolve())]


@pytest.mark.parametrize('debug', [False, True])
def test_graph_shares_packet_and_preserves_it_after_node_deltas(debug):
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    packet = build_packet('TEST', '2026-01-01', [])
    captured = {}

    def initial(company, date, **kwargs):
        captured.update(kwargs)
        return {'company_of_interest': company, 'trade_date': date}

    def invoke(state, **kwargs):
        assert state['research_packet'] == packet.to_dict()
        assert state['evidence_status'] == 'unsupported'
        return {'final_trade_decision': 'A synthetic decision'}

    def stream(state, **kwargs):
        yield {**invoke(state), 'messages': []}

    graph = SimpleNamespace(
        _research_packet=packet, debug=debug, config={},
        memory_log=SimpleNamespace(get_past_context=lambda _: '', store_decision=lambda **_: None),
        resolve_instrument_context=lambda *_: 'Identity context',
        propagator=SimpleNamespace(create_initial_state=initial, get_graph_args=lambda: {}),
        graph=SimpleNamespace(invoke=invoke, stream=stream),
        _log_state=lambda *_: None, process_signal=lambda text: text,
    )
    state, _ = TradingAgentsGraph._run_graph(graph, 'TEST', '2026-01-01')
    assert 'Evidence packet: TEST' in captured['instrument_context']
    assert state['research_packet'] == packet.to_dict()
    assert state['evidence_status'] == 'unsupported'
