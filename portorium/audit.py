"""Every live verdict is written back to Zetaris; Zetaris re-screens past verdicts with DQ rules.

  classified_code_living   a CLASSIFIED verdict's code still exists in the current schedule
  classified_has_receipt   a CLASSIFIED verdict carries its Nightshift receipt
  release_is_snapshot      the live USITC release is the one the schedule snapshot came from
"""
import datetime
import time
import uuid

from . import trace
from .gate import Verdict
from .zetaris import USL, Zetaris, ZetarisError

AUDIT = "lightning.metastore.portorium.audit"
RELEASE = "2026HTSRev21"


def _quote(value) -> str:
    return "'" + str(value or "")[:2000].replace("\\", " ").replace("'", "''") + "'"


def record(zetaris: Zetaris, verdict: Verdict, product: str, importer: str) -> str | None:
    """Insert one verdict into Zetaris (live mode only); returns its id."""
    if not zetaris.live:
        return None
    models, receipt_ids = receipt_columns(verdict.receipts)
    verdict_id = str(uuid.uuid4())
    values = [verdict_id, datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"), importer, product,
              verdict.decision, verdict.reason, verdict.code or "", RELEASE, models, receipt_ids]
    zetaris.query(f"INSERT INTO portorium_files.verdict_log VALUES ({', '.join(_quote(v) for v in values)})")
    trace.event("record", f"{verdict.decision} {verdict.reason} {verdict.code or ''}".strip(),
                f"verdict {verdict_id} written to Zetaris audit.verdict_log, where classified_code_living re-screens it")
    return verdict_id


def receipt_columns(receipts: list[dict]) -> tuple[str, str]:
    """Every role's routed model and receipt id, so the ledger holds the whole decision, not its first call.
    Both fit the ledger's varchar(80): each distinct model once, each receipt tagged with its role's initial."""
    models = ";".join(dict.fromkeys((r.get("routed_model") or "").removeprefix("nvidia/") for r in receipts))
    # A row with any receipt missing has none, so classified_has_receipt sees the gap.
    ids = "" if any(not r.get("receipt_id") for r in receipts) else ";".join(f"{r['role'][0]}:{r['receipt_id']}" for r in receipts)
    return models[:80], ids


class ReleaseCheck:
    """Whether USITC still publishes the release the snapshot came from, asked through Zetaris at most once an hour."""

    def __init__(self, zetaris: Zetaris, ttl: float = 3600):
        self.zetaris, self.ttl, self.checked_at, self.ok = zetaris, ttl, None, True

    def __call__(self) -> bool:
        if self.checked_at is None or time.monotonic() - self.checked_at > self.ttl:
            result = self.zetaris.run_dq("release_is_snapshot", "hts_release")
            try:
                # No release row at all is not a current schedule: USITC must answer, and answer with the snapshot.
                self.ok = int(result["total_record"]) >= 1 and result["valid_record"] == result["total_record"]
            except (KeyError, ValueError) as e:
                raise ZetarisError(f"release_is_snapshot returned {result}") from e
            self.checked_at = time.monotonic()
        return self.ok


def rescreen(zetaris: Zetaris) -> dict:
    release_rule = zetaris.run_dq("release_is_snapshot", "hts_release")
    release = zetaris.query(f"SELECT name FROM {USL}.hts_release")
    living = zetaris.query(f"RUN DQ classified_code_living TABLE {AUDIT}.verdict_log")[0]
    stale = zetaris.query(f"SHOW DQ INVALID RECORD classified_code_living TABLE {AUDIT}.verdict_log")
    trace.event("rescreen", "live USITC release and every recorded verdict",
                f"release {release[0]['name'] if release else '?'} ({'changed' if release_rule['invalid_record'] != '0' else 'unchanged'}); "
                f"{living['total_record']} verdicts checked, {len(stale)} classified with a code no longer in the schedule",
                status="success" if not stale else "needs_review")
    return {"release": release[0]["name"] if release else None,
            "release_changed": release_rule["invalid_record"] != "0",
            "verdicts_checked": living["total_record"], "stale_verdicts": stale}
