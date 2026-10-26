"""Evaluation: the Precedent Gate against baselines on CBP rulings issued after the model cutoff.

Every system sees the same input: the ruling's product description with every code-like string and
the answer's own digits removed (leakage.py). Precedent search is "as of" the split start and excludes
the ruling itself and any ruling it relates to.
"""
import json
import math
import random
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path
from dataclasses import dataclass, field

import numpy as np

from . import trace
from .config import DATA
from .gate import CLASSIFIED, PrecedentGate
from .index import PrecedentIndex
from .leakage import extract_facts, strip_codes
from .liveness import LIVING, Liveness
from .rulings import stored_facts
from .nightshift import LIGHTNING, ULTRA, Nightshift

SUBJECT_PREFIX = re.compile(r"^\s*(the\s+)?(tariff\s+)?classification(,| and| of)?[^.]*?\b(of|on)\s+(an?\s+|the\s+)?", re.I)


@dataclass
class Item:
    ruling_number: str
    ruling_date: str
    as_of: str
    answer: str
    query: str
    exclude: set[str]


@dataclass
class Prediction:
    system: str
    ruling_number: str
    code: str | None
    detail: dict = field(default_factory=dict)


SPLIT_START = {"dev": "2026-01-01", "test": "2026-06-01"}


def load_items(split: str, sample: int | None = None, seed: int = 20261025) -> list[Item]:
    """Items with precedents searched as of the split start, so dev and test see the same corpus rule."""
    items = []
    for line in (DATA / "eval" / f"{split}.jsonl").open():
        m = json.loads(line)
        description = stored_facts(m["ruling_number"])
        if description is None:
            continue
        subject = strip_codes(extract_facts(SUBJECT_PREFIX.sub("", m["subject"])) or SUBJECT_PREFIX.sub("", m["subject"]), [m["answer"]])
        facts = strip_codes(description, [m["answer"]])[:1500]
        items.append(Item(m["ruling_number"], m["ruling_date"], SPLIT_START[split], m["answer"], f"{subject}. {facts}",
                          {m["ruling_number"], *m["related"]}))
    if sample and sample < len(items):
        items = random.Random(seed).sample(items, sample)
    return items


ZERO_SHOT = ("You are a US customs classification analyst. Give the 10-digit HTSUS code for the product. "
             'Reply with only a JSON object: {"code": "<10 digits, no dots>"}')

def zero_shot(ns: Nightshift, item: Item, model: str) -> Prediction:
    data, answer = ns.chat_json([{"role": "system", "content": ZERO_SHOT},
                                 {"role": "user", "content": f"PRODUCT: {item.query}"}], {"code": None}, model=model)
    code = re.sub(r"\D", "", str(data["code"]))[:10] or None
    name = "ultra_zero_shot" if model == ULTRA else "lightning_zero_shot"
    return Prediction(name, item.ruling_number, code, {"routed_model": answer.routed_model})


def gate_predictions(gate: PrecedentGate, item: Item) -> list[Prediction]:
    """The gate and, from the same run, each layer it adds: retrieval, the Proposer, Zetaris liveness, the Critic."""
    v = gate.classify(item.query, item.as_of, exclude=item.exclude)
    precedents = gate.index.search(gate.nvidia, item.query, item.as_of, exclude=item.exclude)[: gate.top]
    n, proposal = item.ruling_number, v.proposal
    before_critic = v.critique.get("code") if v.critique else (v.code if v.decision == CLASSIFIED else None)
    return [
        Prediction("precedent_gate", n, v.code if v.decision == CLASSIFIED else None,
                   {"decision": v.decision, "reason": v.reason, "receipts": v.receipts, "question": v.question,
                    "proposal": proposal, "critique": v.critique, "judged": [asdict(j) for j in v.precedents]}),
        Prediction("gate_without_critic", n, before_critic),
        Prediction("retrieval_ultra", n, proposal.get("code"),
                   {"confidence": proposal.get("confidence", 0.0), "routed_model": proposal.get("routed_model"),
                    "top_score": precedents[0].score if precedents else 0.0}),
        Prediction("retrieval_top1", n, precedents[0].codes[0] if precedents else None),
    ]


class KeywordIndex:
    """IDF-weighted word overlap with ruling subjects: the no-model, no-embedding baseline."""

    def __init__(self, index: PrecedentIndex):
        self.index = index
        postings: dict[str, list[int]] = {}
        for i, m in enumerate(index.meta):
            for w in set(re.findall(r"[a-z]{3,}", m["subject"].lower())):
                postings.setdefault(w, []).append(i)
        n = len(index.meta)
        self.idf = {w: math.log(n / len(ix)) for w, ix in postings.items()}
        self.postings = {w: np.array(ix) for w, ix in postings.items()}
        self.numbers = np.array([m["ruling_number"] for m in index.meta])

    def top1(self, item: Item) -> Prediction:
        scores = np.zeros(len(self.index.meta))
        for w in set(re.findall(r"[a-z]{3,}", item.query.lower())):
            if w in self.postings:
                scores[self.postings[w]] += self.idf[w]
        scores[self.index.dates >= item.as_of] = -1
        scores[np.isin(self.numbers, list(item.exclude))] = -1
        best = int(np.argmax(scores))
        code = self.index.meta[best]["codes"][0] if scores[best] > 0 else None
        return Prediction("keyword_top1", item.ruling_number, code)


def run(gate: PrecedentGate, items: list[Item], systems: set[str], workers: int = 2,
        checkpoint: Path | None = None, tracer: trace.Trace | None = None) -> list[Prediction]:
    """Predictions for every item; with a checkpoint file, finished items are kept and skipped on restart."""
    ns = gate.nightshift
    keyword = KeywordIndex(gate.index) if "keyword_top1" in systems else None

    steps = [("keyword_top1", lambda item: [keyword.top1(item)]),
             ("ultra_zero_shot", lambda item: [zero_shot(ns, item, ULTRA)]),
             ("lightning_zero_shot", lambda item: [zero_shot(ns, item, LIGHTNING)]),
             ("precedent_gate", lambda item: gate_predictions(gate, item))]

    def one(item: Item, names: set[str]) -> list[Prediction]:
        out = []
        with tracer.bind(item.ruling_number) if tracer else nullcontext():
            for name, step in steps:
                if name not in names:
                    continue
                with trace.role(name):
                    try:
                        out.extend(step(item))
                    except Exception as e:
                        error = f"{type(e).__name__}: {e}"[:300]
                        trace.event("error", item.ruling_number, f"{error}; retried when the run resumes", status="error")
                        out.append(Prediction("error", item.ruling_number, None, {"system": name, "error": error}))
        return out

    predictions = []
    if checkpoint and checkpoint.exists():
        predictions = [p for line in checkpoint.open() if (p := Prediction(**json.loads(line))).system != "error"]
    todo = pending(items, systems, predictions)
    print(f"{len(items) - len(todo)} items already done, {len(todo)} to run", flush=True)
    with ThreadPoolExecutor(workers) as pool:
        futures = [pool.submit(one, item, names) for item, names in todo]
        for done, future in enumerate(as_completed(futures), 1):
            preds = future.result()
            predictions.extend(preds)
            if checkpoint:
                with checkpoint.open("a") as f:
                    f.writelines(json.dumps(asdict(p)) + "\n" for p in preds)
            if done % 10 == 0:
                print(f"{done}/{len(todo)}", flush=True)
    return latest(predictions)


EMITS = {"precedent_gate": ("precedent_gate", "gate_without_critic", "retrieval_ultra", "retrieval_top1")}


def pending(items: list[Item], systems: set[str], predictions: list[Prediction]) -> list[tuple[Item, set[str]]]:
    """Items with the steps still to run; a step that failed, or left one of its systems out, runs again."""
    done = {(p.ruling_number, p.system) for p in predictions if p.system != "error"}
    todo = [(i, {s for s in systems if any((i.ruling_number, e) not in done for e in EMITS.get(s, (s,)))}) for i in items]
    return [(i, names) for i, names in todo if names]


def latest(predictions: list[Prediction]) -> list[Prediction]:
    """One prediction per system and item, the last one written."""
    return list({(p.system, p.ruling_number): p for p in predictions if p.system != "error"}.values()) + \
        [p for p in predictions if p.system == "error"]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (centre - margin, centre + margin)


def score(predictions: list[Prediction], items: list[Item], liveness: Liveness) -> list[dict]:
    answers = {i.ruling_number: i.answer for i in items}
    by_system: dict[str, list[Prediction]] = {}
    for p in predictions:
        by_system.setdefault(p.system, []).append(p)
    rows = []
    for system, preds in sorted(by_system.items()):
        covered = [p for p in preds if p.code]
        acc10 = sum(p.code == answers[p.ruling_number] for p in covered)
        acc6 = sum(p.code[:6] == answers[p.ruling_number][:6] for p in covered)
        # An answer that is not a 10-digit line of the schedule counts as stale, whatever its length.
        stale = sum(len(p.code) != 10 or liveness.status(p.code) != LIVING for p in covered)
        lo, hi = wilson(acc10, len(covered))
        rows.append({"system": system, "items": len(preds), "coverage": round(len(covered) / max(1, len(preds)), 3),
                     "acc10_covered": round(acc10 / max(1, len(covered)), 3), "acc10_ci95": [round(lo, 3), round(hi, 3)],
                     "acc6_covered": round(acc6 / max(1, len(covered)), 3), "stale_code_rate": round(stale / max(1, len(covered)), 3),
                     "acc10_all": round(acc10 / max(1, len(preds)), 3)})
    return rows


def threshold_baseline(predictions: list[Prediction], coverage: float) -> list[Prediction]:
    """Retrieval + Ultra, abstaining below the confidence that yields the gate's coverage."""
    picks = sorted((p for p in predictions if p.system == "retrieval_ultra"),
                   key=lambda p: (-p.detail.get("confidence", 0), -p.detail.get("top_score", 0.0)))
    keep = round(coverage * len(picks))
    return [Prediction("confidence_threshold", p.ruling_number, p.code if i < keep else None, p.detail)
            for i, p in enumerate(picks)]
