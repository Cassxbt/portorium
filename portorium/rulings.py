"""CBP ruling texts: read from the local cache, fetched politely from rulings.cbp.gov when live."""
import json
import threading
import time
from pathlib import Path

import httpx

from .config import DATA
from .leakage import extract_facts

FULLTEXT = DATA / "fulltext"
URL = "https://rulings.cbp.gov/api/ruling/{}"
HEADERS = {"User-Agent": "portorium (hackathon; polite 1 req/s)", "Accept": "application/json"}


class RulingTexts:
    def __init__(self, live: bool, directory: Path = FULLTEXT):
        self.live, self.directory = live, directory
        self._http = httpx.Client(timeout=60, headers=HEADERS) if live else None
        self._last = 0.0
        self._lock = threading.Lock()

    def facts(self, ruling_number: str, limit: int = 900) -> str | None:
        """The product description from a ruling, or None when the text is not available."""
        text = self._text(ruling_number)
        return extract_facts(text)[:limit] if text else None

    def _text(self, ruling_number: str) -> str | None:
        path = self.directory / f"{ruling_number}.json"
        if path.exists():
            return json.loads(path.read_text()).get("text")
        if not self.live:
            return None
        with self._lock:
            time.sleep(max(0.0, 1.0 - (time.monotonic() - self._last)))
            self._last = time.monotonic()
            try:
                r = self._http.get(URL.format(ruling_number))
            except httpx.HTTPError:
                return None
        if r.status_code != 200:
            return None
        self.directory.mkdir(parents=True, exist_ok=True)
        path.write_text(r.text)
        return r.json().get("text")
