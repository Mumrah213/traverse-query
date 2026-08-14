"""HTTP server for traverse-query: browse the graph, anchor a movie, query it.

Stdlib only (ThreadingHTTPServer), like the traverse prototype it grew
from. Serves the single-page UI plus a small JSON API:

    GET  /                          the UI
    GET  /api/search?q=             title search (for the entry box)
    GET  /api/movie?id=             a node: movie + its connections
    GET  /api/connection?kind=&value=  members of a chip
    POST /api/query                 {query, anchor_id?} → anchored search
    GET  /api/health                status: browse + router readiness

The browse half loads once at startup and needs nothing but the JSONL
files. The query half needs the router, which needs catalog + embeddings;
when those are missing the app still browses and /api/query answers with
an honest 503 instead of a crash -- the same degrade-not-die contract the
prototype had.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import anchor
from .browse import Graph, connection_members, load, node

STATIC = Path(__file__).parent / "static"

GRAPH: Graph | None = None
ROUTER: Any = None          # query_to_rec Router, or None when it can't boot
ROUTER_ERROR: str = ""


def _build_router(directory: Path) -> Any:
    """Build the router from the shared data dir. Raises on failure."""
    from query_to_rec.service import build_router

    return build_router(directory=directory)


def _warm_up(router: Any) -> None:
    """Load everything the router defers to request time, at boot.

    Two one-time costs otherwise land on the *first user query*: the runtime
    embed model (torch + sentence-transformers, ~15s) and the tag-vocabulary
    embedding matrix (tags.py builds it lazily on the first search, ~5s).
    Both are the sibling's design and this repo does not change them -- but
    moving the cost to startup, with a log line for each step, is this
    server's boot sequence to own. Non-fatal: on failure the app still
    browses and set_ops/inversion still answer; semantic queries degrade.
    """
    print("[traverse-query] warming up embed model (torch + sentence-transformers)...")
    try:
        from query_to_rec.embed_client import embed

        embed(["warm-up"])
        print("[traverse-query] embed model ready")
    except Exception as exc:  # noqa: BLE001
        print(f"[traverse-query] embed model unavailable ({type(exc).__name__}: {exc}); "
              "semantic queries will fail, set_ops/inversion still work")

    print("[traverse-query] warming tag vocabulary (first search pays otherwise)...")
    try:
        router.search("warm-up", k=1)
        print("[traverse-query] tag vocabulary ready")
    except Exception as exc:  # noqa: BLE001
        print(f"[traverse-query] tag vocabulary unavailable ({type(exc).__name__}: {exc})")


class Handler(BaseHTTPRequestHandler):
    server_version = "TraverseQuery/0.1"

    # -- plumbing ----------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:  # quieter logs
        print("[traverse-query]", fmt % args)

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _404(self, msg: str) -> None:
        self._json({"error": msg}, status=404)

    def _503(self, msg: str) -> None:
        self._json({"error": msg}, status=503)

    # -- routes ------------------------------------------------------------
    def do_GET(self) -> None:
        url = urlparse(self.path)
        path = url.path.rstrip("/") or "/"

        if path == "/":
            self._html((STATIC / "index.html").read_bytes())
            return

        qs = parse_qs(url.query)

        if path == "/api/search":
            q = (qs.get("q") or [""])[0]
            hits = [m.to_public() for m in GRAPH.search(q)] if GRAPH else []
            self._json({"query": q, "hits": hits})
            return

        if path == "/api/movie":
            raw = (qs.get("id") or [""])[0]
            try:
                mid = int(raw)
            except ValueError:
                self._404(f"bad id: {raw!r}")
                return
            payload = node(GRAPH, mid) if GRAPH else None
            if payload is None:
                self._404(f"no title with id {mid}")
                return
            self._json(payload)
            return

        if path == "/api/connection":
            kind = (qs.get("kind") or [""])[0]
            value = unquote((qs.get("value") or [""])[0])
            limit = int((qs.get("limit") or ["40"])[0])
            self._json(connection_members(GRAPH, kind, value, limit) if GRAPH else
                       {"members": []})
            return

        if path == "/api/health":
            self._json({
                "status": "ok" if GRAPH else "degraded",
                "titles": len(GRAPH.movies) if GRAPH else 0,
                "router": "ready" if ROUTER else ("error" if ROUTER_ERROR else "disabled"),
                "router_error": ROUTER_ERROR or None,
                "anchor_endpoint": "/api/query",
            })
            return

        self._404(f"no route {path}")

    def do_POST(self) -> None:
        url = urlparse(self.path)
        if url.path != "/api/query":
            self._404(f"no route {url.path}")
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "body must be JSON"}, status=400)
            return

        text = str(payload.get("query") or "").strip()
        if not text:
            self._json({"error": "query is required"}, status=400)
            return
        anchor_id = payload.get("anchor_id")
        k = int(payload.get("k") or 10)

        if ROUTER is None:
            reason = ROUTER_ERROR or "router not configured"
            self._503(f"query engine unavailable: {reason}")
            return

        anchor_title = None
        if anchor_id is not None:
            m = GRAPH.movies.get(int(anchor_id)) if GRAPH else None
            if m is None:
                self._404(f"no title with id {anchor_id}")
                return
            anchor_title = m.title

        try:
            self._json(anchor.search(ROUTER, anchor_title, text, k=k))
        except Exception as exc:  # noqa: BLE001 -- a request must never die silently
            self._json({"error": f"query failed: {type(exc).__name__}: {exc}"},
                       status=500)


def serve(host: str = "127.0.0.1", port: int = 8500, data_dir: Path | None = None) -> None:
    global GRAPH, ROUTER, ROUTER_ERROR

    # Boot takes ~35s on a cold start (catalog + embeddings + warm-up); the
    # log lines explain each step, so they must not sit in the stdout buffer
    # when stdout is a file (block-buffered by default when not a tty).
    import sys as _sys

    _sys.stdout.reconfigure(line_buffering=True)

    from .browse import DATA as DEFAULT_DATA

    d = data_dir or Path(DEFAULT_DATA)
    print(f"[traverse-query] loading catalog + cast + curated from {d}...")
    GRAPH = load(d)
    n = len(GRAPH.movies)
    print(f"[traverse-query] {n} titles, {len(GRAPH.cast)} with cast, "
          f"{sum(1 for m in GRAPH.movies.values() if m.curated_tags)} curated")

    try:
        ROUTER = _build_router(d)
        print(f"[traverse-query] router ready ({len(ROUTER.catalog)} titles, "
              f"{ROUTER.matrix.shape[1]}-dim embeddings)")
    except Exception as exc:  # noqa: BLE001 -- degrade, don't die
        ROUTER = None
        ROUTER_ERROR = f"{type(exc).__name__}: {exc}"
        print(f"[traverse-query] router unavailable ({ROUTER_ERROR}); browse only")

    if ROUTER is not None:
        _warm_up(ROUTER)
    print(f"[traverse-query] http://{host}:{port}  (Ctrl-C to stop)")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
