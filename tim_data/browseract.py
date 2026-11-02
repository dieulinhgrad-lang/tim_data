"""Minimal client for the BrowserAct workflow-template API.

Flow: start a task from a template -> poll its status -> fetch the output.

Upstream scripts in browser-act/skills (google-maps-*, amazon-product-search,
amazon-reviews, ...) place the "timed out" error after the polling loop with no
guard, so a task that finishes is still reported as a timeout and its result is
never fetched. This client returns the result as soon as the task finishes.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests

API_BASE_URL = "https://api.browseract.com/v2/workflow"


class BrowserActError(RuntimeError):
    pass


class AuthError(BrowserActError):
    """API key missing, invalid or expired. Retrying will not help."""


@dataclass
class BrowserActClient:
    api_key: str | None = None
    base_url: str = API_BASE_URL
    poll_interval: float = 10.0
    max_poll_time: float = 600.0
    session: requests.Session | None = None
    log: Callable[[str], None] = print
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        self.api_key = self.api_key or os.getenv("BROWSERACT_API_KEY")
        if not self.api_key:
            raise AuthError(
                "Missing BROWSERACT_API_KEY. Get a key at "
                "https://www.browseract.com/reception/integrations"
            )
        self.session = self.session or requests.Session()

    # -- HTTP helpers -----------------------------------------------------
    def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        resp = self.session.request(
            method, f"{self.base_url}/{path}", headers=headers, timeout=30, **kwargs
        )
        try:
            data = resp.json()
        except ValueError:
            raise BrowserActError(f"Non-JSON response ({resp.status_code}): {resp.text[:200]}")
        if "Invalid authorization" in json.dumps(data, ensure_ascii=False):
            raise AuthError("Invalid authorization: check your BrowserAct API key")
        return data

    # -- Public API -------------------------------------------------------
    def start_task(self, template_id: str, params: dict[str, Any]) -> str:
        payload = {
            "workflow_template_id": template_id,
            "input_parameters": [
                {"name": k, "value": str(v)} for k, v in params.items() if v is not None
            ],
        }
        data = self._request("POST", "run-task-by-template", json=payload)
        if "id" not in data:
            raise BrowserActError(f"Could not start task: {data}")
        return str(data["id"])

    def wait_for_task(self, task_id: str) -> None:
        start = time.monotonic()
        while time.monotonic() - start < self.max_poll_time:
            try:
                status = self._request(
                    "GET", "get-task-status", params={"task_id": task_id}
                ).get("status")
            except (requests.RequestException, BrowserActError) as e:
                if isinstance(e, AuthError):
                    raise
                self.log(f"[poll] transient error: {e}; retrying")
                status = None
            self.log(f"[{time.strftime('%H:%M:%S')}] task {task_id}: {status}")
            if status == "finished":
                return
            if status in ("failed", "canceled"):
                raise BrowserActError(f"Task {task_id} {status}")
            self.sleep(self.poll_interval)
        raise BrowserActError(f"Task {task_id} timed out after {self.max_poll_time}s")

    def get_result(self, task_id: str) -> Any:
        info = self._request("GET", "get-task", params={"task_id": task_id})
        raw = (info.get("output") or {}).get("string")
        if raw is None:
            return info
        return parse_output(raw)

    def run(self, template_id: str, params: dict[str, Any]) -> Any:
        task_id = self.start_task(template_id, params)
        self.log(f"Task started: {task_id}")
        self.wait_for_task(task_id)
        return self.get_result(task_id)


def parse_output(raw: str) -> Any:
    """Output is usually a JSON string; fall back to the raw text."""
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def to_records(result: Any) -> list[dict]:
    """Best-effort flattening of a template result into a list of rows."""
    if isinstance(result, list):
        return [r if isinstance(r, dict) else {"value": r} for r in result]
    if isinstance(result, dict):
        for key in ("data", "items", "results", "list"):
            if isinstance(result.get(key), list):
                return to_records(result[key])
        return [result]
    return [{"value": result}]
