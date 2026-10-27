import json

from portorium.gate import CLASSIFIED, REVIEW, PrecedentGate, Verdict
from portorium.liveness import Liveness
from portorium.nightshift import Answer, Nightshift

LIVE = Liveness({"8518220000"}, {"85182200"})
CRITIC_SAYS_FITS = {"fits": True, "line": "", "reason": "fits", "question": "",
                    "facts": [{"fact": "a speaker", "quote": "party speaker"}]}


class FakeZetaris:
    def __init__(self, rows):
        self.rows = rows

    def query(self, sql, limit=1000):
        return self.rows


class FakeNightshift(Nightshift):
    def __init__(self, receipt, reply=None):
        self.receipt = receipt
        self.reply = reply or CRITIC_SAYS_FITS
        self.prompts = []

    def chat_json(self, messages, required, model, max_tokens=1024):
        self.prompts.append(messages)
        return self.reply, Answer(json.dumps(self.reply), self.receipt)


RECEIPT = {"routed_model": "nvidia/nemotron-3-ultra", "receipt_id": "rcpt_1"}
PATH = [{"kind": "path", "lvl": "1", "r": "1", "htsno": None, "description": "Loudspeakers:"},
        {"kind": "path", "lvl": "2", "r": "2", "htsno": "8518.22.00.00", "description": "Multiple loudspeakers"}]


def classified(receipts=None):
    receipts = receipts or [{"role": "Adjudicator", **RECEIPT}, {"role": "Proposer", **RECEIPT}]
    return Verdict(CLASSIFIED, "LIVING_PRECEDENT", "8518220000", [], "", receipts, {})


def critique(rows, receipt=RECEIPT, verdict=None, reply=None, product="party speaker", answers=()):
    gate = PrecedentGate(None, None, FakeNightshift(receipt, reply), LIVE, zetaris=FakeZetaris(rows))
    return gate, gate.critique(product, list(answers), verdict or classified())


def as_rows(path):
    return {"headers": list(path[0]), "data": [list(r.values()) for r in path]} if path else {"headers": [], "data": []}


def test_a_classification_whose_schedule_text_is_missing_is_not_sent_to_the_critic(monkeypatch):
    monkeypatch.setattr("portorium.gate.schedule_text", lambda zetaris, code: [])
    gate, v = critique([])
    assert (v.decision, v.reason, v.code) == (REVIEW, "EVIDENCE_MISSING", None)
    assert gate.nightshift.prompts == []


def test_a_schedule_path_that_ends_on_another_line_is_missing_evidence(monkeypatch):
    other = PATH[:1] + [{**PATH[1], "htsno": "8518.29.80.00"}]
    monkeypatch.setattr("portorium.gate.schedule_text", lambda zetaris, code: other)
    assert critique(other)[1].reason == "EVIDENCE_MISSING"


def test_a_complete_path_and_receipts_keep_the_classification(monkeypatch):
    monkeypatch.setattr("portorium.gate.schedule_text", lambda zetaris, code: PATH)
    assert critique(PATH)[1].decision == CLASSIFIED


def test_a_classification_without_a_routed_model_receipt_is_not_auditable(monkeypatch):
    monkeypatch.setattr("portorium.gate.schedule_text", lambda zetaris, code: PATH)
    v = critique(PATH, receipt={"routed_model": None, "receipt_id": None})[1]
    assert (v.decision, v.reason) == (REVIEW, "EVIDENCE_MISSING")


def test_a_classification_made_against_an_outdated_schedule_goes_to_review():
    gate = PrecedentGate(None, None, FakeNightshift(RECEIPT), LIVE)
    gate.release_current = lambda: False
    v = gate.current(classified())
    assert (v.decision, v.reason, v.code) == (REVIEW, "SCHEDULE_OUTDATED", None)
    gate.release_current = lambda: True
    assert gate.current(classified()).decision == CLASSIFIED


def test_the_release_check_asks_zetaris_at_most_once_an_hour():
    from portorium.audit import ReleaseCheck
    calls = []

    class Z:
        def run_dq(self, rule, table):
            calls.append(rule)
            return {"total_record": "1", "valid_record": "1", "invalid_record": "0"}
    check = ReleaseCheck(Z())
    assert check() and check() and calls == ["release_is_snapshot"]


def test_the_ledger_keeps_every_role_receipt():
    from portorium.audit import receipt_columns
    receipts = [{"role": "Adjudicator", "routed_model": "m1", "receipt_id": "r1"},
                {"role": "Critic", "routed_model": "m2", "receipt_id": "r2"}]
    assert receipt_columns(receipts) == ("m1;m2", "A:r1;C:r2")


def test_an_importer_answer_also_shapes_which_rulings_are_retrieved():
    asked = []

    class Index:
        def search(self, nvidia, text, as_of, exclude=frozenset()):
            asked.append(text)
            return []
    gate = PrecedentGate(Index(), None, FakeNightshift(RECEIPT), LIVE)
    gate.classify("Men's knitted polo shirt", "2026-10-27")
    gate.classify("Men's knitted polo shirt", "2026-10-27", answers=[("What is the fibre?", "100% polyester")])
    assert asked == ["Men's knitted polo shirt", "Men's knitted polo shirt. What is the fibre? 100% polyester"]


def test_a_ledger_row_with_a_missing_receipt_has_no_receipt_column():
    from portorium.audit import receipt_columns
    models, ids = receipt_columns([{"role": "Adjudicator", "routed_model": "m", "receipt_id": "r1"},
                                   {"role": "Critic", "routed_model": "m", "receipt_id": None}])
    assert ids == ""
    many = [{"role": r, "routed_model": f"nvidia/nemotron-model-number-{i}-with-a-long-name", "receipt_id": "x"}
            for i, r in enumerate(("Adjudicator", "Proposer", "Critic"))]
    assert len(receipt_columns(many)[0]) <= 80


def test_an_empty_release_answer_is_not_a_current_schedule():
    from portorium.audit import ReleaseCheck

    class Z:
        def run_dq(self, rule, table):
            return {"total_record": "0", "valid_record": "0", "invalid_record": "0"}
    assert ReleaseCheck(Z())() is False

def test_a_fit_the_critic_says_it_had_to_assume_becomes_a_question(monkeypatch):
    monkeypatch.setattr("portorium.gate.schedule_text", lambda zetaris, code: PATH)
    reply = {"fits": True, "line": "", "question": "", "reason": "Polo shirts are shirts of heading 6105. The product description "
             "does not specify the fiber composition, but it is presumed the shirt is of cotton."}
    v = critique(PATH, reply=reply, product="Men's knitted polo shirt")[1]
    assert (v.decision, v.reason, v.code) == (REVIEW, "FACT_NOT_STATED", None)
    assert "fiber composition" in v.question


def test_legal_phrases_about_other_headings_are_not_assumptions(monkeypatch):
    monkeypatch.setattr("portorium.gate.schedule_text", lambda zetaris, code: PATH)
    for reason in ("Made-up textile articles not specified or included elsewhere; it is not a cleaning cloth.",
                   "Parts of loudspeakers are not specified or included elsewhere in this chapter; other articles not specifically provided for."):
        reply = {"fits": True, "line": "", "question": "", "reason": reason}
        assert critique(PATH, reply=reply)[1].decision == CLASSIFIED, reason
