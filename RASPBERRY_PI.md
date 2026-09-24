# Running CanFallingDetector on a Raspberry Pi 5

This covers getting the app running natively on **Raspberry Pi OS (64-bit)**
on a **Raspberry Pi 5**. It does not need Windows, PyInstaller, or any code
changes beyond what's already in this repo — `requirements-rpi.txt` and
`scripts/` here are the Pi-specific pieces.

## Hardware

- **Raspberry Pi 5** (4GB or 8GB). Active cooling (official Active Cooler
  or equivalent) is recommended — CPU-only inference will run the cores
  harder than typical desktop use.
- **microSD card**, 32GB+ (A2-rated recommended), or boot from USB SSD for
  better sustained I/O — the reject-event log and fault images are written
  continuously during production.
- **Camera**: a **USB webcam** is the simplest path — `cv2.VideoCapture`
  already works unchanged on Linux (see "Camera" section below). A
  Raspberry Pi Camera Module (CSI ribbon) also works but needs one extra
  adapter step, also covered below.
- **PLC connection**: unchanged either way —
  - Modbus **TCP** (e.g. the Schneider M241): the Pi's Ethernet port.
  - Modbus **serial** (e.g. an Arduino test rig): USB — shows up as
    `/dev/ttyUSB0` or `/dev/ttyACM0`, not a `COM` port.
- A monitor (HDMI) if running it as an attended HMI, or set up VNC if you'd
  rather access it remotely.

## 1. Install

```bash
git clone <your-repo-url> CanFallingDetector   # or copy the folder over
cd CanFallingDetector
bash scripts/setup_raspberry_pi.sh
```

The script installs PyQt5 and OpenCV via `apt` (far more reliable on a Pi
than building them with `pip`), creates a `.venv`, and installs the
remaining packages (`onnxruntime`, `pymodbus`, `pyserial`) from
`requirements-rpi.txt`. It finishes by importing everything and printing
the active `onnxruntime` providers so you can confirm the install worked
before going further.

**Do not use `requirements.txt`** on the Pi — that one pulls in
`onnxruntime-directml`, which is Windows-only and won't install on
Linux/ARM at all. Use `requirements-rpi.txt` (the setup script already
does this for you).

## 2. Run it

```bash
.venv/bin/python3 app/main.py
```

Load your `best.onnx` model on the Image Test tab as usual, or point
`model_path` in `config/settings.json` at it directly.

## 3. GPU acceleration — there isn't any, and that's expected

`onnxruntime-directml` (used on Windows for GPU acceleration via DirectML)
only works on Windows. The Pi's GPU has no ONNX Runtime execution
provider, so inference runs on the CPU cores only — `detector.py` already
handles this automatically: it only tries `DmlExecutionProvider` if that
package is installed, and falls back to `CPUExecutionProvider` otherwise.
No code changes are needed for this; the Diagnostics tab will simply show
**CPU** as the active device.

**Benchmark before relying on this in production.** Load your actual
`best.onnx` on the Image Test tab and check the processing-time readout.
Whether CPU-only inference on a Pi 5 is fast enough depends on your
model's size and how fast cans move on your line — there's no universal
answer here.

## 4. Camera

**USB webcam (recommended, no changes needed):** `camera_tab.py` already
selects the right OpenCV backend per OS
(`cv2.CAP_DSHOW` on Windows, auto/V4L2 on Linux) — just plug it in, click
**Detect Cameras** on the Live Camera tab, and select it.

**Raspberry Pi Camera Module (CSI ribbon):** this is *not* a standard
V4L2 device the same way a USB camera is, so `cv2.VideoCapture(0)` may not
see it directly. Two options:
- Simplest: enable the legacy camera stack's V4L2 compatibility layer
  (`sudo raspi-config` → Interface Options → Legacy Camera, or
  `libcamerify` on newer Pi OS), which exposes it as a normal `/dev/video*`
  device `cv2.VideoCapture` can open.
- Or: capture frames with `picamera2` and feed them into
  `CanDetector.predict()` directly instead of going through
  `cv2.VideoCapture` — ask me if you want this wired in; it's a small,
  contained change localized to the camera-open code in `camera_tab.py`,
  nothing else in the app needs to change.

## 5. Serial port for the PLC/Arduino connection

On the Live Camera tab's PLC Connection panel, the serial port field now
defaults to `/dev/ttyUSB0` automatically when not running on Windows. Run
`ls /dev/tty*` (or `v4l-utils`' cousin `dmesg | grep tty` right after
plugging in) to confirm the actual device name — it may come up as
`/dev/ttyACM0` instead, depending on the USB-serial chip.

## 6. Run it automatically on boot

Two options — pick one:

### Option A — Desktop autostart (simplest)

Best if the Pi boots straight to the desktop with auto-login (the default
for a dedicated HMI Pi).

```bash
mkdir -p ~/.config/autostart
cp scripts/canfallingdetector.desktop ~/.config/autostart/
```

Edit the `Exec=` and `Path=` lines in that file first if your project
isn't at `/home/pi/CanFallingDetector`. The app will now launch
automatically every time the desktop session starts.

### Option B — systemd service (auto-restart on crash + logging)

Better if you want the app supervised — automatically restarted if it
ever crashes, with logs in `journalctl`.

```bash
sudo cp scripts/canfallingdetector.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now canfallingdetector.service
journalctl -u canfallingdetector.service -f    # view logs
```

Edit the `User=`, `WorkingDirectory=`, and `ExecStart=` paths in that file
first if your setup differs from the defaults (`pi` user,
`/home/pi/CanFallingDetector`). This still requires an active graphical
session (auto-login to the desktop) since it's a GUI app.

## 7. What did NOT need to change

Worth knowing, since it explains why the Pi port is this small:

- Detection logic, PLC/Modbus handshake, reject/reset workflow,
  Rejection History, Diagnostics, and the whole UI are all plain
  cross-platform Python/Qt — none of it is Windows-specific.
- File paths already use `os.path.join` throughout — no hardcoded
  backslashes anywhere.
- The camera-open code already branches on `os.name` for the capture
  backend.
- `pymodbus`/`pyserial` (Modbus TCP and serial communication with the
  PLC) are pure Python and work identically on Linux.

The only genuinely Windows-only piece was the `onnxruntime-directml`
package choice — everything above exists to work around *that*, not
around anything in the app's own logic.
