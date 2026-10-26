"""Precedent search over CBP rulings: Nemotron Embed for recall, Nemotron Rerank for order.

The index stores one vector per ruling subject. Every search is "as of" a date, so a ruling can
only find precedents issued before it - the same constraint an importer faces.
"""
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import DATA, SAMPLE
from .nvidia import Nvidia

INDEX = DATA / "index"


def index_dir(live: bool) -> Path:
    """The full index (scripts/build_index.py) for live runs; sample mode uses the committed subset."""
    return INDEX if live else SAMPLE / "index"


@dataclass
class Precedent:
    ruling_number: str
    ruling_date: str
    subject: str
    codes: list[str]
    related: list[str]
    score: float = 0.0


class PrecedentIndex:
    def __init__(self, directory: Path = INDEX):
        self.meta = [json.loads(line) for line in (directory / "meta.jsonl").open()]
        self.vectors = np.load(directory / "vectors.npy").astype(np.float32)
        self.dates = np.array([m["ruling_date"] for m in self.meta])
        self._searches: dict[tuple, list[Precedent]] = {}

    def search(self, nvidia: Nvidia, query: str, as_of: str, k: int = 20, rerank_top: int = 8,
               exclude: set[str] = frozenset()) -> list[Precedent]:
        key = (query, as_of, k, rerank_top, frozenset(exclude))
        if key not in self._searches:
            self._searches[key] = self._search(nvidia, query, as_of, k, rerank_top, exclude)
        return [Precedent(**vars(p)) for p in self._searches[key]]

    def _search(self, nvidia: Nvidia, query: str, as_of: str, k: int, rerank_top: int,
                exclude: set[str]) -> list[Precedent]:
        q = nvidia.embed([query], input_type="query")[0]
        scores = self.vectors @ q
        candidates = []
        for i in self._candidates(scores, as_of, k, exclude):
            m = self.meta[i]
            candidates.append(Precedent(m["ruling_number"], m["ruling_date"], m["subject"], m["codes"],
                                        m.get("related", []), float(scores[i])))
        if rerank_top and candidates:
            logits = nvidia.rerank(query, [c.subject for c in candidates])
            for c, logit in zip(candidates, logits):
                c.score = logit
            candidates.sort(key=lambda c: -c.score)
            candidates = candidates[:rerank_top]
        return candidates

    def _candidates(self, scores: np.ndarray, as_of: str, k: int, exclude: set[str]) -> list[int]:
        """Rows of the k nearest rulings issued before as_of. The sort is stable so rulings with identical
        subjects keep index order, which lets a subset index return the same candidates as the full one."""
        scores = np.where(self.dates >= as_of, -np.inf, scores)
        rows = []
        for i in np.argsort(-scores, kind="stable"):
            if not np.isfinite(scores[i]) or len(rows) == k:
                break
            if self.meta[i]["ruling_number"] not in exclude:
                rows.append(int(i))
        return rows
