"""Precedent search over CBP rulings: Nemotron Embed for recall, Nemotron Rerank for order.

The index stores one vector per ruling subject. Every search is "as of" a date, so a ruling can
only find precedents issued before it - the same constraint an importer faces.
"""
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import DATA, SAMPLE, recordings
from .nvidia import Nvidia
from .recorder import Recorder

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
    """Live, a vector search over the full index; in sample mode, the recorded candidates of each search.

    Every search's candidate list is recorded like a service response, because float32 dot products
    are not bit-identical across machines or matrix sizes and the rerank request depends on the order.
    """

    def __init__(self, directory: Path = INDEX, live: bool = True, recordings_dir: Path | None = None):
        self.meta = [json.loads(line) for line in (directory / "meta.jsonl").open()]
        self.by_number = {m["ruling_number"]: m for m in self.meta}
        self.dates = np.array([m["ruling_date"] for m in self.meta])
        info = directory / "info.json"
        self.info = json.loads(info.read_text()) if info.exists() else {
            "rulings": len(self.meta), "horizon": str(np.datetime64(max(self.dates)) + np.timedelta64(1, "D"))}
        vectors = directory / "vectors.npy"
        self.vectors = np.load(vectors).astype(np.float32) if live and vectors.exists() else None
        self.recorder = Recorder("index", recordings_dir or recordings(live))
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
        # No ruling in the index is on or after the horizon, so any later date gives the same search.
        request = {"query": query, "as_of": min(as_of, self.info["horizon"]), "k": k, "exclude": sorted(exclude)}
        if self.vectors is None:
            found = self.recorder.replay(request)["candidates"]
        else:
            scores = self.vectors @ q
            found = [[self.meta[i]["ruling_number"], round(float(scores[i]), 6)]
                     for i in self._candidates(scores, as_of, k, exclude)]
            self.recorder.record(request, {"candidates": found})
        candidates = []
        for number, score in found:
            m = self.by_number[number]
            candidates.append(Precedent(number, m["ruling_date"], m["subject"], m["codes"], m.get("related", []), score))
        if rerank_top and candidates:
            logits = nvidia.rerank(query, [c.subject for c in candidates])
            for c, logit in zip(candidates, logits):
                c.score = logit
            candidates.sort(key=lambda c: -c.score)
            candidates = candidates[:rerank_top]
        return candidates

    def _candidates(self, scores: np.ndarray, as_of: str, k: int, exclude: set[str]) -> list[int]:
        """Rows of the k nearest rulings issued before as_of."""
        scores = np.where(self.dates >= as_of, -np.inf, scores)
        rows = []
        for i in np.argsort(-scores, kind="stable"):
            if not np.isfinite(scores[i]) or len(rows) == k:
                break
            if self.meta[i]["ruling_number"] not in exclude:
                rows.append(int(i))
        return rows
