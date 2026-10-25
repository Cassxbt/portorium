"""Broker memory on the Meterless H-MEM engine.

When a licensed broker signs off or corrects a verdict, the decision is written to H-MEM as a
long-term factual memory with provenance. The gate recalls it for the same or a twin product.
Persistence and the audit ledger are saved per importer as JSON.
"""
import json
from dataclasses import dataclass
from pathlib import Path

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
        return memory.id

    def recall(self, product: str) -> list[Recall]:
        result = self.engine.query(product, threshold=THRESHOLD)
        self._save()
        recalls = []
        for hit in result["memories"]:
            m = hit["memory"]
            code = next((t[4:] for t in m.tags if t.startswith("hts:")), None)
            broker = next((t[7:] for t in m.tags if t.startswith("broker:")), "")
            if code:
                recalls.append(Recall(code, m.id, round(hit["relevance"], 3), m.content, broker))
        return recalls

    def forget(self, memory_id: str, reason: str) -> None:
        self.engine.store.delete(memory_id, reason)
        self._save()

    def forget_all(self, reason: str) -> None:
        for m in self.engine.store.all():
            self.engine.store.delete(m.id, reason)
        self._save()

    def history(self, memory_id: str) -> list[dict]:
        return self.engine.ledger.history(memory_id)

    def _save(self) -> None:
        records, seq = self.engine.store.snapshot_all()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"memories": [m.__dict__ for m in records], "seq": seq,
                                         "ledger": self.engine.ledger.entries}))
