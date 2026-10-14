"""Level 1 schema: deterministic normalisation and validation of model output.

Nothing here recalculates or "corrects" bill amounts; it only coerces types,
enforces enums, drops zero lines and guarantees every required key exists.
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any

CHARGE_TYPES = ("energy", "fixed", "fpa", "quarterly_adjustment", "surcharge",
                "meter_rent", "subsidy", "other")
TAX_TYPES = ("gst", "electricity_duty", "income_tax", "municipal_tax", "other_tax")
L1_KEYS = ("provider", "tariff", "sanctioned_load_kw", "bill_month", "reading_date",
           "issue_date", "due_date", "previous_reading", "current_reading", "units_consumed",
           "charges", "total_charges", "taxes", "total_taxes", "current_bill", "arrears",
           "payable_within_due_date", "payable_after_due_date")

_PROVIDER_ALIASES = {"KE": "KE", "KESC": "KE", "K-ELECTRIC": "KE", "K ELECTRIC": "KE",
                     "KELECTRIC": "KE", "LESCO": "LESCO", "IESCO": "IESCO"}
_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d-%b-%Y", "%d-%b-%y",
                 "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%d-%B-%Y", "%d/%m/%y", "%d %b %y", "%d %B %y", "%d-%b %y")
_MONTH_FORMATS = ("%Y-%m", "%b %Y", "%B %Y", "%b-%y", "%b-%Y", "%B-%Y", "%m/%Y", "%b %y")


def normalize_provider(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    key = value.strip().upper()
    if key in _PROVIDER_ALIASES:
        return _PROVIDER_ALIASES[key]
    for alias, canon in _PROVIDER_ALIASES.items():
        if alias in key:
            return canon
    return None


def provider_from_bill_id(bill_id: str) -> str | None:
    return normalize_provider(bill_id.split("_")[0])


def to_number(value: Any) -> int | float | None:
    """Coerce '1,234.5', 'Rs. 19,964', '38209CR', '(5.00)' ... to a signed number."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            num = float(value)
        except OverflowError:
            return None
    elif isinstance(value, str):
        s = value.strip()
        if not s or s.lower() in {"null", "none", "n/a", "na", "-"}:
            return None
        negative = bool(re.search(r"cr\.?\s*$", s, re.I)) or (s.startswith("(") and s.endswith(")"))
        negative = negative or s.startswith("-") or s.endswith("-")
        match = re.search(r"\d[\d,]*(?:\.\d+)?|\.\d+", s)  # first real number; skips "Rs."
        if not match:
            return None
        try:
            num = float(match.group().replace(",", ""))
        except (ValueError, OverflowError):
            return None
        num = -num if negative else num
    else:
        return None
    if not math.isfinite(num):
        return None
    return int(num) if num == int(num) else num


def _strip_ordinals(s: str) -> str:
    return re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s.strip(), flags=re.I)


def normalize_date(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    s = _strip_ordinals(value)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def normalize_month(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    s = _strip_ordinals(value)
    for fmt in _MONTH_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m")
        except ValueError:
            continue
    full = normalize_date(s)
    return full[:7] if full else None


def _normalize_lines(items: Any, allowed: tuple[str, ...], default: str, subsidy_negative: bool):
    out: list[dict] = []
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        amount = to_number(item.get("amount"))
        if amount is None or amount == 0:  # blank / printed-as-0 lines are skipped
            continue
        kind = str(item.get("type", "")).strip().lower()
        kind = kind if kind in allowed else default
        if subsidy_negative and kind == "subsidy" and amount > 0:
            amount = -amount
        out.append({"type": kind, "amount": amount})
    return out


def empty_level1(provider: str | None = None) -> dict:
    data: dict[str, Any] = {k: None for k in L1_KEYS}
    data["provider"] = provider
    data["charges"], data["taxes"] = [], []
    return data


def normalize_level1(raw: dict, fallback_provider: str | None = None) -> dict:
    """Return a dict with every required key, correct types and enum values."""
    raw = raw if isinstance(raw, dict) else {}
    out = empty_level1()
    # The bill-id prefix (KESC/LESCO/IESCO) is part of the supplied input and is deterministic.
    out["provider"] = fallback_provider or normalize_provider(raw.get("provider"))
    tariff = raw.get("tariff")
    out["tariff"] = str(tariff).strip() if tariff not in (None, "") else None
    out["bill_month"] = normalize_month(raw.get("bill_month"))
    for key in ("reading_date", "issue_date", "due_date"):
        out[key] = normalize_date(raw.get(key))
    for key in ("sanctioned_load_kw", "previous_reading", "current_reading", "units_consumed",
                "total_charges", "total_taxes", "current_bill", "arrears",
                "payable_within_due_date", "payable_after_due_date"):
        out[key] = to_number(raw.get(key))
    out["charges"] = _normalize_lines(raw.get("charges"), CHARGE_TYPES, "other", True)
    out["taxes"] = _normalize_lines(raw.get("taxes"), TAX_TYPES, "other_tax", False)
    return out


def check_consistency(l1: dict) -> list[str]:
    """Flag likely misreads: line items that do not add up to the printed subtotal."""
    issues = []
    for lines, total, name in ((l1["charges"], l1["total_charges"], "charges"),
                               (l1["taxes"], l1["total_taxes"], "taxes")):
        if total is None or not lines:
            continue
        s = sum(x["amount"] for x in lines)
        if abs(s - total) > max(2.0, 0.005 * abs(total)):
            issues.append(f"The {name} entries add up to {s:.2f} but the printed {name} subtotal "
                          f"is {total}. A line may be missed, duplicated, misread or placed in "
                          f"the wrong section.")
    return issues
