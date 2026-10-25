import json

from portorium.gate import CLASSIFIED, MEMORY, REFUSE, REVIEW, JudgedPrecedent, PrecedentGate
from portorium.index import Precedent
from portorium.liveness import LIVING, REMOVED, SPLIT, Liveness
from portorium.nightshift import Answer

LIVE = Liveness({"6110202079", "6110202075", "6702903500"}, {"61102020", "67029035", "87082951"})


def judged(number, match, code, status, kind="ruling", unresolved=False):
    return JudgedPrecedent(number, "2024-01-01", "subject", [code] if code else [], match, code, "fact", status,
                           kind=kind, unresolved=unresolved)


def decide(*precedents, question=""):
    gate = PrecedentGate(None, None, None, LIVE)
    return gate.decide(list(precedents), question, Answer("{}", {"routed_model": "nvidia/ultra"}))


def test_classified_only_when_living_full_matches_agree_at_ten_digits():
    v = decide(judged("N1", "full", "6110202079", LIVING), judged("N2", "full", "6110202079", LIVING))
    assert (v.decision, v.code, v.reason) == (CLASSIFIED, "6110202079", "LIVING_PRECEDENT_AGREES")


def test_agreement_at_six_digits_is_not_enough():
    v = decide(judged("N1", "full", "6110202079", LIVING), judged("N2", "full", "6110202075", LIVING),
               question="Is it for men or women?")
    assert (v.decision, v.reason, v.question) == (REVIEW, "PRECEDENTS_DISAGREE", "Is it for men or women?")


def test_partial_match_never_classifies():
    assert decide(judged("N1", "partial", "6110202079", LIVING)).reason == "ONLY_PARTIAL"


def test_split_precedent_goes_to_review():
    v = decide(judged("N1", "full", "8708295160", SPLIT))
    assert (v.decision, v.reason) == (REVIEW, "SPLIT_SUCCESSORS")


def test_removed_precedent_is_refused_as_stale():
    v = decide(judged("N1", "full", "9999999999", REMOVED))
    assert (v.decision, v.reason, v.code) == (REFUSE, "STALE_PRECEDENT", None)


def test_no_match_is_refused():
    assert decide(judged("N1", "none", None, None)).reason == "NO_LIVING_PRECEDENT"


def test_an_unresolved_full_match_fails_closed():
    v = decide(judged("N1", "full", "6110202079", LIVING), judged("N2", "full", None, None, unresolved=True))
    assert (v.decision, v.reason) == (REVIEW, "UNRESOLVED_MATCH")


def test_broker_memory_settles_review_only_with_a_candidate_code():
    v = decide(judged("N1", "partial", "6110202079", LIVING), judged("MEM:mem-1", "full", "6110202079", LIVING, MEMORY))
    assert (v.decision, v.reason, v.code) == (CLASSIFIED, "BROKER_MEMORY", "6110202079")
    v = decide(judged("N1", "partial", "6110202079", LIVING), judged("MEM:mem-1", "full", "6110202075", LIVING, MEMORY))
    assert v.decision == REVIEW


def test_broker_memory_judged_partial_does_not_settle_anything():
    v = decide(judged("N1", "partial", "6110202079", LIVING), judged("MEM:mem-1", "partial", "6110202079", LIVING, MEMORY))
    assert v.reason == "ONLY_PARTIAL"


def test_broker_memory_that_contradicts_a_classification_sends_it_to_review():
    v = decide(judged("N1", "full", "6110202079", LIVING), judged("MEM:mem-1", "full", "6110202075", LIVING, MEMORY))
    assert (v.decision, v.reason) == (REVIEW, "MEMORY_CONFLICT")


def test_every_verdict_carries_the_receipt():
    assert decide(judged("N1", "none", None, None)).receipts[0]["routed_model"] == "nvidia/ultra"


class FakeNightshift:
    def __init__(self, rows):
        self.rows = rows

    def chat_json(self, messages, required, model):
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


def test_old_precedent_rule_needs_a_recent_or_a_second_agreeing_precedent():
    old = JudgedPrecedent("N1", "2015-01-01", "s", ["6110202079"], "full", "6110202079", "f", LIVING, pre_hs2022=True)
    recent = JudgedPrecedent("N2", "2023-01-01", "s", ["6110202079"], "full", "6110202079", "f", LIVING)
    gate = PrecedentGate(None, None, None, LIVE, old_precedent_rule=True)
    receipt = Answer("{}", {})
    assert gate.decide([old], "", receipt).reason == "OLD_PRECEDENT_ONLY"
    assert gate.decide([old, recent], "", receipt).decision == CLASSIFIED
    assert gate.decide([old, JudgedPrecedent(**{**vars(old), "ruling_number": "N3"})], "", receipt).decision == CLASSIFIED
