"""Run scripts/zetaris_usl.sql against a live Zetaris, one statement at a time, stopping at the first error.

Usage: python scripts/load_zetaris.py [--from N]   (N = 1-based statement to resume from)
"""
import sys
import time
from pathlib import Path

from portorium.config import settings
from portorium.zetaris import Zetaris, ZetarisError

SQL = Path(__file__).with_name("zetaris_usl.sql")


def statements() -> list[str]:
    text = "\n".join(line for line in SQL.read_text().splitlines() if not line.startswith("--"))
    return [s.strip() for s in text.split("\n;;\n") if s.strip()]


def main() -> int:
    start = int(sys.argv[sys.argv.index("--from") + 1]) if "--from" in sys.argv else 1
    s = settings()
    if s.sample_mode:
        print("load_zetaris needs a live Zetaris; unset SAMPLE_MODE")
        return 1
    z = Zetaris(s, live=True)
    for n, sql in enumerate(statements(), 1):
        if n < start:
            continue
        head = " ".join(sql.split())[:100]
        t = time.time()
        try:
            rows = z.query(sql, limit=50)
        except ZetarisError as e:
            print(f"[{n}] FAIL {head}\n     {e}")
            return 1
        print(f"[{n}] OK {time.time() - t:.1f}s {head}")
        for row in rows[:12]:
            print("     ", {k: str(v)[:90] for k, v in row.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
