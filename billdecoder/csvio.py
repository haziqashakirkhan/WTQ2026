"""CSV reading/writing that preserves template columns and row order (UTF-8, quoted)."""
from __future__ import annotations

import csv
import json
from pathlib import Path


def read_template(path: str | Path) -> tuple[list[str], list[dict]]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"template file not found: {p}")
    with open(p, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        if not fieldnames:
            raise ValueError(f"{p} is not a valid CSV template: missing header row")
        if any(not name or not name.strip() for name in fieldnames):
            raise ValueError(f"{p} is not a valid CSV template: blank column name")
        if len(set(fieldnames)) != len(fieldnames):
            raise ValueError(f"{p} is not a valid CSV template: duplicate column names")
        rows = []
        try:
            for line, row in enumerate(reader, 2):
                if None in row:
                    raise ValueError(f"{p}:{line}: too many values for the CSV columns")
                rows.append(dict(row))
        except csv.Error as exc:
            raise ValueError(f"{p} is not a valid CSV template: {exc}") from exc
        return fieldnames, rows


def write_csv(path: str | Path, fieldnames: list[str], rows: list[dict]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL,
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def dump_json_one_line(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
