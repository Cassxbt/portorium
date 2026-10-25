"""Meterless Nightshift as Portorium's primary AI provider (OpenAI-compatible, local runtime).

Every answer carries a Meterless compute receipt; Portorium keeps it with the verdict so a
judge can see which Nemotron model handled each step.
"""
import json
import time
from dataclasses import dataclass

import httpx

from .config import Settings
from .pacing import BACKOFF_SECONDS, NVIDIA_KEY
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
    def __init__(self, settings: Settings, live: bool):
        self.settings = settings
        self.live = live
        self.pacer = NVIDIA_KEY
        self.recorder = Recorder("nightshift")
        self._http = httpx.Client(timeout=320)

    def chat(self, messages: list[dict], model: str = ULTRA, thinking: bool = False, max_tokens: int = 1024) -> Answer:
        body: dict = {"model": model, "messages": messages, "max_tokens": max_tokens,
                      "chat_template_kwargs": {"enable_thinking": thinking}}
        if not self.live:
            data = self.recorder.replay(body)
        else:
            data = self._post(body)
            self.recorder.record(body, data)
        choice = (data.get("choices") or [{}])[0].get("message") or {}
        return Answer(content=choice.get("content") or "", receipt=data.get("meterless") or {})

    def chat_json(self, messages: list[dict], required: dict[str, tuple | None], model: str = ULTRA,
                  max_tokens: int = 1024) -> tuple[dict, Answer]:
        """JSON requested in the prompt and validated here, with one repair attempt.

        Not response_format: on Nightshift 0.92.3 a json_schema request returned the same text for
        different pinned models while the receipt named each requested model (tested 2026-10-25).
        """
        answer = self.chat(messages, model=model, max_tokens=max_tokens)
        for attempt in range(2):
            parsed = _parse(answer.content, required)
            if parsed is not None:
                return parsed, answer
            if attempt == 0:
                repair = messages + [{"role": "assistant", "content": answer.content},
                                     {"role": "user", "content": "That was not valid. Reply with only the JSON object."}]
                answer = self.chat(repair, model=model, max_tokens=max_tokens)
        raise NightshiftError(f"no valid JSON from {model}: {answer.content[:200]}")

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
                self.pacer.succeeded()
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code not in RETRYABLE:
                break
            self.pacer.rejected()
        raise NightshiftError(last)


def _parse(content: str, required: dict[str, tuple | None]) -> dict | None:
    text = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    for key, allowed in required.items():
        if key not in data or (allowed is not None and data[key] not in allowed):
            return None
    return data
