"""Bridge between the web UI and the existing billdecoder pipeline.

Reuses extract_level1 / extract_facts / answer_questions exactly as the CLI
`demo` command does. No pipeline logic is duplicated or changed here.
"""
import re
import sys
import threading
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from billdecoder.config import load_settings          # noqa: E402
from billdecoder.imaging import load_image_bytes      # noqa: E402
from billdecoder.level1 import extract_level1         # noqa: E402
from billdecoder.level2 import answer_questions, extract_facts  # noqa: E402
from billdecoder.llm import LLMClient                 # noqa: E402

_lock = threading.Lock()
_client = None
_settings = None
_bills: dict[str, dict] = {}   # str(path) -> {"image", "l1", "facts"}


def _get_client():
    global _client, _settings
    with _lock:
        if _client is None:
            _settings = load_settings()          # raises ConfigError with a readable message
            _client = LLMClient(_settings)
        return _client, _settings


def _entry(path: Path) -> dict:
    return _bills.setdefault(str(path), {})


def extract(image_path: Path, bill_id: str | None = None) -> dict:
    """Level 1 JSON for one bill. bill_id is the ORIGINAL file stem (e.g. KESC_0008),
    which the pipeline uses as a deterministic provider hint."""
    client, settings = _get_client()
    e = _entry(image_path)
    if "l1" not in e:
        e["image"] = load_image_bytes(image_path, settings.max_image_side)
        e["l1"] = extract_level1(client, e["image"], bill_id or image_path.stem)
    return e["l1"]


def _facts(image_path: Path) -> dict:
    client, settings = _get_client()
    e = _entry(image_path)
    if "image" not in e:
        e["image"] = load_image_bytes(image_path, settings.max_image_side)
    if "facts" not in e:
        e["facts"] = extract_facts(client, e["image"])
    return e["facts"]


def answer(image_path: Path, questions: list[str]) -> list[str]:
    client, _ = _get_client()
    l1 = extract(image_path)
    facts = _facts(image_path)
    qs = [(f"Q{i}", q) for i, q in enumerate(questions, 1)]
    out = answer_questions(client, _entry(image_path)["image"], l1, facts, qs)
    return [out[qid] for qid, _ in qs]


_FORMATS = ("%Y-%m", "%b%y", "%b-%y", "%b %y", "%b %Y", "%B %Y", "%b-%Y", "%m/%y", "%m/%Y")


def _to_ym(label) -> str | None:
    s = str(label).strip()
    if re.fullmatch(r"\d{4}-\d{2}", s):
        return s
    for fmt in _FORMATS:
        try:
            return datetime.strptime(s.title() if "%b" in fmt or "%B" in fmt else s, fmt).strftime("%Y-%m")
        except ValueError:
            continue
    return None


def history(image_path: Path) -> list[dict]:
    """Usage history transcribed from the bill, oldest first: [{"month","units"}]."""
    rows = _facts(image_path).get("consumption_history") or []
    seen: dict[str, float] = {}
    for r in rows:
        if not isinstance(r, dict) or not isinstance(r.get("units"), (int, float)):
            continue
        ym = _to_ym(r.get("month"))
        if ym and ym not in seen:
            seen[ym] = r["units"]
    return [{"month": m, "units": u} for m, u in sorted(seen.items())]
