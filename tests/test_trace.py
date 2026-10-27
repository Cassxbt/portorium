import json
import threading

from portorium import trace
from portorium.gate import PrecedentGate
from portorium.index import Precedent
from portorium.liveness import LIVING, Liveness
from portorium.memory import BrokerMemory
from portorium.nightshift import Answer

FIELDS = {"timestamp", "agent_name", "action", "input_summary", "output_summary", "target_agent", "model",
          "confidence", "retry_count", "status"}
PRODUCT = "girls' pullover sweater, knitted, 70% cotton 30% nylon"
LIVE = Liveness({"6110202079"}, {"61102020"})


class FakeIndex:
    def search(self, nvidia, product, as_of, exclude=()):
        return [Precedent("N111111", "2024-01-01", "sweater", ["6110202079"], [])]


class FakeTexts:
    def facts(self, ruling_number):
        return "knitted pullover, 70% cotton"


class FakeNightshift:
    def __init__(self, fits=True):
        self.fits = fits

    def chat_json(self, messages, required, model, max_tokens=1024):
        if "code" in required:
            return {"code": "6110.20.2079", "confidence": 90}, Answer("{}", {"routed_model": model, "receipt_id": "rcpt_3"})
        if "fits" in required:
            return ({"fits": self.fits, "line": "" if self.fits else "6110.20.20", "reason": "r", "question": "" if self.fits else "Is it knitted?",
                     "facts": [{"fact": "knitted pullover", "quote": "pullover sweater, knitted"}]},
                    Answer("{}", {"routed_model": model, "receipt_id": "rcpt_2"}))
        rows = [{"ruling": "N111111", "match": "full", "code": "6110202079", "fact": "knitted"}]
        return {"precedents": rows, "question": ""}, Answer("{}", {"routed_model": model, "receipt_id": "rcpt_1"})


class FakeZetaris:
    def query(self, sql, limit=1000):
        trace.event("sql", sql, "1 row(s)")
        if "portorium_files.hts_line" in sql:
            return [{"kind": "path", "lvl": "0", "r": "1", "htsno": "6110", "description": "Sweaters, pullovers"},
                    {"kind": "path", "lvl": "1", "r": "2", "htsno": "6110.20.20.79", "description": "Other"}]
        return [{"ruling_number": "N111111", "ruling_date": "2024-01-01", "revoked": "false",
                 "hts10": "6110202079", "status": LIVING}]


def _lines(t: trace.Trace) -> list[dict]:
    return [json.loads(line) for line in t.path.open()]


def test_a_classification_traces_each_role_handing_off_to_the_next(tmp_path):
    memory = BrokerMemory(tmp_path / "importer.json")
    gate = PrecedentGate(FakeIndex(), None, FakeNightshift(), LIVE, FakeTexts(), FakeZetaris())
    run = trace.Trace(directory=tmp_path, echo=None)
    with run.bind("N999999"):
        verdict = gate.classify(PRODUCT, "2026-01-01", memory=memory)
    lines = _lines(run)
    assert all(FIELDS <= set(line) and line["subject"] == "N999999" for line in lines)
    handoffs = [(l["agent_name"], l["action"], l["target_agent"]) for l in lines if l["action"] != "hmem.query"]
    assert handoffs[:4] == [("Retriever", "retrieve", "Adjudicator"), ("Memory", "recall", "Adjudicator"),
                            ("Auditor", "sql", None), ("Auditor", "check_liveness", "Adjudicator")]
    # The Adjudicator and the Proposer run in parallel, so their lines may come in either order.
    assert sorted(handoffs[4:6]) == [("Adjudicator", "judge", "Gate"), ("Proposer", "propose", "Gate")]
    assert handoffs[6:] == [("Gate", "decide", "Critic"), ("Auditor", "sql", None),
                            ("Auditor", "schedule_text", "Critic"), ("Critic", "critique", "Gate")]
    assert [l["agent_name"] for l in lines if l["action"] == "hmem.query"] == ["Memory", "Memory"]
    decide = next(l for l in lines if l["action"] == "decide")
    assert (decide["status"], decide["detail"]["rule"]) == ("success", verdict.reason)
    assert verdict.critique["fits"] is True
    assert next(l for l in lines if l["action"] == "judge")["model"] == "nvidia/nemotron-3-ultra-550b-a55b"


def test_concurrent_runs_write_to_their_own_traces(tmp_path):
    runs = [trace.Trace(run_id=f"r{i}", directory=tmp_path, echo=None) for i in range(2)]

    def work(run):
        with run.bind(run.run_id), trace.role("Retriever"):
            for _ in range(50):
                trace.event("retrieve", "q", run.run_id)

    threads = [threading.Thread(target=work, args=(r,)) for r in runs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for run in runs:
        assert {(l["subject"], l["output_summary"]) for l in _lines(run)} == {(run.run_id, run.run_id)}


def test_events_outside_a_run_are_not_written(tmp_path):
    run = trace.Trace(directory=tmp_path, echo=None)
    trace.event("retrieve", "q", "a")
    assert not run.path.exists()


def test_a_code_whose_schedule_text_does_not_fit_goes_to_review(tmp_path):
    gate = PrecedentGate(FakeIndex(), None, FakeNightshift(fits=False), LIVE, FakeTexts(), FakeZetaris())
    with trace.Trace(directory=tmp_path, echo=None).bind():
        verdict = gate.classify(PRODUCT, "2026-01-01")
    assert (verdict.decision, verdict.reason, verdict.code) == ("REVIEW", "SCHEDULE_TEXT_DISAGREES", None)
    assert verdict.question == "Is it knitted?" and verdict.critique["code"] == "6110202079"
    assert verdict.proposal["code"] == "6110202079" and [r["role"] for r in verdict.receipts] == ["Adjudicator", "Proposer", "Critic"]


def test_the_critic_reads_answers_remembered_from_an_earlier_request(tmp_path):
    seen = []

    class Recording(FakeNightshift):
        def chat_json(self, messages, required, model, max_tokens=1024):
            if "fits" in required:
                seen.append(messages[-1]["content"])
            return super().chat_json(messages, required, model)

    memory = BrokerMemory(tmp_path / "importer.json")
    memory.remember_answer(PRODUCT, "What is the fibre?", "100% cotton")
    gate = PrecedentGate(FakeIndex(), None, Recording(), LIVE, FakeTexts(), FakeZetaris())
    with trace.Trace(directory=tmp_path, echo=None).bind():
        gate.classify(PRODUCT, "2026-01-01", memory=memory)
    assert "IMPORTER ANSWERED EARLIER for a similar product (use only if it applies): What is the fibre? -> 100% cotton" in seen[0]
