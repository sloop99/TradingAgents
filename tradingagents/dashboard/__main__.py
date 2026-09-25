"""Launch the local research dashboard."""

from __future__ import annotations

import argparse
from pathlib import Path

from .indexer import default_scan_roots
from .server import serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-root", action="append", type=Path, default=[], help="Artifact root; repeatable")
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically")
    parser.add_argument("--sector-file", type=Path, help="Precomputed sector snapshot; no implicit network calls")
    parser.add_argument("--earnings-file", type=Path, help="Precomputed earnings calendar; no implicit network calls")
    args = parser.parse_args(argv)
    roots = args.scan_root or default_scan_roots()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    serve(roots, port=args.port, open_browser=not args.no_open, sector_file=args.sector_file,
          earnings_file=args.earnings_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
