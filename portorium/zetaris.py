"""Zetaris as Portorium's data layer, over the Zetaris web API (no JDBC/Java needed).

Precedent liveness is a Zetaris data-quality decision: the app asks Zetaris which ruling codes
fail the registered rules and acts on the answer, rather than re-implementing the checks.
"""
import time
import uuid

import httpx

from .config import Settings
from .recorder import Recorder

USL = "lightning.metastore.portorium.portorium"


class ZetarisError(RuntimeError):
    pass


class Zetaris:
    def __init__(self, settings: Settings, live: bool):
        self.settings = settings
        self.live = live
        self.recorder = Recorder("zetaris")
        self._http = httpx.Client(timeout=600)
        self._token: str | None = None

    def query(self, sql: str, limit: int = 1000) -> list[dict]:
        """Run one statement (no trailing semicolon) and return rows as dicts of strings."""
        request = {"sql": " ".join(sql.split()), "limit": limit}
        if not self.live:
            data = self.recorder.replay(request)
        else:
            data = self._run(request)
            self.recorder.record(request, data)
        headers = data.get("headers") or []
        return [dict(zip(headers, row)) for row in data.get("data") or []]

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
        return r.json()["idToken"]

    def _run(self, request: dict) -> dict:
        for attempt in range(2):
            self._token = self._token or self._login()
            r = self._http.post(f"{self.settings.zetaris_url}/api/proxy/sql-editor/sqls/run-query", json=request,
                                headers={"Authorization": f"Bearer {self._token}", "X-Org-ID": self.settings.zetaris_org_id,
                                         "X-Request-ID": str(uuid.uuid4())})
            if r.status_code == 401 and attempt == 0:
                self._token = None
                continue
            if r.status_code != 200:
                raise ZetarisError(f"HTTP {r.status_code}: {r.text[:400]}")
            return r.json()
        raise ZetarisError("unauthorised after re-login")
