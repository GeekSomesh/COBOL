"""Python client for the REST API (docs/api.md). Needs only ``requests``.

    from src.sdk import DecisionClient
    api = DecisionClient("http://127.0.0.1:8000", token="...")
    pid = api.upload("SAM2.cbl", copybooks=["CUSTCOPY.cpy", "TRANREC.cpy"])["program_id"]
    api.wait(api.extract(pid)["job_id"])
    for r in api.rules(program=pid)["items"]:
        print(r["rule_id"], r["status"], r["title"])
    api.review("SAM2-R001", "approve", "matches policy 4.2")
    print(api.score("SAM2.cbl", {"tran_code": "UPDATE"}))
    open("sam2.dmn", "w").write(api.export("dmn", program="SAM2.cbl"))

Errors raise ``ApiError`` with the API's error code, message and request id.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterable, Optional


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, request_id: Optional[str] = None):
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message, self.request_id = status, code, message, request_id


class DecisionClient:
    def __init__(self, base_url: str, token: str, timeout: float = 60, session: Any = None):
        import requests
        self.base = base_url.rstrip("/")
        self.http = session or requests.Session()
        self.http.headers["Authorization"] = f"Bearer {token}"
        self.timeout = timeout

    # ---- plumbing ------------------------------------------------------------------
    def _call(self, method: str, path: str, **kw: Any) -> Any:
        r = self.http.request(method, self.base + path, timeout=self.timeout, **kw)
        if r.status_code >= 400:
            try:
                e = r.json()["error"]
            except Exception:
                raise ApiError(r.status_code, "HTTP_ERROR", r.text[:200]) from None
            raise ApiError(r.status_code, e.get("code", "ERROR"), e.get("message", ""), e.get("request_id"))
        if r.status_code == 204:
            return None
        return r.json() if "json" in r.headers.get("content-type", "") else r.text

    # ---- programs and extraction ---------------------------------------------------------
    def upload(self, program: str | Path, copybooks: Iterable[str | Path] = ()) -> dict:
        files = [("program", (Path(program).name, Path(program).read_bytes()))]
        files += [("copybooks", (Path(c).name, Path(c).read_bytes())) for c in copybooks]
        return self._call("POST", "/v1/programs", files=files)

    def extract(self, program_id: str) -> dict:
        return self._call("POST", f"/v1/programs/{program_id}/extract")

    def wait(self, job_id: str, poll: float = 1.0, timeout: float = 3600) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            job = self._call("GET", f"/v1/jobs/{job_id}")
            if job["state"] in ("done", "failed"):
                if job["state"] == "failed":
                    raise ApiError(500, "EXTRACTION_FAILED", job.get("error") or "extraction failed")
                return job
            time.sleep(poll)
        raise TimeoutError(f"job {job_id} did not finish in {timeout} s")

    def programs(self) -> dict:
        return self._call("GET", "/v1/programs")

    # ---- rules and review ------------------------------------------------------------------
    def rules(self, **filters: Any) -> dict:
        return self._call("GET", "/v1/rules", params={k: v for k, v in filters.items() if v is not None})

    def rule(self, rule_id: str, version: Optional[int] = None) -> dict:
        return self._call("GET", f"/v1/rules/{rule_id}", params={"version": version} if version else None)

    def review(self, rule_id: str, action: str, reason: str = "", changes: Optional[dict] = None) -> dict:
        return self._call("PATCH", f"/v1/rules/{rule_id}", json={"action": action, "reason": reason, "changes": changes})

    def bulk_review(self, rule_ids: list[str], action: str, reason: str) -> dict:
        return self._call("POST", "/v1/rules/bulk_review", json={"rule_ids": rule_ids, "action": action, "reason": reason})

    def assign(self, rule_id: str, assignee: str) -> dict:
        return self._call("POST", f"/v1/rules/{rule_id}/assign", json={"assignee": assignee})

    def comment(self, rule_id: str, text: str) -> dict:
        return self._call("POST", f"/v1/rules/{rule_id}/comments", json={"text": text})

    def review_queue(self, mine: bool = False) -> dict:
        return self._call("GET", "/v1/review_queue", params={"mine": mine})

    # ---- decisions and exports ------------------------------------------------------------
    def score(self, rule_set: str, inputs: dict[str, Any]) -> dict:
        return self._call("POST", "/v1/score", json={"rule_set": rule_set, "inputs": inputs})

    def export(self, fmt: str = "json", **filters: Any) -> Any:
        return self._call("GET", "/v1/export", params={"format": fmt, **{k: v for k, v in filters.items() if v}})

    def conflicts(self, program: Optional[str] = None) -> dict:
        return self._call("GET", "/v1/conflicts", params={"program": program} if program else None)

    # ---- repository -----------------------------------------------------------------------------
    def upload_jcl(self, path: str | Path) -> dict:
        return self._call("POST", "/v1/jcl", files=[("jcl", (Path(path).name, Path(path).read_bytes()))])

    def repository(self) -> dict:
        return self._call("GET", "/v1/repository")

    def impact(self, kind: str, name: str) -> dict:
        return self._call("GET", "/v1/impact", params={"kind": kind, "name": name})

    def diff(self, old_program_id: str, new_program_id: str) -> dict:
        return self._call("GET", "/v1/diff", params={"old": old_program_id, "new": new_program_id})

    def glossary(self, update: Optional[dict] = None) -> dict:
        return self._call("PUT", "/v1/glossary", json=update) if update is not None else self._call("GET", "/v1/glossary")
