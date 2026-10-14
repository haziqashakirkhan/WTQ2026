# Utility Bill Decoder (WTQ Build Track)

An AI assistant that reads Pakistani electricity bills (K-Electric, LESCO, IESCO) from a
scan/photo, **extracts** the billing details to JSON (Level 1) and **answers customer
questions** about the bill in plain English (Level 2).

- **Runtime:** Python 3.14.4 (minimum supported: Python 3.11).
- **Model used for the submission:** `gemini-3.5-flash-lite` (Google AI Studio, free tier).

## Setup

```bash
# Python 3.11 or later is required; choose an installed supported version.
py -3.14 -m venv .venv
.venv\Scripts\Activate.ps1          # Windows PowerShell  (macOS/Linux: source .venv/bin/activate)
python -m pip install -r requirements.txt
copy .env.example .env             # macOS/Linux: cp .env.example .env ; then put your key in API_KEY
```

`.env` variables: `MODEL_PROVIDER` (`google`), `MODEL_NAME` (`gemini-3.5-flash-lite`),
`API_KEY`, plus optional `WORKERS`, `MIN_REQUEST_INTERVAL`, `MAX_RETRIES`, `MAX_IMAGE_SIDE`,
`CACHE_DIR`. Only the allowed Gemini models (`gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`) are
accepted; anything else aborts at start-up.

> Gemini free tier is 15 requests/min: set `MIN_REQUEST_INTERVAL=4` and `WORKERS=1`.

## Run

### Web app

Install dependencies, then start the web server from the project folder:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. The page is loaded from `templates/index.html`; its styles and
scripts are served from `static/`.

### Batch processing

Generate completed CSVs from the challenge templates. Keep the downloaded `test/` folder beside
the project so the supplied 10-bill and 120-question templates are used:

```bash
python run.py run --bills test/bills --level1 test/csv/level1.csv --level2 test/csv/level2.csv --out output
```

If no challenge templates are available, the app can generate three built-in questions per bill:

```bash
python run.py run --bills path/to/bills --out output
```

Without `--bills`, the app looks for a bill folder in the project (including the provided
`bills-*/bills` dataset folders). Outputs are written to `output/`.

Decode a single bill (useful for the demo video):

```bash
python run.py demo train/bills/KESC_0008.png
python run.py demo train/bills/KESC_0008.png -q "How much of my bill is taxes?" -q "Estimate my cost per unit"
```

Package the final ZIP (checks row counts, empty cells, secrets and the 15 MB limit):

```bash
python tools/package_submission.py
```

The packager validates the generated files against the latest `csv-*/csv/` test templates and
creates `submission.zip` with the required `output/` and `source/` directories. It excludes `.env`
and local environments/caches.

Run the offline tests (no API key needed): `python -m unittest discover -s tests`

Check extraction quality on the five training bills (needs an API key; diffs against
`tests/train_reference.json`, where only KESC_0008 is official):

```bash
python tools/compare_train.py train/bills
```

## How it works

```
bill image --> imaging (EXIF fix, resize) --+--> Level 1 extraction --> schema normalisation
                                            |        |                     + consistency check
                                            |        +-- if lines != printed subtotal:
                                            |            one targeted re-read of the image
                                            +--> Facts transcription (history, labels, notes)
                                                     |
                  questions --> PLAN (LLM writes arithmetic expressions)
                                  --> COMPUTE (safe AST evaluator, exact maths)
                                  --> ANSWER (LLM writes English using exact results)
```

- **Level 1** (`level1.py`, `schema.py`): one vision call with a rule-dense prompt and the
  worked KESC_0008 example. Code then enforces types/enums/date formats, makes CR values
  negative, drops zero lines, guarantees every key exists, and never recalculates amounts.
  If charges or taxes do not add up to the printed subtotal (a sign of a missed or misread
  line) the model re-reads the image once with the specific discrepancy; the better result wins.
  The provider comes from the bill-id prefix (KESC to KE), a deterministic part of the input.
- **Level 2** (`level2.py`, `calc.py`): the model never does arithmetic. It proposes named
  expressions (e.g. `pct(total_taxes, current_bill)`, `count_above(history, 200)`), a
  whitelisted AST evaluator computes them, and the model writes the final answer from those
  exact results. The prompt forces grounding in the bill only, labelled estimates, stated
  interpretations of ambiguous questions and explicit "not shown on the bill" statements.
  All 12 questions of a bill share one plan call and one answer call (3 LLM calls per
  bill for Level 2 including the facts transcription), which keeps rate limits and cost low.
- **Privacy:** prompts forbid identifiers, the Level 1 schema has no identifier fields, and
  answers are regex-scrubbed for CNIC-like / 10+ digit numbers.
- **Robustness:** retries with exponential backoff, JSON re-ask on malformed output,
  per-stage try/except with fallbacks (a bill never produces an empty cell), an on-disk
  cache so re-runs are free, bounded concurrency, and output validation at the end.

## Design decisions to know about

- Layouts seen in the training bills are handled explicitly in the prompt: K-Electric itemised
  tables (repeated line types, net-metering lines), LESCO/IESCO summaries (gross charges, subsidy,
  net charges, one combined tax figure, `CR` payables, "NOT TO BE PAID" -> null) and multi-row
  meter registers (first row used; headline units used on K-Electric bills).
- For LESCO/IESCO the charges list is `Total Electricity Charges` (energy) plus a negative
  `subsidy` entry, and `total_charges` is the net figure, so entries sum to the subtotal.

- Zero-valued tax lines are dropped just like zero charge lines.
- Per the guide, the CSV `json` column is one line with doubled quotes (handled by Python's `csv`).
- Outputs are 100% generated by code; nothing is hand-edited.

## AI usage

- **Models called at runtime:** Gemini 3.5 Flash-Lite (`gemini-3.5-flash-lite`) via Google AI Studio
  (the model named in `.env.example`).
- **APIs/services:** Google Gemini API (vision). Local libraries only otherwise:
  Pillow for image preprocessing; FastAPI/Uvicorn for the local web UI. No OCR service,
  no invoice-extraction agents.
- **AI tools used to write the code:** Claude (Anthropic chat assistant) for the initial code,
  prompts and tests; GitHub Copilot in VS Code for subsequent debugging and updates.