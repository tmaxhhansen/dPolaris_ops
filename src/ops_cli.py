from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

try:
    import requests
except ModuleNotFoundError:
    requests = None


REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = REPO_ROOT / "logs"
RUN_DIR = REPO_ROOT / "run"
PID_FILE = RUN_DIR / "backend.pid"
DEFAULT_URL = "http://127.0.0.1:8420"


def ensure_requests() -> None:
    if requests is None:
        print("requests is required. Install dependencies first:")
        print("  .\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt")
        raise SystemExit(2)


def setup_logging() -> Path:
    log_path = LOG_DIR / f"ops_cli_{time.strftime('%Y%m%d')}.log"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handlers.insert(0, logging.FileHandler(log_path, encoding="utf-8"))
    except Exception:
        # Fall back to console-only if file system write is restricted.
        pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )
    return log_path


def make_url(base: str, path: str) -> str:
    base_norm = base.rstrip("/") + "/"
    return urljoin(base_norm, path.lstrip("/"))


def request_json(method: str, url: str, timeout: int = 10, body: dict[str, Any] | None = None) -> tuple[bool, int | None, Any, str]:
    ensure_requests()
    try:
        resp = requests.request(method=method, url=url, timeout=timeout, json=body)
    except Exception as exc:
        return False, None, None, str(exc)

    status = int(resp.status_code)
    try:
        payload = resp.json() if resp.text else {}
    except Exception:
        payload = {"raw": resp.text}

    if status >= 400:
        return False, status, payload, f"HTTP {status}"
    return True, status, payload, ""


def health_once(base_url: str, timeout: int = 4) -> tuple[bool, str]:
    ok, status, payload, err = request_json("GET", make_url(base_url, "/health"), timeout=timeout)
    if not ok:
        return False, err or f"health failed ({status})"
    if isinstance(payload, dict):
        state = str(payload.get("status", "")).strip().lower()
        if state and state not in {"healthy", "ok", "running"}:
            return False, f"unexpected health status={state}"
    return True, "healthy"


def wait_healthy(base_url: str, timeout_seconds: int) -> tuple[bool, float, str]:
    start = time.time()
    last = ""
    deadline = start + max(1, timeout_seconds)
    while time.time() < deadline:
        ok, msg = health_once(base_url)
        if ok:
            return True, time.time() - start, msg
        last = msg
        time.sleep(0.8)
    return False, time.time() - start, last or "timeout"


def cmd_health(args: argparse.Namespace) -> int:
    ok, msg = health_once(args.url)
    if ok:
        print("PASS health", msg)
        return 0
    print("FAIL health", msg)
    return 2


def cmd_wait_healthy(args: argparse.Namespace) -> int:
    ok, elapsed, detail = wait_healthy(args.url, args.timeout)
    if ok:
        print(f"PASS wait-healthy in {elapsed:.1f}s")
        return 0
    print(f"FAIL wait-healthy after {elapsed:.1f}s: {detail}")
    return 2


def _extract_job_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("id", "job_id", "jobId"):
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def cmd_smoke(args: argparse.Namespace) -> int:
    summary: dict[str, Any] = {
        "health": "FAIL",
        "enqueue": "FAIL",
        "job": "FAIL",
        "job_id": None,
        "job_status": None,
        "job_error": None,
    }

    ok, elapsed, detail = wait_healthy(args.url, args.timeout)
    if not ok:
        print(f"FAIL health not ready after {elapsed:.1f}s: {detail}")
        return 2
    summary["health"] = "PASS"

    body = {"symbol": args.symbol, "model_type": args.model, "epochs": args.epochs}
    ok, _, payload, err = request_json("POST", make_url(args.url, "/api/jobs/deep-learning/train"), timeout=30, body=body)
    if not ok:
        print(f"FAIL enqueue deep-learning job: {err}")
        return 2

    job_id = _extract_job_id(payload)
    if not job_id:
        print("FAIL enqueue deep-learning job: missing job id")
        print(json.dumps(payload, indent=2))
        return 2

    summary["enqueue"] = "PASS"
    summary["job_id"] = job_id

    deadline = time.time() + max(5, args.job_timeout)
    final_status = ""
    final_error = ""
    while time.time() < deadline:
        ok, _, p, err = request_json("GET", make_url(args.url, f"/api/jobs/{job_id}"), timeout=15)
        if not ok:
            final_error = err
            time.sleep(2)
            continue
        if not isinstance(p, dict):
            final_error = "job payload is not an object"
            time.sleep(2)
            continue

        state = str(p.get("status", "")).strip().lower()
        if state in {"completed", "success"}:
            final_status = state
            summary["job"] = "PASS"
            break
        if state == "failed":
            final_status = state
            final_error = str(p.get("error") or p.get("detail") or p.get("message") or "")
            break
        time.sleep(2)

    summary["job_status"] = final_status or "timeout"
    summary["job_error"] = final_error

    print("Smoke Summary")
    print(json.dumps(summary, indent=2))

    if summary["job"] == "PASS":
        return 0
    print("FAIL deep-learning job did not complete successfully")
    return 2


def _kill_pid(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
    else:
        os.kill(pid, signal.SIGTERM)


def cmd_start_backend(args: argparse.Namespace) -> int:
    ai_root = Path(args.ai_root)
    py = ai_root / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        print(f"FAIL missing backend python: {py}")
        return 2

    env = os.environ.copy()
    env["LLM_PROVIDER"] = "none"

    proc = subprocess.Popen(
        [str(py), "-m", "cli.main", "server", "--host", args.host, "--port", str(args.port)],
        cwd=str(ai_root),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    ok, elapsed, detail = wait_healthy(args.url, args.timeout)
    if ok:
        print(f"PASS backend started pid={proc.pid} healthy in {elapsed:.1f}s")
        return 0
    print(f"FAIL backend start unhealthy after {elapsed:.1f}s: {detail}")
    return 2


def cmd_stop_backend(args: argparse.Namespace) -> int:
    if not PID_FILE.exists():
        print(f"WARN no local pid file: {PID_FILE}")
        return 0
    text = PID_FILE.read_text(encoding="utf-8").strip()
    if not text.isdigit():
        print("FAIL invalid pid file")
        return 2
    pid = int(text)
    _kill_pid(pid)
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass
    print(f"PASS stop requested for pid={pid}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ops", description="dPolaris ops CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_health = sub.add_parser("health", help="Check /health")
    p_health.add_argument("--url", default=DEFAULT_URL)
    p_health.set_defaults(func=cmd_health)

    p_wait = sub.add_parser("wait-healthy", help="Wait for /health")
    p_wait.add_argument("--url", default=DEFAULT_URL)
    p_wait.add_argument("--timeout", type=int, default=30)
    p_wait.set_defaults(func=cmd_wait_healthy)

    p_smoke = sub.add_parser("smoke", help="Run deep-learning smoke test")
    p_smoke.add_argument("--url", default=DEFAULT_URL)
    p_smoke.add_argument("--symbol", default="AAPL")
    p_smoke.add_argument("--model", default="lstm")
    p_smoke.add_argument("--epochs", type=int, default=1)
    p_smoke.add_argument("--timeout", type=int, default=30, help="health wait timeout")
    p_smoke.add_argument("--job-timeout", type=int, default=300)
    p_smoke.set_defaults(func=cmd_smoke)

    p_start = sub.add_parser("start-backend", help="Start backend with Java-control-center conventions")
    p_start.add_argument("--ai-root", default="C:\\my-git\\dpolaris_ai")
    p_start.add_argument("--url", default=DEFAULT_URL)
    p_start.add_argument("--host", default="127.0.0.1")
    p_start.add_argument("--port", type=int, default=8420)
    p_start.add_argument("--timeout", type=int, default=30)
    p_start.set_defaults(func=cmd_start_backend)

    p_stop = sub.add_parser("stop-backend", help="Stop backend using local pid file")
    p_stop.set_defaults(func=cmd_stop_backend)

    return parser


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.info("command=%s", args.command)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
