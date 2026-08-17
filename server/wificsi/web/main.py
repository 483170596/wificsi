"""``wificsi-dashboard`` command line entry point."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from .api import create_app
from .settings import DashboardSettings


def _default_web_dist() -> str | None:
    candidate = Path(__file__).resolve().parents[3] / "web" / "dist"
    if candidate.is_dir():
        return str(candidate)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WIFICSI multi-node web dashboard")
    parser.add_argument("--udp-host", default="10.204.75.168")
    parser.add_argument("--udp-port", type=int, default=5500)
    parser.add_argument("--http-host", default="10.204.75.168")
    parser.add_argument("--http-port", type=int, default=8000)
    parser.add_argument("--publish-hz", type=float, default=10.0)
    parser.add_argument("--web-dist", default=None, help="path to the built web/dist directory")
    parser.add_argument("--reload", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    web_dist = args.web_dist or _default_web_dist()
    settings = DashboardSettings(
        udp_host=args.udp_host,
        udp_port=args.udp_port,
        http_host=args.http_host,
        http_port=args.http_port,
        publish_hz=args.publish_hz,
        web_dist=web_dist,
    )
    app = create_app(settings)
    uvicorn.run(app, host=args.http_host, port=args.http_port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
