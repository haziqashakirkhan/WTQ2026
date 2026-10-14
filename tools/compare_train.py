"""Run Level 1 on the training bills and diff against tests/train_reference.json.

    python tools/compare_train.py ../train/bills
Scoring mirrors the guide: text/dates exact, numbers within +/-1.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from billdecoder.config import load_settings  # noqa: E402
from billdecoder.imaging import load_image_bytes  # noqa: E402
from billdecoder.level1 import extract_level1  # noqa: E402
from billdecoder.llm import LLMClient  # noqa: E402


def same(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(a - b) <= 1
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    return a == b


def main(folder: str) -> int:
    settings = load_settings()
    client = LLMClient(settings)
    ref = json.load(open(ROOT / "tests" / "train_reference.json"))["bills"]
    hit = total = 0
    for bill_id, expected in ref.items():
        img = next((p for p in Path(folder).glob(bill_id + ".*")), None)
        if not img:
            print(f"{bill_id}: image not found"); continue
        got = extract_level1(client, load_image_bytes(img, settings.max_image_side), bill_id)
        print(f"\n== {bill_id}")
        for k, v in expected.items():
            total += 1
            if same(got.get(k), v):
                hit += 1
            else:
                print(f"  {k}: expected {v!r}\n  {' ' * len(k)}  got      {got.get(k)!r}")
    print(f"\nField match: {hit}/{total} ({100 * hit / max(total, 1):.0f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "../train/bills"))
