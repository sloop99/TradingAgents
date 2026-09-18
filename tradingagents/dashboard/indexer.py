"""Discover and normalize TradingAgents report directories."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_RATING_RE = re.compile(r"\*\*Rating\*\*\s*:\s*([^\r\n*]+)", re.IGNORECASE)
_PROPOSAL_RE = re.compile(
    r"FINAL TRANSACTION PROPOSAL\s*:\s*\*\*([^*]+)\*\*", re.IGNORECASE
)
_DATE_RE = re.compile(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)")
_TICKER_DATE_RE = re.compile(r"(?:^|[\\/])([A-Z][A-Z0-9.^=-]{0,14})_(20\d{2}-\d{2}-\d{2})")


@dataclass
class ResearchRun:
    id: str
    ticker: str
    analysis_date: str
    completed_at: str | None
    status: str
    decision: str
    decision_interpretation: str | None
    perspective: str
    horizon: str | None
    thesis: str | None
    evidence_status: str
    valuation_status: str
    provider: str | None
    models: list[str]
    analysts: list[str]
    average_cost_usd: float | None
    average_cost_as_of: str | None
    limitations: list[str]
    sections: dict[str, Path]
    report_path: Path
    source_root: Path
    relative_path: str
    is_smoke: bool
    warnings: list[str] = field(default_factory=list)
    is_latest: bool = False

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ticker": self.ticker,
            "analysis_date": self.analysis_date,
            "completed_at": self.completed_at,
            "status": self.status,
            "decision": self.decision,
            "decision_interpretation": self.decision_interpretation,
            "perspective": self.perspective,
            "horizon": self.horizon,
            "thesis": self.thesis,
            "evidence_status": self.evidence_status,
            "valuation_status": self.valuation_status,
            "provider": self.provider,
            "models": self.models,
            "analysts": self.analysts,
            "average_cost_usd": self.average_cost_usd,
            "average_cost_as_of": self.average_cost_as_of,
            "limitations": self.limitations,
            "sections": list(self.sections),
            "relative_path": self.relative_path,
            "is_smoke": self.is_smoke,
            "warnings": self.warnings,
            "is_latest": self.is_latest,
        }


@dataclass
class ResearchIndex:
    roots: list[Path]
    runs: list[ResearchRun]
    generated_at: str
    warnings: list[str] = field(default_factory=list)

    def public_payload(self) -> dict[str, Any]:
        visible = [run for run in self.runs if not run.is_smoke]
        return {
            "generated_at": self.generated_at,
            "roots": [str(path) for path in self.roots],
            "summary": {
                "runs": len(visible),
                "tickers": len({run.ticker for run in visible}),
                "smoke_runs": sum(run.is_smoke for run in self.runs),
                "latest_date": max((run.analysis_date for run in visible), default=None),
            },
            "runs": [run.to_public_dict() for run in self.runs],
            "warnings": self.warnings,
        }

    def run(self, run_id: str) -> ResearchRun | None:
        return next((item for item in self.runs if item.id == run_id), None)


def default_scan_roots(start: Path | None = None) -> list[Path]:
    """Return the canonical repository's artifact directory.

    A development worktree under ``.tradingagents/worktrees`` should still see
    the canonical checkout's research archive.
    """
    location = (start or Path.cwd()).resolve()
    parts = location.parts
    if ".tradingagents" in parts:
        marker = parts.index(".tradingagents")
        canonical = Path(*parts[:marker])
    else:
        canonical = location
    candidate = canonical / ".tradingagents"
    return [candidate] if candidate.exists() else [canonical]


def build_index(roots: list[Path] | None = None) -> ResearchIndex:
    resolved = _normalize_roots(roots or default_scan_roots())
    runs: list[ResearchRun] = []
    warnings: list[str] = []
    seen: set[Path] = set()
    for root in resolved:
        try:
            reports = root.rglob("complete_report.md")
            for report in reports:
                report = report.resolve()
                if report in seen or not report.is_file():
                    continue
                seen.add(report)
                try:
                    runs.append(_parse_run(report, root))
                except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                    warnings.append(f"Skipped {report}: {type(exc).__name__}: {exc}")
        except OSError as exc:
            warnings.append(f"Could not scan {root}: {exc}")
    runs.sort(key=lambda item: (item.analysis_date, item.completed_at or "", item.ticker), reverse=True)
    _carry_forward_position_costs(runs)
    latest: dict[str, ResearchRun] = {}
    for run in runs:
        if run.is_smoke:
            continue
        latest.setdefault(run.ticker, run)
    for run in latest.values():
        run.is_latest = True
    return ResearchIndex(
        roots=resolved,
        runs=runs,
        generated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        warnings=warnings,
    )


def _normalize_roots(roots: list[Path]) -> list[Path]:
    result: list[Path] = []
    for root in roots:
        resolved = Path(root).expanduser().resolve()
        if resolved not in result:
            result.append(resolved)
    return result


def _carry_forward_position_costs(runs: list[ResearchRun]) -> None:
    """Attach the last recorded cost to later runs without calling it current."""
    known: dict[str, tuple[float, str]] = {}
    ordered = sorted(runs, key=lambda item: (item.analysis_date, item.completed_at or "", item.ticker))
    for run in ordered:
        if run.is_smoke:
            continue
        if run.average_cost_usd is not None:
            known[run.ticker] = (run.average_cost_usd, run.average_cost_as_of or run.analysis_date)
            continue
        prior = known.get(run.ticker)
        if prior is None:
            continue
        run.average_cost_usd, run.average_cost_as_of = prior
        run.warnings.append(f"Position cost carried from the {prior[1]} research record.")


def _parse_run(report: Path, source_root: Path) -> ResearchRun:
    report_dir = report.parent
    run_dir = report_dir.parent if report_dir.name in {"report", "reports"} else report_dir
    manifest, manifest_warning = _load_manifest(run_dir)
    packet = _load_optional_json(run_dir / "packet.json")
    decision_path = report_dir / "5_portfolio" / "decision.md"
    decision_text = _read_text(decision_path) if decision_path.exists() else ""
    complete_text = _read_text(report, limit=180_000)

    ticker, analysis_date = _ticker_and_date(manifest, packet, report, complete_text)
    decision = _decision(manifest, decision_text, complete_text)
    context = " ".join(
        str(value or "")
        for value in (
            manifest.get("decision_context"),
            manifest.get("requested_thesis"),
            manifest.get("packet_thesis"),
            packet.get("thesis"),
            decision_text[:500],
        )
    ).lower()
    perspective = _perspective(context, manifest)
    coverage = packet.get("coverage") if isinstance(packet.get("coverage"), dict) else {}
    financial = packet.get("financial_analysis") if isinstance(packet.get("financial_analysis"), dict) else {}
    capitalization = financial.get("capitalization") if isinstance(financial.get("capitalization"), dict) else {}
    agent_config = manifest.get("agent_configuration") if isinstance(manifest.get("agent_configuration"), dict) else {}
    models = _models(manifest, agent_config)
    analysts = manifest.get("analysts") or agent_config.get("analysts") or []
    if not isinstance(analysts, list):
        analysts = []
    thesis = _first_text(
        manifest.get("packet_thesis"), manifest.get("requested_thesis"), packet.get("thesis")
    )
    horizon = _first_text(
        manifest.get("packet_horizon"), manifest.get("requested_horizon"),
        manifest.get("holding_period"), packet.get("horizon"),
    )
    cost = manifest.get("average_cost_usd")
    if not isinstance(cost, (int, float)) or isinstance(cost, bool):
        cost = _cost_from_text(thesis or "")
    limitations = manifest.get("limitations") if isinstance(manifest.get("limitations"), list) else []
    sections = _sections(report_dir, run_dir)
    relative = _relative_display(report, source_root)
    lower_path = relative.lower()
    smoke = any(token in lower_path for token in ("smoke", "fixture", "test-run"))
    run_warnings = [manifest_warning] if manifest_warning else []
    if not manifest:
        run_warnings.append("Legacy run: metadata inferred from report and path.")
    status = str(manifest.get("status") or "completed").strip().lower()
    if status in {"complete", "success"}:
        status = "completed"
    evidence = str(manifest.get("evidence_status") or coverage.get("facts") or "legacy").lower()
    valuation = str(coverage.get("valuation") or capitalization.get("status") or "unknown").lower()
    run_id = hashlib.sha256(str(report).encode("utf-8")).hexdigest()[:16]
    return ResearchRun(
        id=run_id,
        ticker=ticker,
        analysis_date=analysis_date,
        completed_at=_first_text(
            manifest.get("completed_at"), manifest.get("completed_utc"), manifest.get("started_utc")
        ),
        status=status,
        decision=decision,
        decision_interpretation=_first_text(manifest.get("action_interpretation")),
        perspective=perspective,
        horizon=horizon,
        thesis=thesis,
        evidence_status=evidence,
        valuation_status=valuation,
        provider=_first_text(manifest.get("provider"), agent_config.get("provider")),
        models=models,
        analysts=[str(value) for value in analysts],
        average_cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
        average_cost_as_of=analysis_date if isinstance(cost, (int, float)) else None,
        limitations=[str(value) for value in limitations],
        sections=sections,
        report_path=report,
        source_root=source_root,
        relative_path=relative,
        is_smoke=smoke,
        warnings=run_warnings,
    )


def _load_manifest(run_dir: Path) -> tuple[dict[str, Any], str | None]:
    for name in ("manifest.json", "run_manifest.json"):
        path = run_dir / name
        if not path.exists():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value, None
            return {}, f"Ignored {name}: top-level value is not an object."
        except (OSError, json.JSONDecodeError) as exc:
            return {}, f"Ignored {name}: {type(exc).__name__}: {exc}"
    return {}, None


def _load_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_text(path: Path, limit: int = 80_000) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[:limit]


def _ticker_and_date(
    manifest: dict[str, Any], packet: dict[str, Any], report: Path, complete_text: str
) -> tuple[str, str]:
    ticker = _first_text(manifest.get("ticker"), packet.get("ticker"))
    analysis_date = _first_text(manifest.get("analysis_date"), manifest.get("as_of"), packet.get("as_of"))
    match = _TICKER_DATE_RE.search(str(report))
    if ticker is None and match:
        ticker = match.group(1)
    if analysis_date is None and match:
        analysis_date = match.group(2)
    if ticker is None:
        heading = re.search(r"Trading Analysis Report:\s*([A-Z0-9.^=-]+)", complete_text)
        ticker = heading.group(1) if heading else "UNKNOWN"
    if analysis_date is None:
        date_match = _DATE_RE.search(str(report)) or _DATE_RE.search(complete_text[:500])
        analysis_date = date_match.group(1) if date_match else "unknown"
    return ticker.strip().upper(), analysis_date[:10]


def _decision(manifest: dict[str, Any], decision_text: str, complete_text: str) -> str:
    direct = _first_text(manifest.get("final_rating"), manifest.get("decision"))
    if direct:
        return direct.title()
    for text in (decision_text, complete_text):
        match = _RATING_RE.search(text) or _PROPOSAL_RE.search(text)
        if match:
            return match.group(1).strip().title()
    return "Unresolved"


def _perspective(context: str, manifest: dict[str, Any]) -> str:
    if "existing" in context or "already own" in context or manifest.get("average_cost_usd") is not None:
        return "Existing holder"
    if "prospective" in context or "new buyer" in context or "avoid initiating" in context:
        return "Prospective buyer"
    return "General research"


def _models(manifest: dict[str, Any], agent_config: dict[str, Any]) -> list[str]:
    values = [
        manifest.get("model"), manifest.get("deep_think_llm"), manifest.get("quick_think_llm"),
        agent_config.get("deep_model"), agent_config.get("quick_model"),
    ]
    result: list[str] = []
    for value in values:
        if isinstance(value, str) and value.strip() and value not in result:
            result.append(value.strip())
    return result


def _cost_from_text(text: str) -> float | None:
    match = re.search(r"(?:average (?:broker )?cost|cost per share)[^$\d]{0,25}\$?([\d,]+(?:\.\d+)?)", text, re.I)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _sections(report_dir: Path, run_dir: Path) -> dict[str, Path]:
    candidates = {
        "Complete report": report_dir / "complete_report.md",
        "Final decision": report_dir / "5_portfolio" / "decision.md",
        "Evidence coverage": report_dir / "0_evidence" / "coverage.md",
        "Fundamentals": report_dir / "1_analysts" / "fundamentals.md",
        "News": report_dir / "1_analysts" / "news.md",
        "Market": report_dir / "1_analysts" / "market.md",
        "Sentiment": report_dir / "1_analysts" / "sentiment.md",
        "Bull case": report_dir / "2_research" / "bull.md",
        "Bear case": report_dir / "2_research" / "bear.md",
        "Research manager": report_dir / "2_research" / "manager.md",
        "Trader": report_dir / "3_trading" / "trader.md",
        "Aggressive risk": report_dir / "4_risk" / "aggressive.md",
        "Conservative risk": report_dir / "4_risk" / "conservative.md",
        "Neutral risk": report_dir / "4_risk" / "neutral.md",
        "Research brief": run_dir / "research-brief.md",
    }
    return {name: path.resolve() for name, path in candidates.items() if path.is_file() and path.stat().st_size}


def _relative_display(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
