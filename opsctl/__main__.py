from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

try:
    import requests  # type: ignore
except Exception:
    requests = None

try:
    import psutil  # type: ignore
except Exception:
    psutil = None


HOST = "127.0.0.1"
PORT = 8420
BASE_URL = f"http://{HOST}:{PORT}"
AI_ROOT = Path(r"C:\my-git\dpolaris_ai")
AI_PYTHON = AI_ROOT / ".venv" / "Scripts" / "python.exe"
OPS_ROOT = Path(__file__).resolve().parents[1]
OPS_LOG_DIR = OPS_ROOT / ".ops_logs"
PID_FILE = OPS_LOG_DIR / "opsctl_backend.pid"
BACKEND_LOG = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "dpolaris_data" / "logs" / "ops_backend.log"
LOG_FILE: Any | None = None
LOG_PATH: Path | None = None


def echo(level: str, message: str) -> None:
    line = f"[{level}] {message}"
    print(line)
    if LOG_FILE is not None:
        ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        LOG_FILE.write(f"{ts} {line}\n")
        LOG_FILE.flush()


def setup_logging() -> Path:
    global LOG_FILE
    global LOG_PATH
    OPS_LOG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_PATH = OPS_LOG_DIR / f"ops_{dt.datetime.now().strftime('%Y%m%d')}.log"
    LOG_FILE = open(LOG_PATH, "a", encoding="utf-8")
    return LOG_PATH


def call_json(method: str, path: str, timeout: int = 10, body: dict[str, Any] | None = None) -> tuple[bool, int | None, Any, str]:
    url = urljoin(BASE_URL.rstrip("/") + "/", path.lstrip("/"))
    if requests is not None:
        try:
            resp = requests.request(method=method, url=url, timeout=timeout, json=body)
        except Exception as exc:
            return False, None, None, str(exc)
        try:
            payload = resp.json() if resp.text else {}
        except Exception:
            payload = {"raw": resp.text}
        if resp.status_code >= 400:
            return False, int(resp.status_code), payload, f"HTTP {resp.status_code}"
        return True, int(resp.status_code), payload, ""

    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    from urllib import error as urlerror
    from urllib import request as urlrequest
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


def health_ok(timeout: int = 4) -> tuple[bool, str]:
    ok, code, payload, err = call_json("GET", "/health", timeout=timeout)
    if not ok:
        return False, err or f"health failed ({code})"
    if isinstance(payload, dict):
        state = str(payload.get("status", "")).strip().lower()
        if state and state not in {"healthy", "ok", "running"}:
            return False, f"unexpected health status={state}"
    return True, "healthy"


def wait_healthy(timeout_seconds: int) -> tuple[bool, float, str]:
    start = time.time()
    deadline = start + max(1, timeout_seconds)
    last = ""
    while time.time() < deadline:
        ok, msg = health_ok()
        if ok:
            return True, time.time() - start, msg
        last = msg
        time.sleep(0.8)
    return False, time.time() - start, last or "timeout"


def find_port_owner_pid(port: int) -> int | None:
    if psutil is not None:
        try:
            for conn in psutil.net_connections(kind="tcp"):
                laddr = getattr(conn, "laddr", None)
                status = str(getattr(conn, "status", "")).upper()
                if laddr and getattr(laddr, "port", None) == port and status == "LISTEN":
                    pid = getattr(conn, "pid", None)
                    if pid:
                        return int(pid)
        except Exception:
            pass
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, check=False)
        for line in out.stdout.splitlines():
            text = line.strip()
            if "LISTENING" not in text:
                continue
            parts = [p for p in text.split() if p]
            if len(parts) < 5:
                continue
            if not parts[1].endswith(f":{port}"):
                continue
            return int(parts[-1])
    except Exception:
        pass
    return None


def pid_cmdline(pid: int) -> str:
    if pid <= 0:
        return ""
    if psutil is not None:
        try:
            return " ".join(psutil.Process(pid).cmdline())
        except Exception:
            pass
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine"],
            capture_output=True,
            text=True,
            check=False,
        )
        return (out.stdout or "").strip()
    except Exception:
        return ""


def is_expected_backend_cmdline(cmdline: str) -> bool:
    if not cmdline:
        return False
    norm = cmdline.lower().replace("/", "\\")
    return ("cli.main" in norm and "server" in norm and str(AI_ROOT).lower().replace("/", "\\") in norm)


def kill_pid(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass


def command_status(_: argparse.Namespace) -> int:
    ok, msg = health_ok()
    owner = find_port_owner_pid(PORT)
    cmd = pid_cmdline(owner) if owner else ""
    echo("INFO", f"url={BASE_URL}")
    echo("INFO", f"port_owner_pid={owner}")
    if cmd:
        echo("INFO", f"port_owner_cmdline={cmd}")
    if ok:
        echo("PASS", f"/health {msg}")
        return 0
    echo("FAIL", f"/health {msg}")
    return 1


def command_start_backend(args: argparse.Namespace) -> int:
    if not AI_PYTHON.exists():
        echo("FAIL", f"missing backend python: {AI_PYTHON}")
        return 1

    owner = find_port_owner_pid(PORT)
    if owner:
        cmd = pid_cmdline(owner)
        if is_expected_backend_cmdline(cmd):
            echo("WARN", f"port {PORT} currently owned by expected backend pid={owner}; stopping it first")
            kill_pid(owner)
            time.sleep(1.0)
        else:
            echo("FAIL", f"port {PORT} is in use by non-backend pid={owner}")
            if cmd:
                echo("FAIL", f"owner cmdline: {cmd}")
            return 1

    BACKEND_LOG.parent.mkdir(parents=True, exist_ok=True)
    log_f = open(BACKEND_LOG, "a", encoding="utf-8")
    env = os.environ.copy()
    env["LLM_PROVIDER"] = "none"
    proc = subprocess.Popen(
        [str(AI_PYTHON), "-m", "cli.main", "server", "--host", HOST, "--port", str(PORT)],
        cwd=str(AI_ROOT),
        env=env,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        text=True,
    )
    OPS_LOG_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    echo("INFO", f"backend start requested pid={proc.pid}")

    ok, elapsed, detail = wait_healthy(args.timeout)
    if ok:
        echo("PASS", f"backend healthy in {elapsed:.1f}s")
        return 0
    echo("FAIL", f"backend did not become healthy in {elapsed:.1f}s ({detail})")
    return 1


def command_stop_backend(_: argparse.Namespace) -> int:
    owner = find_port_owner_pid(PORT)
    if owner:
        cmd = pid_cmdline(owner)
        if is_expected_backend_cmdline(cmd):
            kill_pid(owner)
            echo("PASS", f"stopped backend pid={owner}")
            try:
                PID_FILE.unlink(missing_ok=True)
            except Exception:
                pass
            return 0
        echo("FAIL", f"refusing to stop pid={owner}; command line does not match expected backend")
        if cmd:
            echo("FAIL", f"owner cmdline: {cmd}")
        return 1

    if PID_FILE.exists():
        txt = PID_FILE.read_text(encoding="utf-8").strip()
        if txt.isdigit():
            pid = int(txt)
            cmd = pid_cmdline(pid)
            if is_expected_backend_cmdline(cmd):
                kill_pid(pid)
                echo("PASS", f"stopped backend pid={pid} from pid file")
                try:
                    PID_FILE.unlink(missing_ok=True)
                except Exception:
                    pass
                return 0
    echo("WARN", "backend not running")
    return 0


def command_restart_backend(args: argparse.Namespace) -> int:
    stop_rc = command_stop_backend(args)
    if stop_rc != 0:
        return stop_rc
    return command_start_backend(args)


def command_smoke(args: argparse.Namespace) -> int:
    ok, elapsed, detail = wait_healthy(args.timeout)
    if not ok:
        echo("FAIL", f"health check failed after {elapsed:.1f}s ({detail})")
        return 1
    echo("PASS", "GET /health")

    ok, _, _, err = call_json("GET", "/api/status", timeout=15)
    if ok:
        echo("PASS", "GET /api/status")
    else:
        echo("FAIL", f"GET /api/status failed: {err}")
        return 1

    ok, _, _, err = call_json("GET", "/api/universe/list", timeout=15)
    if ok:
        echo("PASS", "GET /api/universe/list")
    else:
        echo("WARN", f"GET /api/universe/list failed: {err}")

    if args.no_dl_job:
        echo("WARN", "deep-learning job smoke skipped by flag")
        return 0

    body = {"symbol": args.symbol, "model_type": args.model, "epochs": args.epochs}
    ok, _, enqueue_payload, err = call_json("POST", "/api/jobs/deep-learning/train", timeout=30, body=body)
    if not ok:
        echo("FAIL", f"POST /api/jobs/deep-learning/train failed: {err}")
        return 1

    job_id = None
    if isinstance(enqueue_payload, dict):
        for k in ("id", "job_id", "jobId"):
            v = enqueue_payload.get(k)
            if v:
                job_id = str(v)
                break
    if not job_id:
        echo("FAIL", "missing job id in enqueue response")
        return 1
    echo("PASS", f"deep-learning job enqueued id={job_id}")

    deadline = time.time() + max(5, args.job_timeout)
    final = "timeout"
    final_error = ""
    model_path = ""
    while time.time() < deadline:
        ok, _, payload, err = call_json("GET", f"/api/jobs/{job_id}", timeout=15)
        if not ok:
            final_error = err
            time.sleep(2)
            continue
        if not isinstance(payload, dict):
            final_error = "unexpected payload"
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
    print(
        json.dumps(
            {
                "status": final,
                "job_id": job_id,
                "model_path": model_path or None,
                "error": final_error or None,
            },
            indent=2,
        )
    )

    if final == "completed":
        echo("PASS", "deep-learning smoke passed")
        return 0
    echo("FAIL", "deep-learning smoke failed")
    return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m opsctl", description="dPolaris ops runner")
    sub = p.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--timeout", type=int, default=30)
    common.add_argument("--url", default=BASE_URL)

    a = sub.add_parser("status", parents=[common], help="Check health + port owner")
    a.set_defaults(func=command_status)

    b = sub.add_parser("start-backend", parents=[common], help="Start backend safely")
    b.set_defaults(func=command_start_backend)

    c = sub.add_parser("stop-backend", parents=[common], help="Stop backend safely")
    c.set_defaults(func=command_stop_backend)

    d = sub.add_parser("restart-backend", parents=[common], help="Restart backend safely")
    d.set_defaults(func=command_restart_backend)

    e = sub.add_parser("smoke", parents=[common], help="Run smoke tests")
    e.add_argument("--symbol", default="AAPL")
    e.add_argument("--model", default="lstm")
    e.add_argument("--epochs", type=int, default=1)
    e.add_argument("--job-timeout", type=int, default=600)
    e.add_argument("--no-dl-job", action="store_true")
    e.set_defaults(func=command_smoke)

    return p


def main(argv: list[str] | None = None) -> int:
    global BASE_URL
    log_path = setup_logging()
    echo("INFO", f"log file: {log_path}")
    parser = build_parser()
    args = parser.parse_args(argv)
    BASE_URL = str(args.url).rstrip("/")
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
