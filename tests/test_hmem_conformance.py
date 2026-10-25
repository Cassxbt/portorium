"""Meterless H-MEM conformance: the official vectors.json run against Portorium's Python port (mirrors conformance/runner.ts)."""
import json, os, pytest
from portorium.engines.hmem import HMEM
V = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "hmem", "vectors.json")))
T0, DAY = V["clock"], 86_400_000
S = V["sections"]
def ids(sec): return [c["id"] for c in S[sec]]

@pytest.mark.parametrize("c", S["record-validation"], ids=ids("record-validation"))
def test_rv(c):
    h = HMEM(clock=lambda: T0)
    try: h.add(content=c["input"]["content"], source=c["input"]["source"]); rejected = False
    except ValueError: rejected = True
    assert rejected == (c["expect"] == "reject")

@pytest.mark.parametrize("c", S["feedback-deltas"], ids=ids("feedback-deltas"))
def test_fd(c):
    h = HMEM(clock=lambda: T0); m = h.add(content="feedback target record", confidence=c["start"], source="conformance")
    before = m.access_count; h.feedback(m.id, c["kind"]); a = h.store.get(m.id)
    assert a.confidence == pytest.approx(c["expectConfidence"], abs=1e-9)
    if "expectAccessDelta" in c: assert a.access_count - before == c["expectAccessDelta"]
    if "expectTag" in c: assert c["expectTag"] in a.tags

@pytest.mark.parametrize("c", S["ranking-order"], ids=ids("ranking-order"))
def test_ro(c):
    h = HMEM(clock=lambda: T0); by = {}
    for r in c["records"]: by[r["key"]] = h.add(content=r["content"], confidence=r["confidence"], layer=r["layer"], source="conformance")
    for r in c["records"]:
        if "supersededByKey" in r: by[r["key"]].superseded_by = by[r["supersededByKey"]].id
    order = [x["memory"].id for x in h.query(c["query"], threshold=0, top_n=10)["memories"]]
    exp = [by[k].id for k in c["expectOrder"]]
    for a, b in zip(exp, exp[1:]): assert order.index(a) < order.index(b)

@pytest.mark.parametrize("c", S["sleep-guardrails"], ids=ids("sleep-guardrails"))
def test_sg(c):
    now = [T0]; h = HMEM(clock=lambda: now[0]); by = {}
    for r in c["records"]: by[r["key"]] = h.add(content=r["content"], source=r["source"], derived_from=r.get("derivedFrom"))
    now[0] = T0 + c["ageDays"] * DAY
    before = sorted(m.id for m in h.store.all()); pv = h.sleep.preview()
    assert sorted(m.id for m in h.store.all()) == before, "preview mutated the store"
    for k in c["expectArchived"]: assert by[k].id in pv["to_archive"]
    for k in c["expectProtected"]: assert by[k].id not in pv["to_archive"]
    rep = h.sleep.execute(pv)
    for k in c["expectProtected"]: assert h.store.get(by[k].id) is not None
    assert isinstance(rep["backup_id"], str) and rep["backup_id"]

@pytest.mark.parametrize("c", S["conflict-gate"], ids=ids("conflict-gate"))
def test_cg(c):
    h = HMEM(clock=lambda: T0)
    a = h.add(content=c["a"]["content"], confidence=c["a"]["confidence"], source="conformance")
    b = h.add(content=c["b"]["content"], confidence=c["b"]["confidence"], source="conformance")
    found = h.conflicts.scan(); assert found
    out = h.conflicts.auto_resolve(found[0]); assert out in ("resolved", "queued")
    if out == "resolved": assert any(m.superseded_by for m in (h.store.get(a.id), h.store.get(b.id)))
    assert h.store.get(a.id) and h.store.get(b.id)

@pytest.mark.parametrize("c", S["ledger-audit"], ids=ids("ledger-audit"))
def test_la(c):
    h = HMEM(clock=lambda: T0); m = h.add(content="audited record", confidence=c["start"], source="conformance")
    h.feedback(m.id, c["kind"]); hist = h.ledger.history(m.id)
    assert [e["action"] for e in hist] == c["expectActions"]
    fb = hist[-1]
    assert fb["previous_state"]["confidence"] == pytest.approx(c["expectPrev"]); assert fb["new_state"]["confidence"] == pytest.approx(c["expectNew"])

@pytest.mark.parametrize("c", S["dream-boundary"], ids=ids("dream-boundary"))
def test_db(c):
    h = HMEM(clock=lambda: T0)
    for r in c["records"]: h.add(content=r["content"], type=r["type"], source="conformance")
    n = len(h.store.all()); props = h.dreaming.dream(); assert props
    assert len(h.store.all()) == n, "dreaming materialized before approval"
    created = h.dreaming.approve(props[0]["id"])
    if created: assert created.layer == "long_term" and created.derived_from
