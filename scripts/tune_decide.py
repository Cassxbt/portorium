"""Replay stored gate judgments through decision policies; no model calls.

Each evaluation run stores the gate's per-precedent judgments, so decision rules can be compared on
the dev set offline. Usage: python scripts/tune_decide.py data/eval/results/<dev run>.json
"""
import json
import sys

from portorium.eval import wilson
from portorium.gate import CLASSIFIED, JudgedPrecedent, PrecedentGate
from portorium.liveness import Liveness
from portorium.nightshift import Answer

POLICIES = {"current": {}, "old_precedent_rule": {"old_precedent_rule": True}}


def main(path: str) -> None:
    run = json.load(open(path))
    answers = {}
    for line in open(f"data/eval/{run['split']}.jsonl"):
        m = json.loads(line)
        answers[m["ruling_number"]] = m["answer"]
    gated = [p for p in run["predictions"] if p["system"] == "precedent_gate" and "judged" in p["detail"]]
    print(f"{len(gated)} gate predictions with stored judgments")
    for name, options in POLICIES.items():
        gate = PrecedentGate(None, None, None, Liveness(set(), set()), **options)
        covered = correct = 0
        reasons: dict[str, int] = {}
        for p in gated:
            judged = [JudgedPrecedent(**j) for j in p["detail"]["judged"]]
            v = gate.decide(judged, p["detail"].get("question", ""), Answer("{}", {}))
            reasons[v.reason] = reasons.get(v.reason, 0) + 1
            if v.decision == CLASSIFIED:
                covered += 1
                correct += v.code == answers[p["ruling_number"]]
        lo, hi = wilson(correct, covered)
        print(f"{name:20s} coverage {covered}/{len(gated)} = {covered / max(1, len(gated)):.3f}  "
              f"acc10 {correct}/{covered} = {correct / max(1, covered):.3f}  CI95 [{lo:.3f}, {hi:.3f}]  {reasons}")


if __name__ == "__main__":
    main(sys.argv[1])
