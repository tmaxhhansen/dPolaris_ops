# dPolaris Ops Doctor (Windows)

This repo provides a lightweight tester that validates a running backend at `http://127.0.0.1:8420` and emits reports + tickets.

## Layout

- `ops/doctor.py`: CLI entrypoint (`python -m ops.doctor`)
- `ops/checks.py`: ordered checks and classification logic
- `ops/report.py`: report file generation
- `ops/tickets.py`: Codex ticket generation
- `scripts/run_doctor.ps1`: Windows helper script

## Setup (PowerShell)

```powershell
cd C:\my-git\dPolaris_ops
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Run doctor

```powershell
.\.venv\Scripts\python.exe -m ops.doctor --base-url http://127.0.0.1:8420 --symbol AAPL --model-type lstm --epochs 1 --timeout 300
```

Or use helper script:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_doctor.ps1
```

## Output files

Reports are written to:

`%USERPROFILE%\dpolaris_data\reports\`

- `doctor_report.json`
- `doctor_report.txt`
- `tickets\codex1_<timestamp>.txt` (when backend action needed)
- `tickets\codex2_<timestamp>.txt` (reserved for java-side issues)

## Example console output

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

## Check order

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
