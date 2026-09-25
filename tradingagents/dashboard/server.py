"""Dependency-free HTTP server for the local research dashboard."""

from __future__ import annotations

import json
import mimetypes
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlparse

from .earnings import earnings_payload
from .indexer import ResearchIndex, build_index
from .sectors import load_sector_snapshot

# Pinned rather than taken from mimetypes: on Windows that reads the registry,
# where a remapped .js (e.g. text/plain) makes browsers refuse module scripts.
_STATIC_TYPES = {
    ".html": "text/html",
    ".js": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".json": "application/json",
}


class DashboardState:
    def __init__(self, roots: list[Path], sector_file: Path | None = None, earnings_file: Path | None = None):
        self.roots = roots
        self.sector_file = sector_file or (roots[0] / "sector-rotation.json" if roots else None)
        self.earnings_file = earnings_file or (roots[0] / "earnings-calendar.json" if roots else None)
        self._lock = Lock()
        self.index = build_index(roots)

    def refresh(self) -> ResearchIndex:
        with self._lock:
            self.index = build_index(self.roots)
            return self.index


def handler_factory(state: DashboardState):
    static_root = Path(__file__).with_name("static").resolve()

    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "TradingAgentsDashboard/0.1"

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/sectors":
                self._json(load_sector_snapshot(state.sector_file))
                return
            if parsed.path == "/api/earnings":
                self._json(earnings_payload(state.earnings_file, state.index))
                return
            if parsed.path == "/api/runs":
                self._json(state.index.public_payload())
                return
            if parsed.path == "/api/report":
                query = parse_qs(parsed.query)
                run_id = (query.get("id") or [""])[0]
                section = (query.get("section") or [""])[0]
                run = state.index.run(run_id)
                path = run.sections.get(section) if run else None
                if path is None:
                    self.send_error(HTTPStatus.NOT_FOUND, "Unknown run or report section")
                    return
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError as exc:
                    self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                    return
                self._json({"run_id": run_id, "section": section, "markdown": text})
                return
            self._static(parsed.path, static_root)

        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/api/refresh":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._json(state.refresh().public_payload())

        def _json(self, value) -> None:
            payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:")
            self.end_headers()
            self.wfile.write(payload)

        def _static(self, request_path: str, root: Path) -> None:
            relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
            candidate = (root / relative).resolve()
            if root not in candidate.parents and candidate != root:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            if not candidate.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            payload = candidate.read_bytes()
            mime = (
                _STATIC_TYPES.get(candidate.suffix.lower())
                or mimetypes.guess_type(candidate.name)[0]
                or "application/octet-stream"
            )
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", f"{mime}; charset=utf-8" if mime.startswith("text/") else mime)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:",
            )
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args) -> None:
            return

    return DashboardHandler


def serve(
    roots: list[Path],
    host: str = "127.0.0.1",
    port: int = 8791,
    *,
    open_browser: bool = False,
    sector_file: Path | None = None,
    earnings_file: Path | None = None,
) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("The research dashboard only binds to a local loopback address")
    state = DashboardState(roots, sector_file=sector_file, earnings_file=earnings_file)
    server = ThreadingHTTPServer((host, port), handler_factory(state))
    if open_browser:
        webbrowser.open(f"http://{host}:{port}")
    print(f"Research dashboard: http://{host}:{port}")
    print(f"Indexed {len(state.index.runs)} runs from {len(state.roots)} root(s). Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
