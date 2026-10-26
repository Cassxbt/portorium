import json

from fastapi.testclient import TestClient

from portorium.config import DATA, ROOT

ORDER = ["classified", "dead-code", "question", "question-answered", "question-remembered"]


def test_sample_bundle_replays_every_example(monkeypatch):
    # A fresh clone shows these outputs; the bundle is stale if today's code no longer reproduces them from it.
    monkeypatch.setenv("SAMPLE_MODE", "true")
    from portorium import web

    importer = "replay-test"
    memory = DATA / "memory" / f"{importer}.json"
    memory.unlink(missing_ok=True)
    try:
        client = TestClient(web.app)
        for name in ORDER:
            body = {**json.loads((ROOT / "input_examples" / f"{name}.json").read_text()), "importer": importer}
            expected = json.loads((ROOT / "output_examples" / f"{name}.json").read_text())["verdict"]
            r = client.post("/api/classify", json=body)
            assert r.status_code == 200, (name, r.text[:300])
            got = r.json()
            assert [got[k] for k in ("decision", "reason", "code", "question")] == \
                [expected[k] for k in ("decision", "reason", "code", "question")], name
    finally:
        memory.unlink(missing_ok=True)
