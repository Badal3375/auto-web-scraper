"""SQLite storage with upsert, change detection and per-item history."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    target      TEXT NOT NULL,
    item_key    TEXT NOT NULL,
    data        TEXT NOT NULL,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (target, item_key)
);
CREATE TABLE IF NOT EXISTS history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target      TEXT NOT NULL,
    item_key    TEXT NOT NULL,
    data        TEXT NOT NULL,
    scraped_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_item ON history(target, item_key);
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT,
    pages       INTEGER DEFAULT 0,
    items_found INTEGER DEFAULT 0,
    new_items   INTEGER DEFAULT 0,
    updated_items   INTEGER DEFAULT 0,
    unchanged_items INTEGER DEFAULT 0,
    error       TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_key(row: dict[str, Any], key_fields: list[str]) -> str:
    basis = "|".join(str(row.get(k)) for k in key_fields) if key_fields else json.dumps(row, sort_keys=True, default=str)
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


class Storage:
    def __init__(self, db_path: Path):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.path = db_path
        self.conn = sqlite3.connect(db_path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # ------------------------------------------------------------------ writes
    def upsert(self, target: str, rows: list[dict[str, Any]], key_fields: list[str]) -> dict[str, int]:
        now = _now()
        stats = {"new": 0, "updated": 0, "unchanged": 0}
        cur = self.conn.cursor()
        for row in rows:
            key = make_key(row, key_fields)
            data = json.dumps(row, ensure_ascii=False)   # keeps config column order
            existing = cur.execute(
                "SELECT data FROM items WHERE target=? AND item_key=?", (target, key)).fetchone()
            if existing is None:
                cur.execute("INSERT INTO items VALUES (?,?,?,?,?,?)", (target, key, data, now, now, now))
                cur.execute("INSERT INTO history(target,item_key,data,scraped_at) VALUES (?,?,?,?)",
                            (target, key, data, now))
                stats["new"] += 1
            elif json.loads(existing["data"]) != row:
                cur.execute("UPDATE items SET data=?, last_seen=?, updated_at=? WHERE target=? AND item_key=?",
                            (data, now, now, target, key))
                cur.execute("INSERT INTO history(target,item_key,data,scraped_at) VALUES (?,?,?,?)",
                            (target, key, data, now))
                stats["updated"] += 1
            else:
                cur.execute("UPDATE items SET last_seen=? WHERE target=? AND item_key=?", (now, target, key))
                stats["unchanged"] += 1
        self.conn.commit()
        return stats

    def start_run(self, target: str) -> int:
        cur = self.conn.execute("INSERT INTO runs(target, started_at, status) VALUES (?,?,?)",
                                (target, _now(), "running"))
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, *, status: str, pages: int, found: int,
                   new: int, updated: int, unchanged: int, error: str | None) -> None:
        self.conn.execute(
            """UPDATE runs SET finished_at=?, status=?, pages=?, items_found=?, new_items=?,
               updated_items=?, unchanged_items=?, error=? WHERE id=?""",
            (_now(), status, pages, found, new, updated, unchanged, error, run_id))
        self.conn.commit()

    # ------------------------------------------------------------------ reads
    def targets(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT DISTINCT target FROM items ORDER BY target")]

    def load_df(self, target: str) -> pd.DataFrame:
        rows = self.conn.execute(
            "SELECT data, first_seen, last_seen FROM items WHERE target=? ORDER BY rowid", (target,)).fetchall()
        return pd.DataFrame([{**json.loads(r["data"]), "first_seen": r["first_seen"], "last_seen": r["last_seen"]}
                             for r in rows])

    def history_df(self, target: str) -> pd.DataFrame:
        rows = self.conn.execute(
            "SELECT data, scraped_at FROM history WHERE target=? ORDER BY id", (target,)).fetchall()
        return pd.DataFrame([{**json.loads(r["data"]), "scraped_at": r["scraped_at"]} for r in rows])

    def runs_df(self, limit: int = 100) -> pd.DataFrame:
        return pd.read_sql_query("SELECT * FROM runs ORDER BY id DESC LIMIT ?", self.conn, params=(limit,))

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
