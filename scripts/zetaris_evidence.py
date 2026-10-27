"""Run the Zetaris evidence queries live and write their real output to docs/zetaris_evidence.md.

  python scripts/zetaris_evidence.py        (live: Zetaris credentials in .env, the USL loaded)

Per source, a few exploratory queries; then queries that only work through the active USL; then data-quality
rules executed with their counts. Every result in the document is what this run returned, with its time.
"""
import datetime
import time

from portorium.config import ROOT, settings
from portorium.zetaris import USL, Zetaris

AUDIT = "lightning.metastore.portorium.audit"
SECTIONS = [
    ("Source 1: HTS 2026 Revision 21, the tariff schedule (USITC export, CSV)", [
        ("How many 10-digit lines does the loaded schedule hold?", f"SELECT count(*) AS lines FROM {USL}.hts_line"),
        ("Which chapters have the most lines?",
         f"SELECT substr(hts10, 1, 2) AS chapter, count(*) AS lines FROM {USL}.hts_line GROUP BY substr(hts10, 1, 2) ORDER BY lines DESC LIMIT 5"),
        ("What does 6110.20.20 cover today, line by line?",
         f"SELECT htsno, description, general_rate_inherited FROM {USL}.hts_line WHERE hts10 LIKE '6110202%' ORDER BY hts10 LIMIT 6"),
    ]),
    ("Source 2: CBP CROSS rulings and the codes they cite (CBP, CSV)", [
        ("How many rulings, and how many are revoked?",
         f"SELECT count(*) AS rulings, sum(CASE WHEN revoked THEN 1 ELSE 0 END) AS revoked FROM {USL}.ruling"),
        ("How many rulings per year, most recent first?",
         f"SELECT year(ruling_date) AS year, count(*) AS rulings FROM {USL}.ruling GROUP BY year(ruling_date) ORDER BY year DESC LIMIT 5"),
        ("Which codes does the 1992 pullover ruling cite?",
         f"SELECT r.ruling_number, r.ruling_date, r.revoked, c.hts10 FROM {USL}.ruling r JOIN {USL}.ruling_code c "
         f"ON c.ruling_number = r.ruling_number WHERE r.ruling_number = '951485'"),
    ]),
    ("Source 3: the current HTS release (USITC REST API, read live on every query)", [
        ("Which release does USITC publish right now?", f"SELECT name, description FROM {USL}.hts_release"),
        ("Is it still the release our snapshot came from?", f"RUN DQ release_is_snapshot TABLE {USL}.hts_release"),
    ]),
    ("Source 4: Portorium's own verdict ledger (written back by the agent)", [
        ("How have live verdicts been decided?",
         f"SELECT decision, count(*) AS verdicts FROM {AUDIT}.verdict_log GROUP BY decision ORDER BY verdicts DESC"),
        ("Does every classification still name a living code?", f"RUN DQ classified_code_living TABLE {AUDIT}.verdict_log"),
        ("Does every classification carry its Nightshift receipt?", f"RUN DQ classified_has_receipt TABLE {AUDIT}.verdict_log"),
    ]),
    ("The USL is active: questions that join the sources", [
        ("Of all codes CBP rulings cite, how many are living, split or removed in Rev 21?",
         f"""SELECT status, count(*) AS codes FROM (SELECT CASE WHEN h.hts10 IS NOT NULL THEN 'living'
             WHEN substr(c.hts10, 1, 8) IN (SELECT substr(code, 1, 8) FROM {USL}.hts_code) THEN 'split' ELSE 'removed' END AS status
             FROM {USL}.ruling_code c LEFT JOIN {USL}.hts_line h ON h.hts10 = c.hts10) t GROUP BY status ORDER BY codes DESC"""),
        ("Which 2025-2026 rulings already cite a code that is gone from Rev 21?",
         f"""SELECT r.ruling_number, r.ruling_date, c.hts10 FROM {USL}.ruling r JOIN {USL}.ruling_code c ON c.ruling_number = r.ruling_number
             LEFT JOIN {USL}.hts_line h ON h.hts10 = c.hts10 WHERE h.hts10 IS NULL AND r.ruling_date >= DATE '2025-01-01'
             ORDER BY r.ruling_date DESC LIMIT 5"""),
        ("The per-decision query the Auditor runs (here for the 1992 pullover rulings)",
         f"""SELECT r.ruling_number, r.revoked, c.hts10, CASE WHEN h.hts10 IS NOT NULL THEN 'living'
             WHEN substr(c.hts10, 1, 8) IN (SELECT substr(code, 1, 8) FROM {USL}.hts_code) THEN 'split' ELSE 'removed' END AS status
             FROM {USL}.ruling r JOIN {USL}.ruling_code c ON c.ruling_number = r.ruling_number
             LEFT JOIN {USL}.hts_line h ON h.hts10 = c.hts10 WHERE r.ruling_number IN ('951485', '951484', '952370')"""),
    ]),
    ("Data-quality rules executed", [
        ("code_in_schedule: is each cited code a line of Rev 21?", f"RUN DQ code_in_schedule TABLE {USL}.ruling_code"),
        ("parent_survives: if not, does its 8-digit line survive (split) or not (removed)?", f"RUN DQ parent_survives TABLE {USL}.ruling_code"),
        ("subject_present: hygiene, and the one rule that fails", f"RUN DQ subject_present TABLE {USL}.ruling"),
        ("A few of the records code_in_schedule rejects", f"SHOW DQ INVALID RECORD code_in_schedule TABLE {USL}.ruling_code"),
    ]),
]


def table(rows: list[dict], limit: int = 6) -> str:
    if not rows:
        return "_no rows_"
    headers = list(rows[0])
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for r in rows[:limit]:
        lines.append("| " + " | ".join(str(r[h])[:90].replace("|", "/") for h in headers) + " |")
    if len(rows) > limit:
        lines.append(f"\n_{len(rows) - limit} more rows not shown_")
    return "\n".join(lines)


def main() -> None:
    s = settings()
    s.check()
    zetaris = Zetaris(s, live=True)
    out = [f"# Zetaris evidence pack\n\nGenerated by `scripts/zetaris_evidence.py` against the live Zetaris instance on "
           f"{datetime.datetime.now(datetime.UTC):%Y-%m-%d %H:%M} UTC. Every table below is that run's output; the USL's "
           f"DDL, activation queries and rule registrations are in [`scripts/zetaris_usl.sql`](../scripts/zetaris_usl.sql).\n"]
    for title, queries in SECTIONS:
        out.append(f"## {title}\n")
        for question, sql in queries:
            started = time.monotonic()
            rows = zetaris.query(sql, limit=1000)
            took = time.monotonic() - started
            out.append(f"**{question}** ({took:.1f} s)\n\n```sql\n{' '.join(sql.split())}\n```\n\n{table(rows)}\n")
            print(f"{took:5.1f}s  {question}")
    (ROOT / "docs" / "zetaris_evidence.md").write_text("\n".join(out))
    print("-> docs/zetaris_evidence.md")


if __name__ == "__main__":
    main()
