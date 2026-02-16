from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

try:
    import requests
except ModuleNotFoundError:
    requests = None


OPS_ROOT = Path(__file__).resolve().parents[1]
HOME = Path.home()
OPS_LOG_DIR = OPS_ROOT / ".ops_logs"
RUN_DIR = HOME / "dpolaris_data" / "run"
LOG_DIR = HOME / "dpolaris_data" / "logs"

BACKEND_PID_FILE = RUN_DIR / "backend.pid"
ORCHESTRATOR_PID_FILE = RUN_DIR / "orchestrator.pid"
ORCHESTRATOR_HEARTBEAT_FILE = RUN_DIR / "orchestrator.heartbeat.json"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8420

SAFE_SERVER_FRAGMENT = "-m cli.main server"
SAFE_AI_REPO_FRAGMENT = "dpolaris_ai"

HEALTH_OK_STATES = {"healthy", "ok", "running"}
JOB_SUCCESS_STATES = {"completed", "success"}
JOB_FAILURE_STATES = {"failed", "error", "cancelled"}
REQUIRED_REPORT_HEADINGS = [
    "## Overview",
    "## Price/Volume Snapshot",
    "## Technical Indicators",
    "## Chart Patterns",
    "## Model Signals",
    "## News",
    "## Risk Notes",
    "## Next Steps",
]

EXIT_OK = 0
EXIT_FAIL = 2


def _ensure_ops_log_dir() -> Path:
    OPS_LOG_DIR.mkdir(parents=True, exist_ok=True)
    return OPS_LOG_DIR


def _ops_log_path() -> Path:
    _ensure_ops_log_dir()
    return OPS_LOG_DIR / f"ops_{time.strftime('%Y%m%d')}.log"


def _ops_log(message: str) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    try:
        with _ops_log_path().open("a", encoding="utf-8") as fh:
            fh.write(f"{timestamp} {message}\n")
    except Exception:
        pass


@dataclass
class HttpResult:
    ok: bool
    status: int | None
    payload: Any
    error: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_command(cmd: list[str]) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as exc:
        return 127, "", str(exc)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _endpoint(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


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
    if ok:
        return HttpResult(True, int(resp.status_code), payload, "")
    return HttpResult(False, int(resp.status_code), payload, f"HTTP {resp.status_code}")


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


def http_json(method: str, url: str, timeout: int = 15, body: dict[str, Any] | None = None) -> HttpResult:
    if requests is not None:
        return _http_requests(method, url, timeout, body)
    return _http_urllib(method, url, timeout, body)


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


def _extract_job_error(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("error", "detail", "message", "reason"):
        value = payload.get(key)
        if value:
            return str(value)
    return ""


def _status_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    status = payload.get("status")
    if status is None:
        return ""
    return str(status).strip().lower()


def health_once(base_url: str, timeout: int = 4) -> tuple[bool, str]:
    result = http_json("GET", _endpoint(base_url, "/health"), timeout=timeout)
    if not result.ok:
        return False, result.error or "health request failed"
    state = _status_text(result.payload)
    if state and state not in HEALTH_OK_STATES:
        return False, f"unexpected health status={state}"
    return True, "healthy"


def wait_healthy(base_url: str, timeout_seconds: int) -> tuple[bool, float, str]:
    started = time.time()
    deadline = started + max(1, timeout_seconds)
    last = ""
    while time.time() < deadline:
        ok, detail = health_once(base_url)
        if ok:
            return True, time.time() - started, detail
        last = detail
        time.sleep(0.8)
    return False, time.time() - started, last or "timeout"


def _listening_pids_on_port(port: int) -> tuple[list[int], str]:
    rc, out, err = _run_command(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"])
    if rc not in (0, 1):
        detail = err or out or f"lsof exited with {rc}"
        return [], detail
    if not out:
        return [], ""

    pids: list[int] = []
    lines = out.splitlines()
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        pid_text = parts[1].strip()
        if pid_text.isdigit():
            pids.append(int(pid_text))
    return sorted(set(pids)), ""


def _command_line_for_pid(pid: int) -> str:
    rc, out, _ = _run_command(["ps", "-p", str(pid), "-o", "command="])
    if rc != 0:
        return ""
    return out.strip()


def _is_safe_backend_command(command: str) -> bool:
    lowered = command.lower()
    return SAFE_SERVER_FRAGMENT in lowered and SAFE_AI_REPO_FRAGMENT in lowered


def _is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_pid(pid: int, grace_seconds: int = 8) -> tuple[bool, str]:
    if not _is_process_alive(pid):
        return True, "not-running"

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True, "not-running"
    except PermissionError as exc:
        return False, f"SIGTERM denied: {exc}"

    deadline = time.time() + max(1, grace_seconds)
    while time.time() < deadline:
        if not _is_process_alive(pid):
            return True, "sigterm"
        time.sleep(0.2)

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True, "sigterm-late"
    except PermissionError as exc:
        return False, f"SIGKILL denied: {exc}"

    hard_deadline = time.time() + 3
    while time.time() < hard_deadline:
        if not _is_process_alive(pid):
            return True, "sigkill"
        time.sleep(0.1)
    return False, "still-running-after-sigkill"


def _collect_port_owners(port: int) -> tuple[list[dict[str, Any]], str]:
    pids, err = _listening_pids_on_port(port)
    owners: list[dict[str, Any]] = []
    for pid in pids:
        command = _command_line_for_pid(pid)
        owners.append(
            {
                "pid": pid,
                "command": command,
                "safe_to_kill": _is_safe_backend_command(command),
            }
        )
    return owners, err


def _read_pid_file(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except Exception:
        return None
    if not text.isdigit():
        return None
    return int(text)


def _unlink_if_exists(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


def _stop_backend_on_port(port: int, force: bool = False) -> tuple[bool, str, list[dict[str, Any]]]:
    """Stop backend process listening on port.

    Args:
        port: The port to check for listeners.
        force: If True, kill any process on the port regardless of allowlist.
               Defaults to False (only kill dpolaris_ai processes).
    """
    owners, inspect_error = _collect_port_owners(port)
    if inspect_error:
        return False, f"failed to inspect port {port}: {inspect_error}", owners
    if not owners:
        return True, f"no listener on port {port}", owners

    if not force:
        for owner in owners:
            if owner.get("safe_to_kill"):
                continue
            pid = owner["pid"]
            command = owner.get("command") or "(unknown)"
            return (
                False,
                (
                    f"port {port} owner is NOT allowlisted; refusing to kill pid={pid}. "
                    "Allowlist requires command containing '-m cli.main server' and 'dPolaris_ai'. "
                    f"Use --force to kill anyway. owner command: {command}"
                ),
                owners,
            )

    failed: list[int] = []
    for owner in owners:
        pid = int(owner["pid"])
        command = owner.get("command") or "(unknown)"
        is_safe = owner.get("safe_to_kill", False)

        if force and not is_safe:
            _ops_log(f"FORCE-KILL pid={pid} command={command}")
            print(f"INFO --force enabled, killing non-allowlisted pid={pid}")

        ok, mode = _terminate_pid(pid, grace_seconds=8)
        owner["termination"] = mode
        if not ok:
            failed.append(pid)

    if failed:
        return False, f"failed to stop pid(s): {', '.join(str(p) for p in failed)}", owners
    return True, f"stopped backend pid(s): {', '.join(str(o['pid']) for o in owners)}", owners


def _stop_backend_from_pid_file(excluded: set[int], force: bool = False) -> tuple[bool, str]:
    pid = _read_pid_file(BACKEND_PID_FILE)
    if pid is None:
        return True, ""
    if pid in excluded:
        _unlink_if_exists(BACKEND_PID_FILE)
        return True, ""
    if not _is_process_alive(pid):
        _unlink_if_exists(BACKEND_PID_FILE)
        return True, f"removed stale backend pid file (pid={pid})"

    command = _command_line_for_pid(pid)
    is_safe = _is_safe_backend_command(command)

    if not is_safe and not force:
        return (
            False,
            (
                f"backend.pid points to non-allowlisted pid={pid}; refusing to kill. "
                "Allowlist requires command containing '-m cli.main server' and 'dPolaris_ai'. "
                f"Use --force to kill anyway. owner command: {command or '(unknown)'}"
            ),
        )

    if not is_safe and force:
        _ops_log(f"FORCE-KILL from pid file: pid={pid} command={command}")
        print(f"INFO --force enabled, killing non-allowlisted pid={pid} from pid file")

    ok, mode = _terminate_pid(pid, grace_seconds=8)
    if not ok:
        return False, f"failed to stop backend pid from pid file: pid={pid}, reason={mode}"
    _unlink_if_exists(BACKEND_PID_FILE)
    return True, f"stopped backend pid from pid file: pid={pid} ({mode})"


def _list_orchestrator_processes() -> list[dict[str, Any]]:
    rc, out, _ = _run_command(["ps", "ax", "-o", "pid=,command="])
    if rc != 0:
        return []

    current_pid = os.getpid()
    procs: list[dict[str, Any]] = []
    for raw in out.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) < 2:
            continue
        pid_text, command = parts[0], parts[1]
        if not pid_text.isdigit():
            continue
        pid = int(pid_text)
        if pid == current_pid:
            continue
        lowered = command.lower()
        if "-m ops.main" in lowered and "dpolaris_ops" in lowered:
            procs.append({"pid": pid, "command": command})
    return sorted(procs, key=lambda item: int(item["pid"]))


def _stop_orchestrator_processes() -> dict[str, Any]:
    targets: dict[int, str] = {}
    for proc in _list_orchestrator_processes():
        targets[int(proc["pid"])] = str(proc["command"])

    pid_from_file = _read_pid_file(ORCHESTRATOR_PID_FILE)
    if pid_from_file is not None and pid_from_file not in targets:
        targets[pid_from_file] = _command_line_for_pid(pid_from_file)

    results: list[dict[str, Any]] = []
    for pid in sorted(targets):
        command = targets[pid]
        if not command:
            results.append({"pid": pid, "killed": False, "reason": "unknown-command", "command": command})
            continue
        if "-m ops.main" not in command.lower() and "dpolaris_ops" not in command.lower():
            results.append({"pid": pid, "killed": False, "reason": "not-ops-main", "command": command})
            continue
        ok, mode = _terminate_pid(pid, grace_seconds=5)
        results.append({"pid": pid, "killed": ok, "reason": mode, "command": command})

    _unlink_if_exists(ORCHESTRATOR_PID_FILE)
    _unlink_if_exists(ORCHESTRATOR_HEARTBEAT_FILE)
    return {"targets": results}


def _resolve_ai_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve(strict=False)

    candidates: list[Path] = []

    env_root = os.environ.get("DPOLARIS_AI_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve(strict=False)

    candidates.append(OPS_ROOT.parent / "dPolaris_ai")
    candidates.append(OPS_ROOT.parent / "dpolaris_ai")
    candidates.append(Path.home() / "my-git" / "dPolaris_ai")
    candidates.append(Path.home() / "my-git" / "dpolaris_ai")

    seen: set[str] = set()
    unique: list[Path] = []
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)

    for candidate in unique:
        if candidate.exists():
            return candidate.resolve()
    return unique[0].resolve(strict=False)


def _find_backend_python(ai_root: Path) -> Path | None:
    # Allow override via env var for dev flexibility
    env_python = os.environ.get("DPOLARIS_PYTHON")
    if env_python:
        p = Path(env_python).expanduser().resolve(strict=False)
        if p.exists():
            return p
    for rel in (".venv/bin/python", ".venv/bin/python3"):
        candidate = ai_root / rel
        if candidate.exists():
            return candidate
    return None


def _resolve_host(args: argparse.Namespace) -> str:
    base_url = getattr(args, "base_url", None)
    if base_url:
        parsed = urlparse.urlparse(base_url)
        if parsed.hostname:
            return str(parsed.hostname)
    return str(getattr(args, "host", DEFAULT_HOST))


def _resolve_port(args: argparse.Namespace) -> int:
    base_url = getattr(args, "base_url", None)
    if base_url:
        parsed = urlparse.urlparse(base_url)
        if parsed.port:
            return int(parsed.port)
    return int(getattr(args, "port", DEFAULT_PORT))


def _resolve_base_url(args: argparse.Namespace) -> str:
    base_url = getattr(args, "base_url", None)
    if base_url:
        return str(base_url).rstrip("/")
    return f"http://{_resolve_host(args)}:{_resolve_port(args)}"


def _tail_file(path: Path, lines: int) -> list[str]:
    tail = deque(maxlen=max(1, lines))
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                tail.append(line.rstrip("\n"))
    except Exception:
        return []
    return list(tail)


def _tail_latest_logs(ai_root: Path | None, lines: int = 30) -> tuple[Path | None, list[str]]:
    candidates: list[Path] = [LOG_DIR, OPS_LOG_DIR, OPS_ROOT / "logs"]
    if ai_root is not None:
        candidates.append(ai_root / "logs")

    files: list[Path] = []
    for directory in candidates:
        if not directory.exists():
            continue
        try:
            files.extend([p for p in directory.rglob("*.log") if p.is_file()])
        except Exception:
            continue
    if not files:
        return None, []
    latest = max(files, key=lambda p: p.stat().st_mtime)
    return latest, _tail_file(latest, lines=lines)


def _print_failure_logs(job_payload: Any, ai_root: Path | None) -> None:
    printed = False
    if isinstance(job_payload, dict):
        logs = job_payload.get("logs")
        if isinstance(logs, list) and logs:
            print("Job logs (last entries):")
            for item in logs[-10:]:
                print(f"  {item}")
            printed = True

    latest, lines = _tail_latest_logs(ai_root=ai_root, lines=30)
    if latest is not None and lines:
        print(f"Latest log tail from {latest}:")
        for line in lines:
            print(line)
        printed = True

    if not printed:
        print("No logs found for failure diagnostics.")


def _start_backend(ai_root: Path, host: str, port: int) -> tuple[bool, int | None, Path | None, str]:
    python_bin = _find_backend_python(ai_root)
    if python_bin is None:
        return False, None, None, f"missing backend python: {(ai_root / '.venv/bin/python')}"

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_ops_log_dir()
    log_path = LOG_DIR / f"backend_{time.strftime('%Y%m%d_%H%M%S')}.log"

    env = os.environ.copy()
    env["LLM_PROVIDER"] = "none"

    cmd = [str(python_bin), "-m", "cli.main", "server", "--host", host, "--port", str(port)]

    _ops_log(f"Starting backend: {' '.join(cmd)}")

    try:
        with log_path.open("a", encoding="utf-8") as log_file:
            proc = subprocess.Popen(
                cmd,
                cwd=str(ai_root),
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except Exception as exc:
        _ops_log(f"Failed to start backend: {exc}")
        return False, None, None, str(exc)

    try:
        BACKEND_PID_FILE.write_text(f"{proc.pid}\n", encoding="utf-8")
    except Exception:
        pass

    _ops_log(f"Backend started: pid={proc.pid} log={log_path}")
    return True, int(proc.pid), log_path, ""


def _remove_runtime_files(remove_backend_pid: bool) -> None:
    if remove_backend_pid:
        _unlink_if_exists(BACKEND_PID_FILE)
    _unlink_if_exists(ORCHESTRATOR_PID_FILE)
    _unlink_if_exists(ORCHESTRATOR_HEARTBEAT_FILE)


def cmd_up(args: argparse.Namespace) -> int:
    base_url = _resolve_base_url(args)
    port = _resolve_port(args)
    host = _resolve_host(args)
    force = getattr(args, "force", True)

    _ops_log(f"CMD up: base_url={base_url} port={port} force={force}")

    healthy, detail = health_once(base_url)
    if healthy:
        print(f"PASS backend already healthy at {base_url}")
        return EXIT_OK

    owners, inspect_error = _collect_port_owners(port)
    if inspect_error:
        print(f"FAIL failed to inspect port {port}: {inspect_error}")
        return EXIT_FAIL
    if owners:
        safe_ok, stop_msg, _ = _stop_backend_on_port(port, force=force)
        if not safe_ok:
            print(f"FAIL {stop_msg}")
            return EXIT_FAIL
        print(f"INFO {stop_msg}")

    ai_root = _resolve_ai_root(args.ai_root)
    if not ai_root.exists():
        print(f"FAIL backend repo path does not exist: {ai_root}")
        return EXIT_FAIL

    started, pid, log_path, start_error = _start_backend(ai_root=ai_root, host=host, port=port)
    if not started:
        print(f"FAIL could not start backend: {start_error}")
        return EXIT_FAIL

    ok, elapsed, wait_detail = wait_healthy(base_url, args.timeout)
    if ok:
        print(f"PASS backend healthy at {base_url} (pid={pid}, elapsed={elapsed:.1f}s)")
        _ops_log(f"Backend healthy: pid={pid} elapsed={elapsed:.1f}s")
        return EXIT_OK

    print(f"FAIL backend failed health check after {args.timeout}s: {wait_detail}")
    if log_path is not None:
        print(f"Backend log file: {log_path}")
    _print_failure_logs(job_payload=None, ai_root=ai_root)
    _ops_log(f"Backend failed health check: {wait_detail}")
    return EXIT_FAIL


def cmd_down(args: argparse.Namespace) -> int:
    port = _resolve_port(args)
    force = getattr(args, "force", True)
    exit_code = EXIT_OK

    _ops_log(f"CMD down: port={port} force={force}")

    orchestrator = _stop_orchestrator_processes()
    killed_orchestrators = [r for r in orchestrator["targets"] if r.get("killed")]
    if killed_orchestrators:
        pids = ", ".join(str(r["pid"]) for r in killed_orchestrators)
        print(f"INFO stopped orchestrator pid(s): {pids}")
    else:
        print("INFO no running orchestrator process found")

    backend_ok, backend_message, owners = _stop_backend_on_port(port, force=force)
    if backend_ok:
        print(f"INFO {backend_message}")
        _ops_log(f"Backend stopped: {backend_message}")
    else:
        exit_code = EXIT_FAIL
        print(f"FAIL {backend_message}")
        _ops_log(f"Backend stop failed: {backend_message}")
        for owner in owners:
            print(f"  pid={owner['pid']} safe={owner['safe_to_kill']} cmd={owner['command'] or '(unknown)'}")

    seen_pids = {int(owner["pid"]) for owner in owners}
    pid_ok, pid_msg = _stop_backend_from_pid_file(excluded=seen_pids, force=force)
    if pid_msg:
        level = "INFO" if pid_ok else "FAIL"
        print(f"{level} {pid_msg}")
    if not pid_ok:
        exit_code = EXIT_FAIL

    _remove_runtime_files(remove_backend_pid=(exit_code == EXIT_OK))
    if exit_code == EXIT_OK:
        print("PASS down complete")
    return exit_code


def _build_status_payload(base_url: str, port: int) -> dict[str, Any]:
    healthy, health_detail = health_once(base_url)
    owners, inspect_error = _collect_port_owners(port)
    orchestrator_procs = _list_orchestrator_processes()

    payload: dict[str, Any] = {
        "timestamp": _now_iso(),
        "base_url": base_url,
        "port": port,
        "ok": healthy,
        "backend": {
            "healthy": healthy,
            "health_detail": health_detail,
            "listening_owners": owners,
            "pid_file": str(BACKEND_PID_FILE),
            "pid_file_pid": _read_pid_file(BACKEND_PID_FILE),
        },
        "orchestrator": {
            "running": bool(orchestrator_procs),
            "processes": orchestrator_procs,
            "pid_file": str(ORCHESTRATOR_PID_FILE),
            "pid_file_pid": _read_pid_file(ORCHESTRATOR_PID_FILE),
        },
        "errors": [],
    }
    if inspect_error:
        payload["errors"].append(f"lsof inspection failed: {inspect_error}")
    return payload


def _print_status_human(payload: dict[str, Any]) -> None:
    backend = payload["backend"]
    orchestrator = payload["orchestrator"]
    owners = backend.get("listening_owners", [])

    print(f"Status @ {payload['timestamp']}")
    print(f"Base URL: {payload['base_url']}")
    print(
        f"Backend health: {'healthy' if backend.get('healthy') else 'unhealthy'} "
        f"({backend.get('health_detail')})"
    )
    if owners:
        print("Port owners:")
        for owner in owners:
            print(f"  pid={owner['pid']} safe={owner['safe_to_kill']} cmd={owner['command'] or '(unknown)'}")
    else:
        print("Port owners: none")

    print(f"Orchestrator running: {orchestrator.get('running')}")
    if orchestrator.get("processes"):
        for proc in orchestrator["processes"]:
            print(f"  pid={proc['pid']} cmd={proc['command']}")

    if payload["errors"]:
        print("Errors:")
        for item in payload["errors"]:
            print(f"  {item}")

    print(f"Overall ok: {payload['ok']}")


def cmd_status(args: argparse.Namespace) -> int:
    base_url = _resolve_base_url(args)
    port = _resolve_port(args)
    payload = _build_status_payload(base_url=base_url, port=port)
    if args.json:
        print(json.dumps(payload, separators=(",", ":")))
        return EXIT_OK
    _print_status_human(payload)
    return EXIT_OK


def cmd_smoke_fast(args: argparse.Namespace) -> int:
    base_url = _resolve_base_url(args)
    _ops_log(f"CMD smoke-fast: base_url={base_url}")

    ok, elapsed, detail = wait_healthy(base_url, args.timeout)
    if not ok:
        print(f"FAIL /health not ready after {elapsed:.1f}s: {detail}")
        _ops_log(f"smoke-fast FAIL: /health not ready: {detail}")
        return EXIT_FAIL

    status_result = http_json("GET", _endpoint(base_url, "/api/status"), timeout=20)
    if not status_result.ok or not isinstance(status_result.payload, dict):
        err = status_result.error or "invalid /api/status response"
        print(f"FAIL GET /api/status: {err}")
        _ops_log(f"smoke-fast FAIL: /api/status: {err}")
        return EXIT_FAIL

    print("PASS smoke-fast")
    print(f"  /health: healthy (elapsed={elapsed:.1f}s)")
    print("  /api/status: ok")
    _ops_log("smoke-fast PASS")
    return EXIT_OK


def _universe_names_from_payload(payload: Any) -> list[str]:
    if isinstance(payload, list):
        return [str(item).strip() for item in payload if str(item).strip()]
    if isinstance(payload, dict):
        candidate = payload.get("universes")
        if isinstance(candidate, list):
            names: list[str] = []
            for item in candidate:
                if isinstance(item, str):
                    text = item.strip()
                elif isinstance(item, dict):
                    text = str(item.get("name") or "").strip()
                else:
                    text = ""
                if text:
                    names.append(text)
            return names
    return []


def _universe_count_from_payload(payload: Any) -> int:
    if isinstance(payload, dict):
        raw_count = payload.get("count")
        if isinstance(raw_count, (int, float)):
            if int(raw_count) > 0:
                return int(raw_count)
        for key in ("tickers", "merged", "items", "rows", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
        nested = payload.get("universe")
        if nested is not None:
            return _universe_count_from_payload(nested)
    elif isinstance(payload, list):
        return len(payload)
    return 0


def cmd_smoke_universe(args: argparse.Namespace) -> int:
    base_url = _resolve_base_url(args)
    _ops_log(f"CMD smoke-universe: base_url={base_url}")

    ok, elapsed, detail = wait_healthy(base_url, args.timeout)
    if not ok:
        print(f"FAIL /health not ready after {elapsed:.1f}s: {detail}")
        _ops_log(f"smoke-universe FAIL: /health not ready: {detail}")
        return EXIT_FAIL

    required = ["nasdaq300", "wsb100", "combined"]
    list_result = http_json("GET", _endpoint(base_url, "/api/universe/list"), timeout=20)
    if not list_result.ok:
        print(f"FAIL GET /api/universe/list: {list_result.error}")
        _ops_log(f"smoke-universe FAIL: /api/universe/list: {list_result.error}")
        return EXIT_FAIL

    names = _universe_names_from_payload(list_result.payload)
    if not names:
        print("FAIL /api/universe/list returned empty")
        _ops_log("smoke-universe FAIL: universe/list empty")
        return EXIT_FAIL

    missing = [name for name in required if name not in names]
    if missing:
        print(f"FAIL /api/universe/list missing required names: {missing}")
        _ops_log(f"smoke-universe FAIL: missing names {missing}")
        return EXIT_FAIL

    print("PASS /api/universe/list")
    print(f"  names={names}")

    for name in required:
        result = http_json("GET", _endpoint(base_url, f"/api/universe/{name}"), timeout=20)
        if not result.ok:
            print(f"FAIL GET /api/universe/{name}: {result.error}")
            _ops_log(f"smoke-universe FAIL: /api/universe/{name}: {result.error}")
            return EXIT_FAIL

        ticker_count = _universe_count_from_payload(result.payload)
        if ticker_count <= 0:
            print(f"FAIL /api/universe/{name} returned no tickers")
            _ops_log(f"smoke-universe FAIL: /api/universe/{name} empty")
            return EXIT_FAIL

        print(f"PASS /api/universe/{name} count={ticker_count}")

    _ops_log("smoke-universe PASS")
    return EXIT_OK


def cmd_report_smoke(args: argparse.Namespace) -> int:
    base_url = _resolve_base_url(args)
    symbol = str(getattr(args, "symbol", "AAPL") or "AAPL").strip().upper()
    _ops_log(f"CMD report-smoke: base_url={base_url} symbol={symbol}")

    ok, elapsed, detail = wait_healthy(base_url, args.timeout)
    if not ok:
        print(f"FAIL /health not ready after {elapsed:.1f}s: {detail}")
        _ops_log(f"report-smoke FAIL: /health not ready: {detail}")
        return EXIT_FAIL

    report_result = http_json(
        "POST",
        _endpoint(base_url, f"/api/analyze/report?symbol={urlparse.quote(symbol)}"),
        timeout=90,
        body={},
    )
    if not report_result.ok or not isinstance(report_result.payload, dict):
        err = report_result.error or "invalid report response"
        print(f"FAIL POST /api/analyze/report: {err}")
        _ops_log(f"report-smoke FAIL: /api/analyze/report: {err}")
        return EXIT_FAIL

    report_payload = report_result.payload
    report_text = str(report_payload.get("report_text") or "")
    missing = [heading for heading in REQUIRED_REPORT_HEADINGS if heading not in report_text]
    if missing:
        print(f"FAIL report missing sections: {missing}")
        _ops_log(f"report-smoke FAIL: missing sections {missing}")
        return EXIT_FAIL

    print("PASS /api/analyze/report")
    print(f"  symbol={symbol}")
    print(f"  report_id={report_payload.get('id')}")

    list_result = http_json("GET", _endpoint(base_url, "/api/analysis/list?limit=20"), timeout=20)
    if not list_result.ok or not isinstance(list_result.payload, list):
        err = list_result.error or "invalid list response"
        print(f"FAIL GET /api/analysis/list: {err}")
        _ops_log(f"report-smoke FAIL: /api/analysis/list: {err}")
        return EXIT_FAIL

    if not list_result.payload:
        print("FAIL /api/analysis/list returned empty")
        _ops_log("report-smoke FAIL: analysis list empty")
        return EXIT_FAIL

    analysis_id = str(report_payload.get("id") or "").strip()
    if not analysis_id:
        first = list_result.payload[0]
        if isinstance(first, dict):
            analysis_id = str(first.get("id") or "").strip()

    if not analysis_id:
        print("FAIL could not resolve analysis id")
        _ops_log("report-smoke FAIL: missing analysis id")
        return EXIT_FAIL

    detail_result = http_json("GET", _endpoint(base_url, f"/api/analysis/{analysis_id}"), timeout=20)
    if not detail_result.ok or not isinstance(detail_result.payload, dict):
        err = detail_result.error or "invalid detail response"
        print(f"FAIL GET /api/analysis/{analysis_id}: {err}")
        _ops_log(f"report-smoke FAIL: /api/analysis/{analysis_id}: {err}")
        return EXIT_FAIL

    detail_report = str(detail_result.payload.get("report_text") or "")
    if not detail_report.strip():
        print(f"FAIL /api/analysis/{analysis_id} missing report_text")
        _ops_log(f"report-smoke FAIL: /api/analysis/{analysis_id} missing report_text")
        return EXIT_FAIL

    print("PASS /api/analysis/list")
    print(f"  count={len(list_result.payload)}")
    print(f"PASS /api/analysis/{analysis_id}")
    _ops_log("report-smoke PASS")
    return EXIT_OK


def _get_device_info(base_url: str) -> dict[str, Any]:
    """Fetch deep-learning device information from the backend."""
    result = http_json("GET", _endpoint(base_url, "/api/deep-learning/status"), timeout=10)
    if not result.ok or not isinstance(result.payload, dict):
        return {"device": "unknown", "error": result.error or "failed to fetch device info"}
    return result.payload


def _format_field(value: Any, numeric: bool = False) -> str:
    """Format a field value for display."""
    if value is None:
        return "null"
    if numeric:
        if isinstance(value, (int, float)):
            return f"{value:,.2f}" if isinstance(value, float) else f"{value:,}"
        return str(value)
    return str(value)


def _validate_metadata_response(payload: Any, symbols: list[str], verbose: bool = False) -> tuple[bool, list[str]]:
    """Validate stocks/metadata response shape and return summary lines."""
    lines: list[str] = []
    if not isinstance(payload, dict):
        return False, ["Invalid response: not a dict"]

    for symbol in symbols:
        data = payload.get(symbol)
        if data is None:
            lines.append(f"  {symbol}: not found")
            continue

        sector = data.get("sector")
        market_cap = data.get("market_cap")
        avg_volume = data.get("avg_volume_7d")

        lines.append(f"  {symbol}:")
        lines.append(f"    sector: {_format_field(sector)}")
        lines.append(f"    market_cap: {_format_field(market_cap, numeric=True)}")
        lines.append(f"    avg_volume_7d: {_format_field(avg_volume, numeric=True)}")

    return True, lines


def _validate_analysis_last_response(payload: Any, symbols: list[str], verbose: bool = False) -> tuple[bool, list[str]]:
    """Validate analysis/last response shape and return summary lines."""
    lines: list[str] = []
    if not isinstance(payload, dict):
        return False, ["Invalid response: not a dict"]

    for symbol in symbols:
        data = payload.get(symbol)
        if data is None:
            lines.append(f"  {symbol}: no analysis yet")
            continue

        change_pct = data.get("change_percent_1d")
        last_at = data.get("last_analysis_at")

        lines.append(f"  {symbol}:")
        lines.append(f"    change_percent_1d: {_format_field(change_pct, numeric=True)}")
        lines.append(f"    last_analysis_at: {_format_field(last_at)}")

    return True, lines


def cmd_smoke_metadata(args: argparse.Namespace) -> int:
    """Smoke test for metadata and analysis endpoints."""
    base_url = _resolve_base_url(args)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    symbols_param = ",".join(symbols)
    verbose = getattr(args, "verbose", False)

    _ops_log(f"CMD smoke-metadata: base_url={base_url} symbols={symbols_param}")

    ok, elapsed, detail = wait_healthy(base_url, args.timeout)
    if not ok:
        print(f"FAIL /health not ready after {elapsed:.1f}s: {detail}")
        _ops_log(f"smoke-metadata FAIL: /health not ready: {detail}")
        return EXIT_FAIL

    print(f"INFO backend healthy (elapsed={elapsed:.1f}s)")

    # Test /api/stocks/metadata
    metadata_url = _endpoint(base_url, f"/api/stocks/metadata?symbols={symbols_param}")
    metadata_result = http_json("GET", metadata_url, timeout=30)

    if not metadata_result.ok:
        print(f"FAIL GET /api/stocks/metadata: {metadata_result.error}")
        if verbose and metadata_result.payload:
            print(json.dumps(metadata_result.payload, indent=2))
        _ops_log(f"smoke-metadata FAIL: /api/stocks/metadata: {metadata_result.error}")
        return EXIT_FAIL

    valid, metadata_lines = _validate_metadata_response(metadata_result.payload, symbols, verbose)
    if not valid:
        print(f"FAIL /api/stocks/metadata invalid response")
        for line in metadata_lines:
            print(line)
        return EXIT_FAIL

    print("PASS /api/stocks/metadata")
    for line in metadata_lines:
        print(line)

    # Test /api/analysis/last
    analysis_url = _endpoint(base_url, f"/api/analysis/last?symbols={symbols_param}")
    analysis_result = http_json("GET", analysis_url, timeout=30)

    if not analysis_result.ok:
        print(f"FAIL GET /api/analysis/last: {analysis_result.error}")
        if verbose and analysis_result.payload:
            print(json.dumps(analysis_result.payload, indent=2))
        _ops_log(f"smoke-metadata FAIL: /api/analysis/last: {analysis_result.error}")
        return EXIT_FAIL

    valid, analysis_lines = _validate_analysis_last_response(analysis_result.payload, symbols, verbose)
    if not valid:
        print(f"FAIL /api/analysis/last invalid response")
        for line in analysis_lines:
            print(line)
        return EXIT_FAIL

    print("PASS /api/analysis/last")
    for line in analysis_lines:
        print(line)

    print("PASS smoke-metadata complete")
    _ops_log("smoke-metadata PASS")
    return EXIT_OK


def _verify_analysis_after_dl(base_url: str, symbol: str, ai_root: Path | None, verbose: bool = False) -> tuple[bool, list[str]]:
    """Verify analysis endpoints after DL training completes."""
    errors: list[str] = []

    # Check /api/analysis/last
    analysis_last_url = _endpoint(base_url, f"/api/analysis/last?symbols={symbol}")
    last_result = http_json("GET", analysis_last_url, timeout=20)

    if not last_result.ok:
        errors.append(f"GET /api/analysis/last failed: {last_result.error}")
        return False, errors

    if not isinstance(last_result.payload, dict):
        errors.append("/api/analysis/last: response is not a dict")
        return False, errors

    symbol_data = last_result.payload.get(symbol)
    if symbol_data is None:
        errors.append(f"/api/analysis/last: {symbol} has no analysis (expected non-null after training)")
        return False, errors

    last_at = symbol_data.get("last_analysis_at")
    if not last_at:
        errors.append(f"/api/analysis/last: {symbol}.last_analysis_at is null")

    # Check /api/analysis/detail/{symbol}
    detail_url = _endpoint(base_url, f"/api/analysis/detail/{symbol}")
    detail_result = http_json("GET", detail_url, timeout=20)

    if not detail_result.ok:
        errors.append(f"GET /api/analysis/detail/{symbol} failed: {detail_result.error}")
        return False, errors

    if not isinstance(detail_result.payload, dict):
        errors.append(f"/api/analysis/detail/{symbol}: response is not a dict")
        return False, errors

    artifacts = detail_result.payload.get("artifacts")
    if not isinstance(artifacts, list):
        errors.append(f"/api/analysis/detail/{symbol}: artifacts is not a list")
        return False, errors

    if len(artifacts) < 1:
        errors.append(f"/api/analysis/detail/{symbol}: artifacts array is empty (expected >= 1)")
        return False, errors

    return True, [
        f"  /api/analysis/last: {symbol}.last_analysis_at = {last_at}",
        f"  /api/analysis/detail/{symbol}: artifacts count = {len(artifacts)}",
    ]


def cmd_smoke_dl(args: argparse.Namespace) -> int:
    base_url = _resolve_base_url(args)
    ai_root = _resolve_ai_root(args.ai_root)

    _ops_log(f"CMD smoke-dl: base_url={base_url} symbol={args.symbol} epochs={args.epochs}")

    ok, elapsed, detail = wait_healthy(base_url, args.timeout)
    if not ok:
        print(f"FAIL /health not ready after {elapsed:.1f}s: {detail}")
        _ops_log(f"smoke-dl FAIL: /health not ready: {detail}")
        _print_failure_logs(job_payload=None, ai_root=ai_root)
        return EXIT_FAIL

    device_info = _get_device_info(base_url)
    device = device_info.get("device", "unknown")
    torch_available = device_info.get("torch_available", False)
    cuda_available = device_info.get("cuda_available", False)
    mps_available = device_info.get("mps_available", False)

    print(f"INFO Deep-learning device: {device}")
    print(f"     torch={torch_available} cuda={cuda_available} mps={mps_available}")
    _ops_log(f"Device info: device={device} torch={torch_available} cuda={cuda_available} mps={mps_available}")

    if not torch_available:
        print("WARN PyTorch not available - deep-learning jobs may fail")

    enqueue = http_json(
        "POST",
        _endpoint(base_url, "/api/jobs/deep-learning/train"),
        timeout=30,
        body={"symbol": args.symbol, "model_type": args.model, "epochs": args.epochs},
    )
    if not enqueue.ok:
        print(f"FAIL enqueue deep-learning job: {enqueue.error}")
        if enqueue.payload is not None:
            print(json.dumps(enqueue.payload, indent=2))
        _print_failure_logs(job_payload=enqueue.payload, ai_root=ai_root)
        _ops_log(f"smoke-dl FAIL: enqueue failed: {enqueue.error}")
        return EXIT_FAIL

    job_id = _extract_job_id(enqueue.payload)
    if not job_id:
        print("FAIL enqueue deep-learning job: missing job id")
        print(json.dumps(enqueue.payload, indent=2))
        _print_failure_logs(job_payload=enqueue.payload, ai_root=ai_root)
        _ops_log("smoke-dl FAIL: missing job id")
        return EXIT_FAIL

    print(f"INFO queued job id={job_id}")
    _ops_log(f"Queued job: id={job_id}")
    deadline = time.time() + max(5, args.job_timeout)
    last_payload: Any = {}
    last_error = ""
    last_status = ""

    while time.time() < deadline:
        poll = http_json("GET", _endpoint(base_url, f"/api/jobs/{job_id}"), timeout=20)
        if not poll.ok:
            last_error = poll.error
            time.sleep(2)
            continue
        if not isinstance(poll.payload, dict):
            last_error = "job poll payload is not JSON object"
            time.sleep(2)
            continue

        last_payload = poll.payload
        state = _status_text(last_payload)

        if state != last_status:
            print(f"INFO job status: {state}")
            last_status = state

        if state in JOB_SUCCESS_STATES:
            model_path = last_payload.get("model_path") or last_payload.get("result", {}).get("model_path")
            print(f"PASS smoke-dl job completed (id={job_id}, status={state})")
            if model_path:
                print(f"     model_path: {model_path}")

            # Verify analysis endpoints after successful training
            print("INFO verifying analysis endpoints after training...")
            verify_ok, verify_lines = _verify_analysis_after_dl(base_url, args.symbol, ai_root)
            if verify_ok:
                print("PASS analysis verification")
                for line in verify_lines:
                    print(line)
            else:
                print("FAIL analysis verification after DL training")
                for line in verify_lines:
                    print(f"  {line}")
                _print_failure_logs(job_payload=last_payload, ai_root=ai_root)
                _ops_log(f"smoke-dl FAIL: analysis verification failed: {verify_lines}")
                return EXIT_FAIL

            _ops_log(f"smoke-dl PASS: job={job_id} status={state}")
            return EXIT_OK
        if state in JOB_FAILURE_STATES:
            error_text = _extract_job_error(last_payload) or "job failed"
            print(f"FAIL smoke-dl job failed (id={job_id}): {error_text}")
            print(json.dumps(last_payload, indent=2))
            _print_failure_logs(job_payload=last_payload, ai_root=ai_root)
            _ops_log(f"smoke-dl FAIL: job={job_id} error={error_text}")
            return EXIT_FAIL
        time.sleep(2)

    print(f"FAIL smoke-dl timeout after {args.job_timeout}s (id={job_id})")
    if last_error:
        print(f"Last poll error: {last_error}")
    if isinstance(last_payload, dict) and last_payload:
        print(json.dumps(last_payload, indent=2))
    _print_failure_logs(job_payload=last_payload, ai_root=ai_root)
    _ops_log(f"smoke-dl FAIL: timeout after {args.job_timeout}s job={job_id}")
    return EXIT_FAIL


def _add_connection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-url", default=None, help="Backend base URL, overrides --host/--port")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Backend host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Backend port")


def _add_force_arg(parser: argparse.ArgumentParser, default: bool = True) -> None:
    """Add --force flag. Default is True for dev mode (kill without hesitation)."""
    parser.add_argument(
        "--force",
        action="store_true",
        default=default,
        help="Force kill processes on port even if not allowlisted (default: True for dev mode)",
    )
    parser.add_argument(
        "--no-force",
        action="store_false",
        dest="force",
        help="Only kill allowlisted dpolaris_ai processes",
    )


def cmd_demo(args: argparse.Namespace) -> int:
    """Run full demo workflow: up -> smoke-metadata -> smoke-dl -> instructions."""
    base_url = _resolve_base_url(args)
    symbol = args.symbol
    epochs = args.epochs

    _ops_log(f"CMD demo: base_url={base_url} symbol={symbol} epochs={epochs}")

    print("=" * 60)
    print("dPolaris Demo Workflow")
    print("=" * 60)

    # Step 1: Bring up backend
    print("\n[1/3] Starting backend...")
    up_args = argparse.Namespace(
        base_url=getattr(args, "base_url", None),
        host=getattr(args, "host", DEFAULT_HOST),
        port=getattr(args, "port", DEFAULT_PORT),
        force=True,
        ai_root=getattr(args, "ai_root", None),
        timeout=args.up_timeout,
    )
    rc = cmd_up(up_args)
    if rc != EXIT_OK:
        print("\nFAIL demo: backend startup failed")
        return EXIT_FAIL
    print("")

    # Step 2: Run smoke-metadata
    print("[2/3] Running smoke-metadata...")
    metadata_args = argparse.Namespace(
        base_url=getattr(args, "base_url", None),
        host=getattr(args, "host", DEFAULT_HOST),
        port=getattr(args, "port", DEFAULT_PORT),
        symbols=f"{symbol},MSFT",
        timeout=30,
        verbose=False,
    )
    rc = cmd_smoke_metadata(metadata_args)
    if rc != EXIT_OK:
        print("\nFAIL demo: smoke-metadata failed")
        return EXIT_FAIL
    print("")

    # Step 3: Run smoke-dl
    print(f"[3/3] Running smoke-dl for {symbol}...")
    dl_args = argparse.Namespace(
        base_url=getattr(args, "base_url", None),
        host=getattr(args, "host", DEFAULT_HOST),
        port=getattr(args, "port", DEFAULT_PORT),
        symbol=symbol,
        model="lstm",
        epochs=epochs,
        timeout=30,
        job_timeout=args.dl_timeout,
        ai_root=getattr(args, "ai_root", None),
    )
    rc = cmd_smoke_dl(dl_args)
    if rc != EXIT_OK:
        print("\nFAIL demo: smoke-dl failed")
        return EXIT_FAIL

    # Success - print instructions
    print("")
    print("=" * 60)
    print("PASS Demo complete!")
    print("=" * 60)
    print("")
    print("Next steps:")
    print(f"  1. Open Java app (dPolaris Control Center)")
    print(f"  2. Go to Deep Learning tab")
    print(f"  3. Click Refresh")
    print(f"  4. Double-click {symbol} to view training artifacts")
    print("")
    print(f"Backend running at: {base_url}")
    print("To stop: ./run_ops down")
    print("")

    _ops_log("demo PASS")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ops", description="dPolaris macOS ops orchestrator CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_up = sub.add_parser("up", help="Ensure backend is healthy (start if needed)")
    _add_connection_args(p_up)
    _add_force_arg(p_up, default=True)
    p_up.add_argument("--ai-root", default=None, help="Path to dPolaris_ai repo")
    p_up.add_argument("--timeout", type=int, default=30, help="Seconds to wait for backend health after start")
    p_up.set_defaults(func=cmd_up)

    p_down = sub.add_parser("down", help="Stop backend and orchestrator")
    _add_connection_args(p_down)
    _add_force_arg(p_down, default=True)
    p_down.set_defaults(func=cmd_down)

    p_status = sub.add_parser("status", help="Print backend/orchestrator status")
    _add_connection_args(p_status)
    p_status.add_argument("--json", action="store_true", help="Print single JSON status object")
    p_status.set_defaults(func=cmd_status)

    p_smoke_fast = sub.add_parser("smoke-fast", help="Quick sanity checks")
    _add_connection_args(p_smoke_fast)
    p_smoke_fast.add_argument("--timeout", type=int, default=30, help="Seconds to wait for /health")
    p_smoke_fast.set_defaults(func=cmd_smoke_fast)

    p_smoke_universe = sub.add_parser("smoke-universe", help="Verify universe endpoints and non-empty payloads")
    _add_connection_args(p_smoke_universe)
    p_smoke_universe.add_argument("--timeout", type=int, default=30, help="Seconds to wait for /health")
    p_smoke_universe.set_defaults(func=cmd_smoke_universe)

    p_report_smoke = sub.add_parser("report-smoke", help="Generate and validate a multi-section analysis report")
    _add_connection_args(p_report_smoke)
    p_report_smoke.add_argument("--symbol", default="AAPL", help="Ticker symbol")
    p_report_smoke.add_argument("--timeout", type=int, default=30, help="Seconds to wait for /health")
    p_report_smoke.set_defaults(func=cmd_report_smoke)

    p_smoke_metadata = sub.add_parser("smoke-metadata", help="Test metadata and analysis endpoints")
    _add_connection_args(p_smoke_metadata)
    p_smoke_metadata.add_argument("--symbols", default="AAPL,MSFT", help="Comma-separated ticker symbols")
    p_smoke_metadata.add_argument("--timeout", type=int, default=30, help="Seconds to wait for /health")
    p_smoke_metadata.add_argument("--verbose", "-v", action="store_true", help="Print full JSON on errors")
    p_smoke_metadata.set_defaults(func=cmd_smoke_metadata)

    p_smoke_dl = sub.add_parser("smoke-dl", help="Run deep-learning smoke job")
    _add_connection_args(p_smoke_dl)
    p_smoke_dl.add_argument("--symbol", default="AAPL", help="Ticker symbol")
    p_smoke_dl.add_argument("--model", default="lstm", help="Model type")
    p_smoke_dl.add_argument("--epochs", type=int, default=1, help="Training epochs")
    p_smoke_dl.add_argument("--timeout", type=int, default=30, help="Seconds to wait for /health")
    p_smoke_dl.add_argument("--job-timeout", type=int, default=600, help="Seconds to wait for job completion")
    p_smoke_dl.add_argument("--ai-root", default=None, help="Path to dPolaris_ai repo for log fallback")
    p_smoke_dl.set_defaults(func=cmd_smoke_dl)

    p_demo = sub.add_parser("demo", help="Run full demo workflow (up + smoke-metadata + smoke-dl)")
    _add_connection_args(p_demo)
    p_demo.add_argument("--symbol", default="AAPL", help="Ticker symbol for DL training")
    p_demo.add_argument("--epochs", type=int, default=1, help="Training epochs")
    p_demo.add_argument("--up-timeout", type=int, default=30, help="Seconds to wait for backend startup")
    p_demo.add_argument("--dl-timeout", type=int, default=600, help="Seconds to wait for DL job completion")
    p_demo.add_argument("--ai-root", default=None, help="Path to dPolaris_ai repo")
    p_demo.set_defaults(func=cmd_demo)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
