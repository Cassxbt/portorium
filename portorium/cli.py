"""Portorium command line: classify, broker sign-off, memory, re-screen.

  portorium classify "<product description>" [--importer NAME] [--as-of YYYY-MM-DD] [--json]
  portorium confirm "<product description>" <10-digit code> --broker NAME [--importer NAME]
  portorium forget [--importer NAME]
  portorium rescreen
"""
import argparse
import datetime
import json
import sys

from .config import DATA, settings
from .gate import CLASSIFIED, REVIEW, PrecedentGate, Verdict
from .index import PrecedentIndex
from .liveness import Liveness
from .memory import BrokerMemory
from .nightshift import Nightshift
from .nvidia import Nvidia
from .rulings import RulingTexts
from .zetaris import Zetaris

MEMORY = DATA / "memory"


def build_gate() -> tuple[PrecedentGate, Zetaris]:
    s = settings()
    zetaris = Zetaris(s, s.live)
    gate = PrecedentGate(PrecedentIndex(), Nvidia(s, s.live), Nightshift(s, s.live),
                         Liveness.from_zetaris(zetaris), RulingTexts(s.live))
    return gate, zetaris


def fmt(code: str | None) -> str:
    return f"{code[:4]}.{code[4:6]}.{code[6:]}" if code and len(code) == 10 else str(code)


def show(v: Verdict) -> None:
    print(f"\n{v.decision}  [{v.reason}]  {fmt(v.code) if v.code else ''}")
    for j in v.precedents:
        flag = f" {j.failed_rule}" if j.failed_rule else ""
        print(f"  {j.ruling_number:9s} {j.ruling_date}  {j.match:7s}  {fmt(j.code) if j.code else '-':13s} "
              f"{(j.status or ''):7s}{flag}  {j.fact[:70]}")
    for m in v.memory:
        print(f"  memory {m['memory_id']}: {fmt(m['code'])} from broker {m['broker']} (relevance {m['relevance']})")
    for r in v.receipts:
        print(f"  receipt {r.get('receipt_id')}  routed_model={r.get('routed_model')}  from_cache={r.get('from_cache')}")


def classify(args) -> int:
    gate, _ = build_gate()
    memory = BrokerMemory(MEMORY / f"{args.importer}.json")
    answers: list[tuple[str, str]] = []
    while True:
        v = gate.classify(args.product, args.as_of, answers=answers, memory=memory)
        if args.json:
            print(json.dumps(v.to_dict(), indent=1))
            return 0
        show(v)
        if v.decision != REVIEW or not v.question or len(answers) >= 2 or not sys.stdin.isatty():
            return 0 if v.decision == CLASSIFIED else 2
        reply = input(f"\n{v.question}\n> ").strip()
        if not reply:
            return 2
        answers.append((v.question, reply))


def confirm(args) -> int:
    memory = BrokerMemory(MEMORY / f"{args.importer}.json")
    memory_id = memory.remember(args.product, args.code.replace(".", ""), broker=args.broker, note=args.note)
    print(f"remembered {memory_id}: {fmt(args.code.replace('.', ''))} signed off by {args.broker}")
    return 0


def forget(args) -> int:
    BrokerMemory(MEMORY / f"{args.importer}.json").forget_all(reason="broker requested wipe")
    print(f"memory for {args.importer} wiped (ledger keeps the deletions)")
    return 0


def rescreen(args) -> int:
    """Zetaris checks the live USITC revision against the snapshot the verdicts were made on."""
    s = settings()
    z = Zetaris(s, s.live)
    result = z.run_dq("release_is_snapshot", "hts_release")
    release = z.query("SELECT name, description FROM lightning.metastore.portorium.portorium.hts_release")
    changed = result["invalid_record"] != "0"
    print(f"live HTS release: {release[0]['name'] if release else 'unknown'}; "
          f"rule release_is_snapshot: {result['valid_record']} valid / {result['invalid_record']} invalid")
    print("schedule changed: re-screen the catalogue" if changed else "schedule unchanged: verdicts stand")
    return 3 if changed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="portorium")
    sub = ap.add_subparsers(dest="command", required=True)
    c = sub.add_parser("classify")
    c.add_argument("product")
    c.add_argument("--importer", default="demo")
    c.add_argument("--as-of", default=datetime.date.today().isoformat())
    c.add_argument("--json", action="store_true")
    k = sub.add_parser("confirm")
    k.add_argument("product")
    k.add_argument("code")
    k.add_argument("--broker", required=True)
    k.add_argument("--importer", default="demo")
    k.add_argument("--note", default="")
    f = sub.add_parser("forget")
    f.add_argument("--importer", default="demo")
    sub.add_parser("rescreen")
    args = ap.parse_args(argv)
    return {"classify": classify, "confirm": confirm, "forget": forget, "rescreen": rescreen}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
