# dPolaris Ops Runner (Windows)

This repo provides a standalone ops runner for backend lifecycle control and smoke testing without editing `dpolaris` or `dpolaris_ai`.

## Paths assumed

- Backend repo: `C:\my-git\dpolaris_ai`
- Java repo: `C:\my-git\dpolaris`
- Backend host/port: `127.0.0.1:8420`

## Quickstart

```powershell
cd C:\my-git\dPolaris_ops
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## CLI usage

Main command:

```powershell
.\.venv\Scripts\python.exe -m opsctl <command>
```

Commands:

```powershell
.\.venv\Scripts\python.exe -m opsctl status
.\.venv\Scripts\python.exe -m opsctl start-backend
.\.venv\Scripts\python.exe -m opsctl stop-backend
.\.venv\Scripts\python.exe -m opsctl restart-backend
.\.venv\Scripts\python.exe -m opsctl smoke
```

Smoke options:

```powershell
.\.venv\Scripts\python.exe -m opsctl smoke --symbol AAPL --model lstm --epochs 1 --timeout 30 --job-timeout 600
```

## Windows one-click launchers

- `scripts\start_backend.bat`
- `scripts\restart_backend.bat`
- `scripts\smoke.bat`

These launchers use this repo's venv Python if available, otherwise `py`/`python`.

## Safety behavior

When port `8420` is already in use, `opsctl` inspects owner PID/command line.
It only terminates the owner if command line matches the expected backend process (`cli.main server` and `C:\my-git\dpolaris_ai`).
Otherwise it refuses to kill and exits with a clear error.

## Logging

`opsctl` writes logs inside this repo:

- `.\.ops_logs\ops_YYYYMMDD.log`
- `.\.ops_logs\opsctl_backend.pid`

Backend process output is redirected to:

- `%USERPROFILE%\dpolaris_data\logs\ops_backend.log`

## Validation

Run:

```powershell
.\.venv\Scripts\python.exe .\scripts\verify.py
```

Or:

```powershell
py -3 .\scripts\verify.py
```
