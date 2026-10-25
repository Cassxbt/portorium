"""Fetch CBP ruling full texts politely (1 request/s, descriptive User-Agent), resumable.

Usage: python scripts/fetch_fulltext.py <file.jsonl with ruling_number> [...]
"""
import json
import sys
import time

import httpx

from portorium.config import DATA

OUT = DATA / "fulltext"
URL = "https://rulings.cbp.gov/api/ruling/{}"
HEADERS = {"User-Agent": "portorium-research (hackathon; polite 1 req/s)", "Accept": "application/json"}


def main(paths: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    numbers = []
    for path in paths:
        numbers += [json.loads(line)["ruling_number"] for line in open(path)]
    todo = [n for n in dict.fromkeys(numbers) if not (OUT / f"{n}.json").exists()]
    print(f"{len(numbers)} requested, {len(todo)} to fetch", flush=True)
    with httpx.Client(timeout=60, headers=HEADERS) as http:
        for i, n in enumerate(todo, 1):
            for wait in (0, 5, 30, 90):
                time.sleep(max(wait, 1.0))
                try:
                    r = http.get(URL.format(n))
                except httpx.HTTPError:
                    continue
                if r.status_code == 200:
                    (OUT / f"{n}.json").write_text(r.text)
                    break
                if r.status_code not in (429, 500, 502, 503, 504):
                    print(f"{n}: HTTP {r.status_code}", flush=True)
                    break
            if i % 100 == 0:
                print(f"{i}/{len(todo)}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
