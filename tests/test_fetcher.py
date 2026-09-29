import pytest
import requests

from scraper.config import RequestConfig
from scraper.fetcher import FetchError, Fetcher


class FakeResp:
    def __init__(self, status, text="<html>ok</html>"):
        self.status_code, self.text = status, text
        self.encoding, self.apparent_encoding, self.headers = "utf-8", "utf-8", {}


class FakeSession:
    def __init__(self, script):
        self.script, self.calls = list(script), 0
        self.headers, self.proxies = {}, {}

    def get(self, url, timeout=None):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        pass


def fetcher(script, retries=3):
    f = Fetcher(RequestConfig(delay_seconds=0, jitter=0, retries=retries, backoff=0.01, respect_robots=False))
    f.session = FakeSession(script)
    return f


def test_retries_then_succeeds():
    f = fetcher([FakeResp(503), requests.ConnectionError("boom"), FakeResp(200)])
    assert "ok" in f.get("http://x/")
    assert f.session.calls == 3


def test_gives_up_after_retries():
    f = fetcher([FakeResp(500)] * 3, retries=2)
    with pytest.raises(FetchError, match="Giving up"):
        f.get("http://x/")
    assert f.session.calls == 3


def test_404_is_not_retried():
    f = fetcher([FakeResp(404), FakeResp(200)])
    with pytest.raises(FetchError, match="HTTP 404"):
        f.get("http://x/")
    assert f.session.calls == 1


def test_robots_txt_blocks(site):
    (site.root / "robots.txt").write_text("User-agent: *\nDisallow: /private/\n")
    f = Fetcher(RequestConfig(delay_seconds=0, jitter=0))
    with pytest.raises(FetchError, match="robots"):
        f.get(f"{site.url}/private/secret.html")
    assert "Café" in f.get(f"{site.url}/page-1.html")   # also proves UTF-8 decoding is right
