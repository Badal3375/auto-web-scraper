#!/usr/bin/env python3
"""AutoScraper command line.

  python main.py run                 scrape every target, store, export
  python main.py run -t books        scrape one target
  python main.py preview -t books    test your selectors on page 1 (nothing is saved)
  python main.py schedule            run now, then keep running on the schedule in config.yaml
  python main.py export              re-export CSV/JSON/XLSX from the database
  python main.py stats               show database totals and recent runs
"""
from __future__ import annotations

import argparse
import json
import sys

from scraper.config import ConfigError, load_config
from scraper.exporter import export_target
from scraper.fetcher import Fetcher, FetchError
from scraper.logger import setup_logging
from scraper.parser import find_next_page, parse_items
from scraper.pipeline import run_all
from scraper.storage import Storage


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Config-driven web scraping pipeline")
    p.add_argument("-c", "--config", default="config.yaml", help="path to config file (default: config.yaml)")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="scrape, store and export")
    run.add_argument("-t", "--target", action="append", help="only this target (repeatable)")
    run.add_argument("--no-export", action="store_true", help="skip CSV/JSON/XLSX export")

    prev = sub.add_parser("preview", help="parse the first page of a target and print the result")
    prev.add_argument("-t", "--target", required=True)

    sub.add_parser("schedule", help="run on the schedule defined in config.yaml")
    sub.add_parser("export", help="export the stored data again")
    sub.add_parser("stats", help="show what is in the database")
    return p


def cmd_preview(cfg, name: str) -> int:
    target = cfg.target(name)
    url = target.start_urls[0] if target.start_urls else target.url_template.format(page=target.start_page)
    with Fetcher(cfg.request) as fetcher:
        html = fetcher.get(url, use_browser=target.use_playwright, wait_selector=target.wait_selector)
    items = parse_items(html, url, target)
    print(json.dumps(items[:5], indent=2, ensure_ascii=False))
    print(f"\n{len(items)} items matched '{target.item_selector}' on {url}")
    print(f"next page: {find_next_page(html, url, target)}")
    if not items:
        print("No matches - check item_selector, or set use_playwright: true if the page is rendered by JavaScript.")
        return 1
    return 0


def cmd_stats(cfg) -> int:
    with Storage(cfg.db_path) as s:
        targets = s.targets()
        if not targets:
            print("Database is empty. Run: python main.py run")
            return 0
        for t in targets:
            print(f"{t}: {len(s.load_df(t))} items")
        print("\nRecent runs:")
        runs = s.runs_df(10)
        print(runs[["id", "target", "started_at", "status", "pages", "items_found",
                    "new_items", "updated_items"]].to_string(index=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
    except (ConfigError, FileNotFoundError) as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    setup_logging(cfg.log_level, cfg.log_dir)

    try:
        if args.cmd == "run":
            results = run_all(cfg, only=args.target, export=not args.no_export)
            return 1 if any(r.status in ("failed", "empty") for r in results) else 0
        if args.cmd == "preview":
            return cmd_preview(cfg, args.target)
        if args.cmd == "schedule":
            from scraper.scheduler import start
            start(cfg)
            return 0
        if args.cmd == "export":
            with Storage(cfg.db_path) as s:
                for t in s.targets():
                    export_target(s, t, cfg.export_dir, cfg.exports)
            return 0
        if args.cmd == "stats":
            return cmd_stats(cfg)
    except (ConfigError, FetchError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
