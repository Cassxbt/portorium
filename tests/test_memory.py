from portorium.memory import BrokerMemory

PRODUCT = "girls' pullover sweater, knitted, 70% cotton 30% nylon, rib-knit cuffs"


def test_a_broker_decision_is_recalled_for_a_twin_product(tmp_path):
    memory = BrokerMemory(tmp_path / "importer.json")
    memory.remember(PRODUCT, "6110202079", broker="broker-1", note="knitted, cotton predominates")
    recalls = memory.recall("girls' pullover sweater, knitted, 70% cotton 30% nylon")
    assert [r.code for r in recalls] == ["6110202079"]
    assert recalls[0].relevance >= 0.35


def test_unrelated_products_recall_nothing(tmp_path):
    memory = BrokerMemory(tmp_path / "importer.json")
    memory.remember(PRODUCT, "6110202079", broker="broker-1")
    assert memory.recall("stainless steel hex bolts, M8 thread") == []


def test_wiping_memory_removes_the_recall_and_logs_it(tmp_path):
    memory = BrokerMemory(tmp_path / "importer.json")
    memory_id = memory.remember(PRODUCT, "6110202079", broker="broker-1")
    memory.forget(memory_id, reason="deletion test")
    assert memory.recall(PRODUCT) == []
    assert [e["action"] for e in memory.history(memory_id)][-1] == "delete"


def test_memory_survives_a_restart(tmp_path):
    path = tmp_path / "importer.json"
    BrokerMemory(path).remember(PRODUCT, "6110202079", broker="broker-1")
    assert [r.code for r in BrokerMemory(path).recall(PRODUCT)] == ["6110202079"]


def test_an_importer_answer_is_recalled_for_a_similar_product(tmp_path):
    memory = BrokerMemory(tmp_path / "importer.json")
    memory.remember_answer(PRODUCT, "Is the fabric knitted or woven?", "Knitted, jersey construction")
    facts = memory.recall_answers("girls' pullover sweater, 70% cotton 30% nylon")
    assert facts and facts[0][1] == "Knitted, jersey construction"


def test_answers_and_code_decisions_are_kept_apart(tmp_path):
    memory = BrokerMemory(tmp_path / "importer.json")
    memory.remember_answer(PRODUCT, "Is the fabric knitted or woven?", "Knitted")
    assert memory.recall(PRODUCT) == []
