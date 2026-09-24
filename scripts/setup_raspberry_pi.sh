#!/usr/bin/env bash
#
# setup_raspberry_pi.sh
# ----------------------
# One-time setup for running CanFallingDetector on a Raspberry Pi 5
# (Raspberry Pi OS, 64-bit / Bookworm or newer).
#
# WHY apt FOR PyQt5 AND OpenCV, NOT pip:
# `pip install PyQt5` on a Pi often has no prebuilt ARM64 wheel for the
# exact Python version shipped with Raspberry Pi OS, so pip falls back to
# compiling PyQt5 from source -- which can take well over an hour on a Pi
# and frequently fails on missing system libraries. Raspberry Pi OS's own
# apt packages (python3-pyqt5, python3-opencv) are prebuilt for the Pi,
# install in seconds, and are what this script uses. The venv is created
# with --system-site-packages so those apt packages are visible inside it,
# and pip is only used for the handful of packages apt doesn't carry
# (onnxruntime, pymodbus). See requirements-rpi.txt for the full list and
# why onnxruntime (not onnxruntime-directml) is used here.
#
# Usage:
#   cd CanFallingDetector
#   bash scripts/setup_raspberry_pi.sh
#
set -euo pipefail

if [ ! -f "app/main.py" ]; then
    echo "ERROR: run this script from the CanFallingDetector project root" \
         "(the folder containing app/main.py)." >&2
    exit 1
fi

echo "==> Updating apt and installing system packages..."
sudo apt update
sudo apt install -y \
    python3-venv python3-pip \
    python3-pyqt5 \
    python3-opencv \
    libatlas-base-dev \
    v4l-utils

echo "==> Creating virtual environment (.venv) with access to apt's PyQt5/OpenCV..."
python3 -m venv --system-site-packages .venv

echo "==> Installing the remaining Python packages into .venv..."
# --no-deps on the two apt-provided packages' cousins is unnecessary here
# since requirements-rpi.txt simply lists them for documentation; pip will
# see PyQt5/opencv are already importable via --system-site-packages and
# either skip or lightly upgrade them. If pip tries to build PyQt5 from
# source here, stop and check that python3-pyqt5 actually installed above.
.venv/bin/pip install --upgrade pip
.venv/bin/pip install onnxruntime pymodbus pyserial numpy

echo "==> Verifying imports..."
.venv/bin/python3 -c "
import PyQt5, cv2, onnxruntime, pymodbus, serial
print('PyQt5 OK')
print('OpenCV OK:', cv2.__version__)
print('onnxruntime OK:', onnxruntime.__version__, '- providers:', onnxruntime.get_available_providers())
print('pymodbus OK')
print('pyserial OK:', serial.__version__)
"

echo ""
echo "==> Setup complete."
echo ""
echo "Run the app with:"
echo "    .venv/bin/python3 app/main.py"
echo ""
echo "To have it start automatically on boot, see scripts/canfallingdetector.service"
echo "and RASPBERRY_PI.md."
