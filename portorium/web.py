"""HTTP API for the Portorium broker console.

  POST /api/classify  {product, importer?, as_of?, answers?: [[question, answer]]}
  POST /api/confirm   {product, code, broker, importer?, note?}
  POST /api/forget    {importer?}
  GET  /api/rescreen
  GET  /api/eval?split=dev|test   latest evaluation table of a split
  GET  /api/examples, /api/examples/{name}   recorded demo runs (output_examples/)
  GET  /api/traces    recent traces in logs/
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
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import audit, trace
from .config import DATA, ROOT, settings
from .gate import PrecedentGate
from .index import PrecedentIndex, index_dir
from .liveness import Liveness
from .memory import BrokerMemory
from .nightshift import Nightshift, NightshiftError
from .nvidia import Nvidia, NvidiaError
from .recorder import MissingRecording
from .rulings import RulingTexts
from .zetaris import Zetaris, ZetarisError

STATIC = ROOT / "web"
EXAMPLES = ROOT / "output_examples"
LOCK = DATA / "eval" / "TEST_RUN.lock"
PAGES = {"": "index", "how": "how", "console": "console", "evidence": "evidence", "traces": "traces",
         "stack": "stack", "judges": "judges", "limits": "limits"}
app = FastAPI(title="Portorium")
app.mount("/assets", StaticFiles(directory=STATIC), name="assets")
_lock = threading.Lock()
_state: dict = {}


def _services() -> tuple[PrecedentGate, Zetaris]:
    with _lock:
        if not _state:
            s = settings()
            zetaris = Zetaris(s, s.live)
            _state["zetaris"] = zetaris
            with trace.Trace().bind("start-up: liveness cross-check"), trace.role("Auditor"):
                liveness = Liveness.from_zetaris(zetaris)
            _state["gate"] = PrecedentGate(PrecedentIndex(index_dir(s.live), s.live), Nvidia(s, s.live), Nightshift(s, s.live),
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
def latest_eval(split: str = "") -> dict:
    """The latest run of a split; without one, the test run if it exists, else dev."""
    if split not in ("", "dev", "test"):
        raise HTTPException(400, "split is dev or test")
    results = sorted(r for r in (DATA / "eval" / "results").glob(f"{split or '*'}_*.json")
                     if re.fullmatch(r"(dev|test)_\d{8}T\d{6}\.json", r.name))
    if not split:
        results = [r for r in results if r.name.startswith("test_")] or results
    if not results:
        raise HTTPException(404, "no evaluation yet")
    data = json.loads(results[-1].read_text())
    return {"file": results[-1].name, "split": data["split"], "items": data["items"], "table": data["table"],
            "gate_prompt_sha256": data.get("gate_prompt_sha256"), "dq": data.get("dq"),
            "lock": json.loads(LOCK.read_text()) if data["split"] == "test" and LOCK.exists() else None}


@app.get("/api/examples")
def examples() -> list[dict]:
    out = []
    for path in sorted(EXAMPLES.glob("*.json")):
        e = json.loads(path.read_text())
        v = e["verdict"]
        out.append({"name": path.stem, "title": e.get("title", path.stem), "product": e["input"]["product"],
                    "decision": v["decision"], "reason": v["reason"], "code": v["code"]})
    return out


@app.get("/api/examples/{name}")
def example(name: str) -> dict:
    if not re.fullmatch(r"[a-z0-9-]+", name) or not (EXAMPLES / f"{name}.json").exists():
        raise HTTPException(404, "no such example")
    return json.loads((EXAMPLES / f"{name}.json").read_text())


@app.get("/api/traces")
def traces(limit: int = 30) -> list[dict]:
    """Recent request traces; evaluation runs (trace-dev-*, trace-test-*) are thousands of lines and stay as files."""
    out = []
    for path in sorted(trace.LOGS.glob("trace-2*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        lines = [json.loads(line) for line in path.open()]
        decide = next((l for l in reversed(lines) if l["action"] == "decide"), None)
        out.append({"run_id": path.stem.removeprefix("trace-"), "subject": next((l["subject"] for l in lines if l["subject"]), None),
                    "lines": len(lines), "decision": decide["output_summary"] if decide else None,
                    "started": lines[0]["timestamp"] if lines else None})
    return out


@app.get("/api/status")
def status() -> dict:
    gate, _ = _services()
    return {"live": _state["live"], "dq": gate.liveness.counts, "precedents": gate.index.info["rulings"]}


@app.get("/api/trace/{run_id}")
def read_trace(run_id: str) -> list[dict]:
    if not re.fullmatch(r"[0-9A-Za-z-]+", run_id) or run_id.startswith("-"):
        raise HTTPException(400, "bad trace id")
    path = trace.LOGS / f"trace-{run_id}.jsonl"
    if not path.exists():
        raise HTTPException(404, "no such trace")
    return [json.loads(line) for line in path.open()]


@app.get("/{page}")
@app.get("/")
def page(page: str = "") -> FileResponse:
    if page not in PAGES:
        raise HTTPException(404, "no such page")
    return FileResponse(STATIC / f"{PAGES[page]}.html")
