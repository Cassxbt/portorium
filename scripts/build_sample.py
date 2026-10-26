"""Build the sample-mode bundle in data/sample/ from one live walkthrough of the console.

  RECORD_SAMPLE=1 python scripts/build_sample.py      (live: credentials, Nightshift and Zetaris up)

The walkthrough goes through the console's own API, so the bundle holds exactly the service
responses those requests produce, plus the index rows their searches touched. Sample mode then
replays the responses and runs the gate's own logic on them.
"""
import json
import os
import sys

import numpy as np
from fastapi.testclient import TestClient

from portorium import web
from portorium.config import DATA, SAMPLE, settings
from portorium.index import INDEX, PrecedentIndex

IMPORTER = "demo"
CLASSIFIED = "Portable Bluetooth party speaker with two drivers in one enclosure, rechargeable battery, LED lights"
DEAD_CODE = "Women's V-neck pullover, 100% cotton jersey knit fabric, long sleeves, hemmed bottom"
QUESTION = "Yoga mat of polyurethane foam, 6 mm thick, 183 x 61 cm, for floor exercise"
ANSWER = "Cellular polyurethane foam throughout, with no textile layer, covering or reinforcement"


def main() -> int:
    if os.environ.get("RECORD_SAMPLE") != "1" or not settings().live:
        print("run live with RECORD_SAMPLE=1 so the responses are recorded into data/sample/recordings")
        return 1
    for path in (SAMPLE / "recordings").glob("*.jsonl"):
        path.unlink()
    (DATA / "memory" / f"{IMPORTER}.json").unlink(missing_ok=True)

    touched: set[int] = set()
    candidates = PrecedentIndex._candidates

    def capture(self, scores, as_of, k, exclude):
        rows = candidates(self, scores, as_of, k, exclude)
        touched.update(rows)
        return rows

    PrecedentIndex._candidates = capture
    client = TestClient(web.app)

    def post(path: str, body: dict) -> dict:
        r = client.post(path, json={"importer": IMPORTER, **body})
        r.raise_for_status()
        return r.json()

    client.get("/api/status").raise_for_status()
    for product in (CLASSIFIED, DEAD_CODE):
        v = post("/api/classify", {"product": product})
        print(f"{v['decision']:10s} {v['reason']:24s} {v['code'] or '':10s}  {product[:60]}")
    v = post("/api/classify", {"product": QUESTION})
    print(f"{v['decision']:10s} {v['reason']:24s} asks: {v['question']}")
    if v["question"]:
        v = post("/api/classify", {"product": QUESTION, "answers": [[v["question"], ANSWER]]})
        print(f"{v['decision']:10s} {v['reason']:24s} after the answer")
        v = post("/api/classify", {"product": QUESTION})
        print(f"{v['decision']:10s} {v['reason']:24s} again, with the answer recalled from memory")
    client.get("/api/rescreen").raise_for_status()
    post("/api/forget", {})

    meta = [line for i, line in enumerate((INDEX / "meta.jsonl").open()) if i in touched]
    rows = sorted(touched)
    (SAMPLE / "index").mkdir(parents=True, exist_ok=True)
    (SAMPLE / "index" / "meta.jsonl").write_text("".join(meta))
    np.save(SAMPLE / "index" / "vectors.npy", np.load(INDEX / "vectors.npy")[rows])
    sizes = {p.name: p.stat().st_size for p in sorted(SAMPLE.rglob("*")) if p.is_file()}
    print(json.dumps({"index_rows": len(rows), "bytes": sizes}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
