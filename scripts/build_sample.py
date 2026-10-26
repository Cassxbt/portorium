"""Build the sample-mode bundle from one live walkthrough of the console.

  RECORD_SAMPLE=1 python scripts/build_sample.py      (live: credentials, Nightshift and Zetaris up)

The walkthrough goes through the console's own API, so data/sample/ holds exactly the service
responses those requests produced (NVIDIA, Nightshift, Zetaris, and each search's candidates) and
the index entries they name. Each request's input is saved to input_examples/ and its verdict and
trace to output_examples/: real outputs of this run, which the site displays and nothing replays.
"""
import json
import os
import subprocess
import sys

from fastapi.testclient import TestClient

from portorium import web
from portorium.config import DATA, ROOT, SAMPLE, settings
from portorium.index import INDEX, PrecedentIndex

IMPORTER = "demo"
CASES = [
    ("classified", "A living precedent for the same article",
     "Portable Bluetooth party speaker with two drivers in one enclosure, rechargeable battery, LED lights", None),
    ("dead-code", "The closest precedent's code no longer exists",
     "Women's V-neck pullover, 100% cotton jersey knit fabric, long sleeves, hemmed bottom", None),
    ("question", "The Critic asks the one fact the schedule needs",
     "Men's knitted polo shirt with short sleeves and a three-button placket", "100% cotton jersey knit"),
]


def main() -> int:
    if os.environ.get("RECORD_SAMPLE") != "1" or not settings().live:
        print("run live with RECORD_SAMPLE=1 so the responses are recorded into data/sample/recordings")
        return 1
    for path in (SAMPLE / "recordings").glob("*.jsonl"):
        path.unlink()
    (DATA / "memory" / f"{IMPORTER}.json").unlink(missing_ok=True)
    for directory in ("input_examples", "output_examples"):
        (ROOT / directory).mkdir(exist_ok=True)
    client = TestClient(web.app)

    def classify(name: str, title: str, product: str, answers: list) -> dict:
        body = {"product": product, "importer": IMPORTER, "answers": answers}
        r = client.post("/api/classify", json=body)
        r.raise_for_status()
        v = r.json()
        trace = client.get(f"/api/trace/{v.pop('trace')}").json()
        (ROOT / "input_examples" / f"{name}.json").write_text(json.dumps(body, indent=1) + "\n")
        (ROOT / "output_examples" / f"{name}.json").write_text(
            json.dumps({"title": title, "input": body, "verdict": v, "trace": trace}, indent=1) + "\n")
        print(f"{name:22s} {v['decision']:10s} {v['reason']:24s} {v['code'] or ''}  {v['question'][:70]}")
        return v

    client.get("/api/status").raise_for_status()
    for name, title, product, answer in CASES:
        v = classify(name, title, product, [])
        if answer and v["question"]:
            classify(f"{name}-answered", "The importer's answer, then the whole loop again", product, [[v["question"], answer]])
            classify(f"{name}-remembered", "The answer recalled from memory on the next request", product, [])
    client.get("/api/rescreen").raise_for_status()
    client.post("/api/forget", json={"importer": IMPORTER}).raise_for_status()
    (DATA / "memory" / f"{IMPORTER}.json").unlink(missing_ok=True)

    used = set()
    for line in (SAMPLE / "recordings" / "index.jsonl").open():
        used.update(number for number, _ in json.loads(line)["response"]["candidates"])
    full = PrecedentIndex(INDEX, live=True)
    (SAMPLE / "index").mkdir(parents=True, exist_ok=True)
    (SAMPLE / "index" / "meta.jsonl").write_text("".join(json.dumps(m) + "\n" for m in full.meta if m["ruling_number"] in used))
    (SAMPLE / "index" / "info.json").write_text(json.dumps(full.info) + "\n")

    # A fresh clone must send byte-identical prompts, so every product description they used must be committed.
    facts = [f"data/facts/{n}.txt" for n in sorted(used) if (DATA / "facts" / f"{n}.txt").exists()]
    untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", *facts], cwd=ROOT,
                               capture_output=True, text=True).stdout.split()
    sizes = {str(p.relative_to(ROOT)): p.stat().st_size for p in sorted(SAMPLE.rglob("*")) if p.is_file()}
    print(json.dumps({"index_entries": len(used), "bytes": sizes, "facts_to_commit": untracked}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
