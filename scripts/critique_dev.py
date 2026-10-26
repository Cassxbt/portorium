"""Apply the schedule-text Critic to a stored dev run's classifications and compare with the baselines.

The gate's other steps are deterministic given the stored judgments, so only the Critic call is new.
Usage: python scripts/critique_dev.py data/eval/results/<dev run>.json [--workers 4]
"""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor

from portorium.config import settings
from portorium.eval import Prediction, load_items, threshold_baseline, wilson
from portorium.gate import CLASSIFIED, PrecedentGate, Verdict
from portorium.liveness import Liveness
from portorium.nightshift import Nightshift
from portorium.zetaris import Zetaris


def summary(name: str, preds: list[Prediction], answers: dict[str, str]) -> str:
    covered = [p for p in preds if p.code]
    right = sum(p.code == answers[p.ruling_number] for p in covered)
    lo, hi = wilson(right, len(covered))
    return (f"{name:22s} coverage {len(covered):3d}/{len(preds)} = {len(covered) / len(preds):.3f}  "
            f"acc10 {right}/{len(covered)} = {right / max(1, len(covered)):.3f}  CI95 [{lo:.3f}, {hi:.3f}]")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    run = json.load(open(args.run))
    items = {i.ruling_number: i for i in load_items(run["split"], run["items"])}
    answers = {n: i.answer for n, i in items.items()}
    s = settings()
    zetaris = Zetaris(s, True)
    gate = PrecedentGate(None, None, Nightshift(s, True, run_tag=f"critic-{time.strftime('%Y%m%dT%H%M%S')}"),
                         Liveness(set(), set()), zetaris=zetaris)
    gate_preds = [Prediction(**p) for p in run["predictions"] if p["system"] == "precedent_gate"]

    def check(p: Prediction) -> Prediction:
        if not p.code:
            return p
        v = gate.critique(items[p.ruling_number].query, [], Verdict(CLASSIFIED, p.detail["reason"], p.code, []))
        return Prediction("gate_with_critic", p.ruling_number, v.code, {**p.detail, "critique": v.critique})

    with ThreadPoolExecutor(args.workers) as pool:
        critiqued = list(pool.map(check, gate_preds))
    critiqued = [p if p.system == "gate_with_critic" else Prediction("gate_with_critic", p.ruling_number, None, p.detail)
                 for p in critiqued]
    coverage = sum(bool(p.code) for p in critiqued) / len(critiqued)
    baseline = threshold_baseline([Prediction(**p) for p in run["predictions"]], coverage)
    print(summary("gate", gate_preds, answers))
    print(summary("gate_with_critic", critiqued, answers))
    print(summary("threshold@same", baseline, answers))
    for p in critiqued:
        c = p.detail.get("critique")
        if c:
            mark = "ok " if c["code"] == answers[p.ruling_number] else "BAD"
            print(f"  {mark} {'kept ' if c['fits'] else 'CUT  '} {p.ruling_number} {c['code']} answer {answers[p.ruling_number]}: {c['reason'][:110]}")
    out = args.run.replace(".json", ".critic.json")
    json.dump([vars(p) for p in critiqued], open(out, "w"), indent=1)
    print("->", out)


if __name__ == "__main__":
    main()
