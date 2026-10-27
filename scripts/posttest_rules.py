"""Apply the gate's post-test evidence rules to the frozen test's accepted items, from its recorded Zetaris answers.

  python scripts/posttest_rules.py data/eval/results/test_20261026T184956.json

The frozen numbers stay as they were run. This replays only Zetaris (precedent rows and schedule paths, SQL with
no run tag) from data/recordings/, the local recordings of that run, and the predictions' own receipts, so no model
is called and nothing is re-decided by a model. It reports which accepted items the rules added after the test
would have sent to review, and whether those were right.
"""
import json
import re
import sys

from portorium.config import DATA, settings
from portorium.gate import CLASSIFIED, JudgedPrecedent, PrecedentGate, UNVERIFIED
from portorium.liveness import LIVING, Liveness, precedent_rows, schedule_text
from portorium.recorder import Recorder
from portorium.zetaris import Zetaris


def main(path: str) -> None:
    run = json.load(open(path))
    answers = {json.loads(line)["ruling_number"]: json.loads(line)["answer"] for line in open(DATA / "eval" / "test.jsonl")}
    zetaris = Zetaris(settings(), live=False)
    zetaris.recorder = Recorder("zetaris", DATA / "recordings")
    liveness = Liveness.from_zetaris(zetaris)
    gate = PrecedentGate(None, None, None, liveness, zetaris=zetaris)
    accepted = [p for p in run["predictions"] if p["system"] == "precedent_gate" and p["detail"].get("decision") == CLASSIFIED]
    moved = []
    for p in accepted:
        code, d = p["code"], p["detail"]
        judged = [JudgedPrecedent(**{k: j[k] for k in JudgedPrecedent.__dataclass_fields__ if k in j}) for j in d["judged"]]
        rows = precedent_rows(zetaris, [j.ruling_number for j in judged if j.kind == "ruling"])
        statuses = gate._statuses(code, judged, rows)
        path = [r for r in schedule_text(zetaris, code) if r["kind"] == "path"]
        leaf = re.sub(r"\D", "", (path[-1]["htsno"] or "") if path else "")
        reasons = []
        if LIVING not in statuses.values():
            reasons.append("no Zetaris-verified living precedent the Adjudicator matched to this code"
                           + (" (row missing)" if UNVERIFIED in statuses.values() else ""))
        if len(leaf) < 8 or not code.startswith(leaf):
            reasons.append("schedule path does not end at the code")
        if any(not r.get("routed_model") or not r.get("receipt_id") for r in d["receipts"]):
            reasons.append("a receipt is missing")
        if reasons:
            moved.append((p["ruling_number"], code, answers[p["ruling_number"]], reasons))
    right = sum(code == answer for _, code, answer, _ in moved)
    print(f"{len(accepted)} accepted; the post-test rules send {len(moved)} to review ({right} right, {len(moved) - right} wrong)")
    for number, code, answer, reasons in moved:
        print(f"  {number}  gave {code}  CBP {answer}  {'right' if code == answer else 'wrong'}: {'; '.join(reasons)}")


if __name__ == "__main__":
    main(sys.argv[1])
