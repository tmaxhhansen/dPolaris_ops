from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any


def reports_root() -> Path:
    home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    return home / "dpolaris_data" / "reports"


def ensure_report_dirs() -> tuple[Path, Path]:
    root = reports_root()
    tickets = root / "tickets"
    root.mkdir(parents=True, exist_ok=True)
    tickets.mkdir(parents=True, exist_ok=True)
    return root, tickets


def write_report_json_txt(report_data: dict[str, Any]) -> tuple[Path, Path]:
    root, _ = ensure_report_dirs()
    json_path = root / "doctor_report.json"
    txt_path = root / "doctor_report.txt"

    json_path.write_text(json.dumps(report_data, indent=2, ensure_ascii=False), encoding="utf-8")
    txt_path.write_text(format_report_text(report_data), encoding="utf-8")
    return json_path, txt_path


def format_report_text(report_data: dict[str, Any]) -> str:
    lines: list[str] = []
    now = datetime.now().isoformat(timespec="seconds")
    lines.append("dPolaris Ops Doctor Report")
    lines.append(f"Generated: {now}")
    lines.append(f"Base URL: {report_data.get('base_url')}")
    lines.append("")

    summary = report_data.get("summary", {})
    lines.append(f"Overall OK: {summary.get('ok')}")
    lines.append(f"Reason: {summary.get('reason')}")

    classifications = report_data.get("classifications", [])
    lines.append("")
    lines.append("Classifications:")
    if classifications:
        for c in classifications:
            lines.append(f"- {c}")
    else:
        lines.append("- none")

    lines.append("")
    lines.append("Checks:")
    for c in report_data.get("checks", []):
        lines.append(
            f"- {c.get('name')}: {'PASS' if c.get('ok') else 'FAIL'} | {c.get('endpoint')} | "
            f"HTTP={c.get('status_code')} | {c.get('details')}"
        )

    job = report_data.get("job", {})
    lines.append("")
    lines.append("Deep Learning Job:")
    lines.append(f"- job_id: {job.get('job_id')}")
    lines.append(f"- final_status: {job.get('final_status')}")
    lines.append(f"- error: {job.get('error')}")
    lines.append(f"- poll_count: {job.get('poll_count')}")

    tickets = report_data.get("tickets", [])
    lines.append("")
    lines.append("Tickets:")
    if tickets:
        for t in tickets:
            lines.append(f"- {t}")
    else:
        lines.append("- none")

    return "\n".join(lines) + "\n"
