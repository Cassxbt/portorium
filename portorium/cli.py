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
from pathlib import Path

from . import audit
from .config import DATA, settings
from .gate import CLASSIFIED, REVIEW, PrecedentGate, Verdict
from .index import PrecedentIndex
from .liveness import Liveness
from .memory import BrokerMemory
from .nightshift import Nightshift, NightshiftError
from .nvidia import Nvidia, NvidiaError
from .recorder import MissingRecording
from .rulings import RulingTexts
from .zetaris import Zetaris, ZetarisError

MEMORY = DATA / "memory"


def build_gate() -> tuple[PrecedentGate, Zetaris]:
    s = settings()
    zetaris = Zetaris(s, s.live)
    gate = PrecedentGate(PrecedentIndex(), Nvidia(s, s.live), Nightshift(s, s.live),
                         Liveness.from_zetaris(zetaris), RulingTexts(s.live), zetaris)
    return gate, zetaris


def fmt(code: str | None) -> str:
    return f"{code[:4]}.{code[4:6]}.{code[6:]}" if code and len(code) == 10 else str(code)


def show(v: Verdict) -> None:
    print(f"\n{v.decision}  [{v.reason}]  {fmt(v.code) if v.code else ''}")
    for j in v.precedents:
        flag = f" {j.failed_rule}" if j.failed_rule else ""
        print(f"  {j.ruling_number:11s} {j.ruling_date or 'broker':10s}  {j.match:7s}  {fmt(j.code) if j.code else '-':13s} "
              f"{(j.status or ''):7s}{flag}  {j.fact[:70]}")
        if j.successors:
            print(f"      now split into: {', '.join(fmt(c) for c in j.successors[:6])}")
    for m in v.remembered:
        print(f"  remembered {m['memory_id']}: {m['question']} -> {m['answer']}")
    for r in v.receipts:
        print(f"  receipt {r.get('receipt_id')}  routed_model={r.get('routed_model')}  from_cache={r.get('from_cache')}")


def classify(args) -> int:
    gate, zetaris = build_gate()
    memory = BrokerMemory(MEMORY / f"{Path(args.importer).name}.json")
    answers: list[tuple[str, str]] = []
    while True:
        v = gate.classify(args.product, args.as_of, answers=answers, memory=memory)
        audit.record(zetaris, v, args.product, args.importer)
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
        memory.remember_answer(args.product, v.question, reply)


def confirm(args) -> int:
    code = args.code.replace(".", "")
    if not (code.isdigit() and len(code) == 10):
        print("code must be 10 digits, e.g. 6110.20.2079")
        return 1
    memory = BrokerMemory(MEMORY / f"{Path(args.importer).name}.json")
    memory_id = memory.remember(args.product, code, broker=args.broker, note=args.note)
    print(f"remembered {memory_id}: {fmt(args.code.replace('.', ''))} signed off by {args.broker}")
    return 0


def forget(args) -> int:
    BrokerMemory(MEMORY / f"{Path(args.importer).name}.json").forget_all(reason="broker requested wipe")
    print(f"memory for {args.importer} wiped (ledger keeps the deletions)")
    return 0


def rescreen(args) -> int:
    """Zetaris re-checks the live USITC release and every past CLASSIFIED verdict."""
    s = settings()
    r = audit.rescreen(Zetaris(s, s.live))
    print(f"live HTS release {r['release']} ({'CHANGED' if r['release_changed'] else 'unchanged'}); "
          f"{r['verdicts_checked']} verdicts checked, {len(r['stale_verdicts'])} classified with a code no longer in the schedule")
    for v in r["stale_verdicts"]:
        print(f"  {v['verdict_id']}  {fmt(v['code'])}  {v['recorded_at']}  {v['product'][:60]}")
    return 3 if r["release_changed"] or r["stale_verdicts"] else 0


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
    try:
        return {"classify": classify, "confirm": confirm, "forget": forget, "rescreen": rescreen}[args.command](args)
    except (MissingRecording, NightshiftError, NvidiaError, ZetarisError) as e:
        print(f"portorium: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
