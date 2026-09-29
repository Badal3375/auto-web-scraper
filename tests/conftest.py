import functools
import http.server
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper.config import config_from_dict  # noqa: E402


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


ITEM = ('<article class="p"><h3><a href="/item/{id}.html" title="{title}">{title}</a></h3>'
        '<span class="price">£{price:,.2f}</span><p class="rating Three">x</p></article>')


def write_site(root: Path, prices: dict[int, float]) -> None:
    """3 pages x 2 items. Page 1 and 2 link to the next page."""
    for page in (1, 2, 3):
        ids = [page * 2 - 1, page * 2]
        items = "".join(ITEM.format(id=i, title=f"Café {i}", price=prices[i]) for i in ids)
        nxt = f'<li class="next"><a href="page-{page + 1}.html">next</a></li>' if page < 3 else ""
        (root / f"page-{page}.html").write_text(
            f"<html><body>{items}<ul>{nxt}</ul></body></html>", encoding="utf-8")


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    write_site(root, {i: 10.0 * i for i in range(1, 7)})
    handler = functools.partial(QuietHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield SimpleNamespace(root=root, url=f"http://127.0.0.1:{server.server_port}")
    server.shutdown()
    server.server_close()


@pytest.fixture
def make_cfg(tmp_path, site):
    def _make(**overrides):
        data = {
            "project": "test",
            "output_dir": str(tmp_path / "out"),
            "request": {"delay_seconds": 0, "jitter": 0, "retries": 1, "backoff": 0.01, "timeout": 5},
            "exports": ["csv", "json", "xlsx"],
            "targets": [{
                "name": "items",
                "start_url": f"{site.url}/page-1.html",
                "max_pages": 5,
                "item_selector": "article.p",
                "next_page": {"selector": "li.next a"},
                "dedupe_on": ["url"],
                "fields": {
                    "title": {"selector": "h3 a", "attr": "title"},
                    "url": {"selector": "h3 a", "attr": "href", "absolute": True},
                    "price": {"selector": ".price", "type": "price"},
                    "rating": {"selector": "p.rating", "attr": "class", "type": "rating"},
                },
            }],
        }
        data.update(overrides)
        return config_from_dict(data, tmp_path)
    return _make
