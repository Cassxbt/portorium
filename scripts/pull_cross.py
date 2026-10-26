#!/usr/bin/env python3
"""Polite snapshot of CBP CROSS rulings via the search backend used by rulings.cbp.gov.

Passes (all resumable; raw pages already on disk are reused, not re-fetched):
  terms    keyword search over TERMS, up to PAGES_PER_TERM pages of 100
  recent   term="*" with fromDate=RECENT_FROM, all pages (post-cutoff supplement)
  months   term="*" one calendar month at a time, 2010-2026 (complete corpus)
  counts   totalHits per ruling year (pageSize=1), for the year distribution of the whole database
  fulltext 50 random rulings from the pulled set via /api/ruling/<number>
  fulltext_hq_extra  20 more random HQ-collection rulings (supplement, kept in a separate dir)
  build    dedupe raw pages into rulings.jsonl

Output goes to data/cross/ (not committed). Usage: python3 scripts/pull_cross.py [terms|recent|months|counts|fulltext|build|all]
"""
import json
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://rulings.cbp.gov"
UA = "research-preflight (hackathon)"
MIN_INTERVAL = 1.0
PAGE_SIZE = 100
PAGES_PER_TERM = 5
RECENT_FROM = "2026-01-01"
FULLTEXT_SAMPLE = 50
SEED = 20261024

OUT = Path(__file__).resolve().parents[1] / "data" / "cross"
RAW = OUT / "raw"
FULLTEXT = OUT / "fulltext"

TERMS = [
    "backpack", "yoga mat", "bluetooth speaker", "cotton t-shirt", "ceramic mug", "LED lamp",
    "phone case", "dog leash", "water bottle", "sneakers", "leather handbag", "wallet",
    "sunglasses", "wristwatch", "headphones", "earbuds", "USB cable", "power bank",
    "laptop sleeve", "tablet stand", "smart watch", "fitness tracker", "electric toothbrush",
    "hair dryer", "coffee maker", "blender", "air fryer", "cutlery set", "stainless steel tumbler",
    "glass jar", "candle", "picture frame", "throw blanket", "bath towel", "bed sheet",
    "pillow", "curtain", "rug", "shower curtain", "storage bin", "plastic container",
    "toy", "doll", "board game", "puzzle", "stuffed animal", "baby stroller", "baby bottle",
    "swimsuit", "jeans", "hoodie", "socks", "gloves", "beanie hat", "baseball cap",
    "boots", "sandals", "umbrella", "suitcase", "duffel bag", "tote bag",
    "pet bed", "cat toy", "bicycle helmet", "camping tent", "sleeping bag", "fishing rod",
    "dumbbell", "resistance band", "garden hose", "flashlight", "extension cord",
    "lithium battery", "solar panel", "electric scooter", "drone", "phone charger",
    "lipstick", "shampoo", "tea",
]

HEADERS = {"User-Agent": UA, "Accept": "application/json"}
_last_request = 0.0


def get(path, params=None, max_tries=6):
    """GET with a global 1 req/s floor and exponential backoff on 429/5xx/network errors."""
    global _last_request
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    delay = 5.0
    for attempt in range(1, max_tries + 1):
        wait = MIN_INTERVAL - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                retry_after = e.headers.get("Retry-After")
                pause = float(retry_after) if retry_after and retry_after.isdigit() else delay
                print(f"  HTTP {e.code} on {url}; backing off {pause:.0f}s (try {attempt})", flush=True)
                time.sleep(pause)
                delay = min(delay * 2, 120)
                continue
            return e.code, e.read()
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"  network error {e!r}; backing off {delay:.0f}s (try {attempt})", flush=True)
            time.sleep(delay)
            delay = min(delay * 2, 120)
    raise RuntimeError(f"giving up on {url}")


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


def fetch_search_page(name, params, manifest):
    """Fetch one search page unless already on disk; returns parsed JSON."""
    path = RAW / f"{name}.json"
    if path.exists():
        return json.loads(path.read_bytes())
    status, body = get("/api/search", params)
    if status != 200:
        raise RuntimeError(f"{name}: HTTP {status}: {body[:200]!r}")
    path.write_bytes(body)
    manifest.write(json.dumps({"file": path.name, "params": params, "status": status,
                               "fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}) + "\n")
    manifest.flush()
    return json.loads(body)


def pull_terms():
    with open(RAW / "manifest.jsonl", "a") as manifest:
        for i, term in enumerate(TERMS, 1):
            page = 1
            while page <= PAGES_PER_TERM:
                params = {"term": term, "pageSize": PAGE_SIZE, "page": page}
                data = fetch_search_page(f"term_{slug(term)}_p{page:03d}", params, manifest)
                total, got = data["totalHits"], len(data["rulings"])
                if page == 1:
                    print(f"[{i}/{len(TERMS)}] {term}: totalHits={total}", flush=True)
                if got < PAGE_SIZE or page * PAGE_SIZE >= total:
                    break
                page += 1


def pull_recent():
    with open(RAW / "manifest.jsonl", "a") as manifest:
        page = 1
        while True:
            params = {"term": "*", "fromDate": RECENT_FROM, "pageSize": PAGE_SIZE, "page": page,
                      "sortBy": "DATE_DESC"}
            data = fetch_search_page(f"recent_p{page:03d}", params, manifest)
            total = data["totalHits"]
            print(f"recent page {page}: totalHits={total}", flush=True)
            if len(data["rulings"]) < PAGE_SIZE or page * PAGE_SIZE >= total:
                break
            page += 1


def pull_months(first_year=2010, last_year=2026):
    """Complete corpus: every ruling, one month at a time (search caps totalHits at 10,000 per query)."""
    with open(RAW / "manifest.jsonl", "a") as manifest:
        for year in range(first_year, last_year + 1):
            for month in range(1, 13):
                start = f"{year}-{month:02d}-01"
                end = f"{year + (month == 12)}-{month % 12 + 1:02d}-01"
                page = 1
                while True:
                    params = {"term": "*", "fromDate": start, "toDate": end, "pageSize": PAGE_SIZE, "page": page,
                              "sortBy": "DATE_DESC"}
                    data = fetch_search_page(f"month_{year}_{month:02d}_p{page:03d}", params, manifest)
                    total = data["totalHits"]
                    if page == 1:
                        print(f"{year}-{month:02d}: totalHits={total}", flush=True)
                    if len(data["rulings"]) < PAGE_SIZE or page * PAGE_SIZE >= total:
                        break
                    page += 1


def pull_counts():
    out = OUT / "year_counts.json"
    counts = json.loads(out.read_text()) if out.exists() else {}
    for year in range(1989, 2027):
        if str(year) in counts:
            continue
        params = {"term": "*", "fromDate": f"{year}-01-01", "toDate": f"{year}-12-31", "pageSize": 1}
        status, body = get("/api/search", params)
        if status != 200:
            raise RuntimeError(f"counts {year}: HTTP {status}")
        counts[str(year)] = json.loads(body)["totalHits"]
        out.write_text(json.dumps(counts, indent=1, sort_keys=True))
    status, body = get("/api/stat/lastupdate")
    (OUT / "lastupdate.json").write_bytes(body)
    print(json.dumps(counts), body.decode())


def iter_raw_rulings():
    for path in sorted(RAW.glob("*.json")):
        if path.name == "manifest.jsonl":
            continue
        data = json.loads(path.read_bytes())
        for r in data["rulings"]:
            yield path.name, r


def build():
    seen = {}
    for fname, r in iter_raw_rulings():
        key = (r["collection"], r["rulingNumber"])
        if key not in seen:
            seen[key] = dict(r, _sources=[])
        seen[key]["_sources"].append(fname.rsplit("_p", 1)[0])
    rows = sorted(seen.values(), key=lambda r: (r["rulingDate"] or "", r["rulingNumber"]))
    with open(OUT / "rulings.jsonl", "w") as f:
        for r in rows:
            r["_sources"] = sorted(set(r["_sources"]))
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"rulings.jsonl: {len(rows)} distinct (collection, rulingNumber)")


def pull_fulltext(directory=FULLTEXT, pool=None, size=FULLTEXT_SAMPLE):
    directory.mkdir(exist_ok=True)
    rows = [json.loads(line) for line in open(OUT / "rulings.jsonl")]
    if pool:
        rows = [r for r in rows if pool(r)]
    sample = random.Random(SEED).sample(rows, size)
    for i, r in enumerate(sample, 1):
        path = directory / f"{r['collection']}_{r['rulingNumber']}.json"
        if path.exists():
            continue
        status, body = get(f"/api/ruling/{urllib.parse.quote(r['rulingNumber'])}")
        if status != 200:
            print(f"[{i}] {r['rulingNumber']}: HTTP {status}", flush=True)
            continue
        doc = json.loads(body)
        doc.pop("url", None)  # short-lived presigned S3 link; not part of the record
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1))
        print(f"[{i}/{size}] {r['collection']} {r['rulingNumber']} ok", flush=True)


def pull_fulltext_hq_extra():
    """Supplement: the random 50 contain few HQ letters, so draw 20 more from HQ only."""
    pull_fulltext(OUT / "fulltext_hq_extra", lambda r: r["collection"].lower() == "hq", 20)


if __name__ == "__main__":
    RAW.mkdir(parents=True, exist_ok=True)
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    steps = {"terms": pull_terms, "recent": pull_recent, "months": pull_months, "counts": pull_counts,
             "build": build, "fulltext": pull_fulltext, "fulltext_hq_extra": pull_fulltext_hq_extra}
    if step == "all":
        for name in ("terms", "recent", "counts", "build", "fulltext"):
            steps[name]()
    else:
        steps[step]()
