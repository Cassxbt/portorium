"""Code liveness against HTS 2026 Rev 21, decided in Zetaris.

  living   the 10-digit line exists                  (rule code_in_schedule)
  split    the 8-digit line survives with other suffixes (fails code_in_schedule, passes parent_survives)
  removed  the 8-digit line is gone                  (fails both)

The schedule comes from the Zetaris USL, so any code can be checked, not only codes that rulings
cite. At load, the same test is compared with the registered rules' invalid records on ruling
codes; a mismatch stops the gate rather than letting two definitions of "living" drift apart.
"""
from dataclasses import dataclass, field

from .zetaris import USL, Zetaris, ZetarisError

LIVING, SPLIT, REMOVED, REVOKED = "living", "split", "removed", "revoked"
RULES = ("code_in_schedule", "parent_survives")
FAILED_RULE = {LIVING: None, SPLIT: "code_in_schedule", REMOVED: "parent_survives", REVOKED: "ruling_revoked"}


class LivenessMismatch(RuntimeError):
    pass


@dataclass
class Liveness:
    schedule: set[str]
    prefixes: set[str]
    counts: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def from_zetaris(cls, zetaris: Zetaris) -> "Liveness":
        schedule = {r["hts10"] for r in zetaris.query(f"SELECT hts10 FROM {USL}.hts_line", limit=100000)}
        prefixes = {r["p"] for r in zetaris.query(f"SELECT DISTINCT substr(code, 1, 8) AS p FROM {USL}.hts_code",
                                                    limit=100000)}
        liveness = cls(schedule, prefixes, {rule: zetaris.run_dq(rule, "ruling_code") for rule in RULES})
        for rule, wanted in ((RULES[0], {SPLIT, REMOVED}), (RULES[1], {REMOVED})):
            invalid = {r["hts10"] for r in zetaris.invalid_records(rule, "ruling_code")}
            ours = {r["hts10"] for r in zetaris.query(f"SELECT DISTINCT hts10 FROM {USL}.ruling_code", limit=200000)
                    if liveness.status(r["hts10"]) in wanted}
            if invalid != ours:
                raise LivenessMismatch(f"{rule}: Zetaris marks {len(invalid)} codes invalid, schedule test {len(ours)}")
        return liveness

    def status(self, hts10: str) -> str:
        if hts10 in self.schedule:
            return LIVING
        return SPLIT if hts10[:8] in self.prefixes else REMOVED

    def rule_failed(self, hts10: str) -> str | None:
        return FAILED_RULE[self.status(hts10)]

    def successors(self, hts10: str) -> list[str]:
        """Current 10-digit lines under a split code's 8-digit line."""
        return sorted(c for c in self.schedule if c[:8] == hts10[:8] and c != hts10)


def precedent_rows(zetaris: Zetaris, ruling_numbers: list[str]) -> dict[str, dict]:
    """Per decision, from Zetaris: each precedent's date, revocation flag and the liveness of its codes.

    The CASE mirrors the registered rules (code_in_schedule, parent_survives), which Liveness
    cross-checks against the rules' own invalid records at load.
    """
    if not ruling_numbers:
        return {}
    ids = ", ".join("'" + n.replace("'", "") + "'" for n in ruling_numbers)
    rows = zetaris.query(f"""SELECT r.ruling_number, r.ruling_date, r.revoked, c.hts10,
        CASE WHEN h.hts10 IS NOT NULL THEN '{LIVING}'
             WHEN substr(c.hts10, 1, 8) IN (SELECT substr(code, 1, 8) FROM {USL}.hts_code) THEN '{SPLIT}'
             ELSE '{REMOVED}' END AS status
        FROM {USL}.ruling r JOIN {USL}.ruling_code c ON c.ruling_number = r.ruling_number
        LEFT JOIN {USL}.hts_line h ON h.hts10 = c.hts10
        WHERE r.ruling_number IN ({ids})""", limit=1000)
    out: dict[str, dict] = {}
    for r in rows:
        try:
            revoked = revoked_flag(r["revoked"])
        except ValueError as e:
            raise ZetarisError(str(e)) from e
        entry = out.setdefault(r["ruling_number"], {"date": r["ruling_date"], "revoked": revoked, "codes": {}})
        entry["codes"][r["hts10"]] = r["status"]
    return out


def revoked_flag(value) -> bool:
    """Zetaris returns booleans as text today; a changed adapter must not quietly turn a revocation into False."""
    if isinstance(value, bool):
        return value
    text = "" if value is None else str(value).strip().lower()
    if text in ("true", "t", "1", "yes"):
        return True
    if text in ("false", "f", "0", "no", ""):
        return False
    raise ValueError(f"unrecognised revoked flag {value!r}")


def schedule_text(zetaris: Zetaris, code: str) -> list[dict]:
    """A 10-digit line of the schedule with every level above it, and the other lines at each level.

    The meaning of a line depends on its ancestors and, for "Other", on its siblings, so a code can
    survive a revision while what it covers moves. Rows come back in schedule order. Where the indentation
    skips a level, the last line at that level belongs to an earlier branch, so an ancestor is kept only if
    it comes after every ancestor above it.
    """
    h = "portorium_files.hts_line"
    rows = zetaris.query(f"""WITH t AS (SELECT CAST(row_idx AS INT) AS r, CAST(indent AS INT) AS d FROM {h}
            WHERE code_digits = '{code.replace("'", "")}'),
        anc0 AS (SELECT CAST(x.indent AS INT) AS lvl, max(CAST(x.row_idx AS INT)) AS r FROM {h} x CROSS JOIN t
            WHERE CAST(x.row_idx AS INT) <= t.r AND CAST(x.indent AS INT) <= t.d GROUP BY CAST(x.indent AS INT)),
        anc AS (SELECT a.lvl, a.r FROM anc0 a LEFT JOIN anc0 b ON b.lvl < a.lvl AND b.r > a.r
            GROUP BY a.lvl, a.r HAVING count(b.lvl) = 0),
        par AS (SELECT a.lvl, a.r AS own, max(p.lvl) AS plvl FROM anc a JOIN anc p ON p.lvl < a.lvl GROUP BY a.lvl, a.r),
        bounds AS (SELECT par.lvl, par.own, p.r AS pr, min(CAST(x.row_idx AS INT)) AS nb FROM par JOIN anc p ON p.lvl = par.plvl
            LEFT JOIN {h} x ON CAST(x.row_idx AS INT) > p.r AND CAST(x.indent AS INT) < par.lvl GROUP BY par.lvl, par.own, p.r)
        SELECT 'path' AS kind, anc.lvl AS lvl, anc.r AS r, x.htsno, x.description FROM anc JOIN {h} x ON CAST(x.row_idx AS INT) = anc.r
        UNION ALL
        SELECT 'alternative' AS kind, b.lvl AS lvl, CAST(x.row_idx AS INT) AS r, x.htsno, x.description FROM bounds b JOIN {h} x
            ON CAST(x.row_idx AS INT) > b.pr AND CAST(x.row_idx AS INT) < coalesce(b.nb, 100000000)
            AND CAST(x.indent AS INT) = b.lvl AND CAST(x.row_idx AS INT) <> b.own""", limit=2000)
    return sorted(({**r, "lvl": int(r["lvl"]), "r": int(r["r"])} for r in rows), key=lambda r: (r["lvl"], r["r"]))
