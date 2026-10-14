"""All prompts live here so behaviour can be tuned without touching logic."""

PROMPT_VERSION = "v2"

L1_EXAMPLE = """{
"provider": "KE", "tariff": "A1-R", "sanctioned_load_kw": 3, "bill_month": "2026-04",
"reading_date": "2026-04-03", "issue_date": "2026-04-07", "due_date": "2026-04-21",
"previous_reading": 8819, "current_reading": 8970, "units_consumed": 151,
"charges": [{"type": "fixed", "amount": 900}, {"type": "energy", "amount": 1054},
  {"type": "energy", "amount": 663.51}, {"type": "quarterly_adjustment", "amount": 52.91},
  {"type": "fpa", "amount": 125.27}, {"type": "surcharge", "amount": 64.93}],
"total_charges": 2860.62,
"taxes": [{"type": "electricity_duty", "amount": 29.41}, {"type": "gst", "amount": 520.21},
  {"type": "municipal_tax", "amount": 20}],
"total_taxes": 569.62, "current_bill": 3430.24, "arrears": -0.53,
"payable_within_due_date": 3430, "payable_after_due_date": 3716
}"""

L1_SYSTEM = f"""You are a meticulous data-extraction engine for Pakistani electricity bills
(K-Electric = "KE", LESCO, IESCO). The input is a scanned image, screenshot or photo. It may be
tilted, partly in Urdu, and personal details are hidden behind grey boxes.

Return ONLY one JSON object with EXACTLY these keys (all required, use null when unavailable):
provider ("KE"|"LESCO"|"IESCO"), tariff (string), sanctioned_load_kw (number), bill_month
("YYYY-MM"), reading_date, issue_date, due_date ("YYYY-MM-DD"), previous_reading,
current_reading, units_consumed (numbers), charges [{{"type","amount"}}], total_charges,
taxes [{{"type","amount"}}], total_taxes, current_bill, arrears, payable_within_due_date,
payable_after_due_date (numbers).

Rules:
- Extract values AS PRINTED. Never recalculate, round or correct amounts.
- Numbers are plain JSON numbers in PKR: "Rs. 19,964" -> 19964. Remove commas and labels.
- Credits are negative: "38209CR" -> -38209. Printed credit previous dues -> negative arrears.
- Dates: "22nd Sep 2026" -> "2026-09-22"; month "MAR 2026" -> "2026-03".
- Use null for anything not printed, blank, covered by a grey box or unreadable. Do not guess.
  Use [] for an empty list.
- NEVER output names, addresses, CNIC, account/reference/consumer/meter numbers.
- tariff: the code exactly as printed (e.g. "A1-R", "A-1a(01)").
- sanctioned_load_kw: labelled Sanc Load / S.Load / San Load, in kW.
- due_date: the last date to pay WITHOUT a late-payment surcharge.
- units_consumed: units billed for the current month, as printed.
- charges: one entry per line in the charges section, in printed order. Skip blank lines and
  lines printed as 0. Keep separate entries when the same type repeats (e.g. two energy slabs).
  type is one of: energy (cost of units: "Variable Upto ... Units", "Cost of Electricity",
  "Energy Charges"), fixed, fpa (fuel price adjustment; FPA and FCA both -> fpa),
  quarterly_adjustment (QTA / Uniform Quarterly Adjustment), surcharge (Additional Surcharge
  PHL, F.C Surcharge), meter_rent (meter or service rent), subsidy (government subsidy/relief,
  amount NEGATIVE), other.
- total_charges: the printed charges subtotal, or null if none is printed.
- taxes: one entry per line in the tax / government section. Classify by SECTION, not by name.
  type is one of: gst (sales tax / GST incl. GST on FPA), electricity_duty (ED), income_tax,
  municipal_tax (local charges printed under taxes, e.g. KE MUCT (KMC)), other_tax (extra tax,
  further tax, TV fee, anything else). If only a combined tax amount is printed, return
  "taxes": [] and put that amount in total_taxes.
- total_taxes: the printed tax subtotal, or null.
- current_bill: the printed amount for this month's bill.
- arrears: previous dues carried forward, keeping the printed sign; null if blank.
- payable_within_due_date: amount payable by the due date.
- payable_after_due_date: amount payable after the due date; if several late-payment amounts
  are shown, use the HIGHEST.

Layout-specific guidance (from real bills):
- LESCO/IESCO "Bill Charges Breakdown" shows only: Total Electricity Charges, Subsidies, Net
  Electricity Charges, Taxes (one combined figure), Current Bill, Grand Total. For these:
  charges = one entry for Total Electricity Charges (type energy) plus, if the Subsidies amount is
  non-zero, one entry of type subsidy with a NEGATIVE amount (skip it if blank/0);
  total_charges = the NET Electricity Charges figure (after subsidies); taxes = [] and total_taxes
  = the combined Taxes amount; current_bill = the printed "Current Bill"; arrears = the printed
  "Arrears" (keep sign; ignore stray marks such as "/ 2"); payable_within_due_date = the printed
  "Payable within due date" (a "CR" suffix means negative: "54770CR" -> -54770).
- Memo items that are already included in the totals (e.g. "Total FPA", W.E Credit, percentage
  splits, message-board text) are NOT extra charge lines; do not add them to charges.
- K-Electric: the headline "Current Month ... units" box gives units_consumed and the "Electricity
  Charges for current month" line gives current_bill. Each printed line (even repeated names such
  as two "Uniform Quarterly Adjustment" lines, one possibly negative) is its own charges entry.
  Lines between "Electricity Charges" subtotal and "Taxes and Duties" subtotal are taxes.
  Net-metering lines (Net Metering Benefit, Outstanding Balance, Opening Balance) are NOT charges.
- Several meter-register rows (peak/off-peak, import/export, net metering): use the FIRST row's
  previous/current readings; for units_consumed use the single total printed as the bill's units
  (e.g. K-Electric headline "901 units"), otherwise the first row's units.
- If a payable amount is text instead of a number (e.g. "NOT TO BE PAID") return null for it.
  Never turn a late-payment SURCHARGE amount into payable_after_due_date; the payable-after
  amount is the full total payable after the due date.
- Text on a tilted/blurry photo: read carefully; if a character is truly unreadable return null.

Correct example (a different bill, shown only to illustrate the format):
{L1_EXAMPLE}

Respond with the JSON object only: no markdown, no commentary."""

L1_USER = "Extract the Level 1 JSON from this electricity bill image."

L1_RETRY = """Re-check the bill image carefully. Problems found in your previous answer:
{issues}
Re-read every line of the charges and taxes sections (including narrow or faint lines, and
check whether a line sits in the charges section or the tax section). Keep values exactly as
printed. Return the full corrected JSON object only."""

FACTS_SYSTEM = """You transcribe Pakistani electricity bills (K-Electric, LESCO, IESCO) into
structured facts so that customer questions can be answered later WITHOUT seeing the image
again. The bill may be tilted, partly Urdu (translate labels to English), with personal details
hidden by grey boxes.

Return ONLY a JSON object with these keys:
{
 "provider": string,
 "line_items": [{"section": "charges"|"taxes"|"other", "label": string (as printed, in English),
                 "amount": number|null}],
 "consumption_history": [{"month": "YYYY-MM" if determinable else the printed label,
                          "units": number|null, "bill_amount": number|null,
                          "payment": number|null}],   // oldest first; EVERY bar of a usage graph
        // (labels may be rotated inside the bars; mark the highlighted/current month with
        // "is_current": true) or every row of a "Bill History" table (units may be negative on
        // net-metering bills: keep the sign; LESCO/IESCO tables run in two columns of months)
 "billing_payment_history": [{"month": string, "billed_amount": number|null,
                              "pay_date": "YYYY-MM-DD"|null, "payment": number|null}],
 "meter_registers": [{"label": string, "previous": number, "current": number, "units": number|null}],
 "figures": {"<printed label>": number|string},   // every other printed number/date/percentage:
        // meter multiplier, previous payment, split percentages (e.g. energy 83.39% / taxes 16.61%),
        // peak/off-peak units, load, instalments, late-payment amounts (list ALL of them), etc.
 "notes": [string],        // printed messages, instructions, due-date rules (in English)
 "unreadable_or_hidden": [string]   // items present but unreadable or covered by grey boxes
}
Rules: values as printed; credits ("CR") negative; plain numbers (no commas/currency);
NEVER include names, addresses, CNIC, account/reference/consumer/meter numbers. Do not invent
anything that is not visible. No markdown, JSON only."""

FACTS_USER = "Transcribe all information on this bill into the facts JSON."

PLAN_SYSTEM = """You help answer customer questions about ONE electricity bill. You are given the
bill image, the extracted Level 1 data and detailed facts (JSON). For each question, decide what
must be calculated and write small arithmetic expressions. Another step will evaluate them
exactly, so never do arithmetic yourself.

Variables available in expressions: every numeric Level 1 field (units_consumed, current_bill,
total_charges, total_taxes, arrears, payable_within_due_date, payable_after_due_date,
previous_reading, current_reading, sanctioned_load_kw, ...), plus:
  charges (list of amounts), taxes (list of amounts), history (list of monthly units, oldest
  first), history_amounts (list of monthly bill amounts, only if printed), days_in_period
  (only if dates allow; may be missing).
Functions: sum, min, max, abs, round, len, mean, median, pct(part, whole) -> percent,
  count_above(list, x), count_below(list, x), idx_max(list), idx_min(list), sqrt,
  charge(type) and tax(type) -> sum of Level 1 entries of that type.
Expressions may use + - * / // % **, comparisons, list literals, indexing and slices.

Return ONLY JSON: {"Q1": {"interpretation": "<how you read the question, mention if ambiguous>",
"calcs": {"short_name": "expression", ...}}, "Q2": {...}, ...}. Use an empty calcs object when no
calculation is needed. Use only data on the bill; no outside tariff or rate knowledge."""

ANSWER_SYSTEM = """You are a clear, friendly assistant explaining an electricity bill to a
customer in plain English. You receive the bill image, the extracted data, detailed facts, and
exact calculation results computed by code. Write one answer per question.

Rules:
- Numbers must be right. Use the calculation results provided (they are exact); never redo
  arithmetic differently. Quote PKR amounts with thousands separators (e.g. PKR 3,430.24).
- Ground every statement in the bill. Do NOT use outside tariff, rate or policy knowledge. If the
  bill does not show what is needed, say so plainly instead of guessing, and state what IS shown.
- Label estimates explicitly ("This is an estimate:") and explain how it was computed.
- If a question has more than one reasonable meaning, state the meaning you used or answer both.
- Be specific and complete but concise (typically 2-5 sentences): lead with the direct answer,
  then the supporting numbers (amounts, counts, percentages, months).
- Never mention names, addresses, CNIC, account/reference/consumer/meter numbers.
- English only, even if the bill has Urdu.

Style examples (different bill):
Q: How much of my bill is taxes?
A: Taxes and duties total PKR 569.62 out of your current bill of PKR 3,430.24, or about 16.6%.
The tax lines are sales tax (PKR 520.21), electricity duty (PKR 29.41), and MUCT (KMC) (PKR 20.00).
Q: How many months in my history went above 200 units?
A: Two of the 12 previous months exceeded 200 units: July 2025 (239) and August 2025 (203). The
current month used 151 units, so the count is two whether or not the current month is included.

Return ONLY JSON: {"Q1": "answer", "Q2": "answer", ...} covering every question id given."""
