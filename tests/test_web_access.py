import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from portorium.trace import Trace
from portorium.web import may_write


def test_an_importer_name_must_be_a_plain_identifier():
    for name in ("another/victim", "../demo", "", "a b"):
        with pytest.raises(HTTPException) as e:
            may_write(name, broker=False, token="", live=False, expected="")
        assert e.value.status_code == 400


def test_the_hosted_sample_lets_visitors_write_only_their_own_memory():
    may_write("visitor-ab12cd", broker=True, token="", live=False, expected="")
    with pytest.raises(HTTPException) as e:
        may_write("demo", broker=False, token="", live=False, expected="")
    assert e.value.status_code == 403


def test_live_broker_actions_need_the_configured_token():
    may_write("acme", broker=False, token="", live=True, expected="")
    may_write("acme", broker=True, token="s3cret", live=True, expected="s3cret")
    for token, expected in (("", "s3cret"), ("wrong", "s3cret"), ("", "")):
        with pytest.raises(HTTPException) as e:
            may_write("acme", broker=True, token=token, live=True, expected=expected)
        assert e.value.status_code == 403


def test_a_trace_id_cannot_be_guessed_from_its_time(tmp_path):
    suffix = Trace(directory=tmp_path, echo=None).run_id.split("-", 1)[1]
    assert len(suffix) == 16


def test_the_hosted_sample_refuses_a_sign_off_into_the_shared_namespace(monkeypatch):
    monkeypatch.setenv("SAMPLE_MODE", "true")
    from portorium import web
    r = TestClient(web.app).post("/api/confirm", json={"product": "x", "code": "8518220000", "broker": "b", "importer": "demo"})
    assert r.status_code == 403
