import json

import pandas as pd

from conftest import write_site
from scraper.pipeline import run_all
from scraper.storage import Storage


def test_end_to_end_with_change_detection(make_cfg, site):
    cfg = make_cfg()

    # first run: 3 pages x 2 items, all new, files exported
    first = run_all(cfg)[0]
    assert (first.status, first.pages, first.found, first.new) == ("ok", 3, 6, 6)
    for ext in ("csv", "json", "xlsx"):
        assert (cfg.export_dir / f"items.{ext}").exists()
    df = pd.read_csv(cfg.export_dir / "items.csv")
    assert len(df) == 6 and df.loc[0, "title"] == "Café 1"
    assert df.loc[0, "price"] == 10.0 and df.loc[0, "rating"] == 3
    assert df.loc[0, "url"].endswith("/item/1.html")
    assert json.loads((cfg.export_dir / "items.json").read_text(encoding="utf-8"))[0]["title"] == "Café 1"

    # second run, nothing changed
    again = run_all(cfg)[0]
    assert (again.new, again.updated, again.unchanged) == (0, 0, 6)

    # a price changes on the site -> exactly one update, and history keeps both prices
    write_site(site.root, {1: 10.0, 2: 99.0, 3: 30.0, 4: 40.0, 5: 50.0, 6: 60.0})
    changed = run_all(cfg)[0]
    assert (changed.new, changed.updated, changed.unchanged) == (0, 1, 5)
    with Storage(cfg.db_path) as s:
        hist = s.history_df("items")
        assert sorted(hist[hist.title == "Café 2"].price) == [20.0, 99.0]
        assert len(s.runs_df()) == 3


def test_partial_when_later_page_fails(make_cfg, site):
    (site.root / "page-3.html").unlink()
    res = run_all(make_cfg())[0]
    assert res.status == "partial" and res.pages == 2 and res.new == 4
    assert "404" in res.error


def test_empty_when_selectors_are_wrong(make_cfg):
    cfg = make_cfg()
    cfg.targets[0].item_selector = "div.does-not-exist"
    res = run_all(cfg)[0]
    assert res.status == "empty" and "selectors" in res.error


def test_url_template_pagination_stops_on_empty_page(make_cfg, site):
    cfg = make_cfg()
    t = cfg.targets[0]
    t.start_urls, t.next_page = [], None
    t.url_template, t.max_pages = site.url + "/page-{page}.html", 10
    res = run_all(cfg)[0]
    # page-4 does not exist (HTTP 404) - that is the normal end of pagination, not an error
    assert (res.status, res.pages, res.found) == ("ok", 3, 6)
