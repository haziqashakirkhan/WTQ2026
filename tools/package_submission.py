"""Validate outputs and assemble the WTQ submission archive."""
from __future__ import annotations

import csv
import json
import sys
import zipfile
from datetime import datetime
from pathlib import Path


def _project_root() -> Path:
    script = Path(__file__).resolve()
    for parent in script.parents:
        if (parent / "output").is_dir():
            return parent
    return script.parents[1]


ROOT = _project_root()
MAX_BYTES = 15 * 1024 * 1024
SKIP_PARTS = {".cache", "__pycache__", ".git", ".venv", "venv"}
LEVEL1_FIELDS = (
    "provider", "tariff", "sanctioned_load_kw", "bill_month", "reading_date",
    "issue_date", "due_date", "previous_reading", "current_reading", "units_consumed",
    "charges", "total_charges", "taxes", "total_taxes", "current_bill", "arrears",
    "payable_within_due_date", "payable_after_due_date",
)
CHARGE_TYPES = {
    "energy", "fixed", "fpa", "quarterly_adjustment", "surcharge", "meter_rent",
    "subsidy", "other",
}
TAX_TYPES = {"gst", "electricity_duty", "income_tax", "municipal_tax", "other_tax"}
SOURCE_FILES = (
    "README.md", ".env.example", "requirements.txt", "run.py", "server.py", "adapter.py",
)
SOURCE_DIRS = ("billdecoder", "tools", "tests", "static", "templates")


def _latest_templates() -> tuple[Path | None, Path | None]:
    candidates = sorted(ROOT.glob("csv-*/csv"))
    if not candidates:
        return None, None
    folder = candidates[-1]
    return folder / "level1.csv", folder / "level2.csv"


def _read_csv(path: Path, expected_columns: list[str], errs: list[str]) -> list[dict]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames != expected_columns:
                errs.append(f"{path.relative_to(ROOT)} must have columns {expected_columns}")
                return []
            rows = []
            for line, row in enumerate(reader, 2):
                if None in row:
                    errs.append(f"{path.relative_to(ROOT)}:{line}: extra CSV values")
                rows.append(row)
            return rows
    except (OSError, UnicodeError, csv.Error) as exc:
        errs.append(f"cannot read {path.relative_to(ROOT)}: {exc}")
        return []


def _valid_number(value) -> bool:
    return value is None or (isinstance(value, (int, float)) and not isinstance(value, bool))


def _valid_date(value, fmt: str) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    try:
        datetime.strptime(value, fmt)
        return True
    except ValueError:
        return False


def _check_level1(rows: list[dict], errs: list[str]) -> None:
    if len(rows) != 10:
        errs.append(f"level1.csv has {len(rows)} rows, expected 10")
    for row in rows:
        bill_id = row.get("bill_id") or "<empty bill_id>"
        raw = row.get("json") or ""
        if not raw.strip():
            errs.append(f"level1 {bill_id}: empty JSON")
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            errs.append(f"level1 {bill_id}: invalid JSON ({exc})")
            continue
        if not isinstance(data, dict) or set(data) != set(LEVEL1_FIELDS):
            errs.append(f"level1 {bill_id}: JSON does not match the required schema")
            continue
        if data["provider"] not in {"KE", "LESCO", "IESCO", None}:
            errs.append(f"level1 {bill_id}: invalid provider")
        for key in LEVEL1_FIELDS:
            value = data[key]
            if key in {"charges", "taxes"}:
                allowed = CHARGE_TYPES if key == "charges" else TAX_TYPES
                if not isinstance(value, list) or any(
                    not isinstance(item, dict)
                    or set(item) != {"type", "amount"}
                    or item.get("type") not in allowed
                    or not _valid_number(item.get("amount"))
                    for item in value
                ):
                    errs.append(f"level1 {bill_id}: invalid {key} entries")
            elif key in {"bill_month", "reading_date", "issue_date", "due_date"}:
                fmt = "%Y-%m" if key == "bill_month" else "%Y-%m-%d"
                if not _valid_date(value, fmt):
                    errs.append(f"level1 {bill_id}: invalid {key}")
            elif key not in {"provider", "tariff"} and not _valid_number(value):
                errs.append(f"level1 {bill_id}: invalid numeric field {key}")


def _check_template_preserved(output_rows: list[dict], template_path: Path | None,
                              expected_columns: tuple[str, ...], preserved_columns: tuple[str, ...],
                              errs: list[str]) -> None:
    if template_path is None or not template_path.is_file():
        errs.append("cannot find the supplied test CSV template to verify row preservation")
        return
    template_rows = _read_csv(template_path, list(expected_columns), errs)
    if len(output_rows) != len(template_rows) or any(
        tuple(output.get(column) for column in preserved_columns)
        != tuple(template.get(column) for column in preserved_columns)
        for output, template in zip(output_rows, template_rows)
    ):
        errs.append(f"{template_path.name}: output changed template rows, values, or order")


def check_outputs() -> list[str]:
    errs: list[str] = []
    l1_path, l2_path = ROOT / "output" / "level1.csv", ROOT / "output" / "level2.csv"
    missing = [path for path in (l1_path, l2_path) if not path.is_file()]
    if missing:
        return [f"missing {path.relative_to(ROOT)} - run the pipeline first" for path in missing]

    rows1 = _read_csv(l1_path, ["bill_id", "json"], errs)
    rows2 = _read_csv(l2_path, ["bill_id", "question_id", "question", "answer"], errs)
    _check_level1(rows1, errs)
    if len(rows2) != 120:
        errs.append(f"level2.csv has {len(rows2)} rows, expected 120")
    if any(not (row.get("answer") or "").strip() for row in rows2):
        errs.append("level2.csv contains one or more empty answers")
    counts: dict[str, int] = {}
    for row in rows2:
        bill_id = (row.get("bill_id") or "").strip()
        qid = (row.get("question_id") or "").strip()
        question = (row.get("question") or "").strip()
        if not bill_id or not qid or not question:
            errs.append("level2.csv contains an empty bill_id, question_id, or question")
        counts[bill_id] = counts.get(bill_id, 0) + 1
    if len(counts) != 10 or any(count != 12 for count in counts.values()):
        errs.append("level2.csv must contain 12 questions for each of 10 bills")

    template1, template2 = _latest_templates()
    _check_template_preserved(
        rows1, template1, ("bill_id", "json"), ("bill_id",), errs
    )
    _check_template_preserved(
        rows2, template2,
        ("bill_id", "question_id", "question", "answer"),
        ("bill_id", "question_id", "question"), errs,
    )
    return errs


def _source_paths() -> list[tuple[Path, str]]:
    paths: list[tuple[Path, str]] = []
    for name in SOURCE_FILES:
        path = ROOT / name
        if not path.is_file():
            raise FileNotFoundError(f"required source file missing: {name}")
        paths.append((path, f"source/{name}"))
    for directory in SOURCE_DIRS:
        base = ROOT / directory
        if not base.is_dir():
            raise FileNotFoundError(f"required source directory missing: {directory}/")
        for path in sorted(base.rglob("*")):
            if path.is_file() and not (SKIP_PARTS & set(path.parts)) and path.name != ".env":
                paths.append((path, str(Path("source") / path.relative_to(ROOT))))
    return paths


def main() -> int:
    errs = check_outputs()
    if errs:
        print("\n".join("ERROR: " + err for err in errs))
        return 1

    try:
        source_paths = _source_paths()
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        return 1

    target = ROOT / "submission.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("output/level1.csv", "output/level2.csv"):
            archive.write(ROOT / name, name)
        for path, archive_name in source_paths:
            archive.write(path, archive_name)
        demo = ROOT / "demo"
        if demo.is_dir():
            for path in sorted(demo.rglob("*")):
                if path.is_file() and not (SKIP_PARTS & set(path.parts)) and path.name != ".env":
                    archive.write(path, str(path.relative_to(ROOT)))

    size = target.stat().st_size
    if size > MAX_BYTES:
        print(f"ERROR: submission.zip is {size / 1024 / 1024:.2f} MB; limit is 15 MB")
        return 1
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
    print(f"Created {target} ({size / 1024 / 1024:.2f} MB)")
    print("Contents:\n  " + "\n  ".join(names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
