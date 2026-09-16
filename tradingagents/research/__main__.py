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
    parser.add_argument("--fixture", action="append", type=Path, default=[],
                        help="Offline provider response JSON; disables all live providers; repeatable")
    parser.add_argument("--require-sufficient", action="store_true",
                        help="Return exit code 2 if packet coverage is not sufficient")
    args = parser.parse_args(argv)
    ticker = args.ticker.strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.^=-]{0,29}", ticker):
        parser.error("Ticker must be a short symbol, not a path or free-form company name")

    if args.fixture:
        providers = [FixtureProvider(path) for path in args.fixture]
    else:
        from .providers.sec import SecEdgarProvider

        cache = EvidenceCache(args.cache_dir)
        providers = [SecEdgarProvider(user_agent=args.sec_user_agent, cache=cache)]
        if not args.no_market:
            from .providers.market import YahooMarketProvider

            providers.append(YahooMarketProvider(cache=cache))
    try:
        packet = build_packet(ticker, args.as_of, providers, args.horizon, args.thesis)
    except (ValueError, TypeError) as exc:
        parser.error(str(exc))
    target = args.output_dir or Path(".tradingagents/research") / ticker / args.as_of[:10]
    target.mkdir(parents=True, exist_ok=True)
    (target / "packet.json").write_text(
        json.dumps(packet.to_dict(), indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )
    (target / "coverage.md").write_text(packet.to_markdown(), encoding="utf-8")
    print(f"Evidence status: {packet.status.value} (not an investment rating)")
    print(f"Packet: {(target / 'packet.json').resolve()}")
    print(f"Coverage: {(target / 'coverage.md').resolve()}")
    return 2 if args.require_sufficient and packet.status != "sufficient" else 0


if __name__ == "__main__":
    raise SystemExit(main())
