"""Zetaris as Portorium's data layer, over the Zetaris web API (no JDBC/Java needed).

Precedent liveness is a Zetaris data-quality decision: the app asks Zetaris which ruling codes
fail the registered rules and acts on the answer, rather than re-implementing the checks.
"""
import base64
import json
import time
import uuid

import httpx

from . import trace
from .config import Settings, recordings
from .recorder import Recorder

USL = "lightning.metastore.portorium.portorium"

# Zetaris answers an expired session with this exact 400 body rather than a 401; SQL errors carry a message.
EXPIRED = {"msg": "Bad Request"}
RENEW_MARGIN_SECONDS = 60


class ZetarisError(RuntimeError):
    pass


class Zetaris:
    def __init__(self, settings: Settings, live: bool):
        self.settings = settings
        self.live = live
        self.recorder = Recorder("zetaris", recordings(live))
        self._http = httpx.Client(timeout=600)
        self._token: str | None = None
        self._renew_at = 0.0

    def query(self, sql: str, limit: int = 1000) -> list[dict]:
        """Run one statement (no trailing semicolon) and return rows as dicts of strings."""
        # Keep line breaks: COMPILE USL needs each CREATE TABLE to start on a new line.
        request = {"sql": "\n".join(line.strip() for line in sql.strip().splitlines()), "limit": limit}
        try:
            if not self.live:
                data = self.recorder.replay(request)
            else:
                data = self._run(request)
                self.recorder.record(request, data)
        except ZetarisError as e:
            trace.event("sql", request["sql"], str(e), status="error")
            raise
        headers = data.get("headers") or []
        rows = [dict(zip(headers, row)) for row in data.get("data") or []]
        shown = f": {rows[0]}" if len(rows) == 1 else ""
        trace.event("sql", request["sql"], f"{len(rows)} row(s){shown}", source="live" if self.live else "recording")
        return rows

    def run_dq(self, rule: str, table: str) -> dict:
        rows = self.query(f"RUN DQ {rule} TABLE {USL}.{table}")
        if not rows:
            raise ZetarisError(f"RUN DQ {rule} returned nothing")
        return rows[0]

    def invalid_records(self, rule: str, table: str, limit: int = 100000) -> list[dict]:
        return self.query(f"SHOW DQ INVALID RECORD {rule} TABLE {USL}.{table}", limit=limit)

    def _login(self) -> str:
        r = self._http.post(f"{self.settings.zetaris_url}/api/auth/login",
                            json={"username": self.settings.zetaris_username, "password": self.settings.zetaris_password},
                            headers={"X-Request-ID": str(uuid.uuid4())})
        if r.status_code != 200:
            raise ZetarisError(f"login failed: HTTP {r.status_code}")
        token = r.json()["idToken"]
        claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
        # Timed on the local clock from the token's own lifetime, so clock skew with the server cannot matter.
        self._renew_at = time.monotonic() + claims["exp"] - claims["iat"] - RENEW_MARGIN_SECONDS
        return token

    def _run(self, request: dict) -> dict:
        for attempt in range(2):
            if not self._token or time.monotonic() >= self._renew_at:
                self._token = self._login()
            r = self._http.post(f"{self.settings.zetaris_url}/api/proxy/sql-editor/sqls/run-query", json=request,
                                headers={"Authorization": f"Bearer {self._token}", "X-Org-ID": self.settings.zetaris_org_id,
                                         "X-Request-ID": str(uuid.uuid4())})
            if attempt == 0 and (r.status_code == 401 or (r.status_code == 400 and _json(r) == EXPIRED)):
                self._token = None
                continue
            if r.status_code != 200:
                raise ZetarisError(f"HTTP {r.status_code}: {r.text[:400]}")
            return r.json()
        raise ZetarisError("unauthorised after re-login")


def _json(response: httpx.Response):
    try:
        return response.json()
    except ValueError:
        return None
