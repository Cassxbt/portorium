"""Build the CSV inputs for the Zetaris `portorium` USL from the HTS snapshot and the CROSS rulings.

Outputs (data/build/zetaris/):
  hts_line.csv     every row of the schedule in published order, for the in-Zetaris hierarchy flatten
  ruling.csv       one row per ruling
  ruling_code.csv  one row per 10-digit code a ruling cites
"""
import csv
import gzip
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTS = ROOT / "data/hts/hts_2026HTSRev21.json.gz"
OUT = ROOT / "data/build/zetaris"
CODE = re.compile(r"^\d{4}\.\d{2}\.\d{4}$")


def clean(value) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


def codes_of(ruling: dict) -> list[str]:
    return [c.strip() for c in str(ruling.get("tariffs") or "").split(",") if CODE.match(c.strip())]


def write_hts(path: Path) -> int:
    rows = json.load(gzip.open(HTS))
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["row_idx", "htsno", "code_digits", "code_len", "indent", "description", "general_rate"])
        for i, r in enumerate(rows):
            digits = (r.get("htsno") or "").replace(".", "")
            w.writerow([i, clean(r.get("htsno") or ""), digits, len(digits), clean(r.get("indent") or ""),
                        clean(r.get("description") or ""), clean(r.get("general") or "")])
    return len(rows)


def write_rulings(rulings_path: Path, ruling_csv: Path, code_csv: Path) -> tuple[int, int]:
    n_rulings = n_codes = 0
    with ruling_csv.open("w", newline="") as rf, code_csv.open("w", newline="") as cf:
        rw, cw = csv.writer(rf), csv.writer(cf)
        rw.writerow(["ruling_number", "ruling_date", "collection", "subject", "revoked", "modified"])
        cw.writerow(["ruling_code_id", "ruling_number", "hts10", "code_position"])
        for line in rulings_path.open():
            r = json.loads(line)
            date = (r.get("rulingDate") or "")[:10]
            if not date or date.startswith("0001"):
                continue
            rw.writerow([r["rulingNumber"], date, r.get("collection") or "", clean(r.get("subject") or "")[:500],
                         str(bool(r.get("revokedBy"))).lower(), str(bool(r.get("modifiedBy"))).lower()])
            n_rulings += 1
            for i, code in enumerate(codes_of(r)):
                cw.writerow([f"{r['rulingNumber']}:{i}", r["rulingNumber"], code.replace(".", ""), i])
                n_codes += 1
    return n_rulings, n_codes


def main(rulings_path: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    hts = write_hts(OUT / "hts_line.csv")
    rulings, codes = write_rulings(Path(rulings_path), OUT / "ruling.csv", OUT / "ruling_code.csv")
    print(f"hts rows {hts}, rulings {rulings}, ruling codes {codes} -> {OUT}")


if __name__ == "__main__":
    main(sys.argv[1])
