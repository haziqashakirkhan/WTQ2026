"""Command line interface.

  python run.py run   --bills test/bills --level1 test/csv/level1.csv --level2 test/csv/level2.csv --out output
  python run.py demo  path/to/bill.png [-q "question" ...]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from .config import ConfigError, load_settings
from .imaging import load_image_bytes
from .level1 import extract_level1
from .level2 import answer_questions, extract_facts
from .llm import LLMClient
from .pipeline import run_batch

DEFAULT_DEMO_QUESTIONS = [
    "How much of my bill is taxes?",
    "How many months in my history went above 200 units?",
    "What is the average cost per unit this month?",
]


def _default_bills_dir() -> str:
    root = Path(__file__).resolve().parent.parent
    for candidate in (root / "bills", root / "test" / "bills", root / "train" / "bills"):
        if candidate.is_dir():
            return str(candidate)
    dated = sorted(root.glob("bills-*/bills"))
    if dated:
        return str(dated[-1])
    ignored = {".venv", "venv", ".git", ".cache", "__pycache__", "output"}
    for current, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in ignored]
        if Path(current).name == "bills" and any(
            Path(name).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} for name in files
        ):
            return current
    return str(root / "bills")


def _default_output_dir() -> str:
    return str(Path(__file__).resolve().parent.parent / "output")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="billdecoder", description="Utility Bill Decoder")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="process a folder of bills and write level1.csv / level2.csv")
    r.add_argument("--bills", default=_default_bills_dir(), help="folder with bill images")
    r.add_argument("--level1", help="level 1 CSV template (generated from bill images if omitted)")
    r.add_argument("--level2", help="level 2 CSV template (generated if omitted)")
    r.add_argument("--out", default=_default_output_dir(), help="output folder for the completed CSVs")
    r.add_argument("--no-cache", action="store_true", help="ignore and do not write the cache")
    d = sub.add_parser("demo", help="decode one bill and print Level 1 JSON + sample answers")
    d.add_argument("bill", help="path to a bill image")
    d.add_argument("-q", "--question", action="append", help="question (repeatable)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S", stream=sys.stderr)
    for noisy in ("google_genai", "google.genai", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    print(f"Using {settings.provider}:{settings.model}", file=sys.stderr)

    if args.command == "run":
        try:
            outcome = run_batch(settings, args.bills, args.level1, args.level2, args.out,
                                use_cache=not args.no_cache)
        except (OSError, ValueError) as exc:
            print(f"Run error: {exc}", file=sys.stderr)
            return 2
        n = len(outcome["results"])
        print(f"Done: {n} bills processed. Files written to {args.out}/")
        for problem in outcome["problems"]:
            print("PROBLEM:", problem, file=sys.stderr)
        return 1 if outcome["problems"] else 0

    client = LLMClient(settings)
    try:
        image = load_image_bytes(args.bill, settings.max_image_side)
        l1 = extract_level1(client, image, Path(args.bill).stem)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Demo error: {exc}", file=sys.stderr)
        return 1
    print("\n=== Level 1: extracted fields ===")
    print(json.dumps(l1, indent=2, ensure_ascii=False))
    questions = [(f"Q{i}", q) for i, q in enumerate(args.question or DEFAULT_DEMO_QUESTIONS, 1)]
    try:
        facts = extract_facts(client, image)
    except RuntimeError as exc:
        print(f"Facts extraction error: {exc}", file=sys.stderr)
        return 1
    answers = answer_questions(client, image, l1, facts, questions)
    print("\n=== Level 2: answers ===")
    for qid, text in questions:
        print(f"\n{qid}: {text}\n -> {answers[qid]}")
    return 0