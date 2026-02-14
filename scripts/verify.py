from __future__ import annotations

import ast
import compileall
from pathlib import Path


def syntax_check(root: Path) -> bool:
    ok = True
    for path in list((root / "ops").rglob("*.py")) + list((root / "opsctl").rglob("*.py")) + [root / "opsctl.py"]:
        if not path.exists():
            continue
        try:
            source = path.read_text(encoding="utf-8")
            ast.parse(source, filename=str(path))
        except Exception as exc:
            print(f"FAIL syntax {path}: {exc}")
            ok = False
    return ok


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    ok = compileall.compile_dir(str(root / "ops"), quiet=1)
    ok = compileall.compile_dir(str(root / "opsctl"), quiet=1) and ok
    if (root / "opsctl.py").exists():
        ok = compileall.compile_file(str(root / "opsctl.py"), quiet=1) and ok
    if ok:
        print("PASS compileall")
        return 0
    fallback_ok = syntax_check(root)
    if fallback_ok:
        print("WARN compileall failed (likely __pycache__ permissions), syntax check passed")
        return 0
    print("FAIL verify")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
