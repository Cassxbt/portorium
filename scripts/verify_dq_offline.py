"""Recompute the Zetaris liveness rules in plain Python from the same CSVs, as an independent check.

Prints the invalid-row counts for code_in_schedule and parent_survives; they must equal what
`RUN DQ` reports in Zetaris. This file is also the "without Zetaris" side of the reflection:
the app code each one-line registered rule replaces.
"""
import csv
from pathlib import Path

BUILD = Path(__file__).resolve().parents[1] / "data/build/zetaris"


def main() -> None:
    schedule, prefixes = set(), set()
    for row in csv.DictReader((BUILD / "hts_line.csv").open()):
        if row["code_len"] == "10":
            schedule.add(row["code_digits"])
        if row["code_len"] in ("8", "10"):
            prefixes.add(row["code_digits"][:8])
    total = not_in_schedule = parent_gone = 0
    for row in csv.DictReader((BUILD / "ruling_code.csv").open()):
        total += 1
        not_in_schedule += row["hts10"] not in schedule
        parent_gone += row["hts10"][:8] not in prefixes
    print(f"ruling codes {total}: code_in_schedule invalid {not_in_schedule}, parent_survives invalid {parent_gone}")


if __name__ == "__main__":
    main()
