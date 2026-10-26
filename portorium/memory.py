"""Broker memory on the Meterless H-MEM engine.

When a licensed broker signs off or corrects a verdict, the decision is written to H-MEM as a
long-term factual memory with provenance. The gate recalls it for the same or a twin product.
Persistence and the audit ledger are saved per importer as JSON.
"""
import json
from dataclasses import dataclass
from pathlib import Path

from . import trace
from .engines.hmem import HMEM, Memory

THRESHOLD = 0.35


@dataclass
class Recall:
    code: str
    memory_id: str
    relevance: float
    content: str
    broker: str


class BrokerMemory:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.engine = HMEM()
        if self.path.exists():
            saved = json.loads(self.path.read_text())
            self.engine.store.restore_all([Memory(**m) for m in saved["memories"]], saved["seq"])
            self.engine.ledger.entries = saved["ledger"]

    def remember(self, product: str, code: str, broker: str, note: str = "") -> str:
        content = f"Broker decision: {product} is classified under HTS {code}. {note}".strip()
        memory = self.engine.add(content=content, source=f"broker:{broker}", type="factual", layer="long_term",
                                 confidence=0.9, tags=[f"hts:{code}", f"broker:{broker}"],
                                 provenance={"origin": "user_correction", "label": f"signed off by {broker}"})
        self._save()
        trace.event("hmem.add", content, f"stored {memory.id} (long_term, factual, user_correction)", engine="H-MEM")
        return memory.id

    def remember_answer(self, product: str, question: str, answer: str) -> str:
        """An importer's answer to the gate's question, a product fact reused for similar products."""
        content = f"Importer answered for {product}: {question} -> {answer}"
        memory = self.engine.add(content=content, source="importer", type="factual", layer="long_term",
                                 confidence=0.8, tags=["answer", f"q:{question}", f"a:{answer}"],
                                 provenance={"origin": "user_answer", "label": "importer answer to gate question"})
        self._save()
        trace.event("hmem.add", content, f"stored {memory.id} (long_term, factual, user_answer)", engine="H-MEM")
        return memory.id

    def recall_answers(self, product: str) -> list[tuple[str, str, str]]:
        """(question, answer, memory id) for remembered answers about similar products."""
        result = self.engine.query(product, threshold=THRESHOLD)
        self._save()
        out = []
        for hit in result["memories"]:
            m = hit["memory"]
            if "answer" in m.tags:
                q = next((t[2:] for t in m.tags if t.startswith("q:")), "")
                a = next((t[2:] for t in m.tags if t.startswith("a:")), "")
                out.append((q, a, m.id))
        trace.event("hmem.query", product, _listed(f"{len(out)} remembered answer(s)", [f"{m} {q} -> {a}" for q, a, m in out]),
                    engine="H-MEM", threshold=THRESHOLD)
        return out

    def recall(self, product: str) -> list[Recall]:
        result = self.engine.query(product, threshold=THRESHOLD)
        self._save()
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
        self.engine.store.delete(memory_id, reason)
        self._save()
        trace.event("hmem.delete", memory_id, f"deleted ({reason}); the ledger keeps the deletion", engine="H-MEM")

    def forget_all(self, reason: str) -> None:
        memories = self.engine.store.all()
        for m in memories:
            self.engine.store.delete(m.id, reason)
        self._save()
        trace.event("hmem.delete", "all memories", f"deleted {len(memories)} ({reason}); the ledger keeps the deletions", engine="H-MEM")

    def history(self, memory_id: str) -> list[dict]:
        return self.engine.ledger.history(memory_id)

    def _save(self) -> None:
        records, seq = self.engine.store.snapshot_all()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"memories": [m.__dict__ for m in records], "seq": seq,
                                         "ledger": self.engine.ledger.entries}))


def _listed(head: str, items: list[str]) -> str:
    return f"{head}: {'; '.join(items)}" if items else head
