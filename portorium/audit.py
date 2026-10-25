"""Every live verdict is written back to Zetaris; Zetaris re-screens past verdicts with DQ rules.

  classified_code_living   a CLASSIFIED verdict's code still exists in the current schedule
  classified_has_receipt   a CLASSIFIED verdict carries its Nightshift receipt
  release_is_snapshot      the live USITC release is the one the schedule snapshot came from
"""
import datetime
import uuid

from .gate import Verdict
from .zetaris import USL, Zetaris

AUDIT = "lightning.metastore.portorium.audit"
RELEASE = "2026HTSRev21"


def _quote(value) -> str:
    return "'" + str(value or "").replace("\\", " ").replace("'", "''")[:2000] + "'"


def record(zetaris: Zetaris, verdict: Verdict, product: str, importer: str) -> str | None:
    """Insert one verdict into Zetaris (live mode only); returns its id."""
    if not zetaris.live:
        return None
    receipt = verdict.receipts[0] if verdict.receipts else {}
    verdict_id = str(uuid.uuid4())
    values = [verdict_id, datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"), importer, product,
              verdict.decision, verdict.reason, verdict.code or "", RELEASE, receipt.get("routed_model"),
              receipt.get("receipt_id")]
    zetaris.query(f"INSERT INTO portorium_files.verdict_log VALUES ({', '.join(_quote(v) for v in values)})")
    return verdict_id


def rescreen(zetaris: Zetaris) -> dict:
    release_rule = zetaris.run_dq("release_is_snapshot", "hts_release")
    release = zetaris.query(f"SELECT name FROM {USL}.hts_release")
    living = zetaris.query(f"RUN DQ classified_code_living TABLE {AUDIT}.verdict_log")[0]
    stale = zetaris.query(f"SHOW DQ INVALID RECORD classified_code_living TABLE {AUDIT}.verdict_log")
    return {"release": release[0]["name"] if release else None,
            "release_changed": release_rule["invalid_record"] != "0",
            "verdicts_checked": living["total_record"], "stale_verdicts": stale}
