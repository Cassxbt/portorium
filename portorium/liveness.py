"""Code liveness against HTS 2026 Rev 21, decided in Zetaris.

  living   the 10-digit line exists                  (rule code_in_schedule)
  split    the 8-digit line survives with other suffixes (fails code_in_schedule, passes parent_survives)
  removed  the 8-digit line is gone                  (fails both)

The schedule comes from the Zetaris USL, so any code can be checked, not only codes that rulings
cite. At load, the same test is compared with the registered rules' invalid records on ruling
codes; a mismatch stops the gate rather than letting two definitions of "living" drift apart.
"""
from dataclasses import dataclass, field

from .zetaris import USL, Zetaris

LIVING, SPLIT, REMOVED = "living", "split", "removed"
RULES = ("code_in_schedule", "parent_survives")


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
        return {LIVING: None, SPLIT: "code_in_schedule", REMOVED: "parent_survives"}[self.status(hts10)]

    def successors(self, hts10: str) -> list[str]:
        """Current 10-digit lines under a split code's 8-digit line."""
        return sorted(c for c in self.schedule if c[:8] == hts10[:8])
