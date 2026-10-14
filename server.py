"""Local web UI for the Utility Bill Decoder (WATTWISE AI).

Run from any directory:
    python -m uvicorn server:app --app-dir "C:\\path\\to\\WTQ_2026" --port 8000
then open http://127.0.0.1:8000
"""
import sys
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))          # for `import adapter`
sys.path.insert(0, str(HERE.parent))   # for `import billdecoder`
import adapter  # noqa: E402

ALLOWED = {".png", ".jpg", ".jpeg"}
MAX_BYTES = 10 * 1024 * 1024
STORE = Path(tempfile.gettempdir()) / "billdecoder_ui"
STORE.mkdir(exist_ok=True)

app = FastAPI(title="WATTWISE AI")


class AskBody(BaseModel):
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    questions: list[str] = Field(min_length=1, max_length=12)


def _path(bill_id: str) -> Path:
    if len(bill_id) != 32 or not all(c in "0123456789abcdef" for c in bill_id):
        raise HTTPException(400, "Invalid bill id.")
    matches = [p for p in STORE.glob(f"{bill_id}.*") if p.suffix.lower() in ALLOWED]
    if not matches:
        raise HTTPException(404, "This bill is no longer on the server. Upload it again.")
    return matches[0]


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/decode")
async def decode(file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(400, "Upload a .png or .jpg bill image.")
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Image is larger than 10 MB.")
    bill_id = uuid.uuid4().hex
    path = STORE / f"{bill_id}{ext}"
    path.write_bytes(data)
    stem = Path(file.filename).stem
    (STORE / f"{bill_id}.stem").write_text(stem, encoding="utf-8")
    try:
        level1 = await run_in_threadpool(adapter.extract, path, stem)
        return {"id": bill_id, "preview_mode": False, "level1": level1}
    except Exception as exc:
        raise HTTPException(502, f"Extraction failed: {exc}")


@app.get("/api/history/{bill_id}")
async def history(bill_id: str):
    path = _path(bill_id)
    try:
        rows = await run_in_threadpool(adapter.history, path)
    except Exception as exc:
        raise HTTPException(502, f"History failed: {exc}")
    months = [{"month": str(r["month"]), "units": r["units"]} for r in rows
              if r.get("units") is not None]
    return {"available": bool(months), "months": months}


@app.post("/api/ask")
async def ask(body: AskBody):
    path = _path(body.id)
    try:
        answers = await run_in_threadpool(adapter.answer, path, body.questions)
    except Exception as exc:
        raise HTTPException(502, f"Answering failed: {exc}")
    return {"answers": [{"question": q, "answer": a} for q, a in zip(body.questions, answers)]}


@app.get("/")
def index():
    return FileResponse(HERE / "templates" / "index.html")


app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
