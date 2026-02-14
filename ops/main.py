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
BACKEND_STDOUT_LOG = OPS_LOG_DIR / "backend_stdout.log"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8420
DEFAULT_BASE_URL = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"
DEFAULT_AI_ROOT = Path(r"C:\my-git\dpolaris_ai")
MANAGED_PY_EXE = r"dpolaris_ai\.venv\scripts\python.exe"


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


def find_listening_pids(port: int) -> list[int]:
    pids: set[int] = set()
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
            pids.add(int(parts[-1]))
        except Exception:
            continue
    return sorted(pids)


def find_listening_pid(port: int) -> int | None:
    pids = find_listening_pids(port)
    return pids[0] if pids else None


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


def managed_cmdline(cmdline: str) -> bool:
    norm = (cmdline or "").lower().replace("/", "\\")
    return MANAGED_PY_EXE in norm


def terminate_pid(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def takeover_port(port: int, rounds: int = 3, wait_seconds: int = 10) -> bool:
    for round_idx in range(1, rounds + 1):
        pids = find_listening_pids(port)
        if not pids:
            _print("PASS", f"port {port} is free")
            return True
        _print("WARN", f"port {port} takeover round {round_idx}: killing pids {pids}")
        for pid in pids:
            try:
                terminate_pid(pid)
            except Exception as exc:
                _print("WARN", f"taskkill failed for pid {pid}: {exc}")
        deadline = time.time() + max(1, wait_seconds)
        while time.time() < deadline:
            if not find_listening_pids(port):
                _print("PASS", f"port {port} released after round {round_idx}")
                return True
            time.sleep(0.5)
    remaining = find_listening_pids(port)
    if remaining:
        _print("FAIL", f"port {port} still occupied after takeover attempts: {remaining}")
        return False
    return True


def kill_recorded_pids() -> None:
    if not PID_FILE.exists():
        return
    try:
        text = PID_FILE.read_text(encoding="utf-8").strip()
    except Exception as exc:
        _print("WARN", f"failed reading pid file: {exc}")
        return
    if text.isdigit():
        pid = int(text)
        _print("WARN", f"killing recorded pid {pid}")
        try:
            terminate_pid(pid)
        except Exception as exc:
            _print("WARN", f"failed killing recorded pid {pid}: {exc}")
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception as exc:
        _print("WARN", f"failed removing pid file: {exc}")


def tail_lines(path: Path, line_count: int) -> list[str]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-max(1, line_count):]
    except Exception:
        return []


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


def extract_job_logs(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    logs: list[str] = []
    for key in ("logs", "log_lines", "history", "messages"):
        value = payload.get(key)
        if isinstance(value, list):
            for item in value:
                line = str(item).strip()
                if line:
                    logs.append(line)
    text_value = payload.get("log") or payload.get("output")
    if isinstance(text_value, str) and text_value.strip():
        logs.extend([line for line in text_value.splitlines() if line.strip()])
    return logs


def cmd_up(args: argparse.Namespace) -> int:
    _print("INFO", f"up: hard takeover on port {args.port} before start")
    takeover_port(args.port, rounds=3, wait_seconds=10)

    ai_root = Path(args.ai_root)
    python_exe = ai_root / ".venv" / "Scripts" / "python.exe"
    if not python_exe.exists():
        _print("FAIL", f"missing backend python: {python_exe}")
        return 1

    env = os.environ.copy()
    env["LLM_PROVIDER"] = "none"
    OPS_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_handle = open(BACKEND_STDOUT_LOG, "a", encoding="utf-8")

    proc = subprocess.Popen(
        [str(python_exe), "-m", "cli.main", "server", "--host", args.host, "--port", str(args.port)],
        cwd=str(ai_root),
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        log_handle.close()
    except Exception:
        pass

    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    _print("INFO", f"started backend pid={proc.pid}")
    ok, elapsed, detail = wait_healthy(args.url, args.timeout)
    if ok:
        _print("PASS", f"backend healthy in {elapsed:.1f}s")
        return 0
    _print("FAIL", f"backend did not become healthy in {elapsed:.1f}s ({detail})")
    _print("WARN", "latest backend log tail:")
    for line in tail_lines(BACKEND_STDOUT_LOG, 30):
        print(f"  {line}")
    return 1


def cmd_down(args: argparse.Namespace) -> int:
    _print("INFO", f"down: hard takeover kill on port {args.port}")
    takeover_port(args.port, rounds=3, wait_seconds=10)
    kill_recorded_pids()
    if find_listening_pids(args.port):
        _print("FAIL", f"down completed but port {args.port} still occupied")
        return 1
    _print("PASS", "down completed")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    owners = find_listening_pids(args.port)
    health, health_detail = health_once(args.url)
    print("Status Summary")
    print(f"  health: {'PASS' if health else 'FAIL'} ({health_detail})")
    print(f"  owner_pids: {owners if owners else 'none'}")
    if not owners:
        print("  owner_cmdline: n/a")
        print("  managed_owner: no")
        return 0 if health else 1
    for pid in owners:
        cmd = pid_cmdline(pid)
        print(f"  pid={pid} cmdline={cmd or 'n/a'}")
        print(f"  pid={pid} managed_owner={'yes' if managed_cmdline(cmd) else 'no'}")
    return 0 if health else 1


def cmd_smoke_fast(args: argparse.Namespace) -> int:
    ok, elapsed, detail = wait_healthy(args.url, args.timeout)
    if not ok:
        _print("FAIL", f"GET /health failed after {elapsed:.1f}s ({detail})")
        return 1
    _print("PASS", "GET /health")

    ok, _, _, err = http_json("GET", args.url, "/api/status", timeout=15)
    if not ok:
        _print("FAIL", f"GET /api/status failed ({err})")
        return 1
    _print("PASS", "GET /api/status")

    ok, code, _, err = http_json("GET", args.url, "/api/universe/list", timeout=20)
    if ok:
        _print("PASS", "GET /api/universe/list")
        return 0
    if code == 404:
        _print("WARN", "GET /api/universe/list returned 404")
        return 0
    _print("FAIL", f"GET /api/universe/list failed ({err})")
    return 1


def cmd_smoke_dl(args: argparse.Namespace) -> int:
    fast_rc = cmd_smoke_fast(args)
    if fast_rc != 0:
        return fast_rc

    body = {"symbol": args.symbol, "model_type": args.model, "epochs": args.epochs}
    ok, _, enqueue_payload, err = http_json("POST", args.url, "/api/jobs/deep-learning/train", timeout=30, body=body)
    if not ok:
        _print("FAIL", f"POST /api/jobs/deep-learning/train failed ({err})")
        return 1

    job_id = extract_job_id(enqueue_payload)
    if not job_id:
        _print("FAIL", "enqueue response missing job id")
        return 1
    _print("PASS", f"deep-learning job enqueued id={job_id}")

    deadline = time.time() + max(10, args.job_timeout)
    final_status = "timeout"
    final_error = ""
    final_logs: list[str] = []
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
        final_logs = extract_job_logs(payload)
        state = str(payload.get("status", "")).strip().lower()
        if state in {"completed", "success"}:
            final_status = "completed"
            break
        if state in {"failed", "error"}:
            final_status = "failed"
            final_error = str(payload.get("error") or payload.get("detail") or payload.get("message") or "")
            break
        time.sleep(2)

    print("DL Smoke Summary")
    print(json.dumps({"job_id": job_id, "status": final_status, "error": final_error or None}, indent=2))
    print(f"Last {args.tail_logs} log lines")
    shown = False
    for line in final_logs[-max(1, args.tail_logs):]:
        shown = True
        print(f"  {line}")
    if not shown:
        repo_lines = tail_lines(BACKEND_STDOUT_LOG, args.tail_logs)
        for line in repo_lines:
            shown = True
            print(f"  {line}")
    if not shown:
        print("  (no logs available)")

    if final_status == "completed":
        _print("PASS", "deep-learning smoke passed")
        return 0
    _print("FAIL", "deep-learning smoke failed")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ops.main", description="dPolaris ops runner")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--url", default=DEFAULT_BASE_URL)
    common.add_argument("--host", default=DEFAULT_HOST)
    common.add_argument("--port", type=int, default=DEFAULT_PORT)
    common.add_argument("--timeout", type=int, default=30)
    common.add_argument("--ai-root", default=str(DEFAULT_AI_ROOT))

    p_status = sub.add_parser("status", parents=[common], help="Show health + owner details")
    p_status.set_defaults(func=cmd_status)

    p_up = sub.add_parser("up", parents=[common], help="Start backend if needed")
    p_up.set_defaults(func=cmd_up)

    p_down = sub.add_parser("down", parents=[common], help="Stop managed backend")
    p_down.set_defaults(func=cmd_down)

    p_smoke_fast = sub.add_parser("smoke-fast", parents=[common], help="Run fast endpoint smoke")
    p_smoke_fast.set_defaults(func=cmd_smoke_fast)

    p_smoke_dl = sub.add_parser("smoke-dl", parents=[common], help="Run deep-learning smoke")
    p_smoke_dl.add_argument("--symbol", default="AAPL")
    p_smoke_dl.add_argument("--model", default="lstm")
    p_smoke_dl.add_argument("--epochs", type=int, default=1)
    p_smoke_dl.add_argument("--job-timeout", type=int, default=600)
    p_smoke_dl.add_argument("--tail-logs", type=int, default=20)
    p_smoke_dl.set_defaults(func=cmd_smoke_dl)

    p_smoke_alias = sub.add_parser("smoke", parents=[common], help="Alias for smoke-dl")
    p_smoke_alias.add_argument("--symbol", default="AAPL")
    p_smoke_alias.add_argument("--model", default="lstm")
    p_smoke_alias.add_argument("--epochs", type=int, default=1)
    p_smoke_alias.add_argument("--job-timeout", type=int, default=600)
    p_smoke_alias.add_argument("--tail-logs", type=int, default=20)
    p_smoke_alias.set_defaults(func=cmd_smoke_dl)

    return parser


def main(argv: list[str] | None = None) -> int:
    log_path = setup_logging()
    _print("INFO", f"log file: {log_path}")
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
