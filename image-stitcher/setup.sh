#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python download_samples.py
if [[ "${1:-}" != "--sift-only" ]]; then
  .venv/bin/python -m pip install 'torch==2.15.0+cpu' 'torchvision==0.29.2+cpu' --index-url https://download.pytorch.org/whl/cpu
  .venv/bin/python -m pip install -r requirements-learned.txt
  .venv/bin/python learned.py
fi
.venv/bin/python -m pytest -q
