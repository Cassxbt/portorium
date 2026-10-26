"""Nemotron retrieval models called directly on NVIDIA's API.

Nightshift's Mac M1-M4 edition does not serve /v1/embeddings ("not served in the web build"),
so precedent search uses NVIDIA directly; all reasoning still goes through Nightshift.
"""
import time

import httpx
import numpy as np

from . import trace
from .config import Settings, recordings
from .pacing import BACKOFF_SECONDS, NVIDIA_KEY
from .recorder import Recorder

EMBED_MODEL = "nvidia/nemotron-3.5-embed-1b"
RERANK_MODEL = "nvidia/nemotron-3.5-rerank-8b"
EMBED_URL = "https://integrate.api.nvidia.com/v1/embeddings"
RERANK_URL = "https://ai.api.nvidia.com/v1/retrieval/nvidia/nemotron-3.5-rerank-8b/reranking"
RETRYABLE = {429, 500, 502, 503, 504}


class NvidiaError(RuntimeError):
    pass


class Nvidia:
    def __init__(self, settings: Settings, live: bool):
        self.settings = settings
        self.live = live
        self.pacer = NVIDIA_KEY
        self.recorder = Recorder("nvidia", recordings(live))
        self._http = httpx.Client(timeout=120)

    def embed(self, texts: list[str], input_type: str) -> np.ndarray:
        """input_type is "query" for products and "passage" for rulings."""
        body = {"model": EMBED_MODEL, "input": texts, "input_type": input_type, "encoding_format": "float"}
        data = self._call(EMBED_URL, body)
        vectors = np.array([row["embedding"] for row in sorted(data["data"], key=lambda r: r["index"])], dtype=np.float32)
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        """Relevance logits, aligned with the passages."""
        body = {"model": RERANK_MODEL, "query": {"text": query}, "passages": [{"text": p} for p in passages]}
        data = self._call(RERANK_URL, body)
        scores = [0.0] * len(passages)
        for row in data["rankings"]:
            scores[row["index"]] = row["logit"]
        return scores

    def _call(self, url: str, body: dict) -> dict:
        request = {"url": url, **body}
        if not self.live:
            data = self.recorder.replay(request)
            _trace(body, data, 0, "recording")
            return data
        last = ""
        for attempt, wait in enumerate((0, *BACKOFF_SECONDS)):
            time.sleep(wait)
            self.pacer.wait()
            try:
                r = self._http.post(url, json=body, headers={"Authorization": f"Bearer {self.settings.nvidia_api_key}"})
            except httpx.HTTPError as e:
                last = f"{type(e).__name__}: {e}"
                continue
            if r.status_code == 200:
                self.pacer.succeeded()
                data = r.json()
                # Embedding batches for the index are large and cached separately; record only small calls.
                if len(body.get("input", [])) <= 8:
                    self.recorder.record(request, data)
                _trace(body, data, attempt, "live")
                return data
            last = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code not in RETRYABLE:
                break
            if r.status_code == 429:
                self.pacer.rejected()
        trace.event(_action(body), _asked(body), last, model=body["model"], retry_count=attempt, status="error")
        raise NvidiaError(last)


def _action(body: dict) -> str:
    return "rerank" if "query" in body else "embed"


def _asked(body: dict) -> str:
    if "query" in body:
        return f"{body['query']['text']} vs {len(body['passages'])} passages"
    return f"{len(body['input'])} {body['input_type']} text(s): {body['input'][0]}"


def _trace(body: dict, data: dict, retries: int, source: str) -> None:
    if "query" in body:
        top = max((r["logit"] for r in data["rankings"]), default=0.0)
        answered = f"{len(data['rankings'])} relevance scores, top logit {top:.2f}"
    else:
        answered = f"{len(data['data'])} vectors of {len(data['data'][0]['embedding'])} dims"
    trace.event(_action(body), _asked(body), answered, model=body["model"], retry_count=retries, source=source)
