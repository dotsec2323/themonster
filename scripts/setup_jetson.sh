#!/usr/bin/env bash
#
# setup_jetson.sh
# ----------------
# One-time setup for running CanFallingDetector on an NVIDIA Jetson Orin
# Nano with GPU-accelerated inference (TensorRT/CUDA via onnxruntime-gpu).
#
# WHAT THIS SCRIPT DOES AUTOMATICALLY:
#   - Installs PyQt5 via apt (not pip -- see requirements-jetson.txt)
#   - Checks whether OpenCV is already present system-wide (it usually is,
#     often CUDA-enabled, on JetPack images) instead of overwriting it
#   - Creates a .venv with --system-site-packages so both are visible
#   - Installs numpy/pymodbus/pyserial from pip
#   - Detects your JetPack/L4T version and tells you exactly where to get
#     the matching onnxruntime-gpu wheel
#
# WHAT THIS SCRIPT DELIBERATELY DOES NOT DO:
#   - It does NOT install onnxruntime-gpu for you. There is no single
#     correct wheel across JetPack versions -- picking the wrong one is
#     worse than not picking one, because get_available_providers() can
#     report a provider that doesn't actually have working GPU kernels for
#     your board. This is a manual, one-time step -- see the printed
#     instructions at the end of this script and JETSON.md.
#
# Usage:
#   cd CanFallingDetector
#   bash scripts/setup_jetson.sh
#
set -euo pipefail

if [ ! -f "app/main.py" ]; then
    echo "ERROR: run this script from the CanFallingDetector project root" \
         "(the folder containing app/main.py)." >&2
    exit 1
fi

echo "==> Installing system packages (PyQt5 via apt)..."
sudo apt update
sudo apt install -y python3-venv python3-pip python3-pyqt5 v4l-utils

echo "==> Checking for a system OpenCV install..."
if python3 -c "import cv2" >/dev/null 2>&1; then
    CV2_VER=$(python3 -c "import cv2; print(cv2.__version__)")
    echo "    Found system OpenCV ${CV2_VER} -- leaving it as-is (JetPack's"
    echo "    build is often CUDA-enabled; do not overwrite it with pip)."
    HAVE_CV2=1
else
    echo "    No system OpenCV found -- will install opencv-python-headless"
    echo "    via pip instead (headless avoids a known Qt-plugin conflict"
    echo "    with PyQt5 on Linux)."
    HAVE_CV2=0
fi

echo "==> Creating virtual environment (.venv) with access to system packages..."
python3 -m venv --system-site-packages .venv

echo "==> Installing remaining Python packages into .venv..."
.venv/bin/pip install --upgrade pip
.venv/bin/pip install numpy pymodbus pyserial
if [ "$HAVE_CV2" -eq 0 ]; then
    .venv/bin/pip install opencv-python-headless
fi

echo ""
echo "==> Detecting JetPack / L4T version..."
if [ -f /etc/nv_tegra_release ]; then
    L4T_INFO=$(cat /etc/nv_tegra_release)
    echo "    $L4T_INFO"
else
    L4T_INFO="(could not read /etc/nv_tegra_release -- is this really a Jetson?)"
    echo "    $L4T_INFO"
fi
PYVER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "    Python version: $PYVER"

cat <<EOF

========================================================================
MANUAL STEP REQUIRED: install onnxruntime-gpu
========================================================================
No general-purpose PyPI wheel of onnxruntime-gpu supports Jetson's
aarch64 + CUDA + TensorRT combination -- you need one built for your
EXACT JetPack/L4T version and Python version (detected above).

1. Go to the Jetson Zoo page and find the row matching your JetPack
   version:
       https://elinux.org/Jetson_Zoo#ONNX_Runtime
   (For very recent JetPack/CUDA 13 releases, Ultralytics also hosts
   matching wheels -- see https://docs.ultralytics.com/guides/nvidia-jetson/)

2. Download the matching .whl file onto this Jetson, then:
       .venv/bin/pip install /path/to/onnxruntime_gpu-<version>-<tag>.whl

3. Verify it actually exposes GPU providers:
       .venv/bin/python3 -c "import onnxruntime as ort; print(ort.get_available_providers())"
   You should see TensorrtExecutionProvider and/or CUDAExecutionProvider
   in the list.

4. The real proof is running the app: load your model on the Image Test
   tab, then check the reported device (detector.active_device_label())
   on screen -- it will say "GPU (TensorRT)" or "GPU (CUDA)" only once a
   GPU actually ran the inference, not just because a provider is listed.
========================================================================

Once onnxruntime-gpu is installed, run the app with:
    .venv/bin/python3 app/main.py
EOF
