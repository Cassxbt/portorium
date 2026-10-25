"""Run the evaluation and write predictions plus the score table to data/eval/results/.

Usage:
  python scripts/run_eval.py dev [--sample N] [--systems a,b,...]
  python scripts/run_eval.py test --final      # the frozen test set, once
"""
import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path

from portorium.config import DATA, settings
from portorium.eval import load_items, run, score, threshold_baseline
from portorium.gate import PrecedentGate
from portorium.index import PrecedentIndex
from portorium.liveness import Liveness
from portorium.nightshift import Nightshift
from portorium.nvidia import Nvidia
from portorium.rulings import RulingTexts
from portorium.zetaris import Zetaris

ALL = "keyword_top1,ultra_zero_shot,lightning_zero_shot,retrieval_ultra,precedent_gate"
RESULTS = DATA / "eval" / "results"
LOCK = DATA / "eval" / "TEST_RUN.lock"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("split", choices=["dev", "test"])
    ap.add_argument("--sample", type=int)
    ap.add_argument("--systems", default=ALL)
    ap.add_argument("--final", action="store_true")
    ap.add_argument("--resume", help="checkpoint file to continue")
    args = ap.parse_args()
    if args.split == "test":
        if not args.final:
            print("the test set runs once: pass --final")
            return 1
        if LOCK.exists():
            print(f"test already run: {LOCK.read_text()}")
            return 1
    s = settings()
    live = s.live
    run_tag = f"{args.split}-{time.strftime('%Y%m%dT%H%M%S')}"
    zetaris, nvidia, nightshift = Zetaris(s, live), Nvidia(s, live), Nightshift(s, live, run_tag=run_tag)
    gate = PrecedentGate(PrecedentIndex(), nvidia, nightshift, Liveness.from_zetaris(zetaris), RulingTexts(live), zetaris)
    items = load_items(args.split, args.sample)
    systems = set(args.systems.split(","))
    print(f"{args.split}: {len(items)} items, systems {sorted(systems)}", flush=True)
    started = time.strftime("%Y%m%dT%H%M%S")
    RESULTS.mkdir(parents=True, exist_ok=True)
    checkpoint = Path(args.resume) if args.resume else RESULTS / f"{args.split}_{started}.checkpoint.jsonl"
    print(f"checkpoint: {checkpoint}", flush=True)
    if args.split == "test":
        LOCK.write_text(f"started {started}")
    predictions = run(gate, items, systems, checkpoint=checkpoint)
    gate_preds = [p for p in predictions if p.system == "precedent_gate"]
    if gate_preds and "retrieval_ultra" in systems:
        coverage = sum(bool(p.code) for p in gate_preds) / len(gate_preds)
        predictions += threshold_baseline(predictions, coverage)
    table = score([p for p in predictions if p.system != "error"], items, gate.liveness)
    errors = [asdict(p) for p in predictions if p.system == "error"]
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{args.split}_{started}.json"
    out.write_text(json.dumps({"split": args.split, "items": len(items), "table": table, "errors": errors,
                               "dq": gate.liveness.counts, "predictions": [asdict(p) for p in predictions]}, indent=1))
    for row in table:
        print(row)
    print(f"errors: {len(errors)} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
