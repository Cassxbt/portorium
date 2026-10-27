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
import os
import re
import secrets
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
    try:
        return _start()
    except (MissingRecording, FileNotFoundError, NightshiftError, NvidiaError, ZetarisError, RuntimeError) as e:
        hint = " Sample mode needs the bundle in data/sample/ (scripts/build_sample.py)." if isinstance(e, (MissingRecording, FileNotFoundError)) else ""
        raise HTTPException(503, f"Portorium could not start: {e}.{hint}") from e


def _start() -> tuple[PrecedentGate, Zetaris]:
    with _lock:
        if not _state:
            s = settings()
            s.check()
            zetaris = Zetaris(s, s.live)
            with trace.Trace().bind("start-up: liveness cross-check"), trace.role("Auditor"):
                liveness = Liveness.from_zetaris(zetaris)
            gate = PrecedentGate(PrecedentIndex(index_dir(s.live), s.live), Nvidia(s, s.live), Nightshift(s, s.live),
                                 liveness, RulingTexts(s.live), zetaris)
            gate.release_current = audit.ReleaseCheck(zetaris)
            # Only a complete start-up is kept, so a failed one is simply tried again on the next request.
            _state.update(zetaris=zetaris, gate=gate, live=s.live)
        return _state["gate"], _state["zetaris"]


def _memory(importer: str) -> BrokerMemory:
    return BrokerMemory(DATA / "memory" / f"{importer}.json")


def may_write(importer: str, broker: bool, token: str, live: bool, expected: str) -> None:
    """Who may change an importer's memory. An importer's answers are theirs to give; live, a sign-off or a wipe is
    a broker's act and needs the BROKER_TOKEN the operator set. The hosted sample has no accounts: the console gives
    each browser its own random namespace, and anyone may write a namespace whose name they know."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", importer):
        raise HTTPException(400, "importer must be letters, digits, '-' or '_'")
    if live and broker and not (expected and secrets.compare_digest(token, expected)):
        raise HTTPException(403, "broker sign-off and wipe need the BROKER_TOKEN set for this deployment")


def _may_write(importer: str, broker: bool, token: str = "") -> None:
    may_write(importer, broker, token, settings().live, os.environ.get("BROKER_TOKEN", ""))


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
    broker_token: str = ""


class ForgetRequest(BaseModel):
    importer: str = "demo"
    broker_token: str = ""


@app.post("/run")
@app.post("/api/classify")
def classify(req: ClassifyRequest) -> dict:
    gate, zetaris = _services()
    if req.answers:
        _may_write(req.importer, broker=False)
    elif not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", req.importer):
        raise HTTPException(400, "importer must be letters, digits, '-' or '_'")
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
    _may_write(req.importer, broker=True, token=req.broker_token)
    run = trace.Trace()
    with run.bind(req.product), trace.role("Broker"):
        trace.event("sign_off", req.product, f"HTS {code} signed off by {req.broker}", target_agent="Memory")
        memory_id = _memory(req.importer).remember(req.product, code, broker=req.broker, note=req.note)
    return {"memory_id": memory_id, "code": code, "trace": run.run_id}


@app.post("/api/forget")
def forget(req: ForgetRequest) -> dict:
    _may_write(req.importer, broker=True, token=req.broker_token)
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
                    "answers": e["input"].get("answers", []),
                    "decision": v["decision"], "reason": v["reason"], "code": v["code"]})
    return out


@app.get("/api/examples/{name}")
def example(name: str) -> dict:
    if not re.fullmatch(r"[a-z0-9-]+", name) or not (EXAMPLES / f"{name}.json").exists():
        raise HTTPException(404, "no such example")
    return json.loads((EXAMPLES / f"{name}.json").read_text())


@app.get("/api/traces")
def traces(limit: int = 30) -> list[dict]:
    """Recent request traces; evaluation runs (trace-dev-*, trace-test-*) are thousands of lines and stay as files.

    Not listed in sample mode, which is the hosted mode: visitors would see each other's products. Each visitor
    still opens their own trace by its id, and everyone sees the recorded examples."""
    if not settings().live:
        return []
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
