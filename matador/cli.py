"""Command-line entry point: `matador serve` runs the dashboard standalone."""

from __future__ import annotations

import argparse

import uvicorn

from matador import create_app


def main(argv: list[str] | None = None) -> None:
    """Parse args and serve the dashboard with uvicorn."""
    parser = argparse.ArgumentParser(prog="matador", description="A dashboard for toro queues.")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the dashboard")
    serve.add_argument("--queues", required=True, help="comma-separated queue names to watch")
    serve.add_argument("--redis", default="redis://localhost:6379", help="Redis URL")
    serve.add_argument("--prefix", default="toro", help="toro key prefix")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)
    names = [q.strip() for q in args.queues.split(",") if q.strip()]
    app = create_app(names, url=args.redis, prefix=args.prefix)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
