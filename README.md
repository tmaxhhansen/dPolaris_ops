# dPolaris Ops (Windows)

This repo provides lightweight operational testing tools for `dpolaris_ai` and `dpolaris`.

## One-command smoke test

Double-click:

- `scripts\smoke.cmd`

Or run from PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\smoke.ps1
```

The smoke runner:

- checks backend venv at `C:\my-git\dpolaris_ai\.venv\Scripts\python.exe`
- ensures `GET /health` is healthy (starts backend server if needed)
- validates:
  - `GET /health`
  - `GET /api/status`
  - `GET /api/deep-learning/status`
- enqueues tiny job:
  - `POST /api/jobs/deep-learning/train` with `AAPL/lstm/epochs=1`
  - polls `GET /api/jobs/{id}` until success/failed/timeout
- prints clear PASS/FAIL and exits `0/1`
- writes log to `dPolaris_ops\logs\smoke_YYYYMMDD_HHMMSS.log`

If port `8420` is LISTENING but `/health` is failing, it prints the owner PID and command-line hint.

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
