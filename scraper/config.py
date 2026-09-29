"""Load and validate config.yaml into typed dataclasses."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

VALID_TYPES = {"text", "int", "float", "price", "rating", "date"}
VALID_EXPORTS = {"csv", "json", "xlsx"}


class ConfigError(ValueError):
    """Raised when config.yaml is missing something or has a bad value."""


@dataclass
class RequestConfig:
    delay_seconds: float = 1.0      # minimum gap between requests
    jitter: float = 0.5             # random extra 0..jitter seconds
    timeout: int = 20
    retries: int = 3                # extra attempts after the first
    backoff: float = 2.0            # wait backoff**attempt seconds between retries
    respect_robots: bool = True
    user_agent: str = "AutoScraper/1.0"
    use_playwright: bool = False    # render JavaScript with a headless browser
    headers: dict[str, str] = field(default_factory=dict)
    proxy: str | None = None


@dataclass
class FieldSpec:
    name: str
    selector: str | None = None     # CSS selector inside the item; None = the item itself
    attr: str | None = None         # read this attribute instead of the text
    type: str = "text"              # text | int | float | price | rating | date
    absolute: bool = False          # turn relative URLs into absolute ones
    regex: str | None = None        # optional post-filter; group 1 (or whole match) is kept
    multiple: bool = False          # join all matches with " | "
    default: Any = None


@dataclass
class NextPage:
    selector: str
    attr: str = "href"


@dataclass
class TargetConfig:
    name: str
    item_selector: str = "body"
    fields: list[FieldSpec] = field(default_factory=list)
    start_urls: list[str] = field(default_factory=list)
    url_template: str | None = None  # e.g. "https://site.com/list?page={page}"
    start_page: int = 1
    max_pages: int = 5
    next_page: NextPage | None = None
    dedupe_on: list[str] = field(default_factory=list)
    use_playwright: bool | None = None
    wait_selector: str | None = None
    is_analysis: bool = False



@dataclass
class ScheduleConfig:
    enabled: bool = False
    interval_minutes: int = 60
    cron: str | None = None          # e.g. "0 */6 * * *" (overrides interval_minutes)
    timezone: str = "Asia/Kolkata"


@dataclass
class AppConfig:
    project: str
    base_dir: Path
    output_dir: Path
    request: RequestConfig
    targets: list[TargetConfig]
    schedule: ScheduleConfig
    exports: list[str]
    analysis_urls: list[str] = field(default_factory=list)
    webhook_url: str | None = None
    log_level: str = "INFO"

    @property
    def db_path(self) -> Path:
        return self.output_dir / f"{self.project}.db"

    @property
    def export_dir(self) -> Path:
        return self.output_dir / "exports"

    @property
    def log_dir(self) -> Path:
        return self.base_dir / "logs"

    def target(self, name: str) -> TargetConfig:
        for t in self.targets:
            if t.name == name:
                return t
        raise ConfigError(f"Unknown target '{name}'. Available: {[t.name for t in self.targets]}")


def _parse_field(name: str, raw: Any) -> FieldSpec:
    if isinstance(raw, str):
        raw = {"selector": raw}
    if not isinstance(raw, dict):
        raise ConfigError(f"Field '{name}' must be a selector string or a mapping")
    allowed = {f.name for f in fields(FieldSpec)} - {"name"}
    unknown = set(raw) - allowed
    if unknown:
        raise ConfigError(f"Field '{name}': unknown keys {sorted(unknown)}. Allowed: {sorted(allowed)}")
    spec = FieldSpec(name=name, **raw)
    if spec.type not in VALID_TYPES:
        raise ConfigError(f"Field '{name}': type must be one of {sorted(VALID_TYPES)}")
    return spec


def _parse_target(raw: dict[str, Any]) -> TargetConfig:
    name = raw.get("name")
    if not name:
        raise ConfigError("Every target needs a 'name'")

    is_analysis = raw.get("mode") in ("analysis", "analyze") or raw.get("type") in ("analysis", "analyze")
    item_selector = raw.get("item_selector")
    if not item_selector:
        item_selector = "body"
        is_analysis = True

    raw_fields = raw.get("fields")
    if not raw_fields:
        raw_fields = {"text": {"selector": "body"}}
        is_analysis = True

    start_urls = raw.get("start_urls") or ([raw["start_url"]] if raw.get("start_url") else [])
    template = raw.get("url_template")
    if bool(start_urls) == bool(template):
        raise ConfigError(f"Target '{name}': set exactly one of start_url(s) or url_template")

    specs = [_parse_field(n, r) for n, r in raw_fields.items()]
    dedupe = list(raw.get("dedupe_on") or [])
    missing = [d for d in dedupe if d not in {s.name for s in specs}]
    if missing:
        raise ConfigError(f"Target '{name}': dedupe_on refers to unknown fields {missing}")

    nxt = raw.get("next_page")
    next_page = NextPage(**nxt) if isinstance(nxt, dict) else (NextPage(selector=nxt) if nxt else None)
    max_pages = int(raw.get("max_pages", 5))
    if max_pages < 1:
        raise ConfigError(f"Target '{name}': max_pages must be >= 1")

    return TargetConfig(
        name=name,
        item_selector=item_selector,
        fields=specs,
        start_urls=start_urls,
        url_template=template,
        start_page=int(raw.get("start_page", 1)),
        max_pages=max_pages,
        next_page=next_page,
        dedupe_on=dedupe,
        use_playwright=raw.get("use_playwright"),
        wait_selector=raw.get("wait_selector"),
        is_analysis=is_analysis,
    )



def config_from_dict(data: dict[str, Any], base_dir: Path) -> AppConfig:
    base_dir = Path(base_dir).resolve()
    if not data.get("targets"):
        raise ConfigError("config needs at least one entry under 'targets'")

    req_raw = data.get("request") or {}
    req_allowed = {f.name for f in fields(RequestConfig)}
    if set(req_raw) - req_allowed:
        raise ConfigError(f"request: unknown keys {sorted(set(req_raw) - req_allowed)}")
    sched_raw = data.get("schedule") or {}
    sched_allowed = {f.name for f in fields(ScheduleConfig)}
    if set(sched_raw) - sched_allowed:
        raise ConfigError(f"schedule: unknown keys {sorted(set(sched_raw) - sched_allowed)}")

    targets = [_parse_target(t) for t in data["targets"]]
    names = [t.name for t in targets]
    if len(names) != len(set(names)):
        raise ConfigError("Target names must be unique")

    exports = [e.lower() for e in data.get("exports", ["csv", "xlsx", "json"])]
    bad = set(exports) - VALID_EXPORTS
    if bad:
        raise ConfigError(f"Unsupported export formats {sorted(bad)}; use {sorted(VALID_EXPORTS)}")

    out = Path(data.get("output_dir", "data"))
    out = out if out.is_absolute() else base_dir / out

    raw_analysis = data.get("analysis_urls") or []
    if isinstance(raw_analysis, str):
        raw_analysis = [raw_analysis]
    all_analysis_urls = list(raw_analysis)
    for t in targets:
        for u in t.start_urls:
            if u not in all_analysis_urls:
                all_analysis_urls.append(u)

    return AppConfig(
        project=data.get("project", "scraper"), base_dir=base_dir, output_dir=out,
        request=RequestConfig(**req_raw), targets=targets, schedule=ScheduleConfig(**sched_raw),
        exports=exports, analysis_urls=all_analysis_urls,
        webhook_url=os.environ.get("SCRAPER_WEBHOOK_URL") or data.get("webhook_url"),
        log_level=str(data.get("log_level", "INFO")).upper(),
    )


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return config_from_dict(data, path.resolve().parent)
