# dPolaris Ops Runner (Windows)

Standalone Windows-first tooling to manage and test `dpolaris_ai` from this repo only.

## Quickstart

```powershell
cd C:\my-git\dPolaris_ops
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## One-command flow

From one Windows PowerShell window:

```powershell
.\ops.bat up
.\ops.bat smoke
.\ops.bat down
```

## Commands

`ops.bat` and `scripts\ops.ps1` call `python -m ops.main`.

Available commands:

- `status`: prints health, port owner PID, command line, and managed/safe state.
- `up`: starts backend only when unhealthy.
- `smoke`: checks `/health`, `/api/status`, `/api/universe/list` (warn-only), and runs a tiny deep-learning train job.
- `down`: stops backend only if process command line matches expected `dpolaris_ai` server.

Direct Python usage:

```powershell
.\.venv\Scripts\python.exe -m ops.main status
.\.venv\Scripts\python.exe -m ops.main up
.\.venv\Scripts\python.exe -m ops.main smoke --symbol AAPL --model lstm --epochs 1 --timeout 30 --job-timeout 600
.\.venv\Scripts\python.exe -m ops.main down
```

## Logging

All runner logs are written under this repo:

- `.\.ops_logs\ops_YYYYMMDD.log`
- `.\.ops_logs\backend.pid`
