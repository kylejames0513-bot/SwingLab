#!/usr/bin/env bash
# Idempotent Cloud Agent setup for SwingLab / CaddieInsight.
# Safe to run repeatedly: apt is idempotent, the venv is only created when
# missing, and the pose-model/Chromium downloads no-op once cached.
set -euo pipefail

cd "$(dirname "$0")/.."

# System packages: ffmpeg drives the video pipeline; mediapipe's native library
# links the GL ES stack even in CPU mode; DejaVu supplies image-label fonts;
# python3-venv provides ensurepip for the project virtualenv.
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    python3-venv ffmpeg libgles2 libegl1 libgl1 fonts-dejavu-core

# Project virtualenv. It is gitignored, so it survives the snapshot and the
# fresh git checkout that happens when a new agent boots from a build.
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate

python -m pip install --upgrade pip

# Editable install with dev (pytest/httpx/playwright) and web (FastAPI) extras.
pip install -e ".[dev,web]"

# Chromium plus its system libraries for the Playwright browser tests.
python -m playwright install --with-deps chromium

# Pre-cache the mediapipe pose model so the first analysis starts warm instead
# of downloading ~6 MB on demand (cached in swinglab/models/, gitignored).
python -c "from swinglab.pose import ensure_model; ensure_model()"

echo "SwingLab environment ready."
