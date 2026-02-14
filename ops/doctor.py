from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .report import write_report_json_txt
from .tickets import generate_tickets


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="dPolaris ops doctor")
    parser.add_argument("--base-url", required=True, help="Backend base URL")
    parser.add_argument("--symbol", default="AAPL", help="Training symbol")
    parser.add_argument("--model-type", default="lstm", help="Model type")
    parser.add_argument("--epochs", type=int, default=1, help="Epochs")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout seconds for job polling")
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        from .checks import run_checks
    except ModuleNotFoundError as exc:
        print("Dependency missing. Install requirements first:")
        print("  .\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt")
        print(f"Import error: {exc}")
        return 2

    report_data: dict[str, Any] = run_checks(
        base_url=args.base_url,
        symbol=args.symbol,
        model_type=args.model_type,
        epochs=args.epochs,
        timeout_seconds=args.timeout,
    )

    tickets = generate_tickets(report_data)
    report_data["tickets"] = tickets

    json_path, txt_path = write_report_json_txt(report_data)

    print("Doctor finished.")
    print(f"JSON report: {json_path}")
    print(f"Text report: {txt_path}")
    if tickets:
        print("Tickets:")
        for ticket in tickets:
            print(f"- {ticket}")

    print("Summary:")
    print(json.dumps(report_data.get("summary", {}), indent=2))

    ok = bool(report_data.get("summary", {}).get("ok"))
    return 0 if ok else 1


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
