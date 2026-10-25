"""Meterless Nightshift as Portorium's primary AI provider (OpenAI-compatible, local runtime).

Every answer carries a Meterless compute receipt; Portorium keeps it with the verdict so a
judge can see which Nemotron model handled each step.
"""
import json
import time
from dataclasses import dataclass

import httpx

from .config import Settings
from .pacing import BACKOFF_SECONDS, Pacer
from .recorder import Recorder

APP = "portorium"
ULTRA = "nvidia/nemotron-3-ultra-550b-a55b"
LIGHTNING = "nvidia/nemotron-3.5-lightning-30b-a3b"
RETRYABLE = {429, 500, 502, 503, 504}


class NightshiftError(RuntimeError):
    pass


@dataclass
class Answer:
    content: str
    receipt: dict

    @property
    def routed_model(self) -> str | None:
        return self.receipt.get("routed_model")

    def json(self) -> dict:
        return json.loads(self.content)


class Nightshift:
    def __init__(self, settings: Settings, live: bool, per_minute: int = 30):
        self.settings = settings
        self.live = live
        self.pacer = Pacer(per_minute)
        self.recorder = Recorder("nightshift")
        self._http = httpx.Client(timeout=320)

    def chat(self, messages: list[dict], model: str = ULTRA, schema: dict | None = None,
             thinking: bool = False, max_tokens: int = 1024) -> Answer:
        body: dict = {"model": model, "messages": messages, "max_tokens": max_tokens,
                      "chat_template_kwargs": {"enable_thinking": thinking}}
        if schema is not None:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "result", "strict": True, "schema": schema}}
        if not self.live:
            data = self.recorder.replay(body)
        else:
            data = self._post(body)
            self.recorder.record(body, data)
        choice = (data.get("choices") or [{}])[0].get("message") or {}
        return Answer(content=choice.get("content") or "", receipt=data.get("meterless") or {})

    def _post(self, body: dict) -> dict:
        headers = {"Authorization": f"Bearer {self.settings.nightshift_token}",
                   "meterless-app": APP, "meterless-scope": "user"}
        last = ""
        for wait in (0, *BACKOFF_SECONDS):
            time.sleep(wait)
            self.pacer.wait()
            try:
                r = self._http.post(f"{self.settings.nightshift_url}/chat/completions", json=body, headers=headers)
            except httpx.HTTPError as e:
                last = f"{type(e).__name__}: {e}"
                continue
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code not in RETRYABLE:
                break
        raise NightshiftError(last)
