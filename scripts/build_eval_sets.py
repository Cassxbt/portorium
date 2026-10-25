"""Dev and test sets of CBP classification rulings, both after the Nemotron 3.5 post-training cutoff.

  dev   2026-01-01 .. 2026-05-31   tuning only
  test  2026-06-01 ..              run once, frozen

An item is a "Classification" ruling citing exactly one 10-digit code, so the label is unambiguous.
Usage: python scripts/build_eval_sets.py <rulings.jsonl>
"""
import json
import re
import sys

from portorium.config import DATA

CODE = re.compile(r"^\d{4}\.\d{2}\.\d{4}$")
SPLITS = {"dev": ("2026-01-01", "2026-06-01"), "test": ("2026-06-01", "9999-12-31")}
OUT = DATA / "eval"


def main(rulings_path: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    items = {name: [] for name in SPLITS}
    for line in open(rulings_path):
        r = json.loads(line)
        codes = [c.strip() for c in str(r.get("tariffs") or "").split(",") if CODE.match(c.strip())]
        if len(codes) != 1 or "Classification" not in (r.get("categories") or ""):
            continue
        date = (r.get("rulingDate") or "")[:10]
        for name, (start, end) in SPLITS.items():
            if start <= date < end:
                related = set(r.get("relatedRulings") or []) | set(r.get("modifies") or []) | set(r.get("revokes") or [])
                items[name].append({"ruling_number": r["rulingNumber"], "ruling_date": date,
                                    "collection": r.get("collection"), "subject": " ".join((r.get("subject") or "").split()),
                                    "answer": codes[0].replace(".", ""), "related": sorted(related)})
    for name, rows in items.items():
        rows.sort(key=lambda m: (m["ruling_date"], m["ruling_number"]))
        with (OUT / f"{name}.jsonl").open("w") as f:
            f.writelines(json.dumps(m) + "\n" for m in rows)
        print(f"{name}: {len(rows)} items, {sum(bool(m['related']) for m in rows)} with related rulings")


if __name__ == "__main__":
    main(sys.argv[1])
