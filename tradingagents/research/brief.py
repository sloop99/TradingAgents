"""A compact, non-LLM starting brief for an investment idea."""

from .models import ResearchPacket


def create_research_brief(packet: ResearchPacket) -> str:
    """Summarize evidence and open questions without inventing a recommendation."""
    analysis = packet.financial_analysis
    cap = analysis.get("capitalization", {})
    lines = [f"# Research brief: {packet.ticker}", "",
             f"Evidence cutoff: {packet.as_of}. Horizon: {packet.horizon}.", "",
             "This preparation brief uses stored evidence and deterministic calculations. It is not an AI investment recommendation.", "",
             "## Idea", "", packet.thesis or "No thesis supplied. Define the business change and why it may create shareholder value.", "",
             "## Company and coverage", "",
             f"- Company: {packet.identity.name if packet.identity and packet.identity.name else 'unresolved'}",
             f"- Business model: {packet.business_model.value}",
             f"- Evidence status: {packet.status.value}; valuation status: {cap.get('status', 'unsupported')}.",
             "- Evidence quality and an investment rating are separate judgments.", "",
             "## Supported calculations", ""]
    shown = {"revenue_ttm", "revenue_growth_yoy", "revenue_ttm_growth_yoy", "operating_margin_ttm",
             "free_cash_flow_ttm", "stock_based_compensation_ttm", "market_cap", "net_debt",
             "enterprise_value", "price_to_earnings", "enterprise_value_to_revenue", "price_to_free_cash_flow"}
    available = [f for f in packet.facts if f.kind.value == "calculated" and f.metric in shown]
    latest_periods = {metric: max(f.period_end or "" for f in available if f.metric == metric)
                      for metric in {f.metric for f in available}}
    available = [f for f in available if (f.period_end or "") == latest_periods[f.metric]]
    lines.append("Latest available period per metric is shown; dates may differ or be stale. Full history is retained in the packet.")
    for fact in available:
        lines.append(f"- {fact.metric}: {fact.value:,.4g} {fact.unit}; period ends {fact.period_end}. Source lineage: `{fact.fact_id}`.")
    if not available:
        lines.append("No supported calculations in this packet. Consult the coverage report for missing inputs.")
    lines += ["", "## Valuation requirements", ""]
    for metric, audit in cap.get("valuation_readiness", {}).items():
        lines.append(f"- {metric}: {audit['status']}; unresolved: {', '.join(audit.get('unresolved', [])) or 'none'}.")
    estimate = analysis.get("estimated_valuation")
    if estimate:
        from .estimated_valuation import describe_estimate

        lines += ["", "## Estimated valuation (unverified)", "", describe_estimate(estimate, max_notes=6),
                  "These multiples use vendor prices and share counts; they are not the verified capitalization audit above."]
    lines += ["", "## Analyst expectations", ""]
    snapshots = analysis.get("analyst_targets", {}).get("snapshots", [])
    for snapshot in snapshots:
        values = ", ".join(f"{k}={v}" for k, v in snapshot.get("values", {}).items())
        lines.append(f"- Vendor aggregate snapshot: {values} {snapshot['currency']}; retrieved {snapshot['retrieved_at']}; source {snapshot['source_url']}.")
    if not snapshots:
        lines.append("No eligible analyst-target snapshot was collected.")
    lines.append("Vendor aggregates do not identify constituent firms or establish a target horizon. They are opinions, not our valuation.")
    lines += ["", "## Questions for the research run", "",
              "- What measurable revenue, customer or margin changes would support the thesis?",
              "- What competitive response, execution failure or valuation assumption would invalidate it?",
              "- Does growth translate into cash per share after investment and dilution?",
              "- Which claims are supported by filings, which are analyst opinions, and which remain scenarios?",
              "- What should be checked at the next earnings release?", "",
              "## Evidence files", "",
              "- `packet.json`: retained evidence, source references and calculation lineage.",
              "- `coverage.md`: full coverage, conflicts and missing-input diagnostics.",
              "- `manifest.json`: workflow mode, configuration and completion status.", ""]
    return "\n".join(lines)
