"""Print an evaluation run's score table as Markdown, for the README.

Usage: python scripts/eval_table.py data/eval/results/<run>.json
"""
import json
import sys

ROWS = [
    ("precedent_gate", "**Portorium**: Proposer + Zetaris liveness + Critic"),
    ("gate_without_critic", "Proposer + Zetaris liveness"),
    ("confidence_threshold", "Proposer, kept when its confidence is high enough to match the gate's coverage"),
    ("retrieval_ultra", "Proposer alone (Nemotron 3 Ultra picks from retrieved rulings)"),
    ("retrieval_top1", "Closest retrieved ruling's code"),
    ("ultra_zero_shot", "Nemotron 3 Ultra asked for a code"),
    ("lightning_zero_shot", "Nemotron 3.5 Lightning asked for a code"),
    ("keyword_top1", "Keyword match"),
]


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def main(path: str) -> None:
    run = json.load(open(path))
    by = {r["system"]: r for r in run["table"]}
    print(f"`{path.split('/')[-1]}` · {run['split']} · {run['items']} rulings · gate prompt `{run.get('gate_prompt_sha256')}`\n")
    print("| System | Coverage | Exact 10-digit accuracy (answered) | 95% CI | Stale codes |")
    print("|---|---|---|---|---|")
    for system, name in ROWS:
        if system in by:
            r = by[system]
            lo, hi = r["acc10_ci95"]
            print(f"| {name} | {pct(r['coverage'])} | {pct(r['acc10_covered'])} | {pct(lo)}–{pct(hi)} | {pct(r['stale_code_rate'])} |")


if __name__ == "__main__":
    main(sys.argv[1])
