"""
Build script for Windows: produces a single signed `.exe` via PyInstaller.

Usage (from this folder, with a venv that has pyinstaller + asn1crypto):
    python build_windows.py

Output: dist/esignagent-host.exe
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
ENTRY = ROOT / "run_host.py"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
SPEC = ROOT / "esignagent-host.spec"


def main() -> int:
    # Use the running interpreter so the script works without `pyinstaller` on PATH
    # (typical when invoked via venv as `.venv/Scripts/python build_windows.py`).
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--noconsole",  # no extra console window — Native Messaging uses stdin/stdout binary streams
        "--name", "esignagent-host",
        "--clean",
        str(ENTRY),
    ]

    # Clean previous artefacts
    for p in (DIST, BUILD, SPEC):
        if p.exists():
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()

    print("Running:", " ".join(cmd))
    rc = subprocess.call(cmd, cwd=str(ROOT))
    if rc != 0:
        return rc

    exe = DIST / "esignagent-host.exe"
    print(f"\nBuilt: {exe} ({exe.stat().st_size // 1024} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
