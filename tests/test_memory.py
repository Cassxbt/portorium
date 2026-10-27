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




def test_answers_and_code_decisions_are_kept_apart(tmp_path):
    memory = BrokerMemory(tmp_path / "importer.json")
    memory.remember_answer(PRODUCT, "Is the fabric knitted or woven?", "Knitted")
    assert memory.recall(PRODUCT) == []


def test_an_importer_answer_is_reused_only_for_the_same_product(tmp_path):
    memory = BrokerMemory(tmp_path / "importer.json")
    memory.remember_answer("Men's knitted polo shirt, three-button placket", "What is the fibre?", "100% cotton jersey knit")
    assert [a for _, a, _ in memory.recall_answers("men's knitted polo shirt,  three-button placket ")] == ["100% cotton jersey knit"]
    assert memory.recall_answers("Men's knitted polo shirt, three-button placket, 100% polyester") == []


def test_two_handles_on_one_importer_keep_both_writes(tmp_path):
    first, second = BrokerMemory(tmp_path / "importer.json"), BrokerMemory(tmp_path / "importer.json")
    first.remember(PRODUCT, "6110202079", broker="broker-1")
    second.remember("portable Bluetooth party speaker, two drivers", "8518220000", broker="broker-2")
    assert sorted(r.code for r in BrokerMemory(tmp_path / "importer.json").recall(PRODUCT)
                  + BrokerMemory(tmp_path / "importer.json").recall("portable Bluetooth party speaker, two drivers")) \
        == ["6110202079", "8518220000"]


def test_asking_memory_writes_nothing(tmp_path):
    memory = BrokerMemory(tmp_path / "importer.json")
    memory.recall(PRODUCT)
    memory.recall_answers(PRODUCT)
    assert not (tmp_path / "importer.json").exists()
