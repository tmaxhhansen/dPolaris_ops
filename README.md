# dPolaris Ops Runner (Windows)

This repo provides a small runner for backend lifecycle control and smoke tests from outside `dpolaris_ai`.

## Requirements

- Windows PowerShell
- Python 3.11+
- `requests` installed in this repo environment (or `urllib` fallback is used automatically)
- Backend repo at `C:\my-git\dpolaris_ai`

## Main command (Python)

```powershell
python -m ops.main <subcommand>
```

Supported subcommands:

- `start-backend`
- `stop-backend`
- `restart-backend`
- `smoke`

## Windows wrapper

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\ops.ps1 start-backend
powershell -ExecutionPolicy Bypass -File .\scripts\ops.ps1 stop-backend
powershell -ExecutionPolicy Bypass -File .\scripts\ops.ps1 restart-backend
powershell -ExecutionPolicy Bypass -File .\scripts\ops.ps1 smoke
```

Optional smoke arguments:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\ops.ps1 smoke --symbol AAPL --model lstm --epochs 1 --timeout 30 --job-timeout 600
```

## Safety behavior

- If port `8420` is in use, runner inspects PID/command line.
- It only terminates owner when command line clearly matches `dpolaris_ai` server (`cli.main server` + `C:\my-git\dpolaris_ai`).
- Otherwise it refuses to kill and exits with a clear message.

## Smoke behavior

- Waits for `GET /health`
- Calls `GET /api/status`
- Calls `GET /api/universe/list` (warn only on failure)
- Triggers tiny deep-learning job:
  - `POST /api/jobs/deep-learning/train` with `AAPL/lstm/epochs=1`
  - polls `GET /api/jobs/{id}` until completion/failure/timeout

## Logs

Logs are written inside this repo only:

- `.\.ops_logs\ops_YYYYMMDD.log`
- `.\.ops_logs\backend.pid` (local runner pid tracking)
