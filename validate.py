"""Pre-launch validation hook for Servy — runs before GG starts.

Checks syntax and module imports. Exits 0 on success, 1 on failure.
Servy calls this before starting the service — if it fails, GG won't start
with broken code after a bad self-evolution.
"""

import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).parent


def main() -> int:
    py_files = list(PROJECT_DIR.glob("*.py"))
    modules_dir = PROJECT_DIR / "modules"
    if modules_dir.exists():
        py_files.extend(modules_dir.glob("*.py"))

    failed = False
    for f in py_files:
        r = subprocess.run(
            [sys.executable, "-m", "py_compile", str(f)],
            capture_output=True, text=True,
            timeout=30,
        )
        if r.returncode != 0:
            print(f"FAIL: {f.name} — {r.stderr.strip()}", file=sys.stderr)
            failed = True

    if failed:
        print("Pre-launch validation FAILED (syntax)", file=sys.stderr)
        return 1

    # Import check — verify module discovery works
    r = subprocess.run(
        [sys.executable, "-c",
         "import modules; modules.discover(); print('Modules:', modules.get_module_names())"],
        cwd=str(PROJECT_DIR),
        capture_output=True, text=True,
        timeout=30,
    )
    if r.returncode != 0:
        print(f"FAIL: module discovery — {r.stderr.strip()}", file=sys.stderr)
        return 1

    print(f"OK: {len(py_files)} files checked, {r.stdout.strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
