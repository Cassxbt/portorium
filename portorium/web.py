"""HTTP API for the Portorium broker console.

  POST /api/classify  {product, importer?, as_of?, answers?: [[question, answer]]}
  POST /api/confirm   {product, code, broker, importer?, note?}
  POST /api/forget    {importer?}
  GET  /api/rescreen
  GET  /api/eval      latest evaluation table
  GET  /api/status    mode, HTS release, DQ rule counts
  GET  /api/trace/{run_id}  the trace of one request, as written to logs/
"""
import datetime
import json
import re
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import audit, trace
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
            with trace.role("Auditor"):
                liveness = Liveness.from_zetaris(zetaris)
            _state["gate"] = PrecedentGate(PrecedentIndex(), Nvidia(s, s.live), Nightshift(s, s.live),
                                           liveness, RulingTexts(s.live), zetaris)
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
    run = trace.Trace()
    try:
        with run.bind(req.product):
            memory = _memory(req.importer)
            if req.answers:
                with trace.role("Importer"):
                    trace.event("answer", req.answers[-1][0], req.answers[-1][1], target_agent="Adjudicator",
                                retry_count=len(req.answers), status="feedback")
                    memory.remember_answer(req.product, *req.answers[-1])
            verdict = gate.classify(req.product, as_of, answers=req.answers, memory=memory)
            with trace.role("Ledger"):
                audit.record(zetaris, verdict, req.product, req.importer)
    except MissingRecording as e:
        raise HTTPException(409, f"Sample mode has no recording for this input. {e}") from e
    except (NightshiftError, NvidiaError, ZetarisError) as e:
        raise HTTPException(502, f"{e} (trace {run.run_id})") from e
    return {**verdict.to_dict(), "trace": run.run_id}


@app.post("/api/confirm")
def confirm(req: ConfirmRequest) -> dict:
    code = req.code.replace(".", "")
    if not (code.isdigit() and len(code) == 10):
        raise HTTPException(400, "code must be 10 digits")
    run = trace.Trace()
    with run.bind(req.product), trace.role("Broker"):
        trace.event("sign_off", req.product, f"HTS {code} signed off by {req.broker}", target_agent="Memory")
        memory_id = _memory(req.importer).remember(req.product, code, broker=req.broker, note=req.note)
    return {"memory_id": memory_id, "code": code, "trace": run.run_id}


@app.post("/api/forget")
def forget(req: ForgetRequest) -> dict:
    run = trace.Trace()
    with run.bind(), trace.role("Broker"):
        _memory(req.importer).forget_all(reason="broker requested wipe")
    return {"wiped": req.importer, "trace": run.run_id}


@app.get("/api/rescreen")
def rescreen() -> dict:
    _, zetaris = _services()
    run = trace.Trace()
    with run.bind(), trace.role("Auditor"):
        result = audit.rescreen(zetaris)
    return {**result, "trace": run.run_id}


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


@app.get("/api/trace/{run_id}")
def read_trace(run_id: str) -> list[dict]:
    if not re.fullmatch(r"[0-9T]+-[0-9a-f]{6}", run_id):
        raise HTTPException(400, "bad trace id")
    path = trace.LOGS / f"trace-{run_id}.jsonl"
    if not path.exists():
        raise HTTPException(404, "no such trace")
    return [json.loads(line) for line in path.open()]


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")
