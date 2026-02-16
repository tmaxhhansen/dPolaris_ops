# dPolaris Ops

Operational testing and management tools for `dpolaris_ai` backend.

## Quick Start (macOS)

From `~/my-git/dPolaris_ops`:

```bash
# Bring up backend (auto-kills stale processes on port 8420)
./run_ops up

# Bring down backend
./run_ops down

# Check status
./run_ops status

# Quick smoke test (health + status endpoints)
./run_ops smoke-fast

# Universe smoke test (list + nasdaq300/wsb100/combined non-empty)
./run_ops smoke-universe

# Multi-section report smoke test (generate + validate artifact persistence)
./run_ops report-smoke --symbol AAPL

# Deep-learning smoke test
./run_ops smoke-dl --symbol AAPL --epochs 1 --timeout 600
```

Or using Python module directly:

```bash
python -m ops.main up
python -m ops.main down
python -m ops.main status
python -m ops.main smoke-fast
python -m ops.main smoke-universe
python -m ops.main report-smoke --symbol AAPL
python -m ops.main smoke-dl --symbol AAPL --model lstm --epochs 1 --job-timeout 600
```

## Commands

### `up`

Ensures backend is healthy. If port 8420 is occupied, kills the owner process and starts fresh.

```bash
./run_ops up [--timeout 30] [--ai-root PATH] [--force] [--no-force]
```

Options:
- `--timeout`: Seconds to wait for backend health (default: 30)
- `--ai-root`: Path to dpolaris_ai repo (auto-detected by default)
- `--force`: Kill any process on port 8420 (default: True for dev mode)
- `--no-force`: Only kill allowlisted dpolaris_ai processes

### `down`

Stops backend and orchestrator processes.

```bash
./run_ops down [--force] [--no-force]
```

Options:
- `--force`: Kill any process on port 8420 (default: True for dev mode)
- `--no-force`: Only kill allowlisted dpolaris_ai processes

### `status`

Shows current backend and orchestrator status.

```bash
./run_ops status [--json]
```

Options:
- `--json`: Output machine-readable JSON

### `smoke-fast`

Quick sanity check: verifies `/health` and `/api/status` endpoints.

```bash
./run_ops smoke-fast [--timeout 30]
```

### `smoke-universe`

Verifies universe endpoints return non-empty data:
- `GET /api/universe/list`
- `GET /api/universe/nasdaq300`
- `GET /api/universe/wsb100`
- `GET /api/universe/combined`

```bash
./run_ops smoke-universe [--timeout 30]
```

### `report-smoke`

Generates an LLM-free analysis report and validates persistence:
- `POST /api/analyze/report?symbol=AAPL`
- checks required report headings:
  - `Overview`
  - `Price/Volume Snapshot`
  - `Technical Indicators`
  - `Chart Patterns`
  - `Model Signals`
  - `News`
  - `Risk Notes`
  - `Next Steps`
- verifies artifact APIs:
  - `GET /api/analysis/list`
  - `GET /api/analysis/{id}`

```bash
./run_ops report-smoke [--symbol AAPL] [--timeout 30]
```

### `smoke-dl`

Deep-learning smoke test: submits a training job and polls until completion.

```bash
./run_ops smoke-dl [--symbol AAPL] [--model lstm] [--epochs 1] [--timeout 30] [--job-timeout 600]
```

Options:
- `--symbol`: Ticker symbol (default: AAPL)
- `--model`: Model type (default: lstm)
- `--epochs`: Training epochs (default: 1)
- `--timeout`: Health check timeout (default: 30s)
- `--job-timeout`: Job completion timeout (default: 600s)

Output includes:
- Deep-learning device info (cpu/mps/cuda)
- Job status updates
- Model path on success
- Last 100 logs on failure

## Process Ownership (macOS)

The ops tool detects and manages processes on port 8420:

```bash
# Inspect port owner
lsof -nP -iTCP:8420 -sTCP:LISTEN
ps -p <PID> -o command=
```

### Safe-kill Rules

By default (`--force`), ops will kill any process on port 8420 in dev mode.

With `--no-force`, only processes matching the allowlist are killed:
- Command must contain `-m cli.main server`
- Command must contain `dpolaris_ai` (case-insensitive)

If a non-allowlisted process is found with `--no-force`, the command fails with a clear error.

## Logs

Ops logs are written to:

```
~/my-git/dPolaris_ops/.ops_logs/ops_YYYYMMDD.log
```

Backend logs are written to:

```
~/dpolaris_data/logs/backend_YYYYMMDD_HHMMSS.log
```

## Exit Codes

- `0`: Success (PASS)
- `2`: Failure (FAIL)

## Example Output

### `up`

```text
PASS backend already healthy at http://127.0.0.1:8420
```

or

```text
INFO stopped backend pid(s): 12345
PASS backend healthy at http://127.0.0.1:8420 (pid=67890, elapsed=3.2s)
```

### `down`

```text
INFO no running orchestrator process found
INFO stopped backend pid(s): 12345
PASS down complete
```

### `status`

```text
Status @ 2025-02-14T19:30:00+00:00
Base URL: http://127.0.0.1:8420
Backend health: healthy (healthy)
Port owners:
  pid=12345 safe=True cmd=/path/to/python -m cli.main server --host 127.0.0.1 --port 8420
Orchestrator running: False
Overall ok: True
```

### `smoke-dl`

```text
INFO Deep-learning device: mps
     torch=True cuda=False mps=True
INFO queued job id=abc123
INFO job status: running
INFO job status: completed
PASS smoke-dl job completed (id=abc123, status=completed)
     model_path: /path/to/model.pt
```

## Windows Usage

For Windows, use the PowerShell scripts in `scripts/` or the `src/ops_cli.py` tool.

See the original sections below for Windows-specific documentation.

---

## Legacy: Backend deep-learning smoke runner (Windows)

Run from `C:\my-git\dPolaris_ops`:

```powershell
python ops_smoke.py
```

## Legacy: Ops CLI (Windows one-liner)

```powershell
.\.venv\Scripts\python.exe .\src\ops_cli.py smoke --url http://127.0.0.1:8420 --symbol AAPL --model lstm --epochs 1
```

## Legacy: Doctor tool

```powershell
.\.venv\Scripts\python.exe -m ops.doctor --base-url http://127.0.0.1:8420 --symbol AAPL --model-type lstm --epochs 1 --timeout 300
```

Reports are written to `%USERPROFILE%\dpolaris_data\reports\`.
