from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
import socket
from textwrap import shorten
import webbrowser

from webcrawler.app import App
from webcrawler.config import AppConfig
from webcrawler.utils.logger import setup_logging
from webcrawler.web.server import run_web_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="webcrawler",
        description="Single-machine web crawler backend skeleton",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    index_cmd = sub.add_parser("index", help="Run crawler + indexing")
    index_cmd.add_argument("--origin", dest="origin_url")
    index_cmd.add_argument("--max-depth", type=int, dest="max_depth")
    index_cmd.add_argument("--max-pages", type=int, dest="max_pages")
    index_cmd.add_argument("--no-resume", action="store_true")

    search_cmd = sub.add_parser("search", help="Search indexed content")
    search_cmd.add_argument("--query", required=True)
    search_cmd.add_argument("--limit", type=int, default=10)
    search_cmd.add_argument("--domain", default=None)
    search_cmd.add_argument("--crawler-id", dest="crawl_run_id", default=None)
    search_cmd.add_argument("--from-date", dest="indexed_from", default=None)
    search_cmd.add_argument("--to-date", dest="indexed_to", default=None)

    sub.add_parser("status", help="Show crawler status snapshot")

    web_cmd = sub.add_parser("web", help="Run browser UI")
    web_cmd.add_argument("--host", default="127.0.0.1")
    web_cmd.add_argument("--port", type=int, default=8080)

    start_cmd = sub.add_parser("start", help="User-friendly launcher for web UI")
    start_cmd.add_argument("--host", default="127.0.0.1")
    start_cmd.add_argument("--port", type=int, default=8080)
    start_cmd.add_argument("--open-browser", action="store_true")
    start_cmd.add_argument("--max-port-tries", type=int, default=10)
    return parser


def apply_overrides(config: AppConfig, args: argparse.Namespace) -> AppConfig:
    if getattr(args, "origin_url", None):
        config.origin_url = args.origin_url
    if getattr(args, "max_depth", None) is not None:
        config.max_depth = args.max_depth
    if getattr(args, "max_pages", None) is not None:
        config.max_pages = args.max_pages
    return config


def main() -> None:
    setup_logging()
    parser = build_parser()
    args = parser.parse_args()

    config = apply_overrides(AppConfig.from_env(), args)
    app = App(config)

    if args.command == "index":
        asyncio.run(
            app.index(
                origin=config.origin_url,
                max_depth=config.max_depth,
                resume=not args.no_resume,
            )
        )
        _print_status_block("Index completed", app.get_status())
        return

    if args.command == "search":
        hits = app.run_search(
            query=args.query,
            limit=args.limit,
            domain=args.domain,
            crawl_run_id=args.crawl_run_id,
            indexed_from=args.indexed_from,
            indexed_to=args.indexed_to,
        )
        _print_search_results(query=args.query, hits=[asdict(hit) for hit in hits])
        return

    if args.command == "status":
        _print_status_block("Current status", app.get_status())
        return

    if args.command == "web":
        run_web_server(config=config, host=args.host, port=args.port)
        return

    if args.command == "start":
        _start_launcher(
            config=config,
            host=args.host,
            preferred_port=args.port,
            open_browser=args.open_browser,
            max_port_tries=args.max_port_tries,
        )
        return

    raise RuntimeError(f"Unknown command: {args.command}")


def _print_status_block(title: str, status: dict) -> None:
    print(f"\n=== {title} ===")
    print(f"Running: {status.get('is_running')}")
    print(f"Visited URLs: {status.get('total_visited', status.get('seen_url_count', 0))}")
    print(f"Indexed Documents: {status.get('indexed_documents', 0)}")
    print(f"Indexed Pages (run): {status.get('pages_indexed', 0)}")
    print(f"Failed Pages (run): {status.get('pages_failed', 0)}")
    print(f"Queue Size: {status.get('queue_size', 0)}")
    print(f"Active Workers: {status.get('active_workers', 0)}")
    if status.get("current_url"):
        print(f"Current URL: {status.get('current_url')}")
    print("")


def _print_search_results(query: str, hits: list[dict]) -> None:
    print(f"\n=== Search Results for: {query} ===")
    if not hits:
        print("No results found.\n")
        return
    for i, hit in enumerate(hits, start=1):
        print(f"[{i}] {hit.get('title', '')}")
        print(f"    URL: {hit.get('url', '')}")
        print(f"    Snippet: {shorten(hit.get('snippet', ''), width=180, placeholder='...')}")
    print("")


def _start_launcher(
    *,
    config: AppConfig,
    host: str,
    preferred_port: int,
    open_browser: bool,
    max_port_tries: int,
) -> None:
    port = _pick_available_port(host=host, start_port=preferred_port, max_port_tries=max_port_tries)
    base_url = f"http://{host}:{port}"
    print("\n=== CrawlDesk Launcher ===")
    print(f"Web UI: {base_url}")
    print(f"New Crawler: {base_url}/crawler/new")
    print(f"Search: {base_url}/search")
    print(f"Status: {base_url}/status")
    print("Stop server: Ctrl+C\n")

    if open_browser:
        try:
            webbrowser.open(f"{base_url}/crawler/new")
            print("Browser opened.\n")
        except Exception:  # noqa: BLE001
            print("Could not open browser automatically.\n")

    try:
        run_web_server(config=config, host=host, port=port)
    except KeyboardInterrupt:
        print("\nShutting down CrawlDesk...")
        print("Bye! You can restart anytime with ./run.sh\n")


def _pick_available_port(*, host: str, start_port: int, max_port_tries: int) -> int:
    port = start_port
    tries = max(1, max_port_tries)
    for _ in range(tries):
        if _is_port_available(host=host, port=port):
            return port
        port += 1
    raise RuntimeError(
        f"No available port found from {start_port} to {start_port + tries - 1}. "
        "Try --port with a different value."
    )


def _is_port_available(*, host: str, port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


if __name__ == "__main__":
    main()
