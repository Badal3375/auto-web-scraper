#!/usr/bin/env python3
"""AutoScraper command line.

  python main.py run                 scrape every target, store, export
  python main.py run -t books        scrape one target
  python main.py preview -t books    test your selectors on page 1 (nothing is saved)
  python main.py analyze https://x.com analyze website URL for Company, HR & emails
  python main.py schedule            run now, then keep running on the schedule in config.yaml
  python main.py export              re-export CSV/JSON/XLSX from the database
  python main.py stats               show database totals and recent runs
"""
from __future__ import annotations

import argparse
import json
import sys

# Ensure UTF-8 output across Windows and all platforms
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


from scraper.analyzer import WebsiteAnalyzer
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

    ana = sub.add_parser("analyze", help="analyze a website URL to extract Company, HR, Email & contact details")
    ana.add_argument("url", help="website URL to analyze (e.g. https://company.com)")
    ana.add_argument("--format", choices=["table", "list", "json", "csv"], default="table",
                     help="output format: table (default), list, json, csv")
    ana.add_argument("--no-crawl", action="store_true",
                     help="only analyze the given URL without crawling subpages (about/contact/careers)")
    ana.add_argument("--max-pages", type=int, default=4, help="max subpages to crawl (default: 4)")
    ana.add_argument("--ai", action="store_true",
                     help="enable AI model extraction (uses GEMINI_API_KEY or OPENAI_API_KEY)")
    ana.add_argument("--ai-provider", choices=["gemini", "openai", "ollama"], default="gemini",
                     help="AI model provider (default: gemini)")
    ana.add_argument("--api-key", default=None, help="API key for AI model (or set environment variable)")
    ana.add_argument("--save", action="store_true", help="save the analysis result to export directory")
    return p


def cmd_analyze(cfg, args) -> int:
    url_to_analyze = args.url
    for t in cfg.targets:
        if t.name.lower() == args.url.lower():
            if t.start_urls:
                url_to_analyze = t.start_urls[0]
            elif t.url_template:
                url_to_analyze = t.url_template.format(page=t.start_page)
            print(f"Target '{args.url}' resolved to URL: {url_to_analyze}")
            break

    with Fetcher(cfg.request) as fetcher:
        analyzer = WebsiteAnalyzer(fetcher=fetcher, request_config=cfg.request)
        print(f"Analyzing website: {url_to_analyze} ...")
        res = analyzer.analyze(
            url=url_to_analyze,
            crawl_subpages=not args.no_crawl,
            max_subpages=args.max_pages,
            use_ai=args.ai,
            ai_provider=args.ai_provider,
            api_key=args.api_key,
        )

    if res.error:
        print(f"Analysis error: {res.error}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(res.to_dict(), indent=2, ensure_ascii=False))
    elif args.format == "csv":
        print("=== COMPANY DETAILS ===")
        print(res.to_company_df().to_csv(index=False))
        print("\n=== HR & KEY CONTACTS ===")
        print(res.to_hr_df().to_csv(index=False))
        print("\n=== EMAILS FOUND ===")
        print(res.to_emails_df().to_csv(index=False))
        if res.social_links:
            print("\n=== SOCIAL PROFILES ===")
            print(res.to_socials_df().to_csv(index=False))
    elif args.format == "list":
        print("\n" + "=" * 65)
        print(f"  ANALYSIS FOR: {res.url}")
        print("=" * 65)
        print(f"\n[COMPANY]: {res.company.get('name') or 'N/A'}")
        for k, v in res.company.items():
            if v and k not in ("name", "url"):
                print(f"  - {k.capitalize()}: {v}")
        print("\n[HR & PEOPLE CONTACTS]:")
        if res.hr_contacts:
            for c in res.hr_contacts:
                print(f"  - {c.get('name')} | {c.get('role')} | Email: {c.get('email') or 'N/A'} | Phone: {c.get('phone') or 'N/A'}")
        else:
            print("  - No specific HR individuals identified by name")
        print("\n[EMAILS]:")
        if res.emails:
            for e in res.emails:
                print(f"  - {e['email']} [{e['category']}] (found on: {e['source_url']})")
        else:
            print("  - None found")
        if res.phones:
            print("\n[PHONES]:")
            for p in res.phones:
                print(f"  - {p}")
        if res.social_links:
            print("\n[SOCIAL LINKS]:")
            for plat, link in res.social_links.items():
                print(f"  - {plat.capitalize()}: {link}")
        print("=" * 65)
    else:  # default 'table'
        print(res.to_cli_text())

    if args.save:
        out_dir = cfg.export_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_domain = res.domain.replace(".", "_")
        json_file = out_dir / f"analysis_{safe_domain}.json"
        json_file.write_text(json.dumps(res.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nSaved analysis results to {json_file}")

    return 0



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
        if args.cmd == "analyze":
            return cmd_analyze(cfg, args)
        if args.cmd == "stats":
            return cmd_stats(cfg)
    except (ConfigError, FetchError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
