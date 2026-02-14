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
from urllib import error as urlerror
from urllib import request as urlrequest

try:
    import requests  # type: ignore
except Exception:
    requests = None

try:
    import psutil  # type: ignore
except Exception:
    psutil = None


OPS_ROOT = Path(__file__).resolve().parents[1]
OPS_LOG_DIR = OPS_ROOT / ".ops_logs"
PID_FILE = OPS_LOG_DIR / "backend.pid"
DEFAULT_BASE_URL = "http://127.0.0.1:8420"
DEFAULT_AI_ROOT = Path(r"C:\my-git\dpolaris_ai")
DEFAULT_PYTHON = DEFAULT_AI_ROOT / ".venv" / "Scripts" / "python.exe"


def setup_logging() -> Path:
    OPS_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = OPS_LOG_DIR / f"ops_{time.strftime('%Y%m%d')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return log_path


def _print(level: str, message: str) -> None:
    logging.info("[%s] %s", level, message)


def _http_requests(method: str, url: str, timeout: int, body: dict[str, Any] | None) -> tuple[bool, int | None, Any, str]:
    assert requests is not None
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


def _http_urllib(method: str, url: str, timeout: int, body: dict[str, Any] | None) -> tuple[bool, int | None, Any, str]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urlrequest.Request(url=url, method=method, data=data, headers=headers)
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            payload = json.loads(raw) if raw.strip() else {}
            return True, int(resp.status), payload, ""
    except urlerror.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except Exception:
            payload = {"raw": raw}
        return False, int(exc.code), payload, f"HTTP {exc.code}"
    except Exception as exc:
        return False, None, None, str(exc)


def http_json(method: str, base_url: str, path: str, timeout: int = 15, body: dict[str, Any] | None = None) -> tuple[bool, int | None, Any, str]:
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    if requests is not None:
        return _http_requests(method, url, timeout, body)
    return _http_urllib(method, url, timeout, body)


def health_once(base_url: str) -> tuple[bool, str]:
    ok, code, payload, err = http_json("GET", base_url, "/health", timeout=4)
    if not ok:
        return False, err or f"health HTTP {code}"
    if isinstance(payload, dict):
        status = str(payload.get("status", "")).strip().lower()
        if status and status not in {"healthy", "ok", "running"}:
            return False, f"unexpected health status={status}"
    return True, "healthy"


def wait_healthy(base_url: str, timeout_seconds: int) -> tuple[bool, float, str]:
    start = time.time()
    deadline = start + max(1, timeout_seconds)
    last = "unknown"
    while time.time() < deadline:
        ok, detail = health_once(base_url)
        if ok:
            return True, time.time() - start, detail
        last = detail
        time.sleep(0.8)
    return False, time.time() - start, last


def find_listening_pid(port: int) -> int | None:
    proc = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, check=False)
    for line in proc.stdout.splitlines():
        text = line.strip()
        if "LISTENING" not in text:
            continue
        parts = [p for p in text.split() if p]
        if len(parts) < 5:
            continue
        local_addr = parts[1]
        if not local_addr.endswith(f":{port}"):
            continue
        try:
            return int(parts[-1])
        except Exception:
            continue
    return None


def pid_cmdline(pid: int) -> str:
    if pid <= 0:
        return ""
    if psutil is not None:
        try:
            p = psutil.Process(pid)
            return " ".join(p.cmdline())
        except Exception:
            pass
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return (out.stdout or "").strip()
    except Exception:
        return ""


def is_expected_backend_cmdline(cmdline: str, ai_root: Path) -> bool:
    if not cmdline:
        return False
    norm = cmdline.lower().replace("/", "\\")
    ai_norm = str(ai_root).lower().replace("/", "\\")
    return ("cli.main" in norm and "server" in norm and ai_norm in norm)


def is_managed_backend(pid: int, cmdline: str, ai_root: Path) -> bool:
    if is_expected_backend_cmdline(cmdline, ai_root):
        return True
    if PID_FILE.exists():
        try:
            saved = PID_FILE.read_text(encoding="utf-8").strip()
            if saved.isdigit() and int(saved) == pid:
                return True
        except Exception:
            return False
    return False


def terminate_pid(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def extract_job_id(payload: Any) -> str | None:
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


def cmd_up(args: argparse.Namespace) -> int:
    healthy, detail = health_once(args.url)
    if healthy:
        _print("PASS", f"backend already healthy at {args.url} ({detail})")
        return 0

    ai_root = Path(args.ai_root)
    python_exe = ai_root / ".venv" / "Scripts" / "python.exe"
    if not python_exe.exists():
        _print("FAIL", f"missing backend python: {python_exe}")
        return 1

    owner = find_listening_pid(args.port)
    if owner:
        cmd = pid_cmdline(owner)
        if is_managed_backend(owner, cmd, ai_root):
            _print("WARN", f"port {args.port} in use by matching backend pid={owner}; terminating before start")
            terminate_pid(owner)
            time.sleep(1.0)
        else:
            _print("FAIL", f"port {args.port} is in use by non-backend process pid={owner}")
            if cmd:
                _print("FAIL", f"owner cmdline: {cmd}")
            return 1

    env = os.environ.copy()
    env["LLM_PROVIDER"] = args.llm_provider

    proc = subprocess.Popen(
        [str(python_exe), "-m", "cli.main", "server", "--host", args.host, "--port", str(args.port)],
        cwd=str(ai_root),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    OPS_LOG_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    _print("INFO", f"backend start requested pid={proc.pid}")

    ok, elapsed, detail = wait_healthy(args.url, args.timeout)
    if ok:
        _print("PASS", f"backend healthy in {elapsed:.1f}s")
        return 0
    _print("FAIL", f"backend did not become healthy in {elapsed:.1f}s ({detail})")
    return 1


def cmd_down(args: argparse.Namespace) -> int:
    ai_root = Path(args.ai_root)
    owner = find_listening_pid(args.port)
    if owner:
        cmd = pid_cmdline(owner)
        if is_managed_backend(owner, cmd, ai_root):
            terminate_pid(owner)
            _print("PASS", f"stopped backend pid={owner}")
            try:
                PID_FILE.unlink(missing_ok=True)
            except Exception:
                pass
            return 0
        _print("FAIL", f"refusing to stop pid={owner} on port {args.port}; command line does not match backend")
        if cmd:
            _print("FAIL", f"owner cmdline: {cmd}")
        return 1

    if PID_FILE.exists():
        text = PID_FILE.read_text(encoding="utf-8").strip()
        if text.isdigit():
            pid = int(text)
            cmd = pid_cmdline(pid)
            if is_expected_backend_cmdline(cmd, ai_root):
                terminate_pid(pid)
                _print("PASS", f"stopped backend pid={pid} from pid file")
                try:
                    PID_FILE.unlink(missing_ok=True)
                except Exception:
                    pass
                return 0
    _print("WARN", "backend not running")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    owner = find_listening_pid(args.port)
    health, health_detail = health_once(args.url)
    print("Status Summary")
    print(f"  URL: {args.url}")
    print(f"  Port: {args.port}")
    print(f"  Health: {'PASS' if health else 'FAIL'} ({health_detail})")
    if not owner:
        print("  Port Owner PID: none")
        print("  Port Owner Cmdline: n/a")
        print("  Managed/Safe: no")
        return 1 if not health else 0
    cmd = pid_cmdline(owner)
    managed = is_managed_backend(owner, cmd, Path(args.ai_root))
    print(f"  Port Owner PID: {owner}")
    print(f"  Port Owner Cmdline: {cmd or 'n/a'}")
    print(f"  Managed/Safe: {'yes' if managed else 'no'}")
    if health:
        _print("PASS", "status check complete")
        return 0
    _print("WARN", "service not healthy")
    return 1


def cmd_smoke(args: argparse.Namespace) -> int:
    ok, elapsed, detail = wait_healthy(args.url, args.timeout)
    if not ok:
        _print("FAIL", f"health check failed after {elapsed:.1f}s ({detail})")
        return 1
    _print("PASS", "GET /health")

    ok, _, status_payload, err = http_json("GET", args.url, "/api/status", timeout=15)
    if ok:
        _print("PASS", "GET /api/status")
    else:
        _print("FAIL", f"GET /api/status failed: {err}")
        return 1

    ok, _, _, err = http_json("GET", args.url, "/api/universe/list", timeout=15)
    if ok:
        _print("PASS", "GET /api/universe/list")
    else:
        _print("WARN", f"GET /api/universe/list failed: {err}")

    if args.no_dl_job:
        _print("WARN", "deep-learning job smoke skipped by flag")
        return 0

    body = {"symbol": args.symbol, "model_type": args.model, "epochs": args.epochs}
    ok, _, enqueue_payload, err = http_json("POST", args.url, "/api/jobs/deep-learning/train", timeout=30, body=body)
    if not ok:
        _print("FAIL", f"POST /api/jobs/deep-learning/train failed: {err}")
        return 1

    job_id = extract_job_id(enqueue_payload)
    if not job_id:
        _print("FAIL", "job enqueue returned no job id")
        return 1
    _print("PASS", f"deep-learning job enqueued id={job_id}")

    deadline = time.time() + max(5, args.job_timeout)
    final = "timeout"
    final_error = ""
    model_path = ""
    while time.time() < deadline:
        ok, _, payload, err = http_json("GET", args.url, f"/api/jobs/{job_id}", timeout=15)
        if not ok:
            final_error = err
            time.sleep(2)
            continue
        if not isinstance(payload, dict):
            final_error = "unexpected job payload"
            time.sleep(2)
            continue
        state = str(payload.get("status", "")).strip().lower()
        model_path = str(payload.get("model_path") or payload.get("artifact_path") or "")
        if state in {"completed", "success"}:
            final = "completed"
            break
        if state == "failed":
            final = "failed"
            final_error = str(payload.get("error") or payload.get("detail") or payload.get("message") or "")
            break
        time.sleep(2)

    print("Smoke Summary")
    print(json.dumps({
        "status": final,
        "job_id": job_id,
        "model_path": model_path or None,
        "error": final_error or None,
    }, indent=2))

    if final == "completed":
        _print("PASS", "deep-learning smoke passed")
        return 0
    _print("FAIL", "deep-learning smoke failed")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ops.main", description="dPolaris ops runner")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--ai-root", default=str(DEFAULT_AI_ROOT))
    common.add_argument("--url", default=DEFAULT_BASE_URL)
    common.add_argument("--host", default="127.0.0.1")
    common.add_argument("--port", type=int, default=8420)
    common.add_argument("--timeout", type=int, default=30)

    p_status = sub.add_parser("status", parents=[common], help="Show health + port owner and managed safety")
    p_status.set_defaults(func=cmd_status)

    p_up = sub.add_parser("up", parents=[common], help="Start backend if unhealthy")
    p_up.add_argument("--llm-provider", default="none")
    p_up.set_defaults(func=cmd_up)

    p_down = sub.add_parser("down", parents=[common], help="Stop managed backend safely")
    p_down.set_defaults(func=cmd_down)

    p_smoke = sub.add_parser("smoke", help="Run API + deep-learning smoke checks")
    p_smoke.add_argument("--url", default=DEFAULT_BASE_URL)
    p_smoke.add_argument("--timeout", type=int, default=30)
    p_smoke.add_argument("--symbol", default="AAPL")
    p_smoke.add_argument("--model", default="lstm")
    p_smoke.add_argument("--epochs", type=int, default=1)
    p_smoke.add_argument("--job-timeout", type=int, default=600)
    p_smoke.add_argument("--no-dl-job", action="store_true")
    p_smoke.set_defaults(func=cmd_smoke)

    return parser


def main(argv: list[str] | None = None) -> int:
    log_path = setup_logging()
    _print("INFO", f"log file: {log_path}")
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
