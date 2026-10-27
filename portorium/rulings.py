"""CBP ruling product descriptions.

Portorium uses only the product description of a ruling (the text after the salutation, before the
legal analysis). The repository keeps those descriptions in data/facts/; full ruling texts, whose
headers carry addressees' names and addresses, stay in a local cache that is not committed.
"""
import json
import re
import threading
import time
from pathlib import Path

import httpx

from .config import DATA
from .leakage import extract_facts

FACTS = DATA / "facts"
FULLTEXT = DATA / "fulltext"
URL = "https://rulings.cbp.gov/api/ruling/{}"
HEADERS = {"User-Agent": "portorium (hackathon; polite 1 req/s)", "Accept": "application/json"}


def stored_facts(ruling_number: str) -> str | None:
    """The committed product description, or the one derived from the local full-text cache."""
    path = FACTS / f"{ruling_number}.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    raw = FULLTEXT / f"{ruling_number}.json"
    if raw.exists():
        return save_facts(ruling_number, json.loads(raw.read_text(encoding="utf-8")).get("text") or "")
    return None


_PERSONAL = re.compile(
    r"Dear |\b(Mr|Ms|Mrs|Dr)\.? [A-Z]|, [A-Z]{2} \d{5}|[\w.+-]+@[\w-]+\.\w+|\(?\d{3}\)?[ -.]\d{3}[ -.]\d{4}")


def scrub(facts: str) -> str:
    """Drop any sentence that carries a salutation, a titled name, a postal address, an email or a phone number."""
    facts = re.sub(r"Dear [^:]{1,80}:\s*", "", facts)
    sentences = re.split(r"(?<!\bMr\.)(?<!\bMs\.)(?<!\bDr\.)(?<!\bMrs\.)(?<=[.!?])\s+", facts)
    return " ".join(s for s in sentences if not _PERSONAL.search(s))


def save_facts(ruling_number: str, text: str) -> str:
    # CBP text carries \r\n; a prompt built now must equal one built later from the file, which reads back as \n.
    facts = scrub(extract_facts(text)).replace("\r\n", "\n").replace("\r", "\n")
    FACTS.mkdir(parents=True, exist_ok=True)
    (FACTS / f"{ruling_number}.txt").write_text(facts, encoding="utf-8")
    return facts


class RulingTexts:
    def __init__(self, live: bool):
        self.live = live
        self._http = httpx.Client(timeout=60, headers=HEADERS) if live else None
        self._last = 0.0
        self._lock = threading.Lock()

    def facts(self, ruling_number: str, limit: int = 900) -> str | None:
        """A ruling's product description, fetched from CBP when live and not yet stored."""
        facts = stored_facts(ruling_number)
        if facts is None and self.live:
            text = self._fetch(ruling_number)
            facts = save_facts(ruling_number, text) if text else None
        return facts[:limit] if facts else None

    def _fetch(self, ruling_number: str) -> str | None:
        with self._lock:
            time.sleep(max(0.0, 1.0 - (time.monotonic() - self._last)))
            self._last = time.monotonic()
            try:
                r = self._http.get(URL.format(ruling_number))
            except httpx.HTTPError:
                return None
        if r.status_code != 200:
            return None
        FULLTEXT.mkdir(parents=True, exist_ok=True)
        (FULLTEXT / f"{ruling_number}.json").write_text(r.text, encoding="utf-8")
        return r.json().get("text")
