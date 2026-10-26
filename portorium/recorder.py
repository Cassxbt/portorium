"""Record live service responses and replay them in sample mode.

Each service keeps one JSONL file of {key, request, response}. The key is a hash of the
request, so a fresh clone without credentials reproduces exactly what the live run saw.
"""
import hashlib
import json
import threading
from pathlib import Path

from .config import RECORDINGS


class MissingRecording(LookupError):
    pass


class Recorder:
    def __init__(self, service: str, directory: Path = RECORDINGS):
        self.path = directory / f"{service}.jsonl"
        self._cache: dict[str, dict] | None = None
        self._lock = threading.Lock()

    @staticmethod
    def key(request: dict) -> str:
        return hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()[:24]

    def _load(self) -> dict[str, dict]:
        """Read the file once; the gate's parallel calls must never see a half-filled cache."""
        with self._lock:
            if self._cache is None:
                cache = {}
                if self.path.exists():
                    for line in self.path.open():
                        row = json.loads(line)
                        cache[row["key"]] = row["response"]
                self._cache = cache
            return self._cache

    def replay(self, request: dict) -> dict:
        response = self._load().get(self.key(request))
        if response is None:
            raise MissingRecording(
                f"{self.path.name}: no recording for this request. Sample mode replays the inputs listed in the README; "
                "if you answered a question or signed off first, run `portorium forget` to reset memory, or run live.")
        return response

    def record(self, request: dict, response: dict) -> None:
        """Append the live response; on replay the latest line for a request wins."""
        key = self.key(request)
        cache = self._load()
        with self._lock:
            if cache.get(key) == response:
                return
            cache[key] = response
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps({"key": key, "request": request, "response": response}) + "\n")
