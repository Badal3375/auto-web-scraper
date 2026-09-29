# AutoScraper - end-to-end web scraping pipeline

Describe a site in `config.yaml` (URL + CSS selectors) and AutoScraper does the rest:

```
fetch  ->  parse  ->  clean  ->  store (SQLite)  ->  export (CSV/XLSX/JSON)  ->  schedule  ->  dashboard
```

**What you get**

| Stage | What it does |
|---|---|
| Fetch | Rate limiting with jitter, retries with exponential backoff (429/5xx/timeouts), `Retry-After`, robots.txt, proxy support, correct UTF-8 decoding (`£`, `₹`, `é`), optional headless Chromium for JavaScript sites |
| Parse | Selector-driven extraction, next-link *or* `{page}` URL-template pagination, multiple start URLs, typed fields (`price`, `int`, `float`, `rating`, `date`), regex post-filters, defaults |
| Clean | Whitespace/NBSP normalisation, ISO dates, de-duplication on the keys you choose |
| Store | SQLite upsert with **new / updated / unchanged** detection and a per-item **history** table (price tracking for free) |
| Export | `data/exports/<target>.csv / .xlsx / .json` (Excel-safe UTF-8, frozen header, auto-width) |
| Automate | Built-in scheduler (interval or cron), GitHub Actions workflow, Dockerfile |
| Monitor | Run log table, rotating log file, webhook alert (Slack/Discord) on `failed` / `empty` / `partial` runs, non-zero exit code for CI, Streamlit dashboard |

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python main.py preview -t books     # test selectors on page 1, saves nothing
python main.py run                  # scrape every target -> data/demo.db + data/exports/
python main.py stats                # totals + recent runs
python main.py schedule             # run now, then keep running (see `schedule:` in config.yaml)
```

Dashboard (optional): `pip install streamlit` then `python -m streamlit run dashboard/app.py`

Tests: `pip install pytest` then `python -m pytest` (uses a local mock site, no internet needed).

## Scrape your own site

1. Open the page in Chrome -> right-click an item -> *Inspect* to find the repeating element (`item_selector`) and the selectors for each field inside it.
2. Copy a block under `targets:` in `config.yaml` and edit it.
3. `python main.py preview -t your_target` until the printed rows look right.
4. `python main.py run`.

Field options: `selector` (omit to use the item itself), `attr` (read an attribute such as `href`, `src`, `class` instead of text), `type` (`text|int|float|price|rating|date`), `absolute` (make URLs absolute), `regex` (keep group 1 / the match), `multiple` (join all matches with ` | `), `default`.

Pagination: `next_page: {selector: "li.next a"}` follows a link; `url_template: "https://site/list?page={page}"` counts pages and stops at the first empty page or 404. `max_pages` caps both. Use `start_urls:` (list) to crawl several categories.

JavaScript-rendered pages (preview prints 0 items but you can see them in the browser):
```bash
pip install playwright && playwright install chromium
```
then set `use_playwright: true` under `request:` (all targets) or on a single target, plus `wait_selector` for the element to wait for.

Numbers are parsed assuming `1,234.56` style. For European `1.234,56` add a `regex`, or extend `to_number` in `scraper/parser.py`.

## Run it unattended

* **Built-in scheduler:** `python main.py schedule` (interval or `cron:` in config).
* **Cron / Task Scheduler:** `0 8 * * * cd /path/to/auto-web-scraper && .venv/bin/python main.py run`
* **GitHub Actions (free):** push this folder to a repo; `.github/workflows/scrape.yml` runs daily and commits `data/*.db` back so change detection persists. Add repo secret `SCRAPER_WEBHOOK_URL` for alerts.
* **Docker:** `docker build -t auto-web-scraper . && docker run -d -v "$(pwd)/data:/app/data" auto-web-scraper`

## Reading the results

* Files: `data/exports/<target>.csv|xlsx|json` (latest state).
* SQL: `sqlite3 data/demo.db` -> tables `items` (current state, JSON in `data`), `history` (every change), `runs` (audit log).
* Python: `Storage("data/demo.db").load_df("books")` returns a DataFrame; `.history_df("books")` gives price-over-time data.

## Project layout

```
main.py              CLI (run | preview | schedule | export | stats)
config.yaml          everything you customise
scraper/
  config.py          YAML -> validated dataclasses
  fetcher.py         requests + retries + robots.txt + Playwright
  parser.py          selectors -> typed rows, pagination
  cleaner.py         normalise + de-duplicate
  storage.py         SQLite upsert, history, run log
  exporter.py        CSV / JSON / XLSX
  pipeline.py        orchestration, status, alerts
  scheduler.py       APScheduler wrapper
dashboard/app.py     Streamlit UI
tests/               pytest suite with a local mock website
```

## Scrape responsibly

Only scrape data you are allowed to collect: read the site's Terms of Service, keep `respect_robots: true`, keep the delay generous, identify yourself in `user_agent`, and prefer an official API when one exists. Don't collect personal data without a lawful basis. The two demo targets are sandbox sites built for practice.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Status `empty` / preview shows 0 items | Selector wrong, or page is JavaScript-rendered (`use_playwright: true`) |
| `Blocked by robots.txt` | The site disallows that path for bots. Respect it, or ask the owner |
| Many `HTTP 403/429` | Increase `delay_seconds`, set a real `user_agent`; a 403 that persists means the site doesn't want automated access |
| Garbled `Â£` characters | Shouldn't happen (auto-fixed); if a site lies about its encoding, set `headers` or report it |
| Rows keep showing as `updated` | A field changes every scrape (timestamp, ad text). Remove it from `fields` |
