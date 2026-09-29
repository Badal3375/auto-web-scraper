from scraper.cleaner import clean_rows
from scraper.config import config_from_dict


def test_dedupe_and_normalise(tmp_path):
    t = config_from_dict({"targets": [{
        "name": "t", "start_url": "http://x/", "item_selector": "i", "dedupe_on": ["id"],
        "fields": {"id": "a", "name": "b", "when": {"selector": "c", "type": "date"}}}]}, tmp_path).targets[0]
    rows = [
        {"id": "1", "name": "  Foo \u00a0 Bar ", "when": "5 March 2024"},
        {"id": "1", "name": "duplicate", "when": None},
        {"id": "2", "name": "", "when": "garbage"},
        {"id": None, "name": None, "when": None},
    ]
    out = clean_rows(rows, t)
    assert [r["id"] for r in out] == ["1", "2"]
    assert out[0]["name"] == "Foo Bar" and out[0]["when"] == "2024-03-05"
    assert out[1]["name"] is None and out[1]["when"] is None
