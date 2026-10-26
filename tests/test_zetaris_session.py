import base64
import json

import httpx

from portorium.config import Settings
from portorium.zetaris import Zetaris

SETTINGS = Settings("", "", "", "http://zetaris", "user", "pass", "1", sample_mode=False)


def _token(n: int, lifetime: int = 900) -> str:
    claims = base64.urlsafe_b64encode(json.dumps({"iat": 0, "exp": lifetime, "n": n}).encode()).decode().rstrip("=")
    return f"h.{claims}.s"


class FakeZetaris:
    """Zetaris answers an expired token with HTTP 400 {"msg":"Bad Request"}, not 401."""

    def __init__(self):
        self.logins = 0
        self.expired: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            self.logins += 1
            return httpx.Response(200, json={"idToken": _token(self.logins)})
        if request.headers["Authorization"].removeprefix("Bearer ") in self.expired:
            return httpx.Response(400, json={"msg": "Bad Request"})
        if json.loads(request.content)["sql"].startswith("SELEC "):
            return httpx.Response(400, json={"msg": "LightningDdlParseException: mismatched input 'SELEC'"})
        return httpx.Response(200, json={"headers": ["1"], "data": [["1"]]})


def _client(server: FakeZetaris) -> Zetaris:
    zetaris = Zetaris(SETTINGS, live=True)
    zetaris._http = httpx.Client(transport=httpx.MockTransport(server))
    zetaris.recorder.record = lambda request, data: None
    return zetaris


def test_an_expired_session_is_renewed_and_the_query_retried():
    server = FakeZetaris()
    zetaris = _client(server)
    zetaris.query("SELECT 1")
    server.expired.add(zetaris._token)
    assert zetaris.query("SELECT 1") == [{"1": "1"}]
    assert server.logins == 2


def test_the_session_is_renewed_before_it_expires(monkeypatch):
    server = FakeZetaris()
    zetaris = _client(server)
    clock = [1000.0]
    monkeypatch.setattr("portorium.zetaris.time.monotonic", lambda: clock[0])
    zetaris.query("SELECT 1")
    clock[0] += 850
    zetaris.query("SELECT 1")
    assert server.logins == 2


def test_a_sql_error_is_raised_without_a_second_login():
    server = FakeZetaris()
    zetaris = _client(server)
    try:
        zetaris.query("SELEC broken")
    except Exception as e:
        assert "mismatched input" in str(e)
    else:
        raise AssertionError("expected a ZetarisError")
    assert server.logins == 1
