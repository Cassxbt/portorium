"""Reproduce the README's design-history table from the committed runs, with no service calls.

  v1, v2  the gate's own dev runs (the Adjudicator's "same article" decided)
  v3      the proposer-verifier rule replayed on v2's stored calls: the Proposer's code (v2's
          retrieval_ultra pick) must be cited by a retrieved ruling that is not revoked, must be a
          line of HTS 2026 Rev 21, and the Critic (whose stored answers are in v3_critic_on_v2_calls.json)
          must find the product fits its wording
Each row is compared with the Proposer's own confidence threshold at the same coverage.
Usage: python scripts/design_history.py
"""
import gzip
import json

from portorium.config import DATA
from portorium.eval import Prediction, load_items, threshold_baseline, wilson

H = DATA / "eval" / "history"


def row(name: str, preds: list[Prediction], picks: list[Prediction], answers: dict[str, str]) -> str:
    covered = [p for p in preds if p.code]
    right = sum(p.code == answers[p.ruling_number] for p in covered)
    base = [p for p in threshold_baseline(picks, len(covered) / len(preds)) if p.code]
    base_right = sum(p.code == answers[p.ruling_number] for p in base)
    lo, hi = wilson(right, len(covered))
    return (f"{name:46s} coverage {len(covered)/len(preds):6.1%}  accuracy {right}/{len(covered)} = {right/len(covered):5.1%} "
            f"[{lo:.1%}, {hi:.1%}]  threshold at same coverage {base_right}/{len(base)} = {base_right/len(base):5.1%}")


def main() -> None:
    answers = {i.ruling_number: i.answer for i in load_items("dev", 150)}
    schedule = {r["htsno"].replace(".", "") for r in json.load(gzip.open(DATA / "hts" / "hts_2026HTSRev21.json.gz"))
                if r.get("htsno") and len(r["htsno"].replace(".", "")) == 10}
    v2 = json.load(open(H / "v2_dev_20261026T055441.json"))
    picks = [Prediction(**p) for p in v2["predictions"] if p["system"] == "retrieval_ultra"]
    for name, path in (("v1: same-article judgement decides", "v1_dev_20261026T053527.json"),
                       ("v2: stricter same-article prompt", "v2_dev_20261026T055441.json")):
        run = json.load(open(H / path))
        print(row(name, [Prediction(**p) for p in run["predictions"] if p["system"] == "precedent_gate"], picks, answers))
    critic = json.load(open(H / "v3_critic_on_v2_calls.json"))
    judged = {p["ruling_number"]: p["detail"]["judged"] for p in v2["predictions"] if p["system"] == "precedent_gate"}
    v3 = []
    for pick in picks:
        code = pick.code
        standing = [j for j in judged[pick.ruling_number] if code in j["codes"] and j.get("status") != "revoked"]
        ok = bool(code) and bool(standing) and code in schedule and (critic.get(pick.ruling_number) or {}).get("fits") is True
        v3.append(Prediction("v3", pick.ruling_number, code if ok else None))
    print(row("v3: Proposer + liveness + Critic (replay)", v3, picks, answers))


if __name__ == "__main__":
    main()
