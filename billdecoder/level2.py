"""Level 2: answer customer questions with code-verified arithmetic.

Pipeline per bill (2 LLM calls for all questions + 1 facts call):
  facts   -> full transcription of the bill (history, line labels, notes)
  plan    -> model writes arithmetic expressions per question
  compute -> expressions evaluated safely in code (calc.py)
  answer  -> model writes final English answers using the exact results
"""
from __future__ import annotations

import json
import logging
import re

from . import calc, prompts
from .llm import LLMClient

log = logging.getLogger(__name__)

FALLBACK_ANSWER = ("I could not read enough of this bill to answer this question reliably, so I "
                   "cannot give a figure without guessing.")
_ID_PATTERNS = (re.compile(r"\b\d{5}-\d{7}-\d\b"), re.compile(r"\b\d{10,}\b"))


def scrub(text: str) -> str:
    for pat in _ID_PATTERNS:
        text = pat.sub("[redacted]", text)
    return text


def extract_facts(client: LLMClient, image: bytes) -> dict:
    return client.complete_json(prompts.FACTS_SYSTEM, prompts.FACTS_USER, [image], max_tokens=4000)


def build_env(l1: dict, facts: dict):
    """Variables and helper functions exposed to model-written expressions."""
    env: dict = {k: v for k, v in l1.items() if isinstance(v, (int, float)) and v is not None}
    env["charges"] = [c["amount"] for c in l1.get("charges", [])]
    env["taxes"] = [t["amount"] for t in l1.get("taxes", [])]
    rows = facts.get("consumption_history") if isinstance(facts, dict) else None
    rows = rows if isinstance(rows, list) else []
    env["history"] = [r["units"] for r in rows if isinstance(r, dict) and isinstance(r.get("units"), (int, float))]
    amounts = [r["bill_amount"] for r in rows if isinstance(r, dict) and isinstance(r.get("bill_amount"), (int, float))]
    if amounts:
        env["history_amounts"] = amounts
    try:
        from datetime import date
        a, b = (date.fromisoformat(l1[k]) for k in ("issue_date", "due_date"))
        env["days_to_due"] = (b - a).days
    except (KeyError, TypeError, ValueError):
        pass
    funcs = {
        "charge": lambda t: sum(c["amount"] for c in l1.get("charges", []) if c["type"] == t),
        "tax": lambda t: sum(x["amount"] for x in l1.get("taxes", []) if x["type"] == t),
    }
    return env, funcs


def compute_plan(plan: dict, env: dict, funcs: dict, qids: list[str]) -> dict:
    results: dict = {}
    for qid in qids:
        entry = plan.get(qid) if isinstance(plan, dict) else None
        calcs = entry.get("calcs", {}) if isinstance(entry, dict) else {}
        out = {}
        for name, expr in (calcs.items() if isinstance(calcs, dict) else []):
            try:
                out[name] = {"expression": expr, "value": calc.evaluate(expr, env, funcs)}
            except calc.CalcError as exc:
                out[name] = {"expression": expr, "error": str(exc)}
        results[qid] = {
            "interpretation": entry.get("interpretation", "") if isinstance(entry, dict) else "",
            "results": out,
        }
    return results


def answer_questions(client: LLMClient, image: bytes, l1: dict, facts: dict,
                     questions: list[tuple[str, str]]) -> dict[str, str]:
    """questions: [(question_id, text)] -> {question_id: answer}. Never raises."""
    qids = [q for q, _ in questions]
    context = json.dumps({"level1": l1, "facts": facts}, ensure_ascii=False)
    qtext = "\n".join(f"{qid}: {text}" for qid, text in questions)
    answers: dict[str, str] = {}
    try:
        plan = client.complete_json(
            prompts.PLAN_SYSTEM, f"BILL DATA:\n{context}\n\nQUESTIONS:\n{qtext}", [image], max_tokens=4000)
        env, funcs = build_env(l1, facts)
        computed = compute_plan(plan, env, funcs, qids)
        raw = client.complete_json(
            prompts.ANSWER_SYSTEM,
            f"BILL DATA:\n{context}\n\nQUESTIONS:\n{qtext}\n\nEXACT CALCULATION RESULTS "
            f"(per question):\n{json.dumps(computed, ensure_ascii=False)}",
            [image], max_tokens=6000)
        for qid in qids:
            val = raw.get(qid)
            if isinstance(val, str) and val.strip():
                answers[qid] = scrub(val.strip())
    except Exception as exc:  # noqa: BLE001
        log.error("batch answering failed: %s", exc)

    missing = [(q, t) for q, t in questions if q not in answers]
    if missing and len(missing) < len(questions):  # partial success: retry the gaps one by one
        for q, t in missing:
            answers.update(answer_questions(client, image, l1, facts, [(q, t)]))
    for qid in qids:
        answers.setdefault(qid, FALLBACK_ANSWER)
    return answers
