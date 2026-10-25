"""Evaluation: the Precedent Gate against baselines on CBP rulings issued after the model cutoff.

Every system sees the same input: the ruling's product description with every code-like string and
the answer's own digits removed (leakage.py). Precedent search is "as of" the ruling date and excludes
the ruling itself and any ruling it relates to.
"""
import json
import math
import random
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from dataclasses import dataclass, field

import numpy as np

from .config import DATA
from .gate import CLASSIFIED, PrecedentGate, Verdict
from .index import PrecedentIndex
from .leakage import extract_facts, strip_codes
from .liveness import LIVING, Liveness
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
        path = DATA / "fulltext" / f"{m['ruling_number']}.json"
        if not path.exists():
            continue
        text = json.loads(path.read_text()).get("text") or ""
        subject = strip_codes(extract_facts(SUBJECT_PREFIX.sub("", m["subject"])) or SUBJECT_PREFIX.sub("", m["subject"]), [m["answer"]])
        facts = strip_codes(extract_facts(text), [m["answer"]])[:1500]
        items.append(Item(m["ruling_number"], m["ruling_date"], SPLIT_START[split], m["answer"], f"{subject}. {facts}",
                          {m["ruling_number"], *m["related"]}))
    if sample and sample < len(items):
        items = random.Random(seed).sample(items, sample)
    return items


ZERO_SHOT = ("You are a US customs classification analyst. Give the 10-digit HTSUS code for the product. "
             'Reply with only a JSON object: {"code": "<10 digits, no dots>"}')

PICK = ("You are a US customs classification analyst. Choose the code for the product from the prior CBP rulings "
        "listed, and state your confidence from 0 to 100 that it is correct at 10 digits. "
        'Reply with only a JSON object: {"code": "<one listed code, 10 digits>", "confidence": <0-100>}')


def zero_shot(ns: Nightshift, item: Item, model: str) -> Prediction:
    data, answer = ns.chat_json([{"role": "system", "content": ZERO_SHOT},
                                 {"role": "user", "content": f"PRODUCT: {item.query}"}], {"code": None}, model=model)
    code = re.sub(r"\D", "", str(data["code"]))[:10] or None
    name = "ultra_zero_shot" if model == ULTRA else "lightning_zero_shot"
    return Prediction(name, item.ruling_number, code, {"routed_model": answer.routed_model})


def retrieval_pick(ns: Nightshift, gate: PrecedentGate, item: Item) -> tuple[Prediction, Prediction]:
    precedents = gate.index.search(gate.nvidia, item.query, item.as_of, exclude=item.exclude)[: gate.top]
    top1 = Prediction("retrieval_top1", item.ruling_number, precedents[0].codes[0] if precedents else None)
    facts = {p.ruling_number: f for p in precedents if gate.texts and (f := gate.texts.facts(p.ruling_number))}
    listing = "\n".join(f"{p.ruling_number}: {p.subject}. CODES: {', '.join(p.codes)}."
                        + (f" FACTS: {facts[p.ruling_number]}" if p.ruling_number in facts else "") for p in precedents)
    data, answer = ns.chat_json([{"role": "system", "content": PICK},
                                 {"role": "user", "content": f"PRODUCT: {item.query}\nPRIOR RULINGS:\n{listing}"}],
                                {"code": None, "confidence": None})
    code = re.sub(r"\D", "", str(data["code"]))[:10] or None
    pick = Prediction("retrieval_ultra", item.ruling_number, code,
                      {"confidence": float(data.get("confidence") or 0), "routed_model": answer.routed_model,
                       "top_score": precedents[0].score if precedents else 0.0})
    return top1, pick


def gate_predictions(gate: PrecedentGate, item: Item) -> tuple[Prediction, Prediction]:
    verdict = gate.classify(item.query, item.as_of, exclude=item.exclude)
    full = Prediction("precedent_gate", item.ruling_number, verdict.code if verdict.decision == CLASSIFIED else None,
                      {"decision": verdict.decision, "reason": verdict.reason, "receipts": verdict.receipts,
                       "question": verdict.question, "judged": [asdict(j) for j in verdict.precedents]})
    living_blind = _ignore_liveness(verdict)
    return full, Prediction("gate_without_liveness", item.ruling_number, living_blind, {})


def _ignore_liveness(v: Verdict) -> str | None:
    codes = {j.code for j in v.precedents if j.match == "full" and j.code}
    return codes.pop() if len(codes) == 1 else None


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
        checkpoint: Path | None = None) -> list[Prediction]:
    """Predictions for every item; with a checkpoint file, finished items are kept and skipped on restart."""
    ns = gate.nightshift
    keyword = KeywordIndex(gate.index) if "keyword_top1" in systems else None

    steps = [("keyword_top1", lambda item: [keyword.top1(item)]),
             ("ultra_zero_shot", lambda item: [zero_shot(ns, item, ULTRA)]),
             ("lightning_zero_shot", lambda item: [zero_shot(ns, item, LIGHTNING)]),
             ("retrieval_ultra", lambda item: list(retrieval_pick(ns, gate, item))),
             ("precedent_gate", lambda item: list(gate_predictions(gate, item)))]

    def one(item: Item) -> list[Prediction]:
        out = []
        for name, step in steps:
            if name not in systems:
                continue
            try:
                out.extend(step(item))
            except Exception as e:
                out.append(Prediction("error", item.ruling_number, None, {"system": name, "error": f"{type(e).__name__}: {e}"[:300]}))
        return out

    predictions = []
    if checkpoint and checkpoint.exists():
        predictions = [Prediction(**json.loads(line)) for line in checkpoint.open()]
    done_items = {p.ruling_number for p in predictions}
    todo = [i for i in items if i.ruling_number not in done_items]
    print(f"{len(items) - len(todo)} items already done, {len(todo)} to run", flush=True)
    with ThreadPoolExecutor(workers) as pool:
        futures = [pool.submit(one, item) for item in todo]
        for done, future in enumerate(as_completed(futures), 1):
            preds = future.result()
            predictions.extend(preds)
            if checkpoint:
                with checkpoint.open("a") as f:
                    f.writelines(json.dumps(asdict(p)) + "\n" for p in preds)
            if done % 10 == 0:
                print(f"{done}/{len(todo)}", flush=True)
    return predictions


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
        stale = sum(liveness.status(p.code) != LIVING for p in covered if len(p.code) == 10)
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
