"""End-to-end orchestration: bills + CSV templates -> completed CSVs."""
from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from . import prompts
from .config import Settings
from .csvio import dump_json_one_line, read_template, write_csv
from .imaging import load_image_bytes
from .level1 import extract_level1
from .level2 import FALLBACK_ANSWER, answer_questions, extract_facts
from .llm import LLMClient
from .schema import L1_KEYS, empty_level1, provider_from_bill_id

log = logging.getLogger(__name__)
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
DEFAULT_QUESTION_BANK = [
    "How much of my bill is taxes?",
    "How many months in my history went above 200 units?",
    "What is the average cost per unit this month?",
]


@dataclass
class BillResult:
    bill_id: str
    level1: dict
    answers: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class Cache:
    """On-disk JSON cache so re-runs are free and a crash never loses finished work."""

    def __init__(self, directory: str, enabled: bool = True):
        self.dir, self.enabled = Path(directory), enabled
        if enabled:
            self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, *parts: str) -> Path:
        return self.dir / (hashlib.sha256("|".join(parts).encode()).hexdigest()[:40] + ".json")

    def get(self, *parts: str):
        p = self._path(*parts)
        if self.enabled and p.exists():
            try:
                 return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                 log.warning("Ignoring unreadable cache entry %s: %s", p.name, exc)
                 return None
        return None

    def put(self, value, *parts: str) -> None:
        if self.enabled:
            self._path(*parts).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _list_bill_ids(bills_dir: str | Path) -> list[str]:
    base = Path(bills_dir)
    if not base.is_dir():
        raise NotADirectoryError(f"bill image folder not found: {base}")
    return list(dict.fromkeys(
        p.stem for p in sorted(base.iterdir())
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    ))


def find_image(bills_dir: str | Path, bill_id: str) -> Path | None:
    base = Path(bills_dir)
    if not base.is_dir():
        return None
    for p in sorted(base.iterdir()):
        if p.stem == bill_id and p.suffix.lower() in IMAGE_EXTS:
            return p
    return None


def _validate_rows(path: str | Path, fields: list[str], rows: list[dict], kind: str) -> None:
    required = {"bill_id", "json"} if kind == "l1" else {
        "bill_id", "question_id", "question", "answer"
    }
    missing = sorted(required.difference(fields))
    if missing:
        raise ValueError(f"{path} is missing required column(s): {', '.join(missing)}")
    if not rows:
        raise ValueError(f"{path} contains no data rows")
    seen_questions: set[tuple[str, str]] = set()
    for line, row in enumerate(rows, 2):
        bill_id = (row.get("bill_id") or "").strip()
        if not bill_id:
            raise ValueError(f"{path}:{line}: bill_id is empty")
        if kind == "l2":
            qid = (row.get("question_id") or "").strip()
            question = (row.get("question") or "").strip()
            if not qid or not question:
                raise ValueError(f"{path}:{line}: question_id and question must not be empty")
            key = bill_id, qid
            if key in seen_questions:
                raise ValueError(f"{path}:{line}: duplicate question_id {qid!r} for {bill_id}")
            seen_questions.add(key)


def _read_or_build_template(path: str | Path | None, kind: str, bill_ids: list[str]):
    if path is not None:
        fields, rows = read_template(path)
        _validate_rows(path, fields, rows, kind)
        return fields, rows
    if kind == "l1":
        return ["bill_id", "json"], [{"bill_id": bill_id, "json": ""} for bill_id in bill_ids]
    fields = ["bill_id", "question_id", "question", "answer"]
    rows = [
        {"bill_id": bill_id, "question_id": f"Q{idx}", "question": question, "answer": ""}
        for bill_id in bill_ids
        for idx, question in enumerate(DEFAULT_QUESTION_BANK, 1)
    ]
    return fields, rows


def process_bill(client: LLMClient, settings: Settings, bill_id: str, image_path: Path | None,
                 questions: list[tuple[str, str]], cache: Cache) -> BillResult:
    """Run both levels for one bill. Every stage degrades gracefully; nothing is left empty."""
    result = BillResult(bill_id, empty_level1(provider_from_bill_id(bill_id)))
    if image_path is None:
        result.errors.append("bill image not found")
        result.answers = {q: FALLBACK_ANSWER for q, _ in questions}
        return result
    try:
        image = load_image_bytes(image_path, settings.max_image_side)
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"image unreadable: {exc}")
        result.answers = {q: FALLBACK_ANSWER for q, _ in questions}
        return result

    base = (bill_id, hashlib.sha256(image).hexdigest(), settings.provider, settings.model,
            prompts.PROMPT_VERSION)
    try:
        l1 = cache.get("l1", *base)
        if not isinstance(l1, dict) or not set(L1_KEYS).issubset(l1):
            l1 = extract_level1(client, image, bill_id)
            cache.put(l1, "l1", *base)
        result.level1 = l1
    except Exception as exc:  # noqa: BLE001
        log.error("%s: level 1 failed: %s", bill_id, exc)
        result.errors.append(f"level1: {exc}")

    if questions:
        try:
            facts = cache.get("facts", *base)
            if not isinstance(facts, dict):
                facts = extract_facts(client, image)
                cache.put(facts, "facts", *base)
        except Exception as exc:  # noqa: BLE001
            log.error("%s: facts extraction failed: %s", bill_id, exc)
            result.errors.append(f"facts: {exc}")
            facts = {}
        qkey = hashlib.sha256(json.dumps(questions).encode()).hexdigest()
        answers = cache.get("l2", qkey, *base) if facts else None
        if not isinstance(answers, dict) or any(
            not isinstance(answers.get(qid), str) or not answers[qid].strip()
            for qid, _ in questions
        ):
            answers = None
        if answers is None:
            answers = answer_questions(client, image, result.level1, facts, questions)
            if answers and all(
                answers.get(qid) != FALLBACK_ANSWER for qid, _ in questions
            ):
                cache.put(answers, "l2", qkey, *base)
        result.answers = answers
    return result


def run_batch(settings: Settings, bills_dir: str, l1_template: str | None, l2_template: str | None,
              out_dir: str, use_cache: bool = True, client: LLMClient | None = None) -> dict:
    client = client or LLMClient(settings)
    cache = Cache(settings.cache_dir, use_cache)
    image_ids = _list_bill_ids(bills_dir)
    if not image_ids:
        raise FileNotFoundError(f"no bill images found in {bills_dir}")
    l1_fields, l1_rows = _read_or_build_template(l1_template, "l1", image_ids)
    l2_fields, l2_rows = _read_or_build_template(l2_template, "l2", image_ids)

    bill_ids = list(dict.fromkeys([r["bill_id"].strip() for r in l1_rows] +
                                  [r["bill_id"].strip() for r in l2_rows]))
    questions: dict[str, list[tuple[str, str]]] = {b: [] for b in bill_ids}
    for r in l2_rows:
        questions[r["bill_id"].strip()].append((r["question_id"].strip(), r["question"].strip()))

    results: dict[str, BillResult] = {}
    with ThreadPoolExecutor(max_workers=settings.workers) as pool:
        futures = {pool.submit(process_bill, client, settings, b, find_image(bills_dir, b),
                                questions[b], cache): b for b in bill_ids}
        for done, fut in enumerate(as_completed(futures), 1):
            b = futures[fut]
            try:
                results[b] = fut.result()
            except Exception as exc:  # noqa: BLE001 - last-resort guard per bill
                log.exception("%s crashed", b)
                results[b] = BillResult(b, empty_level1(provider_from_bill_id(b)),
                                        {q: FALLBACK_ANSWER for q, _ in questions[b]}, [str(exc)])
            log.info("[%d/%d] finished %s %s", done, len(bill_ids), b,
                      f"(errors: {results[b].errors})" if results[b].errors else "")

    for row in l1_rows:
        row["json"] = dump_json_one_line(results[row["bill_id"].strip()].level1)
    for row in l2_rows:
        row["answer"] = results[row["bill_id"].strip()].answers.get(
            row["question_id"].strip()) or FALLBACK_ANSWER

    out = Path(out_dir)
    write_csv(out / "level1.csv", l1_fields, l1_rows)
    write_csv(out / "level2.csv", l2_fields, l2_rows)
    return {"results": results, "problems": validate_outputs(l1_rows, l2_rows)}


def validate_outputs(l1_rows: list[dict], l2_rows: list[dict]) -> list[str]:
    problems = []
    for r in l1_rows:
        try:
            json.loads(r["json"])
        except (json.JSONDecodeError, TypeError):
            problems.append(f"level1 {r['bill_id']}: invalid JSON")
    problems += [f"level2 {r['bill_id']} {r['question_id']}: empty answer"
                  for r in l2_rows if not (r.get("answer") or "").strip()]
    return problems
