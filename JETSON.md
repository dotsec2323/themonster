# Running CanFallingDetector on an NVIDIA Jetson Orin Nano

This replaces the Raspberry Pi 5 as the deployment target when you need
real GPU-accelerated inference to keep up with fast line speeds (e.g. the
~8 cans/second case that CPU-only inference on a Pi can't reliably cover
-- see the notes below on why). The Jetson Orin Nano has an actual onboard
NVIDIA GPU (1024 CUDA cores), used here via ONNX Runtime's TensorRT/CUDA
execution providers.

## Why this instead of the Raspberry Pi

At high line speeds, CPU-only inference (Pi 5 without an accelerator)
often can't process enough frames per can to catch every one reliably. The
Jetson Orin Nano gives you real GPU inference -- benchmarks for
YOLOv8-class models on Orin Nano with TensorRT are typically well into the
hundreds of FPS range at 640x640, which comfortably covers fast lines.
**Still benchmark your actual `best.onnx` model before relying on this in
production** -- exact numbers depend on your model size and TensorRT
optimization.

## Hardware

- **Jetson Orin Nano** (Developer Kit or a production module + carrier
  board), running **JetPack** (NVIDIA's Ubuntu-based Linux image, ARM64).
- **Storage**: a fast NVMe SSD is strongly recommended over the microSD
  path some Jetson kits ship with -- both for JetPack itself and for the
  continuously-written reject-event log/fault images.
- **Camera**: a **USB webcam** is the simplest path (see "Camera" below).
- **PLC connection**: identical to the Pi -- Ethernet for Modbus TCP (e.g.
  the M241), USB for Modbus serial (e.g. an Arduino test rig), shows up as
  `/dev/ttyUSB0` / `/dev/ttyACM0`.

## 1. Install

```bash
git clone <your-repo-url> CanFallingDetector   # or copy the folder over
cd CanFallingDetector
bash scripts/setup_jetson.sh
```

This installs PyQt5 via `apt`, detects (and leaves alone) JetPack's
usually-CUDA-enabled system OpenCV, creates a `.venv`, and installs
`numpy`/`pymodbus`/`pyserial`. **It deliberately stops short of installing
`onnxruntime-gpu` for you** -- read on.

### The one manual step: `onnxruntime-gpu`

There is no generic PyPI wheel of `onnxruntime-gpu` for Jetson's
aarch64 + CUDA + TensorRT combination. You need a wheel built for your
**exact JetPack/L4T version and Python version**, or it may install "successfully"
while reporting GPU providers that don't actually have working kernels for
this board's GPU architecture.

`scripts/setup_jetson.sh` detects and prints your JetPack/L4T version at
the end of the run, along with where to get the matching wheel:

1. **Jetson Zoo** (eLinux.org) — community-maintained wheels covering most
   JetPack versions: https://elinux.org/Jetson_Zoo#ONNX_Runtime
2. **Ultralytics' hosted wheels** — for recent JetPack/CUDA 13 releases:
   https://docs.ultralytics.com/guides/nvidia-jetson/

Download the matching `.whl` onto the Jetson and:
```bash
.venv/bin/pip install /path/to/onnxruntime_gpu-<version>-<tag>.whl
```

Then verify:
```bash
.venv/bin/python3 -c "import onnxruntime as ort; print(ort.get_available_providers())"
```
You should see `TensorrtExecutionProvider` and/or `CUDAExecutionProvider`
in the list. **Providers being listed isn't proof they work** — the wheel
can list a provider at compile time without having a usable kernel for
your specific GPU. The real proof is running the app.

## 2. Run it

```bash
.venv/bin/python3 app/main.py
```

Load `best.onnx` on the Image Test tab. The app already knows how to use
TensorRT/CUDA with **no code changes needed on your part** —
`detector.py`'s provider selection tries, in order:
`TensorrtExecutionProvider` → `CUDAExecutionProvider` → `DmlExecutionProvider`
(Windows only) → `CPUExecutionProvider`, falling back automatically at
each step if a provider fails to actually load. Whichever one is *really*
running your model shows up as the reported device
(`detector.active_device_label()`) — e.g. **"GPU (TensorRT)"** — on the
Image Test / Diagnostics screens. If it still says "CPU" after installing
`onnxruntime-gpu`, the wheel doesn't have working kernels for this board;
double check it's matched to your exact JetPack version.

**First inference after loading a model will be noticeably slower** than
the rest — TensorRT builds/caches an optimized engine for your exact model
+ input shape the first time it runs, then subsequent frames are fast.
This is expected, not a bug; don't judge steady-state FPS from the first
frame.

## 3. Camera

Same guidance as the Pi: a **USB webcam** works unchanged —
`camera_tab.py` already picks the right OpenCV backend per OS, so just
plug it in and use **Detect Cameras** on the Live Camera tab.

If you'd rather use a Jetson CSI camera module (e.g. IMX219/IMX477 on the
15-pin connector), it isn't a plain V4L2 device the way a USB camera is —
it needs a GStreamer pipeline string (`nvarguscamerasrc ! ...`) passed to
`cv2.VideoCapture(...)`, which is a different code path from the current
`cv2.VideoCapture(index)` call. This is a small, contained change local to
the camera-open code in `camera_tab.py` if you want it — ask and I'll wire
it in; nothing else in the app would need to change.

## 4. Serial port for the PLC/Arduino connection

Same as the Pi — the serial port field already defaults to `/dev/ttyUSB0`
on any non-Windows machine (`config.py`). Confirm the actual device name
with `ls /dev/tty*` after plugging it in; it may enumerate as
`/dev/ttyACM0` depending on the USB-serial chip.

## 5. Run it automatically on boot

Same two options documented for the Pi in `RASPBERRY_PI.md` — both files
are OS/board-agnostic, just edit the paths inside them for this machine:

- **`scripts/canfallingdetector.desktop`** — simplest, autostarts with the
  desktop session. Copy to `~/.config/autostart/`.
- **`scripts/canfallingdetector.service`** — systemd option with
  auto-restart-on-crash and `journalctl` logging. Edit `User=`,
  `WorkingDirectory=`, and `ExecStart=` to match this machine's username
  and path, then install to `/etc/systemd/system/`.

## 6. What did NOT need to change

Same story as the Pi port, and for the same reason — the detection logic,
PLC/Modbus handshake, reject/reset workflow, Rejection History,
Diagnostics, and UI are all plain cross-platform Python/Qt. The only
genuinely platform-specific piece is which `onnxruntime` package gets
installed and which execution provider it exposes — `detector.py` already
handles trying each one and falling back safely, so nothing there needed
to change to add Jetson support either; it only needed the provider list
extended (`TensorrtExecutionProvider` / `CUDAExecutionProvider`), which is
already done in this repo.
