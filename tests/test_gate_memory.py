from portorium.gate import CLASSIFIED, REVIEW, JudgedPrecedent, Verdict, apply_memory
from portorium.liveness import LIVING, Liveness
from portorium.memory import BrokerMemory

PRODUCT = "girls' pullover sweater, knitted, 70% cotton 30% nylon, rib-knit cuffs"


def precedent(code, match="partial"):
    return JudgedPrecedent("N1", "2024-01-01", "subject", [code], match, code, "fact", LIVING, None, False)


def review(*codes):
    return Verdict(REVIEW, "ONLY_PARTIAL", None, [precedent(c) for c in codes], question="Knitted or woven?")


def test_a_remembered_broker_decision_resolves_review_to_a_candidate_code(tmp_path):
    memory = BrokerMemory(tmp_path / "m.json")
    memory.remember(PRODUCT, "6110202079", broker="broker-1")
    v = apply_memory(review("6110202079", "6110202075"), PRODUCT, memory, Liveness(set(), set()))
    assert (v.decision, v.reason, v.code) == (CLASSIFIED, "BROKER_MEMORY", "6110202079")
    assert v.memory[0]["code"] == "6110202079"


def test_memory_never_introduces_a_code_no_precedent_supports(tmp_path):
    memory = BrokerMemory(tmp_path / "m.json")
    memory.remember(PRODUCT, "6110309000", broker="broker-1")
    v = apply_memory(review("6110202079"), PRODUCT, memory, Liveness(set(), set()))
    assert v.decision == REVIEW


def test_memory_that_contradicts_a_classification_sends_it_to_review(tmp_path):
    memory = BrokerMemory(tmp_path / "m.json")
    memory.remember(PRODUCT, "6110202075", broker="broker-1")
    classified = Verdict(CLASSIFIED, "LIVING_PRECEDENT_AGREES", "6110202079", [precedent("6110202079", "full")])
    v = apply_memory(classified, PRODUCT, memory, Liveness(set(), set()))
    assert (v.decision, v.reason, v.code) == (REVIEW, "MEMORY_CONFLICT", None)


def test_wiping_memory_restores_the_original_verdict(tmp_path):
    memory = BrokerMemory(tmp_path / "m.json")
    memory.remember(PRODUCT, "6110202079", broker="broker-1")
    memory.forget_all(reason="deletion test")
    v = apply_memory(review("6110202079", "6110202075"), PRODUCT, memory, Liveness(set(), set()))
    assert (v.decision, v.reason) == (REVIEW, "ONLY_PARTIAL")


def test_memory_for_a_stale_code_is_ignored(tmp_path):
    memory = BrokerMemory(tmp_path / "m.json")
    memory.remember(PRODUCT, "6110202079", broker="broker-1")
    stale = Liveness({"6110202079"}, {"6110202079"})
    v = apply_memory(review("6110202079"), PRODUCT, memory, stale)
    assert v.decision == REVIEW
