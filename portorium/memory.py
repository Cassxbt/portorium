"""Broker memory on the Meterless H-MEM engine.

When a licensed broker signs off or corrects a verdict, the decision is written to H-MEM as a
long-term factual memory with provenance. The gate recalls it for the same or a twin product, and
the Adjudicator must still judge that product the same article. An importer's answer is a fact about
one product, so it is reused only for that product. Persistence and the audit ledger are saved per
importer as JSON.
"""
import json
import os
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import trace
from .engines.hmem import HMEM, Memory

THRESHOLD = 0.35
_LOCKS: dict[Path, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _same_product(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" .,;").lower()


@dataclass
class Recall:
    code: str
    memory_id: str
    relevance: float
    content: str
    broker: str


class BrokerMemory:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(self.path, threading.Lock())
        self._load()

    def _load(self) -> None:
        self.engine = HMEM()
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self.engine.store.restore_all([Memory(**m) for m in saved["memories"]], saved["seq"])
            self.engine.ledger.entries = saved["ledger"]

    @contextmanager
    def _session(self, write: bool = True):
        """Every read or write starts from the file, and a write ends by replacing it, under one lock per importer,
        so two handles never overwrite each other's memories and asking never creates a file."""
        with self._lock:
            self._load()
            yield
            if write:
                self._save()

    def remember(self, product: str, code: str, broker: str, note: str = "") -> str:
        content = f"Broker decision: {product} is classified under HTS {code}. {note}".strip()
        with self._session():
            memory = self.engine.add(content=content, source=f"broker:{broker}", type="factual", layer="long_term",
                                     confidence=0.9, tags=[f"hts:{code}", f"broker:{broker}"],
                                     provenance={"origin": "user_correction", "label": f"signed off by {broker}"})
        trace.event("hmem.add", content, f"stored {memory.id} (long_term, factual, user_correction)", engine="H-MEM")
        return memory.id

    def remember_answer(self, product: str, question: str, answer: str) -> str:
        """An importer's answer to the gate's question: a fact about this product, reused only for this product."""
        content = f"Importer answered for {product}: {question} -> {answer}"
        with self._session():
            memory = self.engine.add(content=content, source="importer", type="factual", layer="long_term",
                                     confidence=0.8, tags=["answer", f"q:{question}", f"a:{answer}",
                                                           f"product:{_same_product(product)}"],
                                     provenance={"origin": "user_answer", "label": "importer answer to gate question"})
        trace.event("hmem.add", content, f"stored {memory.id} (long_term, factual, user_answer)", engine="H-MEM")
        return memory.id

    def recall_answers(self, product: str) -> list[tuple[str, str, str]]:
        """(question, answer, memory id) for the importer's earlier answers about this same product."""
        with self._session(write=False):
            result = self.engine.query(product, threshold=THRESHOLD)
        out = []
        for hit in result["memories"]:
            m = hit["memory"]
            about = next((t[8:] for t in m.tags if t.startswith("product:")), None)
            if about is None:
                about = _same_product(m.content.removeprefix("Importer answered for ").rsplit(": ", 1)[0])
            if "answer" in m.tags and about == _same_product(product):
                q = next((t[2:] for t in m.tags if t.startswith("q:")), "")
                a = next((t[2:] for t in m.tags if t.startswith("a:")), "")
                out.append((q, a, m.id))
        trace.event("hmem.query", product, _listed(f"{len(out)} remembered answer(s)", [f"{m} {q} -> {a}" for q, a, m in out]),
                    engine="H-MEM", threshold=THRESHOLD)
        return out

    def recall(self, product: str) -> list[Recall]:
        with self._session(write=False):
            result = self.engine.query(product, threshold=THRESHOLD)
        recalls = []
        for hit in result["memories"]:
            m = hit["memory"]
            if "answer" in m.tags:
                continue
            code = next((t[4:] for t in m.tags if t.startswith("hts:")), None)
            broker = next((t[7:] for t in m.tags if t.startswith("broker:")), "")
            if code:
                recalls.append(Recall(code, m.id, round(hit["relevance"], 3), m.content, broker))
        trace.event("hmem.query", product, _listed(f"{len(recalls)} broker decision(s)",
                                                   [f"{r.memory_id} HTS {r.code} relevance {r.relevance}" for r in recalls]),
                    engine="H-MEM", threshold=THRESHOLD)
        return recalls

    def forget(self, memory_id: str, reason: str) -> None:
        with self._session():
            self.engine.store.delete(memory_id, reason)
        trace.event("hmem.delete", memory_id, f"deleted ({reason}); the ledger keeps the deletion", engine="H-MEM")

    def forget_all(self, reason: str) -> None:
        with self._session():
            memories = self.engine.store.all()
            for m in memories:
                self.engine.store.delete(m.id, reason)
        trace.event("hmem.delete", "all memories", f"deleted {len(memories)} ({reason}); the ledger keeps the deletions", engine="H-MEM")

    def history(self, memory_id: str) -> list[dict]:
        with self._lock:
            self._load()
            return self.engine.ledger.history(memory_id)

    def _save(self) -> None:
        records, seq = self.engine.store.snapshot_all()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        partial = self.path.with_name(self.path.name + ".partial")
        partial.write_text(json.dumps({"memories": [m.__dict__ for m in records], "seq": seq,
                                       "ledger": self.engine.ledger.entries}), encoding="utf-8")
        os.replace(partial, self.path)


def _listed(head: str, items: list[str]) -> str:
    return f"{head}: {'; '.join(items)}" if items else head
