"""Write the latest state of each target to CSV / JSON / Excel."""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd
from openpyxl.utils import get_column_letter

from .storage import Storage

log = logging.getLogger(__name__)


def _write_excel(df: pd.DataFrame, path: Path, sheet: str) -> None:
    sheet = re.sub(r"[\[\]\*\?/\\:]", "_", sheet)[:31] or "data"
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name=sheet)
        ws = xw.sheets[sheet]
        ws.freeze_panes = "A2"
        for i, col in enumerate(df.columns, start=1):
            longest = max([len(str(col))] + [len(str(v)) for v in df[col].head(200)])
            ws.column_dimensions[get_column_letter(i)].width = min(60, longest + 2)


def export_target(storage: Storage, target: str, export_dir: Path, formats: list[str]) -> list[Path]:
    df = storage.load_df(target)
    if df.empty:
        log.info("[%s] nothing to export yet", target)
        return []
    export_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for fmt in formats:
        path = export_dir / f"{target}.{fmt}"
        if fmt == "csv":
            df.to_csv(path, index=False, encoding="utf-8-sig")  # BOM so Excel shows £ / ₹ correctly
        elif fmt == "json":
            df.to_json(path, orient="records", force_ascii=False, indent=2)
        elif fmt == "xlsx":
            _write_excel(df, path, target)
        else:
            log.warning("Unknown export format %s", fmt)
            continue
        written.append(path)
    log.info("[%s] exported %d rows -> %s", target, len(df), ", ".join(p.name for p in written))
    return written
