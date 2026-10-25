"""Embed every labelled CBP ruling subject before a cutoff with Nemotron 3.5 Embed 1B.

Resumable: vectors are written in chunks to data/index/parts/ and merged at the end.
Usage: python scripts/build_index.py <rulings.jsonl> [cutoff=2026-06-01]
"""
import json
import re
import sys

import numpy as np

from portorium.config import settings
from portorium.index import INDEX
from portorium.nvidia import Nvidia

CODE = re.compile(r"^\d{4}\.\d{2}\.\d{4}$")
BATCH = 50
CHUNK = 2000


def labelled(rulings_path: str, cutoff: str) -> list[dict]:
    out = []
    for line in open(rulings_path):
        r = json.loads(line)
        date = (r.get("rulingDate") or "")[:10]
        codes = [c.strip().replace(".", "") for c in str(r.get("tariffs") or "").split(",") if CODE.match(c.strip())]
        subject = " ".join((r.get("subject") or "").split())
        if codes and subject and "0001" < date < cutoff:
            related = set(r.get("relatedRulings") or []) | set(r.get("modifies") or []) | set(r.get("revokes") or [])
            out.append({"ruling_number": r["rulingNumber"], "ruling_date": date, "subject": subject,
                        "codes": codes, "related": sorted(related)})
    out.sort(key=lambda m: (m["ruling_date"], m["ruling_number"]))
    return out


def main() -> None:
    cutoff = sys.argv[2] if len(sys.argv) > 2 else "2026-06-01"
    meta = labelled(sys.argv[1], cutoff)
    parts = INDEX / "parts"
    parts.mkdir(parents=True, exist_ok=True)
    nvidia = Nvidia(settings(), live=True)
    for start in range(0, len(meta), CHUNK):
        path = parts / f"{start:06d}.npy"
        if path.exists():
            continue
        texts = [m["subject"] for m in meta[start:start + CHUNK]]
        vecs = np.concatenate([nvidia.embed(texts[i:i + BATCH], input_type="passage") for i in range(0, len(texts), BATCH)])
        np.save(path, vecs.astype(np.float16))
        print(f"embedded {start + len(texts)}/{len(meta)}", flush=True)
    vectors = np.concatenate([np.load(parts / f"{s:06d}.npy") for s in range(0, len(meta), CHUNK)])
    np.save(INDEX / "vectors.npy", vectors)
    with (INDEX / "meta.jsonl").open("w") as f:
        f.writelines(json.dumps(m) + "\n" for m in meta)
    print(f"index: {len(meta)} rulings before {cutoff}, dim {vectors.shape[1]}")


if __name__ == "__main__":
    main()
