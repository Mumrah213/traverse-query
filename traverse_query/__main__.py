from __future__ import annotations

import argparse

from .server import serve


def main() -> None:
    p = argparse.ArgumentParser(description="traverse-query: browse the movie graph, anchor a movie, query it")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8500)
    p.add_argument("--data", default=None, help="data dir (default: env TRAVERSE_QUERY_DATA_DIR, then ./data)")
    args = p.parse_args()
    serve(host=args.host, port=args.port, data_dir=args.data)


if __name__ == "__main__":
    main()
