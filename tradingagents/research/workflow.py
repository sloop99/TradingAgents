"""Prepare sourced equity research, with an opt-in TradingAgents run.

This is deliberately a separate entry point from :mod:`tradingagents.research`.
Preparing evidence does not create an LLM client or make an agent call.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.research import ResearchPacket, build_packet, parse_as_of
from tradingagents.research.brief import create_research_brief
from tradingagents.research.integration import load_packet


class FixtureProvider:
    """Replay one supplied provider payload for an offline evidence build."""

    def __init__(self, path: Path):
        self.path = path

    def fetch(self, ticker: str, as_of: str) -> dict[str, Any]:
        del ticker, as_of
        return json.loads(self.path.read_text(encoding="utf-8"))


class MemoizedProvider:
    """Share a provider response between the core and filing adapters."""

    def __init__(self, provider: Any):
        self.provider = provider
        self.name = getattr(provider, "name", provider.__class__.__name__)
        self.responses: dict[tuple[str, str], dict[str, Any]] = {}

    def fetch(self, ticker: str, as_of: str) -> dict[str, Any]:
        key = (ticker, as_of)
        if key not in self.responses:
            self.responses[key] = self.provider.fetch(ticker, as_of)
        return self.responses[key]


def _ticker(value: str) -> str:
    value = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.^=-]{0,29}", value):
        raise argparse.ArgumentTypeError("Ticker must be a short symbol, not a path or company name")
    return value


def _as_of(value: str) -> str:
    value = value.strip()
    try:
        parse_as_of(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return value


def _analysts(value: str) -> tuple[str, ...]:
    selected = tuple(item.strip() for item in value.split(",") if item.strip())
    allowed = {"market", "social", "news", "fundamentals"}
    unknown = set(selected) - allowed
    if not selected or unknown:
        choices = ", ".join(sorted(allowed))
        raise argparse.ArgumentTypeError(f"--analysts must use one or more of: {choices}")
    return selected


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _new_run_dir(root: Path, ticker: str, as_of: str) -> Path:
    # Microseconds make normal repeated runs independent.  Still refuse a rare
    # collision rather than mixing two research records.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = root / ticker / as_of[:10] / stamp
    if path.exists() and any(path.iterdir()):
        raise FileExistsError(f"Refusing to overwrite non-empty run directory: {path}")
    path.mkdir(parents=True, exist_ok=False)
    return path


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def _issue_codes(packet: ResearchPacket) -> set[str]:
    return {issue.code for issue in packet.issues}


def _packet_limitations(packet: ResearchPacket) -> list[str]:
    limits = []
    if packet.identity is None:
        limits.append("Issuer identity is unavailable.")
    if packet.status.value != "sufficient":
        limits.append(f"Evidence status is {packet.status.value}; this is not an investment rating.")
    codes = sorted(_issue_codes(packet))
    if codes:
        limits.append("Packet issues: " + ", ".join(codes) + ".")
    return limits


def _validate_report_tree(reports_path: Path, analysts: tuple[str, ...]) -> None:
    """Require every selected and downstream graph section before completion."""
    selected_reports = {
        "market": Path("1_analysts/market.md"),
        "social": Path("1_analysts/sentiment.md"),
        "news": Path("1_analysts/news.md"),
        "fundamentals": Path("1_analysts/fundamentals.md"),
    }
    required = [
        Path("0_evidence/packet.json"),
        Path("0_evidence/coverage.md"),
        Path("2_research/bull.md"),
        Path("2_research/bear.md"),
        Path("2_research/manager.md"),
        Path("3_trading/trader.md"),
        Path("4_risk/aggressive.md"),
        Path("4_risk/conservative.md"),
        Path("4_risk/neutral.md"),
        Path("5_portfolio/decision.md"),
        Path("complete_report.md"),
    ]
    required.extend(selected_reports[key] for key in analysts)
    missing = [str(path) for path in required
               if not (reports_path / path).is_file()
               or (reports_path / path).stat().st_size == 0]
    if missing:
        raise ValueError(
            "Graph report is incomplete; missing or empty required sections: "
            + ", ".join(missing)
        )


def _live_providers(args: argparse.Namespace):
    from .cache import EvidenceCache
    from .providers.market import YahooMarketProvider
    from .providers.sec import SecEdgarProvider

    cache = EvidenceCache(args.cache_dir)
    sec = MemoizedProvider(SecEdgarProvider(user_agent=args.sec_user_agent, cache=cache))
    providers = [sec]
    if args.filings:
        from .providers.filing import SecFilingProvider

        providers.append(MemoizedProvider(SecFilingProvider(
            user_agent=args.sec_user_agent,
            cache=cache,
            sec_provider=sec,
            max_filings=args.max_filings,
        )))
    if not args.no_market or args.analyst_targets:
        import yfinance as yf

        yf.set_tz_cache_location(str((args.cache_dir / "yfinance").resolve()))
    if not args.no_market:
        providers.append(YahooMarketProvider(cache=cache))
    if args.analyst_targets:
        from .providers.analyst import YahooAnalystTargetsProvider

        providers.append(YahooAnalystTargetsProvider(cache=cache))
    return providers


def _prepare_packet(args: argparse.Namespace, run_dir: Path) -> tuple[ResearchPacket, Path, str]:
    if args.packet:
        packet_source = args.packet.resolve()
        packet, digest = load_packet(packet_source, args.ticker, args.as_of)
        # Do not rewrite an existing packet merely to fit a new command line.
        # Its thesis/horizon and exact content hash remain the reviewable inputs.
        (run_dir / "packet.json").write_bytes(packet_source.read_bytes())
    else:
        providers = [FixtureProvider(path) for path in args.fixture] if args.fixture else _live_providers(args)
        packet = build_packet(args.ticker, args.as_of, providers, args.horizon, args.thesis)
        packet_path = run_dir / "packet.json"
        _write_json(packet_path, packet.to_dict())
        digest = _sha256(packet_path)
    packet_path = run_dir / "packet.json"
    (run_dir / "coverage.md").write_text(packet.to_markdown(), encoding="utf-8")
    return packet, packet_path, digest


def run_workflow(
    args: argparse.Namespace,
    graph_factory: Callable[..., Any] | None = None,
) -> tuple[int, Path]:
    """Run a parsed workflow request.  ``graph_factory`` keeps tests offline."""
    if args.fixture and args.run_agents:
        raise ValueError("--fixture is offline evidence replay and cannot be combined with --run-agents")
    run_dir = _new_run_dir(args.output_dir, args.ticker, args.as_of)
    manifest: dict[str, Any] = {
        "status": "prepared",
        "ticker": args.ticker,
        "as_of": args.as_of,
        "requested_thesis": args.thesis,
        "requested_horizon": args.horizon,
        "packet_source": str(args.packet.resolve()) if args.packet else "built",
        "agent_run_requested": args.run_agents,
        "fixture_replay": bool(args.fixture),
        "costs": "not quantified",
    }
    _write_json(run_dir / "manifest.json", manifest)
    try:
        packet, packet_path, digest = _prepare_packet(args, run_dir)
        manifest.update({
            "packet_path": str(packet_path.resolve()), "packet_sha256": digest,
            "packet_thesis": packet.thesis, "packet_horizon": packet.horizon,
            "evidence_status": packet.status.value, "limitations": _packet_limitations(packet),
        })
        brief_path = run_dir / "research-brief.md"
        brief_path.write_text(create_research_brief(packet), encoding="utf-8")
        manifest["research_brief_path"] = str(brief_path.resolve())
        target_input_path = None
        if getattr(args, "analyst_records", None):
            from .analyst_consensus import render_consensus_markdown
            from .expectations import load_target_import

            consensus, _input, target_digest, raw = load_target_import(
                args.analyst_records, args.ticker, args.as_of,
            )
            target_input_path = run_dir / "analyst-target-input.json"
            target_input_path.write_bytes(raw)
            _write_json(run_dir / "analyst-consensus.json", consensus)
            markdown = render_consensus_markdown(consensus)
            (run_dir / "analyst-expectations.md").write_text(markdown, encoding="utf-8")
            with brief_path.open("a", encoding="utf-8") as brief:
                brief.write("\n\n" + markdown)
            manifest["analyst_target_input_sha256"] = target_digest
            manifest["analyst_target_input_path"] = str(target_input_path.resolve())
            manifest["analyst_consensus_path"] = str((run_dir / "analyst-consensus.json").resolve())
        if args.run_agents:
            blockers = []
            if packet.identity is None or not packet.identity.name:
                blockers.append("missing issuer identity")
            dangerous = {"IDENTITY_CONFLICT", "FACT_ID_COLLISION"} & _issue_codes(packet)
            if dangerous:
                blockers.append("identity/fact collision: " + ", ".join(sorted(dangerous)))
            if blockers:
                raise ValueError("Cannot run agents with this packet: " + "; ".join(blockers))
            manifest["status"] = "running"
            _write_json(run_dir / "manifest.json", manifest)
            config = DEFAULT_CONFIG.copy()
            config["research_packet_path"] = str(packet_path.resolve())
            if target_input_path is not None:
                config["analyst_target_input_path"] = str(target_input_path.resolve())
            if args.llm_provider:
                config["llm_provider"] = args.llm_provider
            if args.quick_model:
                config["quick_think_llm"] = args.quick_model
            if args.deep_model:
                config["deep_think_llm"] = args.deep_model
            manifest["agent_configuration"] = {
                "provider": config["llm_provider"],
                "quick_model": config["quick_think_llm"],
                "deep_model": config["deep_think_llm"],
                "analysts": list(args.analysts or ("market", "social", "news", "fundamentals")),
            }
            _write_json(run_dir / "manifest.json", manifest)
            if graph_factory is None:
                from tradingagents.graph.trading_graph import TradingAgentsGraph

                graph_factory = TradingAgentsGraph
            graph_kwargs: dict[str, Any] = {"config": config}
            if args.analysts:
                graph_kwargs["selected_analysts"] = tuple(args.analysts)
            graph = graph_factory(**graph_kwargs)
            state, _decision = graph.propagate(args.ticker, args.as_of)
            reports = graph.save_reports(state, args.ticker, save_path=run_dir / "reports")
            reports = Path(reports)
            if not reports.is_file() or reports.stat().st_size == 0:
                raise ValueError("Graph did not create a non-empty complete_report.md")
            _validate_report_tree(
                reports.parent,
                tuple(args.analysts or ("market", "social", "news", "fundamentals")),
            )
            manifest["reports_path"] = str(reports.resolve())
        manifest["status"] = "completed"
        _write_json(run_dir / "manifest.json", manifest)
        return 0, run_dir
    except Exception as exc:
        manifest.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        _write_json(run_dir / "manifest.json", manifest)
        raise


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("ticker", type=_ticker)
    result.add_argument("--as-of", type=_as_of, default=datetime.now(timezone.utc).date().isoformat())
    result.add_argument("--horizon", default="long_term")
    result.add_argument("--thesis")
    result.add_argument("--analyst-targets", action=argparse.BooleanOptionalAction, default=True,
                        help="Collect a current Yahoo aggregate analyst-target snapshot (default: on)")
    result.add_argument("--analyst-records", type=Path,
                        help="Import sourced firm-level target JSON; compute averages without a model call")
    source = result.add_mutually_exclusive_group()
    source.add_argument("--packet", type=Path, help="Existing packet; exact bytes are retained in the new run")
    source.add_argument("--fixture", action="append", type=Path, default=[], help="Offline provider JSON; repeatable")
    result.add_argument("--output-dir", type=Path, default=Path(".tradingagents/research-runs"))
    result.add_argument("--cache-dir", type=Path, default=Path(".tradingagents/evidence-cache"))
    result.add_argument("--sec-user-agent", default=os.getenv("SEC_USER_AGENT", ""))
    result.add_argument("--no-market", action="store_true")
    result.add_argument("--filings", action=argparse.BooleanOptionalAction, default=True,
                        help="Extract capital-structure candidates from recent SEC filings (default: on)")
    result.add_argument("--max-filings", type=int, choices=(1, 2, 3), default=2)
    result.add_argument("--run-agents", action="store_true", help="Opt in to normal graph/provider and LLM costs")
    result.add_argument("--analysts", type=_analysts,
                        help="Comma-separated graph analysts: market,social,news,fundamentals")
    result.add_argument("--llm-provider")
    result.add_argument("--quick-model")
    result.add_argument("--deep-model")
    return result


def main(argv: list[str] | None = None) -> int:
    parsed = parser().parse_args(argv)
    try:
        _code, run_dir = run_workflow(parsed)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"Workflow failed: {exc}", file=sys.stderr)
        return 2
    print(f"Research run: {run_dir.resolve()}")
    print(f"Manifest: {(run_dir / 'manifest.json').resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
