"""Per-run trace: logs/trace-<run_id>.jsonl, echoed to stdout.

Each line is written by the code that did the work, at the moment it did it: the gate's roles
(Retriever, Memory, Auditor, Adjudicator, Gate, Importer) and every service request they make
(NVIDIA, Nightshift, Zetaris, H-MEM) under the role that made it. Nothing is summarised afterwards.
"""
import contextvars
import datetime
import json
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .config import ROOT

LOGS = ROOT / "logs"

_trace: contextvars.ContextVar["Trace | None"] = contextvars.ContextVar("trace", default=None)
_subject: contextvars.ContextVar[str | None] = contextvars.ContextVar("subject", default=None)
_role: contextvars.ContextVar[str] = contextvars.ContextVar("role", default="portorium")


class Trace:
    def __init__(self, run_id: str | None = None, directory: Path = LOGS, echo=sys.stdout):
        self.run_id = run_id or f"{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}-{uuid.uuid4().hex[:6]}"
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"trace-{self.run_id}.jsonl"
        self.echo = echo
        self._lock = threading.Lock()

    @contextmanager
    def bind(self, subject: str | None = None):
        """Make this the active trace for the current thread or request."""
        tokens = _trace.set(self), _subject.set(subject)
        try:
            yield self
        finally:
            _subject.reset(tokens[1])
            _trace.reset(tokens[0])

    def write(self, record: dict) -> None:
        line = json.dumps(record, default=str)
        with self._lock:
            with self.path.open("a") as f:
                f.write(line + "\n")
            if self.echo:
                target = f" -> {record['target_agent']}" if record["target_agent"] else ""
                model = f" [{record['model']}]" if record["model"] else ""
                print(f"  trace {record['agent_name']}.{record['action']}{target}{model} {record['status']}: "
                      f"{record['output_summary'][:110]}", file=self.echo, flush=True)


@contextmanager
def role(name: str):
    token = _role.set(name)
    try:
        yield
    finally:
        _role.reset(token)


def event(action: str, input_summary: str, output_summary: str, *, agent_name: str | None = None,
          target_agent: str | None = None, model: str | None = None, confidence: float | None = None,
          retry_count: int = 0, status: str = "success", **detail) -> None:
    trace = _trace.get()
    if trace is None:
        return
    trace.write({
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "run_id": trace.run_id, "subject": _subject.get(), "agent_name": agent_name or _role.get(),
        "action": action, "input_summary": _short(input_summary), "output_summary": _short(output_summary),
        "target_agent": target_agent, "model": model, "confidence": confidence, "retry_count": retry_count,
        "status": status, **({"detail": detail} if detail else {})})


def _short(text: str, limit: int = 400) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
