# dPolaris Runbook (macOS)

Quick reference for running the dPolaris stack locally on macOS.

## Prerequisites

- Python 3.11+ (3.12 recommended)
- Java 17+ (for control center)
- Gradle 8+

## One-Time Setup

### 1. Set up dpolaris_ai venv (REQUIRED)

```bash
cd ~/my-git/dpolaris_ai
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Verify installation:

```bash
.venv/bin/python -c "import fastapi, uvicorn, torch; print('OK')"
```

### 2. Build Java control center (optional)

```bash
cd ~/my-git/dpolaris
./gradlew build
```

## Quick Start: Demo Workflow

The fastest way to verify everything works:

```bash
cd ~/my-git/dPolaris_ops
./run_ops demo
```

This runs:
1. `up` - starts the backend
2. `smoke-metadata` - tests metadata/analysis endpoints
3. `smoke-dl` - trains a model and verifies artifacts

## Daily Operations

All commands from `~/my-git/dPolaris_ops`:

```bash
# Start backend (auto-kills stale port 8420)
./run_ops up

# Stop backend
./run_ops down

# Check status
./run_ops status

# Quick smoke (health check)
./run_ops smoke-fast

# Metadata + analysis endpoint tests
./run_ops smoke-metadata --symbols AAPL,MSFT

# DL smoke test (includes analysis verification)
./run_ops smoke-dl --symbol AAPL --epochs 1 --job-timeout 600

# Full demo workflow
./run_ops demo --symbol AAPL --epochs 1
```

## Command Reference

### `smoke-metadata`

Tests the stock metadata and analysis endpoints.

```bash
./run_ops smoke-metadata [--symbols AAPL,MSFT] [--verbose]
```

Verifies:
- `GET /api/stocks/metadata?symbols=...` returns valid JSON with:
  - `sector` (string or null)
  - `market_cap` (numeric or null)
  - `avg_volume_7d` (numeric or null)
- `GET /api/analysis/last?symbols=...` returns valid JSON with:
  - `change_percent_1d` (numeric or null)
  - `last_analysis_at` (timestamp or null)

### `smoke-dl`

Runs a deep-learning training job and verifies the analysis detail flow.

```bash
./run_ops smoke-dl [--symbol AAPL] [--epochs 1] [--job-timeout 600]
```

After training completes, verifies:
- `GET /api/analysis/last?symbols={symbol}` returns non-null data
- `GET /api/analysis/detail/{symbol}` returns `artifacts` array with >= 1 item

### `demo`

Full workflow for developers:

```bash
./run_ops demo [--symbol AAPL] [--epochs 1] [--dl-timeout 600]
```

Runs: `up` → `smoke-metadata` → `smoke-dl` → prints next steps for Java app.

## Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `DPOLARIS_AI_ROOT` | Override dpolaris_ai path | `~/my-git/dpolaris_ai` |
| `DPOLARIS_PYTHON` | Override Python executable | `/path/to/python` |
| `DPOLARIS_DEVICE` | Force DL device | `auto`, `cpu`, `mps`, `cuda` |

## Manual Backend Start

If ops tool isn't working:

```bash
cd ~/my-git/dpolaris_ai
.venv/bin/python -m cli.main server --host 127.0.0.1 --port 8420
```

## Troubleshooting

### "missing backend python"

The venv isn't set up. Run:

```bash
cd ~/my-git/dpolaris_ai && bash bootstrap_env.sh
```

### Port 8420 in use

```bash
lsof -nP -iTCP:8420 -sTCP:LISTEN
kill -9 <PID>
```

Or use ops to force-kill:

```bash
./run_ops down --force
./run_ops up
```

### Health check fails

Check backend logs:

```bash
ls -lt ~/dpolaris_data/logs/*.log | head -1 | xargs tail -50
```

### DL smoke fails

1. Ensure torch is installed: `.venv/bin/python -c "import torch; print(torch.__version__)"`
2. Check device: `.venv/bin/python -c "import torch; print('mps' if torch.backends.mps.is_available() else 'cpu')"`

### Analysis verification fails after DL

If `smoke-dl` passes training but fails analysis verification:
1. Check that the backend properly saves training artifacts
2. Verify `/api/analysis/detail/{symbol}` endpoint is implemented
3. Check backend logs for errors during artifact persistence

## Exit Codes

- `0`: PASS
- `2`: FAIL

## Logs

- Ops logs: `~/my-git/dPolaris_ops/.ops_logs/ops_*.log`
- Backend logs: `~/dpolaris_data/logs/backend_*.log`

## API Endpoints Tested

| Command | Endpoints |
|---------|-----------|
| `smoke-fast` | `/health`, `/api/status` |
| `smoke-metadata` | `/api/stocks/metadata`, `/api/analysis/last` |
| `smoke-dl` | `/api/jobs/deep-learning/train`, `/api/jobs/{id}`, `/api/analysis/last`, `/api/analysis/detail/{symbol}` |
