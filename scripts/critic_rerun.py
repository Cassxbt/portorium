"""Re-run only the Critic, live, on every item that reached it in a stored run; everything before it is held fixed.

  python scripts/critic_rerun.py test data/eval/results/test_20261026T184956.json
  python scripts/critic_rerun.py dev data/eval/history/v3_critic_on_v2_calls.json

The Retriever, Adjudicator, Proposer and Zetaris liveness decided which code reached the Critic in that run; those
codes are taken as they were. Each item's schedule text comes from Zetaris live (the loaded schedule is still Rev 21),
and the Critic, with the prompt and the quoted-fact check as they are now, is called live through
Nightshift with a fresh run tag. The result goes to data/eval/results/critic_<split>_<time>.json. CRITIC_PROMPT=<file> tries a
candidate prompt instead of the shipped one; choose on dev, then run the choice once on test.
"""
import datetime
import json
import os
import sys
from pathlib import Path
import time
from concurrent.futures import ThreadPoolExecutor

from portorium.config import DATA, settings
from portorium.eval import load_items, wilson
from portorium import gate as gate_module
from portorium.gate import CLASSIFIED, PrecedentGate, Verdict, prompts_sha256
from portorium.liveness import Liveness
from portorium.nightshift import Nightshift
from portorium.zetaris import Zetaris

RECEIPT = {"routed_model": "as run", "receipt_id": "as run"}


def reached_critic(split: str, path: str) -> dict[str, str]:
    data = json.load(open(path))
    if split == "test":
        return {p["ruling_number"]: p["code"] for p in data["predictions"] if p["system"] == "gate_without_critic" and p["code"]}
    return {number: c["code"] for number, c in data.items() if c.get("code")}


def main(split: str, path: str, workers: int = 2) -> None:
    s = settings()
    s.check()
    if not s.live:
        sys.exit("the Critic is called live: credentials and services are needed")
    if os.environ.get("CRITIC_PROMPT"):
        gate_module.CRITIC = Path(os.environ["CRITIC_PROMPT"]).read_text().strip()
    codes = reached_critic(split, path)
    items = {i.ruling_number: i for i in load_items(split, 300 if split == "test" else 150)}
    zetaris = Zetaris(s, live=True)
    gate = PrecedentGate(None, None, Nightshift(s, True, run_tag=f"critic-{split}-{time.strftime('%Y%m%dT%H%M%S')}"),
                         Liveness.from_zetaris(zetaris), zetaris=zetaris)

    def one(number: str) -> dict:
        receipts = [{"role": role, **RECEIPT} for role in ("Adjudicator", "Proposer")]
        try:
            v = gate.critique(items[number].query, [], Verdict(CLASSIFIED, "LIVING_PRECEDENT", codes[number], [], "", receipts, {}))
        except Exception as e:
            return {"ruling_number": number, "error": f"{type(e).__name__}: {e}"[:300]}
        return {"ruling_number": number, "code": codes[number], "answer": items[number].answer, "decision": v.decision,
                "reason": v.reason, "question": v.question, "critique": v.critique}

    with ThreadPoolExecutor(workers) as pool:
        results = list(pool.map(one, sorted(n for n in codes if n in items)))
    done = [r for r in results if "error" not in r]
    kept = [r for r in done if r["decision"] == CLASSIFIED]
    right = sum(r["code"] == r["answer"] for r in kept)
    removed = [r for r in done if r["decision"] != CLASSIFIED]
    by_reason = {}
    for r in removed:
        by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + 1
    n = len(items)
    summary = {"split": split, "source": path, "items": n, "reached_critic": len(codes), "errors": len(results) - len(done),
               "classified": len(kept), "coverage": round(len(kept) / n, 3), "right": right,
               "acc10_covered": round(right / max(1, len(kept)), 3), "acc10_ci95": [round(x, 3) for x in wilson(right, len(kept))],
               "removed_right": sum(r["code"] == r["answer"] for r in removed),
               "removed_wrong": sum(r["code"] != r["answer"] for r in removed), "removed_by_reason": by_reason,
               "gate_prompt_sha256": prompts_sha256(), "critic_prompt": gate_module.CRITIC}
    out = DATA / "eval" / "results" / f"critic_{split}_{datetime.datetime.now(datetime.UTC).strftime('%Y%m%dT%H%M%S')}.json"
    out.write_text(json.dumps({**summary, "results": results}, indent=1) + "\n")
    print(json.dumps(summary, indent=1))
    print(f"-> {out}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 2)
