import pytest

from scraper.config import ConfigError, config_from_dict
from scraper.parser import coerce, find_next_page, parse_items, to_number

HTML = """
<div class="card"><a class="t" href="/a">  First   item </a><b class="p">Rs. 1,299.50</b>
  <span class="tag">x</span><span class="tag">y</span><time>5 March 2024</time></div>
<div class="card"><a class="t" href="/b">Second</a><b class="p">n/a</b></div>
<a class="more" href="?page=2">more</a>
"""


def target(tmp_path, **extra):
    t = {"name": "t", "start_url": "http://x.test/", "item_selector": "div.card", "fields": {
        "title": "a.t", "url": {"selector": "a.t", "attr": "href", "absolute": True},
        "price": {"selector": "b.p", "type": "price"},
        "tags": {"selector": "span.tag", "multiple": True},
        "when": {"selector": "time", "type": "date"},
        "missing": {"selector": ".nope", "default": "n/a"}}}
    t.update(extra)
    return config_from_dict({"targets": [t]}, tmp_path).targets[0]


def test_to_number():
    assert to_number("£51.77") == 51.77
    assert to_number("₹ 1,29,900") == 129900.0
    assert to_number("free") is None


def test_coerce_rating_and_int():
    assert coerce("star-rating Four", "rating") == 4
    assert coerce("4.5 out of 5", "rating") == 4.5
    assert coerce("12 reviews", "int") == 12


def test_parse_items(tmp_path):
    rows = parse_items(HTML, "http://x.test/list/", target(tmp_path))
    assert len(rows) == 2
    assert rows[0]["title"] == "First item"                # whitespace collapsed
    assert rows[0]["url"] == "http://x.test/a"             # made absolute
    assert rows[0]["price"] == 1299.5
    assert rows[0]["tags"] == "x | y"
    assert rows[1]["price"] is None and rows[1]["missing"] == "n/a"


def test_next_page(tmp_path):
    t = target(tmp_path, next_page={"selector": "a.more"})
    assert find_next_page(HTML, "http://x.test/list", t) == "http://x.test/list?page=2"
    assert find_next_page("<html></html>", "http://x.test/", t) is None


@pytest.mark.parametrize("bad", [
    {"fields": {"a": {"selector": "x", "type": "nonsense"}}},
    {"fields": {"a": {"selector": "x", "colour": "red"}}},
    {"dedupe_on": ["ghost"]},
    {"start_url": None, "url_template": None},
])
def test_config_validation(tmp_path, bad):
    with pytest.raises(ConfigError):
        target(tmp_path, **bad)
