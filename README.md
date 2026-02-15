# dPolaris Ops

This repo provides lightweight operational testing tools for `dpolaris_ai` and `dpolaris`.

## Mac Ops usage

Run from `~/my-git/dPolaris_ops`:

```bash
python -m ops.main up
python -m ops.main down
python -m ops.main status
python -m ops.main status --json
python -m ops.main smoke-fast
python -m ops.main smoke-dl --symbol AAPL --model lstm --epochs 1 --timeout 30 --job-timeout 600
```

Command behavior:

- `up`: ensures backend is healthy; starts backend if needed.
- `down`: stops backend and orchestrator (idempotent).
- `status`: friendly status output.
- `status --json`: prints one JSON object to stdout for machine parsing.
- `smoke-fast`: quick `/health` + `/api/status` checks.
- `smoke-dl`: enqueues one deep-learning train job and polls until done or timeout.

### Troubleshooting port 8420

`down` and `up` inspect the listener on `8420` with:

```bash
lsof -nP -iTCP:8420 -sTCP:LISTEN
ps -p <PID> -o command=
```

Safe-kill rule on macOS:

- Kill is allowed only if command contains `-m cli.main server` **and** contains `dPolaris_ai` (case-insensitive path match).
- If the listener does not match that allowlist, the command does not kill it and returns a clear error.

## Backend deep-learning smoke runner

Run from `C:\my-git\dPolaris_ops`:

```powershell
python ops_smoke.py
```

Expected success output:

```text
Smoke Summary
- job_id: <uuid>
- status: completed
- model_path: <path or (none)>
- error: (none)
```

Expected failure output:

```text
FAIL: backend is not healthy after 30s (...)
```

or

```text
Smoke Summary
- job_id: <uuid>
- status: failed
- model_path: (none)
- error: <error details>
```

## Ops CLI (Windows one-liner)

```powershell
.\.venv\Scripts\python.exe .\src\ops_cli.py smoke --url http://127.0.0.1:8420 --symbol AAPL --model lstm --epochs 1
```

Other commands:

```powershell
.\.venv\Scripts\python.exe .\src\ops_cli.py health --url http://127.0.0.1:8420
.\.venv\Scripts\python.exe .\src\ops_cli.py wait-healthy --url http://127.0.0.1:8420 --timeout 30
.\.venv\Scripts\python.exe .\src\ops_cli.py start-backend --ai-root C:\my-git\dpolaris_ai
.\.venv\Scripts\python.exe .\src\ops_cli.py stop-backend
```

CLI logs are written to `.\logs\ops_cli_YYYYMMDD.log`.

## How to run smoke

From `C:\my-git\dPolaris_ops`:

```powershell
powershell -ExecutionPolicy Bypass -File .\smoke\smoke.ps1
```

Optional JSON output:

```powershell
powershell -ExecutionPolicy Bypass -File .\smoke\smoke.ps1 -Json
```

The smoke runner validates:

1. `GET /health` (retry up to timeout)
2. `GET /api/status`
3. `GET /api/universe/list` (fallback to `/api/scan/universe/list`, WARN if both unavailable)
4. `POST /api/jobs/deep-learning/train` then polls `GET /api/jobs/{id}` for completion/failure

Exit codes:

- `0`: PASS (WARN allowed)
- `2`: FAIL (one or more failing checks)

JSON output location when `-Json` is used:

- `.\out\smoke_result.json`

## Doctor tool

## Layout

- `ops/doctor.py`: CLI entrypoint (`python -m ops.doctor`)
- `ops/checks.py`: ordered checks and classification logic
- `ops/report.py`: report file generation
- `ops/tickets.py`: Codex ticket generation
- `scripts/run_doctor.ps1`: Windows helper script

### Setup (PowerShell)

```powershell
cd C:\my-git\dPolaris_ops
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Run doctor

```powershell
.\.venv\Scripts\python.exe -m ops.doctor --base-url http://127.0.0.1:8420 --symbol AAPL --model-type lstm --epochs 1 --timeout 300
```

Or use:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_doctor.ps1
```

### Output files

Reports are written to:

`%USERPROFILE%\dpolaris_data\reports\`

- `doctor_report.json`
- `doctor_report.txt`
- `tickets\codex1_<timestamp>.txt` (when backend action needed)
- `tickets\codex2_<timestamp>.txt` (reserved for java-side issues)

### Example console output

```text
Doctor finished.
JSON report: C:\Users\you\dpolaris_data\reports\doctor_report.json
Text report: C:\Users\you\dpolaris_data\reports\doctor_report.txt
Summary:
{
  "ok": false,
  "reason": "issues detected"
}
```

### Check order

1. `GET /health`
2. `GET /api/status`
3. `GET /api/deep-learning/status`
4. `POST /api/jobs/deep-learning/train`
5. Poll `GET /api/jobs/{job_id}` every 2 seconds

Classifications include:

- `BACKEND_DOWN`
- `DL_JOB_TIMEOUT`
- `MISSING_TORCH`
- `API_CONTRACT_INCONSISTENT`
