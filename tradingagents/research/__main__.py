"""Generate a free-first equity evidence packet without an LLM invocation."""

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from .cache import EvidenceCache
from .engine import build_packet


class FixtureProvider:
    """Replay an explicitly supplied provider response for offline validation."""

    def __init__(self, path):
        self.path = Path(path)

    def fetch(self, ticker, as_of):
        del ticker, as_of
        return json.loads(self.path.read_text(encoding="utf-8"))


class MemoizedProvider:
    """Share one provider response within a CLI invocation."""

    def __init__(self, provider):
        self.provider = provider
        self.responses = {}

    def fetch(self, ticker, as_of):
        key = (ticker, as_of)
        if key not in self.responses:
            self.responses[key] = self.provider.fetch(ticker, as_of)
        return self.responses[key]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ticker", help="Stock ticker; initial filing coverage is US SEC issuers")
    parser.add_argument("--as-of", default=datetime.now(timezone.utc).date().isoformat(),
                        help="ISO date (end of day UTC) or timezone-aware timestamp")
    parser.add_argument("--horizon", default="long_term")
    parser.add_argument("--thesis", default=None)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--cache-dir", type=Path, default=Path(".tradingagents/evidence-cache"))
    parser.add_argument("--sec-user-agent", default=os.getenv("SEC_USER_AGENT", ""),
                        help="Identifying SEC User-Agent, normally organization/name and contact email")
    parser.add_argument("--no-market", action="store_true", help="Skip the optional Yahoo market feed")
    parser.add_argument("--filings", action="store_true",
                        help="Extract capital-structure candidates from the latest SEC annual/quarterly filing")
    parser.add_argument("--max-filings", type=int, choices=(1, 2, 3), default=1,
                        help="Maximum primary filings fetched with --filings (default: 1)")
    parser.add_argument("--fixture", action="append", type=Path, default=[],
                        help="Offline provider response JSON; disables all live providers; repeatable")
    parser.add_argument("--require-sufficient", action="store_true",
                        help="Return exit code 2 if packet coverage is not sufficient")
    parser.add_argument("--capital-review", type=Path,
                        help="Apply an explicitly reviewed, evidence-bound capital-input manifest")
    parser.add_argument("--write-review-draft", action="store_true",
                        help="Save a pending capital-review-draft.json alongside the packet")
    args = parser.parse_args(argv)
    ticker = args.ticker.strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.^=-]{0,29}", ticker):
        parser.error("Ticker must be a short symbol, not a path or free-form company name")

    review_manifest = None
    if args.capital_review:
        try:
            review_manifest = json.loads(args.capital_review.read_text(encoding="utf-8"))
            if not isinstance(review_manifest, dict):
                raise ValueError("capital review must be a JSON object")
        except (OSError, ValueError) as exc:
            parser.error(str(exc))

    filing_provider = None
    if args.fixture:
        providers = [FixtureProvider(path) for path in args.fixture]
    else:
        from .providers.sec import SecEdgarProvider

        cache = EvidenceCache(args.cache_dir)
        sec = MemoizedProvider(SecEdgarProvider(user_agent=args.sec_user_agent, cache=cache))
        providers = [sec]
        if args.filings:
            from .providers.filing import SecFilingProvider

            filing_provider = MemoizedProvider(SecFilingProvider(
                user_agent=args.sec_user_agent, cache=cache, sec_provider=sec,
                max_filings=args.max_filings,
            ))
            providers.append(filing_provider)
        if not args.no_market:
            import yfinance as yf

            from .providers.market import YahooMarketProvider

            # yfinance has its own SQLite cache, separate from EvidenceCache.
            # Keep it in the caller-selected writable cache tree as well.
            yf.set_tz_cache_location(str((args.cache_dir / "yfinance").resolve()))
            providers.append(YahooMarketProvider(cache=cache))
    try:
        packet = build_packet(ticker, args.as_of, providers, args.horizon, args.thesis,
                              review_manifest=review_manifest)
    except (ValueError, TypeError) as exc:
        parser.error(str(exc))
    target = args.output_dir or Path(".tradingagents/research") / ticker / args.as_of[:10]
    target.mkdir(parents=True, exist_ok=True)
    (target / "packet.json").write_text(
        json.dumps(packet.to_dict(), indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )
    (target / "coverage.md").write_text(packet.to_markdown(), encoding="utf-8")
    if args.write_review_draft:
        from .reviewed_inputs import draft_review_manifest

        draft = draft_review_manifest(packet.facts, ticker, packet.as_of)
        (target / "capital-review-draft.json").write_text(
            json.dumps(draft, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8"
        )
    if filing_provider is not None:
        (target / "filing-evidence.json").write_text(
            json.dumps(list(filing_provider.responses.values()), indent=2, ensure_ascii=False,
                       allow_nan=False), encoding="utf-8"
        )
    print(f"Evidence status: {packet.status.value} (not an investment rating)")
    print(f"Packet: {(target / 'packet.json').resolve()}")
    print(f"Coverage: {(target / 'coverage.md').resolve()}")
    if args.capital_review:
        review_status = packet.financial_analysis["reviewed_inputs"]["status"]
        print(f"Capital-input review: {review_status} (analyst assertions)")
        if review_status != "applied":
            return 2
    return 2 if args.require_sufficient and packet.status != "sufficient" else 0


if __name__ == "__main__":
    raise SystemExit(main())
