"""Orchestrates fetch -> parse -> clean -> store -> export for every target."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterator

import requests

from .cleaner import clean_rows
from .config import AppConfig, ConfigError, TargetConfig
from .exporter import export_target
from .fetcher import FetchError, Fetcher
from .parser import find_next_page, parse_items
from .storage import Storage

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    target: str
    status: str = "ok"          # ok | partial | empty | failed
    pages: int = 0
    found: int = 0
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    error: str | None = None


def crawl(target: TargetConfig, fetcher: Fetcher) -> Iterator[tuple[str, list[dict]]]:
    """Yield (url, parsed_items) page by page, following pagination."""
    kwargs = {"use_browser": target.use_playwright, "wait_selector": target.wait_selector}

    if target.url_template:
        for page in range(target.start_page, target.start_page + target.max_pages):
            url = target.url_template.format(page=page)
            try:
                items = parse_items(fetcher.get(url, **kwargs), url, target)
            except FetchError as exc:
                if exc.status_code == 404 and page > target.start_page:
                    return         # sites often 404 past the last page: that's the normal end
                raise
            if not items:          # empty page also means we ran past the end
                return
            yield url, items
        return

    seen: set[str] = set()
    for start in target.start_urls:
        url: str | None = start
        for _ in range(target.max_pages):
            if not url or url in seen:
                break
            seen.add(url)
            html = fetcher.get(url, **kwargs)
            items = parse_items(html, url, target)
            yield url, items
            if not items:
                break
            url = find_next_page(html, url, target)


def run_target(target: TargetConfig, fetcher: Fetcher, storage: Storage) -> RunResult:
    res = RunResult(target=target.name)
    run_id = storage.start_run(target.name)
    collected: list[dict] = []
    try:
        for url, items in crawl(target, fetcher):
            res.pages += 1
            collected.extend(items)
            log.info("[%s] page %d: %d items  %s", target.name, res.pages, len(items), url)
    except FetchError as exc:
        res.error, res.status = str(exc), ("partial" if res.pages else "failed")
        log.error("[%s] %s", target.name, exc)
    except Exception as exc:  # never let one target kill the whole run
        res.error, res.status = f"{type(exc).__name__}: {exc}", "failed"
        log.exception("[%s] unexpected error", target.name)

    res.found = len(collected)
    if collected:
        stats = storage.upsert(target.name, clean_rows(collected, target), target.dedupe_on)
        res.new, res.updated, res.unchanged = stats["new"], stats["updated"], stats["unchanged"]
    elif res.status == "ok":
        res.status = "empty"
        res.error = "Pages loaded but no items matched - the site layout or your selectors may have changed."
        log.warning("[%s] %s", target.name, res.error)

    storage.finish_run(run_id, status=res.status, pages=res.pages, found=res.found,
                       new=res.new, updated=res.updated, unchanged=res.unchanged, error=res.error)
    return res


def _notify(cfg: AppConfig, results: list[RunResult]) -> None:
    bad = [r for r in results if r.status != "ok"]
    if not bad or not cfg.webhook_url:
        return
    text = f"AutoScraper [{cfg.project}] needs attention:\n" + "\n".join(
        f"- {r.target}: {r.status} ({r.error})" for r in bad)
    try:  # 'text' works for Slack, 'content' for Discord
        requests.post(cfg.webhook_url, json={"text": text, "content": text}, timeout=10)
    except requests.RequestException as exc:
        log.warning("Webhook notification failed: %s", exc)


def print_summary(results: list[RunResult]) -> None:
    print(f"\n{'target':<18}{'status':<9}{'pages':>6}{'found':>7}{'new':>6}{'upd':>6}{'same':>6}")
    print("-" * 58)
    for r in results:
        print(f"{r.target:<18}{r.status:<9}{r.pages:>6}{r.found:>7}{r.new:>6}{r.updated:>6}{r.unchanged:>6}")
        if r.error:
            print(f"   ! {r.error}")


def run_all(cfg: AppConfig, only: list[str] | None = None, export: bool = True) -> list[RunResult]:
    targets = [cfg.target(n) for n in only] if only else cfg.targets
    if not targets:
        raise ConfigError("No targets selected")
    results: list[RunResult] = []
    with Fetcher(cfg.request) as fetcher, Storage(cfg.db_path) as storage:
        for target in targets:
            log.info("=== %s ===", target.name)
            results.append(run_target(target, fetcher, storage))
        if export:
            for target in targets:
                export_target(storage, target.name, cfg.export_dir, cfg.exports)
    _notify(cfg, results)
    print_summary(results)
    return results
