"""Windows environment setup and self-check script for OCR Workbench."""

from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent


def main() -> int:
    if sys.version_info < (3, 10):
        raise SystemExit("Python 3.10 or newer is required.")

    python = ROOT / ".venv" / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
    if (ROOT / ".venv").exists() and not python.exists():
        raise SystemExit("Existing .venv is incomplete or for another OS. Remove or rename it first.")

    if not python.exists():
        print("Creating virtual environment (.venv)...")
        venv.EnvBuilder(with_pip=True).create(ROOT / ".venv")

    def run(*args: str) -> None:
        subprocess.run([str(python), *args], cwd=ROOT, check=True)

    print("Installing dependencies from requirements.txt...")
    run("-m", "pip", "install", "-r", "requirements.txt")
    run("-m", "pip", "check")

    print("Pre-warming RapidOCR models...")
    run("-c", "from ocr_pipeline import get_ocr_engine; get_ocr_engine(); print('RapidOCR ready')")

    print("Running tests...")
    run("-m", "pytest", "-q")
    print("\n[SUCCESS] OCR Workbench setup and verification completed. Run run.cmd to start.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"Setup failed at command {exc.cmd} (exit {exc.returncode}).")
