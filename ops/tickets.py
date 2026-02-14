from __future__ import annotations

from datetime import datetime
from pathlib import Path
import subprocess
from typing import Any

from .report import ensure_report_dirs


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _collect_netstat_8420() -> str:
    try:
        proc = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            check=False,
        )
        lines = [line for line in (proc.stdout or "").splitlines() if ":8420" in line]
        if not lines:
            return "(no netstat entries for :8420)"
        return "\n".join(lines[:40])
    except Exception as exc:
        return f"(failed to collect netstat output: {exc})"


def _write_ticket(prefix: str, content: str) -> Path:
    _, tickets_dir = ensure_report_dirs()
    path = tickets_dir / f"{prefix}_{_timestamp()}.txt"
    path.write_text(content, encoding="utf-8")
    return path


def generate_tickets(report_data: dict[str, Any]) -> list[str]:
    created: list[str] = []
    classes = set(report_data.get("classifications", []))
    job = report_data.get("job", {})
    base_url = report_data.get("base_url", "http://127.0.0.1:8420")

    if "BACKEND_DOWN" in classes:
        netstat = _collect_netstat_8420()
        content = (
            "Ticket for Codex #1 (backend)\n"
            "Title: Backend down on localhost:8420\n\n"
            f"Doctor classification: BACKEND_DOWN\n"
            f"Base URL: {base_url}\n"
            "Symptom: GET /health failed before other checks.\n\n"
            "Observed netstat output for :8420:\n"
            f"{netstat}\n\n"
            "Requested fix:\n"
            "- Verify backend/orchestrator process ownership for port 8420.\n"
            "- Ensure backend responds at GET /health.\n"
            "- If stale process is holding the port, stop it and restart using project venv.\n"
        )
        created.append(str(_write_ticket("codex1", content)))

    if "MISSING_TORCH" in classes:
        content = (
            "Ticket for Codex #1 (backend)\n"
            "Title: Deep-learning job failed due to missing torch\n\n"
            f"Doctor classification: MISSING_TORCH\n"
            f"Endpoint: POST {base_url}/api/jobs/deep-learning/train\n"
            f"Job ID: {job.get('job_id')}\n"
            f"Error: {job.get('error')}\n\n"
            "Requested fix:\n"
            "- Install torch in the backend runtime/venv.\n"
            "- Confirm /api/deep-learning/status reports torch/cuda availability flags.\n"
            "- Re-run doctor to verify job reaches status=success.\n"
        )
        created.append(str(_write_ticket("codex1", content)))

    if "API_CONTRACT_INCONSISTENT" in classes:
        content = (
            "Ticket for Codex #1 (backend)\n"
            "Title: API contract inconsistency detected\n\n"
            f"Doctor classification: API_CONTRACT_INCONSISTENT\n"
            f"Base URL: {base_url}\n"
            "Symptom: One or more endpoints returned unexpected JSON structure or non-JSON payload.\n"
            "Expected endpoints:\n"
            "- GET /api/status\n"
            "- GET /api/deep-learning/status\n"
            "- POST /api/jobs/deep-learning/train (returns job id)\n"
            "- GET /api/jobs/{job_id} (returns job status)\n\n"
            "Requested fix:\n"
            "- Normalize response payloads to stable JSON objects.\n"
            "- Ensure job-start response always includes job id.\n"
            "- Ensure job-poll response always includes status field.\n"
        )
        created.append(str(_write_ticket("codex1", content)))

    # Placeholder for future Java-side contract issues.
    if "JAVA_FIX_NEEDED" in classes:
        content = (
            "Ticket for Codex #2 (java)\n"
            "Title: Java-side follow-up required\n\n"
            "Doctor reported JAVA_FIX_NEEDED classification.\n"
        )
        created.append(str(_write_ticket("codex2", content)))

    return created
