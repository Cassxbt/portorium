"""Precedent liveness, decided by the data-quality rules registered in Zetaris.

A precedent's code is:
  living   passes `code_in_schedule` (the 10-digit line exists in HTS 2026 Rev 21)
  split    fails `code_in_schedule` but passes `parent_survives` (its 8-digit line now has other suffixes)
  removed  fails both
"""
from dataclasses import dataclass, field

from .zetaris import Zetaris

LIVING, SPLIT, REMOVED = "living", "split", "removed"
RULES = ("code_in_schedule", "parent_survives")


@dataclass
class Liveness:
    not_in_schedule: set[str]
    parent_gone: set[str]
    counts: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def from_zetaris(cls, zetaris: Zetaris) -> "Liveness":
        counts = {rule: zetaris.run_dq(rule, "ruling_code") for rule in RULES}
        invalid = {rule: {r["hts10"] for r in zetaris.invalid_records(rule, "ruling_code")} for rule in RULES}
        return cls(invalid["code_in_schedule"], invalid["parent_survives"], counts)

    def status(self, hts10: str) -> str:
        if hts10 not in self.not_in_schedule:
            return LIVING
        return REMOVED if hts10 in self.parent_gone else SPLIT

    def rule_failed(self, hts10: str) -> str | None:
        status = self.status(hts10)
        if status == LIVING:
            return None
        return "parent_survives" if status == REMOVED else "code_in_schedule"
