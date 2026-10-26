"""Run the evaluation and write predictions plus the score table to data/eval/results/.

Usage:
  python scripts/run_eval.py dev [--sample N] [--systems a,b,...]
  python scripts/run_eval.py test --final      # the frozen test set, once
"""
import argparse
import hashlib
import json
import subprocess
import time
from dataclasses import asdict
from pathlib import Path

from portorium.config import DATA, settings
from portorium.eval import load_items, run, score, threshold_baseline
from portorium.gate import PrecedentGate, prompts_sha256
from portorium.index import PrecedentIndex, index_dir
from portorium.liveness import Liveness
from portorium.nightshift import Nightshift
from portorium.nvidia import Nvidia
from portorium.rulings import RulingTexts
from portorium.trace import Trace, role
from portorium.zetaris import Zetaris

ALL = "keyword_top1,ultra_zero_shot,lightning_zero_shot,precedent_gate"
RESULTS = DATA / "eval" / "results"
LOCK = DATA / "eval" / "TEST_RUN.lock"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("split", choices=["dev", "test"])
    ap.add_argument("--sample", type=int)
    ap.add_argument("--systems", default=ALL)
    ap.add_argument("--final", action="store_true")
    ap.add_argument("--resume", help="checkpoint file to continue")
    ap.add_argument("--workers", type=int, default=2, help="items in flight; the shared NVIDIA pacer caps the request rate")
    args = ap.parse_args()
    if args.split == "test":
        if not args.final:
            print("the test set runs once: pass --final")
            return 1
        if LOCK.exists():
            lock = json.loads(LOCK.read_text())
            # The one test run may be resumed after a failure, on the same commit, never started again.
            same_run = args.resume and Path(args.resume).name == f"test_{lock['started']}.checkpoint.jsonl"
            commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
            if not same_run or commit != lock["git_commit"]:
                print(f"test already run: {LOCK.read_text()}")
                return 1
        if not settings().live:
            print("the frozen test run must be live, not sample mode")
            return 1
    s = settings()
    live = s.live
    run_tag = f"{args.split}-{time.strftime('%Y%m%dT%H%M%S')}"
    zetaris, nvidia, nightshift = Zetaris(s, live), Nvidia(s, live), Nightshift(s, live, run_tag=run_tag)
    tracer = Trace(run_id=run_tag)
    print(f"trace: {tracer.path}", flush=True)
    with tracer.bind("start-up: liveness cross-check"), role("Auditor"):
        liveness = Liveness.from_zetaris(zetaris)
    gate = PrecedentGate(PrecedentIndex(index_dir(live), live), nvidia, nightshift, liveness, RulingTexts(live), zetaris)
    items = load_items(args.split, args.sample)
    systems = set(args.systems.split(","))
    print(f"{args.split}: {len(items)} items, systems {sorted(systems)}", flush=True)
    started = time.strftime("%Y%m%dT%H%M%S")
    RESULTS.mkdir(parents=True, exist_ok=True)
    checkpoint = Path(args.resume) if args.resume else RESULTS / f"{args.split}_{started}.checkpoint.jsonl"
    print(f"checkpoint: {checkpoint}", flush=True)
    if args.split == "test" and LOCK.exists():
        lock = json.loads(LOCK.read_text())
        lock.setdefault("resumed", []).append(started)
        LOCK.write_text(json.dumps(lock))
    elif args.split == "test":
        digest = hashlib.sha256((DATA / "eval" / "test.jsonl").read_bytes()).hexdigest()
        commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        LOCK.write_text(json.dumps({"started": started, "test_jsonl_sha256": digest, "git_commit": commit,
                                    "gate_prompt_sha256": prompts_sha256()}))
    predictions = run(gate, items, systems, workers=args.workers, checkpoint=checkpoint, tracer=tracer)
    # Every system is scored on the same items: an item where any system failed is left out for all of them.
    errors = [asdict(p) for p in predictions if p.system == "error"]
    failed = {e["ruling_number"] for e in errors}
    scored = [i for i in items if i.ruling_number not in failed]
    kept = [p for p in predictions if p.system != "error" and p.ruling_number not in failed]
    gate_preds = [p for p in kept if p.system == "precedent_gate"]
    if gate_preds and any(p.system == "retrieval_ultra" for p in kept):
        threshold = threshold_baseline(kept, sum(bool(p.code) for p in gate_preds) / len(gate_preds))
        kept += threshold
        predictions += threshold
    table = score(kept, scored, gate.liveness)
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{args.split}_{started}.json"
    prompt = prompts_sha256()
    out.write_text(json.dumps({"split": args.split, "items": len(scored), "items_run": len(items), "items_failed": len(failed),
                               "gate_prompt_sha256": prompt, "table": table, "errors": errors,
                               "dq": gate.liveness.counts, "predictions": [asdict(p) for p in predictions]}, indent=1))
    for row in table:
        print(row)
    print(f"errors: {len(errors)} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
