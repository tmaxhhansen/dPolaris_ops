from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
import time
from typing import Any

import requests


@dataclass
class CheckResult:
    name: str
    endpoint: str
    ok: bool
    status_code: int | None = None
    details: str = ""
    payload: Any = None
    duration_seconds: float | None = None


@dataclass
class DoctorContext:
    base_url: str
    symbol: str
    model_type: str
    epochs: int
    timeout_seconds: int
    checks: list[CheckResult] = field(default_factory=list)
    classifications: list[str] = field(default_factory=list)
    job_id: str | None = None
    job_error: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record(
    ctx: DoctorContext,
    name: str,
    endpoint: str,
    ok: bool,
    status_code: int | None,
    details: str,
    payload: Any,
    started: float,
) -> None:
    ctx.checks.append(
        CheckResult(
            name=name,
            endpoint=endpoint,
            ok=ok,
            status_code=status_code,
            details=details,
            payload=payload,
            duration_seconds=round(time.time() - started, 3),
        )
    )


def _safe_json(response: requests.Response) -> tuple[bool, Any]:
    try:
        return True, response.json()
    except ValueError:
        return False, response.text


def _request_json(
    session: requests.Session,
    method: str,
    url: str,
    timeout: int,
    json_body: dict[str, Any] | None = None,
) -> tuple[bool, int | None, Any, str]:
    try:
        resp = session.request(method=method, url=url, timeout=timeout, json=json_body)
    except requests.RequestException as exc:
        return False, None, None, str(exc)

    ok_json, payload = _safe_json(resp)
    if not ok_json:
        return False, resp.status_code, payload, "response was not valid JSON"
    if resp.status_code >= 400:
        return False, resp.status_code, payload, f"HTTP {resp.status_code}"
    return True, resp.status_code, payload, ""


def _extract_job_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    candidates = ["job_id", "id", "jobId"]
    for key in candidates:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _extract_job_status(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    status = payload.get("status")
    if status is None:
        return ""
    return str(status).strip().lower()


def _extract_job_error(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ["error", "detail", "message", "reason"]:
        value = payload.get(key)
        if value:
            return str(value)
    return ""


def _contains_torch_missing(error_text: str) -> bool:
    return bool(re.search(r"no module named ['\"]?torch['\"]?", error_text, re.IGNORECASE))


def run_checks(
    base_url: str,
    symbol: str,
    model_type: str,
    epochs: int,
    timeout_seconds: int,
    poll_seconds: int = 2,
) -> dict[str, Any]:
    base = base_url.rstrip("/")
    ctx = DoctorContext(
        base_url=base,
        symbol=symbol,
        model_type=model_type,
        epochs=epochs,
        timeout_seconds=timeout_seconds,
    )

    session = requests.Session()

    # A) GET /health
    started = time.time()
    ok, code, payload, err = _request_json(session, "GET", f"{base}/health", timeout=15)
    health_ok = ok and isinstance(payload, dict)
    _record(
        ctx,
        name="A_HEALTH",
        endpoint="GET /health",
        ok=health_ok,
        status_code=code,
        details="healthy" if health_ok else (err or "health check failed"),
        payload=payload,
        started=started,
    )
    if not health_ok:
        ctx.classifications.append("BACKEND_DOWN")
        return {
            "started_at": _now_iso(),
            "base_url": ctx.base_url,
            "inputs": {
                "symbol": ctx.symbol,
                "model_type": ctx.model_type,
                "epochs": ctx.epochs,
                "timeout": ctx.timeout_seconds,
            },
            "checks": [c.__dict__ for c in ctx.checks],
            "classifications": ctx.classifications,
            "job": {"job_id": None, "final_status": "not_started", "error": None},
            "diagnostics": ctx.diagnostics,
            "summary": {"ok": False, "reason": "backend down"},
        }

    # B) GET /api/status
    started = time.time()
    ok, code, payload, err = _request_json(session, "GET", f"{base}/api/status", timeout=20)
    status_ok = ok and isinstance(payload, dict)
    _record(
        ctx,
        name="B_API_STATUS",
        endpoint="GET /api/status",
        ok=status_ok,
        status_code=code,
        details="ok" if status_ok else (err or "unexpected response"),
        payload=payload,
        started=started,
    )
    if not status_ok:
        ctx.classifications.append("API_CONTRACT_INCONSISTENT")

    # C) GET /api/deep-learning/status
    started = time.time()
    ok, code, payload, err = _request_json(session, "GET", f"{base}/api/deep-learning/status", timeout=20)
    dl_status_ok = ok and isinstance(payload, dict)
    _record(
        ctx,
        name="C_DL_STATUS",
        endpoint="GET /api/deep-learning/status",
        ok=dl_status_ok,
        status_code=code,
        details="ok" if dl_status_ok else (err or "unexpected response"),
        payload=payload,
        started=started,
    )
    if not dl_status_ok:
        ctx.classifications.append("API_CONTRACT_INCONSISTENT")

    # D) POST /api/jobs/deep-learning/train
    body = {"symbol": symbol, "model_type": model_type, "epochs": epochs}
    started = time.time()
    ok, code, payload, err = _request_json(
        session,
        "POST",
        f"{base}/api/jobs/deep-learning/train",
        timeout=30,
        json_body=body,
    )
    job_start_ok = ok and isinstance(payload, dict)
    job_id = _extract_job_id(payload if isinstance(payload, dict) else None)
    ctx.job_id = job_id
    _record(
        ctx,
        name="D_START_DL_JOB",
        endpoint="POST /api/jobs/deep-learning/train",
        ok=job_start_ok and bool(job_id),
        status_code=code,
        details="job started" if (job_start_ok and job_id) else (err or "missing job id"),
        payload=payload,
        started=started,
    )
    if not job_start_ok or not job_id:
        ctx.classifications.append("API_CONTRACT_INCONSISTENT")
        return {
            "started_at": _now_iso(),
            "base_url": ctx.base_url,
            "inputs": {
                "symbol": ctx.symbol,
                "model_type": ctx.model_type,
                "epochs": ctx.epochs,
                "timeout": ctx.timeout_seconds,
            },
            "checks": [c.__dict__ for c in ctx.checks],
            "classifications": sorted(set(ctx.classifications)),
            "job": {"job_id": job_id, "final_status": "start_failed", "error": err},
            "diagnostics": ctx.diagnostics,
            "summary": {"ok": False, "reason": "job start failed"},
        }

    # Poll GET /api/jobs/{job_id}
    poll_deadline = time.time() + timeout_seconds
    final_status = "running"
    poll_count = 0
    last_payload: Any = None
    while time.time() < poll_deadline:
        poll_count += 1
        started = time.time()
        ok, code, payload, err = _request_json(
            session,
            "GET",
            f"{base}/api/jobs/{job_id}",
            timeout=20,
        )
        last_payload = payload
        status_text = _extract_job_status(payload)
        check_ok = ok and isinstance(payload, dict)
        _record(
            ctx,
            name="D_POLL_DL_JOB",
            endpoint=f"GET /api/jobs/{job_id}",
            ok=check_ok,
            status_code=code,
            details=status_text or (err or "poll error"),
            payload=payload,
            started=started,
        )

        if not check_ok:
            ctx.classifications.append("API_CONTRACT_INCONSISTENT")
            final_status = "failed"
            ctx.job_error = err or "job poll failed"
            break

        if status_text == "success":
            final_status = "success"
            break
        if status_text == "failed":
            final_status = "failed"
            ctx.job_error = _extract_job_error(payload)
            break

        time.sleep(max(1, poll_seconds))

    if final_status not in {"success", "failed"}:
        final_status = "timeout"
        ctx.classifications.append("DL_JOB_TIMEOUT")

    if final_status == "failed" and _contains_torch_missing(ctx.job_error or ""):
        ctx.classifications.append("MISSING_TORCH")

    report = {
        "started_at": _now_iso(),
        "base_url": ctx.base_url,
        "inputs": {
            "symbol": ctx.symbol,
            "model_type": ctx.model_type,
            "epochs": ctx.epochs,
            "timeout": ctx.timeout_seconds,
        },
        "checks": [c.__dict__ for c in ctx.checks],
        "classifications": sorted(set(ctx.classifications)),
        "job": {
            "job_id": job_id,
            "final_status": final_status,
            "error": ctx.job_error,
            "poll_count": poll_count,
            "last_payload": last_payload,
        },
        "diagnostics": ctx.diagnostics,
        "summary": {
            "ok": final_status == "success" and not ctx.classifications,
            "reason": "success" if final_status == "success" and not ctx.classifications else "issues detected",
        },
    }
    return report
