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
.\ops.bat smoke-fast
.\ops.bat smoke-dl
.\ops.bat smoke
.\ops.bat down
```

## Commands

`ops.bat` and `scripts\ops.ps1` call `python -m ops.main`.

Available commands:

- `status`: prints health, port owner PID, command line, and managed/safe state.
- `up`: hard-takeover mode. Kills anything listening on port `8420` (up to 3 rounds), then starts backend.
- `down`: hard-takeover mode. Kills anything listening on `8420`, then clears tracked pid file.
- `smoke-fast`: checks `/health`, `/api/status`, and `/api/universe/list`.
- `smoke-dl`: runs `smoke-fast`, submits deep-learning job (`AAPL`, `lstm`, `epochs=1`), polls job, and prints last logs.
- `smoke`: alias for `smoke-dl`.

Direct Python usage:

```powershell
.\.venv\Scripts\python.exe -m ops.main status
.\.venv\Scripts\python.exe -m ops.main up
.\.venv\Scripts\python.exe -m ops.main smoke-fast --timeout 30
.\.venv\Scripts\python.exe -m ops.main smoke-dl --symbol AAPL --model lstm --epochs 1 --job-timeout 600 --tail-logs 20
.\.venv\Scripts\python.exe -m ops.main smoke --symbol AAPL --model lstm --epochs 1 --job-timeout 600 --tail-logs 20
.\.venv\Scripts\python.exe -m ops.main down
```

## Logging

All runner logs are written under this repo:

- `.\.ops_logs\ops_YYYYMMDD.log`
- `.\.ops_logs\backend.pid`
- `.\.ops_logs\backend_stdout.log`
