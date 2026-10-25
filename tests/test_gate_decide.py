from portorium.gate import CLASSIFIED, REFUSE, REVIEW, JudgedPrecedent, PrecedentGate
from portorium.liveness import LIVING, REMOVED, SPLIT, Liveness
from portorium.nightshift import Answer


def judged(number, match, code, status):
    return JudgedPrecedent(number, "2024-01-01", "subject", [code], match, code, "fact", status, None, False)


def decide(*precedents, question=""):
    gate = PrecedentGate(None, None, None, Liveness(set(), set()))
    return gate._decide(list(precedents), question, Answer("{}", {"routed_model": "nvidia/ultra"}))


def test_classified_only_when_living_full_matches_agree_at_ten_digits():
    v = decide(judged("N1", "full", "6110202079", LIVING), judged("N2", "full", "6110202079", LIVING))
    assert (v.decision, v.code, v.reason) == (CLASSIFIED, "6110202079", "LIVING_PRECEDENT_AGREES")


def test_agreement_at_six_digits_is_not_enough():
    v = decide(judged("N1", "full", "6110202079", LIVING), judged("N2", "full", "6110202075", LIVING),
               question="Is it for men or women?")
    assert (v.decision, v.reason, v.question) == (REVIEW, "PRECEDENTS_DISAGREE", "Is it for men or women?")


def test_partial_match_never_classifies():
    v = decide(judged("N1", "partial", "6110202079", LIVING))
    assert (v.decision, v.reason) == (REVIEW, "ONLY_PARTIAL")


def test_split_precedent_goes_to_review():
    v = decide(judged("N1", "full", "8708295160", SPLIT))
    assert (v.decision, v.reason) == (REVIEW, "SPLIT_SUCCESSORS")


def test_removed_precedent_is_refused_as_stale():
    v = decide(judged("N1", "full", "9999999999", REMOVED))
    assert (v.decision, v.reason, v.code) == (REFUSE, "STALE_PRECEDENT", None)


def test_no_match_is_refused():
    v = decide(judged("N1", "none", None, None))
    assert (v.decision, v.reason) == (REFUSE, "NO_LIVING_PRECEDENT")


def test_every_verdict_carries_the_receipt():
    assert decide(judged("N1", "none", None, None)).receipts[0]["routed_model"] == "nvidia/ultra"
