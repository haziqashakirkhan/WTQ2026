"""Offline tests: no API key or network needed (LLM is replaced by a stub)."""
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from billdecoder import calc  # noqa: E402
from billdecoder.config import ConfigError, Settings, load_settings  # noqa: E402
from billdecoder.llm import extract_json  # noqa: E402
from billdecoder.pipeline import run_batch  # noqa: E402
from billdecoder.prompts import L1_EXAMPLE  # noqa: E402
from billdecoder.schema import check_consistency, normalize_level1, to_number  # noqa: E402


class StubClient:
    """Mimics LLMClient: returns canned JSON depending on which prompt is being used."""

    def complete_json(self, system, prompt, images=(), max_tokens=0):
        if "data-extraction engine" in system:
            return json.loads(L1_EXAMPLE)
        if "transcribe" in system:
            return {"consumption_history": [{"month": "2025-07", "units": 239},
                                            {"month": "2025-08", "units": 203},
                                            {"month": "2025-09", "units": 120}]}
        if "write small arithmetic" in system or "arithmetic expressions" in system:
            return {"Q1": {"interpretation": "tax share",
                           "calcs": {"share": "pct(total_taxes, current_bill)",
                                     "bad": "__import__('os')"}},
                    "Q2": {"interpretation": "months over 200",
                           "calcs": {"n": "count_above(history, 200)"}}}
        return {"Q1": "Taxes are about 16.6% of the bill.", "Q2": "Two months exceeded 200 units."}


class Tests(unittest.TestCase):
    def test_to_number(self):
        self.assertEqual(to_number("Rs. 19,964"), 19964)
        self.assertEqual(to_number("38209CR"), -38209)
        self.assertEqual(to_number("0.53 CR"), -0.53)
        self.assertEqual(to_number("(5.50)"), -5.5)
        self.assertIsNone(to_number("abc"))
        self.assertIsNone(to_number(None))
        self.assertIsNone(to_number(float("nan")))
        self.assertIsNone(to_number(float("inf")))

    def test_normalize_sample_is_stable_and_consistent(self):
        sample = json.loads(L1_EXAMPLE)
        out = normalize_level1(sample, "KE")
        self.assertEqual(out, sample)
        self.assertEqual(check_consistency(out), [])

    def test_normalize_cleans_bad_values(self):
        out = normalize_level1({"provider": "K-Electric", "bill_month": "MAR 2026",
                                "due_date": "22nd Sep 2026",
                                "charges": [{"type": "Subsidy", "amount": "100"},
                                            {"type": "weird", "amount": 5},
                                            {"type": "energy", "amount": 0}],
                                "taxes": [{"type": "x", "amount": "7"}]})
        self.assertEqual(out["provider"], "KE")
        self.assertEqual(out["bill_month"], "2026-03")
        self.assertEqual(out["due_date"], "2026-09-22")
        self.assertEqual(out["charges"], [{"type": "subsidy", "amount": -100},
                                          {"type": "other", "amount": 5}])
        self.assertEqual(out["taxes"], [{"type": "other_tax", "amount": 7}])
        self.assertIsNone(out["tariff"])

    def test_short_dates_from_lesco_iesco(self):
        out = normalize_level1({"reading_date": "17 JUL 26", "due_date": "08 OCT 26",
                                "payable_after_due_date": "NOT TO BE PAID",
                                "payable_within_due_date": "54770CR"})
        self.assertEqual((out["reading_date"], out["due_date"]), ("2026-07-17", "2026-10-08"))
        self.assertIsNone(out["payable_after_due_date"])
        self.assertEqual(out["payable_within_due_date"], -54770)

    def test_calc_is_safe_and_correct(self):
        env = {"history": [239, 203, 100], "a": 569.62, "b": 3430.24}
        self.assertEqual(calc.evaluate("count_above(history, 200)", env), 2)
        self.assertAlmostEqual(calc.evaluate("pct(a, b)", env), 16.6061, places=3)
        for evil in ("__import__('os')", "history.__class__", "open('x')", "[x for x in history]"):
            with self.assertRaises(calc.CalcError):
                calc.evaluate(evil, env)
        with self.assertRaises(calc.CalcError):
            calc.evaluate("1/0", env)
        for invalid in ("1e309", "True"):
            with self.subTest(invalid=invalid), self.assertRaises(calc.CalcError):
                calc.evaluate(invalid, env)

    def test_extract_json_handles_fences(self):
        self.assertEqual(extract_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(extract_json('Sure! {"a": 1} done'), {"a": 1})

    def test_config_rejects_placeholder_key(self):
        with patch.dict("os.environ", {
            "MODEL_PROVIDER": "google", "MODEL_NAME": "gemini-3.5-flash-lite",
            "API_KEY": "paste-your-aistudio-key-here",
        }):
            with self.assertRaises(ConfigError):
                load_settings()

    def test_config_rejects_unlisted_model(self):
        with patch.dict("os.environ", {
            "MODEL_PROVIDER": "google", "MODEL_NAME": "gemini-1.5-pro", "API_KEY": "k",
        }):
            with self.assertRaises(ConfigError):
                load_settings()

    def test_config_rejects_invalid_numeric_settings(self):
        for name, value in (("WORKERS", "zero"), ("MIN_REQUEST_INTERVAL", "nan"),
                            ("MAX_RETRIES", "-1"), ("MAX_IMAGE_SIDE", "0")):
            with self.subTest(name=name), patch.dict("os.environ", {
                "MODEL_PROVIDER": "google", "MODEL_NAME": "gemini-3.5-flash-lite",
                "API_KEY": "k", name: value,
            }):
                with self.assertRaises(ConfigError):
                    load_settings()

    def test_end_to_end_with_stub(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "bills").mkdir()
            Image.new("RGB", (300, 400), "white").save(tmp / "bills" / "KESC_0008.png")
            (tmp / "l1.csv").write_text("bill_id,json\nKESC_0008,\nLESCO_0001,\n", encoding="utf-8")
            (tmp / "l2.csv").write_text(
                "bill_id,question_id,question,answer\n"
                'KESC_0008,Q1,"How much, in %, is tax?",\nKESC_0008,Q2,Months over 200?,\n'
                "LESCO_0001,Q1,Missing image?,\nLESCO_0001,Q2,Another?,\n", encoding="utf-8")
            settings = Settings("google", "gemini-3.5-flash-lite", "k", cache_dir=str(tmp / "cache"))
            outcome = run_batch(settings, str(tmp / "bills"), str(tmp / "l1.csv"), str(tmp / "l2.csv"),
                                str(tmp / "out"), client=StubClient())
            self.assertEqual(outcome["problems"], [])
            with open(tmp / "out" / "level1.csv", encoding="utf-8") as fh:
                l1_text = fh.read()
            with open(tmp / "out" / "level1.csv", encoding="utf-8", newline="") as fh:
                l1 = list(csv.DictReader(fh))
            with open(tmp / "out" / "level2.csv", encoding="utf-8", newline="") as fh:
                l2 = list(csv.DictReader(fh))
            self.assertEqual([r["bill_id"] for r in l1], ["KESC_0008", "LESCO_0001"])
            self.assertEqual(json.loads(l1[0]["json"])["units_consumed"], 151)
            self.assertEqual(json.loads(l1[1]["json"])["provider"], "LESCO")  # graceful fallback
            self.assertEqual(l2[0]["question"], "How much, in %, is tax?")  # untouched, quoted
            self.assertIn("16.6%", l2[0]["answer"])
            self.assertTrue(all(r["answer"].strip() for r in l2))
            self.assertEqual(len(l1_text.strip().split("\n")), 3)  # header + one line per bill

    def test_default_templates_refresh_from_current_bill_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bills = tmp / "bills"
            bills.mkdir()
            image = Image.new("RGB", (300, 400), "white")
            image.save(bills / "KESC_0008.png")
            image.save(bills / "LESCO_0005.png")
            settings = Settings("google", "gemini-3.5-flash-lite", "k",
                                cache_dir=str(tmp / "cache"))

            run_batch(settings, str(bills), None, None, str(tmp / "out"), client=StubClient())
            with open(tmp / "out" / "level1.csv", encoding="utf-8", newline="") as fh:
                first_run = list(csv.DictReader(fh))
            self.assertEqual([r["bill_id"] for r in first_run], ["KESC_0008", "LESCO_0005"])
            self.assertEqual(json.loads(first_run[0]["json"])["provider"], "KE")
            self.assertEqual(json.loads(first_run[1]["json"])["provider"], "LESCO")

            image.save(bills / "IESCO_0001.png")
            run_batch(settings, str(bills), None, None, str(tmp / "out"), client=StubClient())
            with open(tmp / "out" / "level1.csv", encoding="utf-8", newline="") as fh:
                second_run = list(csv.DictReader(fh))
            with open(tmp / "out" / "level2.csv", encoding="utf-8", newline="") as fh:
                questions = list(csv.DictReader(fh))
            second_by_id = {r["bill_id"]: json.loads(r["json"]) for r in second_run}
            self.assertEqual(len(second_by_id), 3)
            self.assertEqual(len(questions), 9)
            self.assertEqual(second_by_id["IESCO_0001"]["provider"], "IESCO")

    def test_invalid_template_reports_missing_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bills = tmp / "bills"
            bills.mkdir()
            Image.new("RGB", (300, 400), "white").save(bills / "KESC_0008.png")
            l1 = tmp / "l1.csv"
            l2 = tmp / "l2.csv"
            l1.write_text("identifier,json\nKESC_0008,\n", encoding="utf-8")
            l2.write_text("bill_id,question_id,question,answer\n"
                          "KESC_0008,Q1,Test?,\n", encoding="utf-8")
            settings = Settings("google", "gemini-3.5-flash-lite", "k",
                                cache_dir=str(tmp / "cache"))
            with self.assertRaisesRegex(ValueError, "bill_id"):
                run_batch(settings, str(bills), str(l1), str(l2), str(tmp / "out"),
                          client=StubClient())


if __name__ == "__main__":
    unittest.main()