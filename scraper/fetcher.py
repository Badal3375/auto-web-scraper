"""HTTP layer: polite rate limiting, retries with backoff, robots.txt, optional Playwright."""
from __future__ import annotations

import logging
import random
import time
from urllib import robotparser
from urllib.parse import urlparse

import requests

from .config import RequestConfig

log = logging.getLogger(__name__)
RETRY_STATUS = {429, 500, 502, 503, 504}


class FetchError(Exception):
    """A page could not be retrieved (blocked, HTTP error, or retries exhausted)."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class Fetcher:
    def __init__(self, cfg: RequestConfig):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": cfg.user_agent,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            **cfg.headers,
        })
        if cfg.proxy:
            self.session.proxies.update({"http": cfg.proxy, "https": cfg.proxy})
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}
        self._last_request = 0.0
        self._pw = self._browser = self._context = None

    # ------------------------------------------------------------------ robots.txt
    def _load_robots(self, origin: str) -> robotparser.RobotFileParser | None:
        rp = robotparser.RobotFileParser()
        try:
            resp = self.session.get(f"{origin}/robots.txt", timeout=self.cfg.timeout)
        except requests.RequestException as exc:
            log.warning("Could not read robots.txt for %s (%s); continuing", origin, exc)
            return None
        if resp.status_code == 200:
            rp.parse(resp.text.splitlines())
            return rp
        if resp.status_code >= 500:  # RFC 9309: server errors => assume disallowed
            log.warning("robots.txt at %s returned %s; treating site as disallowed", origin, resp.status_code)
            rp.parse(["User-agent: *", "Disallow: /"])
            return rp
        return None  # 4xx => no robots.txt => everything allowed

    def allowed(self, url: str) -> bool:
        if not self.cfg.respect_robots:
            return True
        parts = urlparse(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(origin)
        rp = self._robots[origin]
        return True if rp is None else rp.can_fetch(self.cfg.user_agent, url)

    # ------------------------------------------------------------------ helpers
    def _throttle(self) -> None:
        gap = self.cfg.delay_seconds + random.uniform(0, self.cfg.jitter)
        wait = gap - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)

    def _retry_delay(self, attempt: int, resp: requests.Response | None = None) -> float:
        delay = self.cfg.backoff ** attempt
        retry_after = resp.headers.get("Retry-After") if resp is not None else None
        if retry_after and str(retry_after).isdigit():
            delay = max(delay, min(int(retry_after), 60))
        return delay

    @staticmethod
    def _decode(resp: requests.Response) -> str:
        # requests assumes ISO-8859-1 when a server omits the charset, which garbles £, ₹, é...
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding
        return resp.text

    # ------------------------------------------------------------------ public API
    def get(self, url: str, *, use_browser: bool | None = None, wait_selector: str | None = None) -> str:
        if not self.allowed(url):
            raise FetchError(f"Blocked by robots.txt: {url}")
        if self.cfg.use_playwright if use_browser is None else use_browser:
            return self._get_with_browser(url, wait_selector)

        attempts = self.cfg.retries + 1
        last_error = "unknown error"
        for attempt in range(1, attempts + 1):
            self._throttle()
            resp = None
            try:
                resp = self.session.get(url, timeout=self.cfg.timeout)
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            finally:
                self._last_request = time.monotonic()

            if resp is not None:
                if resp.status_code < 400:
                    return self._decode(resp)
                if resp.status_code not in RETRY_STATUS:
                    raise FetchError(f"HTTP {resp.status_code} for {url}", resp.status_code)
                last_error = f"HTTP {resp.status_code}"

            if attempt < attempts:
                delay = self._retry_delay(attempt, resp)
                log.warning("Attempt %d/%d failed for %s (%s); retrying in %.1fs",
                            attempt, attempts, url, last_error, delay)
                time.sleep(delay)
        raise FetchError(f"Giving up on {url} after {attempts} attempts ({last_error})")

    def _get_with_browser(self, url: str, wait_selector: str | None) -> str:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise FetchError("Playwright not installed. Run: pip install playwright && playwright install chromium") from exc
        if self._pw is None:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
            self._context = self._browser.new_context(user_agent=self.cfg.user_agent)
        self._throttle()
        page = self._context.new_page()
        try:
            page.goto(url, timeout=self.cfg.timeout * 1000, wait_until="domcontentloaded")
            if wait_selector:
                page.wait_for_selector(wait_selector, timeout=self.cfg.timeout * 1000)
            else:
                page.wait_for_load_state("networkidle", timeout=self.cfg.timeout * 1000)
            return page.content()
        except Exception as exc:  # playwright raises its own error types
            raise FetchError(f"Browser fetch failed for {url}: {exc}") from exc
        finally:
            page.close()
            self._last_request = time.monotonic()

    def close(self) -> None:
        self.session.close()
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()
        self._pw = self._browser = self._context = None

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
