import json

from portorium.gate import CLASSIFIED, MEMORY, REFUSE, REVIEW, JudgedPrecedent, PrecedentGate, Proposal
from portorium.index import Precedent
from portorium.liveness import LIVING, REMOVED, REVOKED, SPLIT, Liveness
from portorium.nightshift import Answer

LIVE = Liveness({"6110202079", "6110202075", "6702903500"}, {"61102020", "67029035", "87082951"})


def judged(number, match, code, status, kind="ruling", unresolved=False):
    return JudgedPrecedent(number, "2024-01-01", "subject", [code] if code else [], match, code, "fact", status,
                           kind=kind, unresolved=unresolved)


def decide(*precedents, proposed="6110202079", statuses=None, question=""):
    gate = PrecedentGate(None, None, None, LIVE)
    if statuses is None:
        statuses = gate._statuses(proposed, list(precedents), {})
    proposal = Proposal(proposed, 95.0, Answer("{}", {"routed_model": "nvidia/proposer"}))
    return gate.decide(list(precedents), question, Answer("{}", {"routed_model": "nvidia/ultra"}), proposal, statuses)


def test_a_proposal_cited_by_a_living_precedent_is_a_candidate():
    v = decide(judged("N1", "partial", "6110202079", LIVING))
    assert (v.decision, v.code, v.reason) == (CLASSIFIED, "6110202079", "LIVING_PRECEDENT")


def test_a_proposal_no_retrieved_ruling_cites_goes_to_review():
    v = decide(judged("N1", "full", "6110202075", LIVING), question="Is it for men or women?")
    assert (v.decision, v.reason, v.question) == (REVIEW, "NO_PRECEDENT_FOR_PROPOSAL", "Is it for men or women?")


def test_a_split_code_goes_to_review_and_names_its_successors():
    v = decide(judged("N1", "full", "6110202076", SPLIT), proposed="6110202076")
    assert (v.decision, v.reason) == (REVIEW, "SPLIT_SUCCESSORS")
    assert v.proposal["successors"] == ["6110202075", "6110202079"]


def test_a_dead_proposal_is_refused_unless_a_living_candidate_remains():
    v = decide(judged("N1", "full", "9999999999", REMOVED), proposed="9999999999")
    assert (v.decision, v.reason, v.code) == (REFUSE, "STALE_PRECEDENT", None)
    v = decide(judged("N1", "full", "9999999999", REMOVED), judged("N2", "partial", "6110202079", LIVING), proposed="9999999999")
    assert (v.decision, v.reason) == (REVIEW, "STALE_PROPOSAL")


def test_a_revoked_ruling_does_not_carry_its_code():
    v = decide(judged("N1", "full", "6110202079", REVOKED), statuses={"N1": REVOKED})
    assert v.decision == REFUSE


def test_a_broker_decision_alone_classifies_as_broker_memory():
    v = decide(judged("MEM:mem-1", "full", "6110202079", LIVING, MEMORY))
    assert (v.decision, v.reason) == (CLASSIFIED, "BROKER_MEMORY")


def test_a_broker_decision_that_contradicts_the_proposal_sends_it_to_review():
    v = decide(judged("N1", "full", "6110202079", LIVING), judged("MEM:mem-1", "full", "6110202075", LIVING, MEMORY))
    assert (v.decision, v.reason) == (REVIEW, "MEMORY_CONFLICT")


def test_every_verdict_carries_both_receipts_and_the_proposal():
    v = decide(judged("N1", "none", None, None))
    assert [r["routed_model"] for r in v.receipts] == ["nvidia/ultra", "nvidia/proposer"]
    assert v.proposal == {"code": "6110202079", "confidence": 95.0, "routed_model": "nvidia/proposer", "receipt_id": None,
                          "status": "living", "successors": []}


class FakeNightshift:
    def __init__(self, rows):
        self.rows = rows

    def chat_json(self, messages, required, model, max_tokens=1024):
        return {"precedents": self.rows, "question": ""}, Answer(json.dumps(self.rows), {"routed_model": model})


def judge(rows, precedents, facts=None):
    gate = PrecedentGate(None, None, FakeNightshift(rows), LIVE)
    facts = facts if facts is not None else {p.ruling_number: "facts" for p in precedents}
    return gate.judge("product", precedents, [], facts)[0]


def test_ruling_ids_and_codes_are_normalised():
    p = Precedent("N111111", "2024-01-01", "sweater", ["6110202079", "6110202075"], [])
    j = judge([{"ruling": "NY N111111", "match": "full", "code": "6110 20 2075", "fact": "x"}], [p])[0]
    assert (j.match, j.code, j.unresolved) == ("full", "6110202075", False)


def test_a_code_outside_the_precedent_is_unresolved_not_dropped():
    p = Precedent("N111111", "2024-01-01", "sweater", ["6110202079", "6110202075"], [])
    j = judge([{"ruling": "N111111", "match": "full", "code": "6110309000", "fact": "x"}], [p])[0]
    assert (j.code, j.unresolved) == (None, True)


def test_full_without_facts_is_downgraded_to_partial():
    p = Precedent("N111111", "2024-01-01", "sweater", ["6110202079"], [])
    j = judge([{"ruling": "N111111", "match": "full", "code": "6110202079", "fact": "x"}], [p], facts={})[0]
    assert j.match == "partial"


def test_a_revoked_ruling_reported_by_zetaris_cannot_classify():
    p = Precedent("N222222", "2024-01-01", "sweater", ["6110202079"], [])
    rows = {"N222222": {"date": "2024-01-01", "revoked": True, "codes": {"6110202079": LIVING}}}
    gate = PrecedentGate(None, None, FakeNightshift([{"ruling": "N222222", "match": "full", "code": "6110202079"}]), LIVE)
    j = gate.judge("product", [p], [], {"N222222": "facts"}, rows)[0][0]
    assert (j.status, j.failed_rule) == ("revoked", "ruling_revoked")
    assert decide(j).decision == REFUSE


def test_zetaris_status_per_decision_overrides_the_startup_snapshot():
    p = Precedent("N333333", "2024-01-01", "sweater", ["6110202079"], [])
    rows = {"N333333": {"date": "2024-01-01", "revoked": False, "codes": {"6110202079": SPLIT}}}
    gate = PrecedentGate(None, None, FakeNightshift([{"ruling": "N333333", "match": "full", "code": "6110202079"}]), LIVE)
    j = gate.judge("product", [p], [], {"N333333": "facts"}, rows)[0][0]
    assert (j.status, j.failed_rule) == (SPLIT, "code_in_schedule")
    assert j.successors == ["6110202075"]


def test_a_precedent_judged_a_different_product_does_not_carry_the_proposal():
    assert decide(judged("N1", "none", "6110202079", LIVING)).reason == "NO_PRECEDENT_FOR_PROPOSAL"
    assert decide(judged("MEM:mem-7", "partial", "6110202079", LIVING, MEMORY)).reason == "NO_PRECEDENT_FOR_PROPOSAL"


ZETARIS = object()


def statuses_with_zetaris(proposed, precedents, rows):
    return PrecedentGate(None, None, None, LIVE, zetaris=ZETARIS)._statuses(proposed, list(precedents), rows)


def test_a_ruling_zetaris_returned_no_row_for_cannot_classify():
    p = judged("N1", "full", "6110202079", LIVING)
    v = decide(p, statuses=statuses_with_zetaris("6110202079", [p], {}))
    assert (v.decision, v.reason, v.code) == (REVIEW, "EVIDENCE_MISSING", None)


def test_a_zetaris_row_without_the_proposed_code_cannot_classify():
    p = judged("N1", "full", "6110202079", LIVING)
    rows = {"N1": {"date": "2024-01-01", "revoked": False, "codes": {"6110202075": LIVING}}}
    assert decide(p, statuses=statuses_with_zetaris("6110202079", [p], rows)).reason == "EVIDENCE_MISSING"


def test_a_ruling_the_adjudicator_matched_to_another_of_its_codes_does_not_carry_the_proposal():
    p = JudgedPrecedent("N1", "2024-01-01", "subject", ["6110202079", "6110202075"], "full", "6110202075", "fact", LIVING)
    assert decide(p).reason == "NO_PRECEDENT_FOR_PROPOSAL"


def test_an_unresolved_multi_article_ruling_does_not_carry_the_proposal():
    p = JudgedPrecedent("N1", "2024-01-01", "subject", ["6110202079", "6110202075"], "full", None, "fact", None, unresolved=True)
    assert decide(p).reason == "NO_PRECEDENT_FOR_PROPOSAL"


def test_zetaris_revocation_flags_parse_whatever_their_type():
    from portorium.liveness import revoked_flag
    assert [revoked_flag(v) for v in (True, "true", "TRUE", "1", False, "false", "0", None, "")] == [True] * 4 + [False] * 5
    import pytest
    with pytest.raises(ValueError):
        revoked_flag("maybe")
