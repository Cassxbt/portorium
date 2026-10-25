"""HTTP API for the Portorium broker console.

  POST /api/classify  {product, importer?, as_of?, answers?: [[question, answer]]}
  POST /api/confirm   {product, code, broker, importer?, note?}
  POST /api/forget    {importer?}
  GET  /api/rescreen
  GET  /api/eval      latest evaluation table
  GET  /api/status    mode, HTS release, DQ rule counts
"""
import datetime
import json
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import audit
from .config import DATA, ROOT, settings
from .gate import PrecedentGate
from .index import PrecedentIndex
from .liveness import Liveness
from .memory import BrokerMemory
from .nightshift import Nightshift, NightshiftError
from .nvidia import Nvidia, NvidiaError
from .recorder import MissingRecording
from .rulings import RulingTexts
from .zetaris import Zetaris, ZetarisError

STATIC = ROOT / "web"
app = FastAPI(title="Portorium")
_lock = threading.Lock()
_state: dict = {}


def _services() -> tuple[PrecedentGate, Zetaris]:
    with _lock:
        if not _state:
            s = settings()
            zetaris = Zetaris(s, s.live)
            _state["zetaris"] = zetaris
            _state["gate"] = PrecedentGate(PrecedentIndex(), Nvidia(s, s.live), Nightshift(s, s.live),
                                           Liveness.from_zetaris(zetaris), RulingTexts(s.live))
            _state["live"] = s.live
        return _state["gate"], _state["zetaris"]


def _memory(importer: str) -> BrokerMemory:
    return BrokerMemory(DATA / "memory" / f"{Path(importer).name or 'demo'}.json")


class ClassifyRequest(BaseModel):
    product: str
    importer: str = "demo"
    as_of: str | None = None
    answers: list[tuple[str, str]] = []


class ConfirmRequest(BaseModel):
    product: str
    code: str
    broker: str
    importer: str = "demo"
    note: str = ""


class ForgetRequest(BaseModel):
    importer: str = "demo"


@app.post("/api/classify")
def classify(req: ClassifyRequest) -> dict:
    gate, zetaris = _services()
    as_of = req.as_of or datetime.date.today().isoformat()
    try:
        verdict = gate.classify(req.product, as_of, answers=req.answers, memory=_memory(req.importer))
        audit.record(zetaris, verdict, req.product, req.importer)
    except MissingRecording as e:
        raise HTTPException(409, f"Sample mode has no recording for this input. {e}") from e
    except (NightshiftError, NvidiaError, ZetarisError) as e:
        raise HTTPException(502, str(e)) from e
    return verdict.to_dict()


@app.post("/api/confirm")
def confirm(req: ConfirmRequest) -> dict:
    code = req.code.replace(".", "")
    if not (code.isdigit() and len(code) == 10):
        raise HTTPException(400, "code must be 10 digits")
    memory_id = _memory(req.importer).remember(req.product, code, broker=req.broker, note=req.note)
    return {"memory_id": memory_id, "code": code}


@app.post("/api/forget")
def forget(req: ForgetRequest) -> dict:
    _memory(req.importer).forget_all(reason="broker requested wipe")
    return {"wiped": req.importer}


@app.get("/api/rescreen")
def rescreen() -> dict:
    _, zetaris = _services()
    return audit.rescreen(zetaris)


@app.get("/api/eval")
def latest_eval() -> dict:
    results = sorted((DATA / "eval" / "results").glob("*.json"))
    if not results:
        raise HTTPException(404, "no evaluation yet")
    data = json.loads(results[-1].read_text())
    return {"file": results[-1].name, "split": data["split"], "items": data["items"], "table": data["table"]}


@app.get("/api/status")
def status() -> dict:
    gate, _ = _services()
    return {"live": _state["live"], "dq": gate.liveness.counts, "precedents": len(gate.index.meta)}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")
