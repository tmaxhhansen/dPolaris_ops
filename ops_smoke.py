from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

BASE_URL = "http://127.0.0.1:8420"
HEALTH_TIMEOUT_SECONDS = 30
JOB_TIMEOUT_SECONDS = 600
POLL_SECONDS = 2


try:
    import requests  # type: ignore
except Exception:
    requests = None


@dataclass
class HttpResult:
    ok: bool
    status: int | None
    payload: Any
    error: str


def _http_requests(method: str, url: str, timeout: int, body: dict[str, Any] | None) -> HttpResult:
    assert requests is not None
    try:
        resp = requests.request(method=method, url=url, timeout=timeout, json=body)
    except Exception as exc:
        return HttpResult(False, None, None, str(exc))
    try:
        payload = resp.json() if resp.text else {}
    except Exception:
        payload = {"raw": resp.text}
    ok = 200 <= int(resp.status_code) < 300
    return HttpResult(ok, int(resp.status_code), payload, "" if ok else f"HTTP {resp.status_code}")


def _http_urllib(method: str, url: str, timeout: int, body: dict[str, Any] | None) -> HttpResult:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urlrequest.Request(url=url, data=data, method=method, headers=headers)
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            payload = json.loads(raw) if raw.strip() else {}
            return HttpResult(True, int(resp.status), payload, "")
    except urlerror.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except Exception:
            payload = {"raw": raw}
        return HttpResult(False, int(exc.code), payload, f"HTTP {exc.code}")
    except Exception as exc:
        return HttpResult(False, None, None, str(exc))


def http_json(method: str, path: str, timeout: int = 15, body: dict[str, Any] | None = None) -> HttpResult:
    url = BASE_URL.rstrip("/") + "/" + path.lstrip("/")
    if requests is not None:
        return _http_requests(method, url, timeout, body)
    return _http_urllib(method, url, timeout, body)


def wait_healthy(timeout_seconds: int) -> tuple[bool, str]:
    deadline = time.time() + max(1, timeout_seconds)
    last_error = "not started"
    while time.time() < deadline:
        r = http_json("GET", "/health", timeout=4)
        if r.ok:
            status = ""
            if isinstance(r.payload, dict):
                status = str(r.payload.get("status", "")).strip().lower()
            if not status or status in {"healthy", "ok", "running"}:
                return True, "healthy"
            last_error = f"unexpected status={status}"
        else:
            last_error = r.error or "health request failed"
        time.sleep(0.8)
    return False, last_error


def extract_job_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("id", "job_id", "jobId"):
        v = payload.get(key)
        if v is None:
            continue
        text = str(v).strip()
        if text:
            return text
    return None


def main() -> int:
    ok, detail = wait_healthy(HEALTH_TIMEOUT_SECONDS)
    if not ok:
        print(f"FAIL: backend is not healthy after {HEALTH_TIMEOUT_SECONDS}s ({detail})")
        return 1

    enqueue = http_json(
        "POST",
        "/api/jobs/deep-learning/train",
        timeout=30,
        body={"symbol": "AAPL", "model_type": "lstm", "epochs": 1},
    )
    if not enqueue.ok:
        print(f"FAIL: could not enqueue deep-learning job ({enqueue.error})")
        return 1

    job_id = extract_job_id(enqueue.payload)
    if not job_id:
        print("FAIL: enqueue response missing job id")
        print(json.dumps(enqueue.payload, indent=2))
        return 1

    deadline = time.time() + JOB_TIMEOUT_SECONDS
    final_status = "timeout"
    model_path = ""
    error_text = ""

    while time.time() < deadline:
        poll = http_json("GET", f"/api/jobs/{job_id}", timeout=15)
        if not poll.ok:
            error_text = poll.error or "poll failed"
            time.sleep(POLL_SECONDS)
            continue
        if not isinstance(poll.payload, dict):
            error_text = "invalid job payload"
            time.sleep(POLL_SECONDS)
            continue

        state = str(poll.payload.get("status", "")).strip().lower()
        model_path = str(
            poll.payload.get("model_path")
            or poll.payload.get("artifact_path")
            or poll.payload.get("output_path")
            or ""
        )

        if state in {"completed", "success"}:
            final_status = "completed"
            break
        if state == "failed":
            final_status = "failed"
            error_text = str(
                poll.payload.get("error")
                or poll.payload.get("detail")
                or poll.payload.get("message")
                or ""
            )
            break
        time.sleep(POLL_SECONDS)

    print("Smoke Summary")
    print(f"- job_id: {job_id}")
    print(f"- status: {final_status}")
    print(f"- model_path: {model_path or '(none)'}")
    print(f"- error: {error_text or '(none)'}")

    return 0 if final_status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
