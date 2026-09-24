# CanFallingDetector

Offline Windows can-falling detection system for a variable-speed conveyor
(10,000–30,000 cans/hour). Built in validated stages per the project plan —
**this README reflects Stage 1 only.**

## Current build — hardened production version

**Architecture decision: ONNX Runtime only, no PyTorch in the packaged app.**
This is deliberate, not a workaround — PyTorch's Windows DLLs (`c10.dll`)
crashed repeatedly on the target production PC across multiple root
causes. Removing PyTorch from the production build eliminates that entire
class of failure rather than patching each instance. The app therefore:

- **Loads `.onnx` models only.** Selecting a `.pt` file shows a clear
  message explaining why, instead of crashing.
- If you retrain the model and get a new `.pt`, convert it to `.onnx`
  once (outside the packaged app — ask whoever maintains this project to
  do the conversion) and drop the resulting `.onnx` into `models/`.

**What works, tested against real files/hardware:**
- Image Test tab: load an image, run detection, see boxes/class/confidence
- Live Camera tab: pick a camera, Start/Stop, live detection overlay, FPS
- GOOD/REJECT counting with a configurable detection **zone** (top/bottom
  % sliders) — only detections inside that vertical band count, so
  stationary cans elsewhere in the camera's view don't get counted
- Automatic fault-image capture on every REJECT event, saved to
  `logs/fault_images/`
- Background model loading (window stays responsive)
- Camera-disconnect handling: after sustained read failures, detection
  stops cleanly with a clear message instead of crashing or hanging
- Inference-error handling: a bad frame or model error stops detection
  cleanly with a message instead of crashing
- All activity logged to `logs/app.log`, in addition to the on-screen log
- A global error handler: any unexpected error is logged and shown in a
  dialog instead of the app silently disappearing

## GPU acceleration (RTX 3050 production PC)

The app tries **DirectML** first (Windows' built-in GPU acceleration,
works with any DirectX 12 GPU — NVIDIA/AMD/Intel — no separate CUDA/cuDNN
install needed) and automatically falls back to CPU if unavailable or if
GPU initialization fails for any reason. This is deliberately not the
CUDA/cuDNN route — that would reintroduce a native-driver-dependency risk
similar to what caused the original PyTorch crashes, just with NVIDIA's
toolkit instead of PyTorch's.

The **Device** field in the Status panel shows which one is actually
active (`GPU (DirectML)` or `CPU`) — check this after loading the model on
the RTX 3050 PC to confirm GPU acceleration is really being used. If it
says CPU there, GPU drivers may need updating, or DirectML isn't
available on that Windows build; the app will still run correctly, just
without the speed benefit.

**Expected speed impact:** CPU-only inference measured ~10-14 FPS in
testing. A capable GPU via DirectML should be dramatically faster for a
small model like this (potentially 5-15x), which is the difference
between "borderline at 30,000 cans/hour" and comfortable headroom above
it — but this must be confirmed on the actual hardware, since DirectML
performance varies by GPU/driver and wasn't testable in this development
environment (Linux, no DirectX).

**Known, honestly-stated limitation:** there is still no true object
*tracking*. Counting uses a detection zone + a time-based cooldown, which
reduces but does not eliminate double-counting of the same physical can,
and can miss two genuinely separate cans that arrive within the cooldown
window. This is the single biggest remaining gap between this build and
an accurate production counter — closing it requires real tracking
(ByteTrack/BoT-SORT), which is a substantial follow-up piece of work, not
a setting to tune.

**Build pipeline note:** the GitHub Actions workflow also strips a set of
bundled Visual C++ runtime DLLs (`msvcp140.dll`, `vcruntime140.dll`, etc.)
that PyQt5 ships privately — these were shadowing the correctly-installed
system versions on the target PC and are believed to be the cause of the
final `onnxruntime` DLL failure encountered during testing.


Run it:
```
pip install -r requirements.txt
pip install torch --index-url https://download.pytorch.org/whl/cpu   # or cu121 for GPU, see requirements.txt
python app/main.py
```

Build the standalone `.exe` (run on Windows):
```
cd installer
build_exe.bat
```

## Important findings from testing against your actual files (read before Stage 2)

1. **Class names differ from the spec.** Your `best.pt` reports classes
   `{0: 'fall can', 1: 'Good can'}`, not `good_can` / `reject_can`. The app
   maps roles via `app/config.py` (`good_class_names` / `reject_class_names`)
   rather than hard-coding the string anywhere, so this is a one-line change
   if you rename classes in a future training run — but confirm the mapping
   is right: **is `fall can` really always a reject, or could it also mean
   "in the falling zone" for a can that's otherwise fine?** That distinction
   matters a lot for Stage 7's event logic.

2. **Detection coverage is incomplete at the spec'd settings.** At
   conf=0.25 / iou=0.50 / imgsz=640, the model found 5 of the ~11 visible
   cans in `1.jpg` (confidences 0.26–0.48) and 3 of ~10 in `2.jpg`
   (0.26–0.36). This is not an app bug — it's the model. Lowering confidence
   will surface more detections but also more false positives; before Stage
   5 (live camera) we should test whether this improves on cleaner,
   better-lit frames from an actual mounted camera, or whether the model
   needs more/better training data. I'm flagging this now rather than later.

3. **CPU inference is far too slow for this application.** On this
   machine's CPU, one image took **~1,500 ms** (~0.7 FPS). Your target is up
   to 8.33 cans/second. **This project needs a CUDA-capable NVIDIA GPU on
   the production PC** — CPU-only will not keep up at any of your target
   speeds, let alone 30,000 cans/hour. Please confirm what GPU (if any) is
   on the machine that will run this, since it changes both the torch
   install and realistic FPS expectations for Stage 4/5 validation.

4. **Video file (`good_2.mp4`) is workable**: 120 fps, 464×832, ~14s. At
   120 fps even your fastest target (8.33 cans/sec) gives ~14 frames per can
   — enough headroom for tracking, assuming inference keeps up (see #3).

## Project structure
```
CanFallingDetector/
├── app/
│   ├── main.py          # PyQt5 GUI - Stage 1: Image Test tab
│   ├── detector.py       # YOLO load/predict wrapper (shared by all future stages)
│   ├── draw.py            # Box/label rendering (shared by all future stages)
│   ├── config.py          # Settings load/save (config/settings.json)
│   └── _validate_headless.py  # Dev-only headless test script, not shipped
├── models/best.pt         # Your model (copied in for local testing)
├── config/                 # settings.json persists here
├── logs/                    # Event logs land here from Stage 8 onward
├── test_images/, test_videos/, output/, installer/
└── requirements.txt
```

## Not yet built (per the staged plan — do not skip ahead)
Video test mode (Stage 4), tracking (Stage 6), full ROI/event-line (Stage 7),
CSV export from the event log (Stage 8), configurable simulated output
pulses (Stage 9), PLC interface prep (Stage 10).

## Stage 5 + early Stage 8 — Live Camera with counting and fault images (added)

New "Live Camera" tab, in `app/camera_tab.py`, reusing `detector.py` and
`draw.py` unchanged (Requirement #9):
- Detects available USB cameras, lets you pick one, Start/Stop
- Live detection overlay with FPS + inference time
- GOOD / REJECT running counts, reject rate
- Saves an annotated image to `logs/fault_images/` automatically on every
  REJECT event (toggleable)

**Read this before trusting the counts on the real line:** there is no
object tracking yet (that's Stage 6). The same physical can crossing the
camera's view over several frames can be detected on more than one of
those frames. To reduce (not eliminate) this, a configurable time-based
cooldown is used per role (default 400ms) — after a GOOD or REJECT event
fires, further events of that role are ignored until the cooldown elapses.
This is a stopgap, not the real fix, and it has a real failure mode:
**at high can speeds/close spacing, a genuinely separate can arriving
within the cooldown window will be missed entirely.** Tune the cooldown
against your actual conveyor speed, and treat the counts as indicative
until Stage 6 (proper tracking) replaces this logic.

## Reject workflow: stop-and-latch until operator RESET (added)

New behavior on top of the existing Live Camera tab, in `app/camera_tab.py`
and the new `app/plc_output.py`:

- **Camera rotation** (0°/90°/180°/270°) — applied to every frame before
  detection, so detection works correctly regardless of camera mounting
  orientation.
- **Reject = stop, not just count.** When a REJECT is confirmed (inside the
  detection zone, same logic as before): the reject image is saved to a
  **configurable, auto-created folder** (only confirmed rejects are ever
  saved — never GOOD cans or routine frames), the **Last Reject** panel
  updates to show it, the simulated **PLC reject output latches ON**, and
  detection **stops** — no further frames are processed until the operator
  presses **RESET**.
- **RESET button** clears the latch: PLC output OFF, detection resumes.
- **PLC output is guaranteed OFF** at application startup and on
  application close (even if a reject is still latched when the window is
  closed) — see `closeEvent` in `main.py` and `SimulatedPLCOutput` in
  `plc_output.py`.
- The reject latch naturally prevents the same reject from firing multiple
  times: detection is fully paused while stopped, so there's nothing left
  to re-trigger until RESET explicitly resumes it.
- PLC calls and folder saves are wrapped so failures are logged, never
  crash the app (`plc_output.py`'s `reject_on`/`reject_off` never raise).

**Still simulated, not a real PLC connection** — `SimulatedPLCOutput` is
deliberately isolated so a real protocol (Siemens S7 / Modbus TCP /
digital I/O) can replace it later without touching detection or UI code;
that real integration is still Stage 10, not built yet.

## PLC target changed: Siemens LOGO! (replaces Schneider M241)

**This supersedes the "Schneider M241" sections below** (kept underneath,
clearly marked, as a historical record of that phase of the project --
not deleted, since it's still accurate about what was true at the time).
The PLC communication layer (`app/plc_modbus.py`) was swapped to talk to
a **Siemens LOGO! (8)** instead, per an explicit request to change only
the PLC communication and map two specific signals:

- **Q0.0** (first digital output) -- the reject actuator
- **I0.0** (first digital input) -- the physical reset button

**Why this looks different from the M241 version**: the M241 integration
assumed a PLC-side ladder/structured-text program computing a
status/event-ID/ack word scheme (word-based Modbus holding registers).
LOGO! has no such program for this -- it's just two plain bits (a coil for
Q0.0, a discrete input for I0.0). So the small state machine
(READY / WAITING_FOR_RESET) that used to live partly on the PLC now lives
entirely on the PC side, in `plc_modbus.py`. Every public signal and
method the rest of the app depends on is **unchanged** -- `camera_tab.py`
and `diagnostics_tab.py` needed no code changes at all; only
`plc_modbus.py`'s internals, the PLC Connection panel's two address
fields, `config.py`'s PLC address fields, and the Arduino test-rig sketch
(now speaking Modbus coils/discrete inputs instead of holding registers)
changed. See the module docstring at the top of `app/plc_modbus.py` for
the full technical explanation of the new behavior.

**Address mapping is NOT confirmed against a real LOGO! unit.** The
defaults (Q1 → coil address 0, I1 → discrete input address 0) are
standard/illustrative, not verified against your actual LOGO! Modbus
server configuration -- LOGO!'s exact address/function-code mapping
varies by firmware version and how "Network Inputs/Outputs" are
configured in LOGO! Soft Comfort. Confirm before going live.

**What was actually verified for this change, stated plainly:**
- The full state machine (connect → reject write → readback confirmation
  → WAITING_FOR_RESET → physical I0.0 reset → READY; the READY interlock;
  refusing a second reject while not ready; the "stale latched coil on
  reconnect" safety check; the diagnostic test being refused during a
  real reject) was exercised against an in-process simulated Modbus
  device in this session and behaves as designed -- every scenario above
  was actually run, not just written and assumed.
- **Not tested**: a real physical Siemens LOGO! unit, or real Ethernet
  hardware/cabling. Also not tested: the updated
  `arduino/CanFallingDetector_TestPLC.ino` sketch on physical Arduino
  hardware, for the same reason as before -- there's no way to compile or
  run Arduino code from this environment. It mirrors the same
  coil/discrete-input protocol logic verified in Python above; verify it
  on real hardware (e.g. with QModMaster or Modbus Poll) before trusting
  it, per `arduino/README.md`.

## Copyright notice (added)

An About dialog (Help → About, in the menu bar) now shows: *"Software
designed, directed, generated, and created by RAMI HAFIENE."* This is the
only UI addition made for this change -- no existing screen, layout, or
functionality was altered to add it.

---

**Everything below this point describes the earlier Schneider M241
integration phase of the project. It has been superseded by the Siemens
LOGO! integration above, but is kept as an accurate historical record of
that phase -- register addresses, the M241-specific handshake protocol,
and references to `%MW` words no longer apply to the current code.**

## Real PLC integration: Schneider M241 (TM241CE24T) via Modbus TCP (added)

New, on top of everything above, in `app/plc_modbus.py` (a completely
separate module from detection/UI code, per the architecture requirement)
and a "PLC Connection" panel added to the existing Live Camera tab:

- **PLC Mode selector**: "Simulated" (default, unchanged behavior, no
  hardware assumed) or "Modbus TCP (Schneider M241)". Switching modes
  never auto-connects to anything -- you explicitly click **Connect**.
- **Connect / Disconnect** buttons, IP/Port/Unit ID fields, and the three
  register-address fields (reject command, PLC status, reset/event
  status), all persisted to config.
- **PLC: CONNECTED / DISCONNECTED** and **PLC STATE: READY / REJECT_ACTIVE
  / WAITING_FOR_RESET** shown live in the UI, updated via Qt signals from
  a dedicated background thread -- the GUI never blocks on PLC I/O, even
  if the PLC is unreachable or slow to respond.
- **Automatic reconnection** on connection loss, with `PLC: DISCONNECTED`
  shown immediately and every comm error logged -- the app never claims a
  reject command was delivered unless the Modbus write actually succeeded
  without error.
- **The existing reject-detection code is completely unchanged.** The
  same `_latch_reject()` method from the previous update is what triggers
  this -- it now additionally calls `modbus_plc.request_reject()` when in
  Modbus mode, alongside (not instead of) the existing simulated-output
  and image-saving behavior, so nothing that worked before was altered.
- **Physical reset is the primary reset path**, exactly as specified: the
  background thread watches the configured reset/status register, and
  when it sees the PLC report its reset input is active, it calls the
  **same `on_reset()`** the existing software RESET button already used
  -- both paths are guaranteed identical in behavior. The existing
  software RESET button is kept (not removed) as a manual/local override.
- **No repeated triggers, tested explicitly**: a dedicated test proved
  the reset signal fires exactly once per reject cycle even when the
  PLC's reset register is held high for several seconds, and correctly
  fires again on a genuinely new reject cycle. An earlier version of this
  logic had a real bug here (it re-fired every poll cycle) -- caught and
  fixed before delivery, not left for you to discover.
- **Tested end-to-end against a real Modbus TCP server** (a local test
  PLC simulator), not just unit-tested in isolation: connect → reject
  detected → image saved → command delivered to the PLC's register →
  PLC status read back → physical reset observed → latch cleared →
  detection resumed. Every step of that chain was verified to actually
  work, not assumed.

### ⚠️ Register addresses are NOT confirmed against your real M241 program

The default addresses (illustrative: reject command / status / reset at
consecutive `%MW` words) come directly from the example in the
integration spec. **I have no access to your actual M241 project and did
not invent or guess real addresses for it.** Before connecting to the
real PLC:
1. Confirm the actual addresses your M241's EcoStruxure Machine Expert
   program uses for the reject command word, status word, and reset word.
2. Update them in the PLC Connection panel (or `config.py` defaults) to
   match.
3. Confirm the physical reset button is wired to a PLC input that your
   PLC program reflects into the reset/status word this app reads --
   this app only reads whatever register you configure; your PLC program
   is what actually latches the physical output and reports reset state.
4. The status-word convention this app assumes by default (0=READY,
   1=REJECT_ACTIVE, 2=WAITING_FOR_RESET) must match your PLC program, or
   be adjusted in `plc_modbus.py`'s `_interpret_status_word()`.

**The PLC-side ladder/structured-text program itself was not written or
verified here** -- only the PC-side Modbus TCP client. Per the spec's own
"before coding" instruction, I inspected and built on the existing
project rather than rebuilding it, but I want to be equally clear about
this boundary: the M241 program logic (latching the output, clearing it
on physical reset, exposing status/reset words) is real PLC-programming
work that still needs to happen in EcoStruxure Machine Expert, matching
whatever addresses you configure here.

## Production readiness hardening (added)

Full handshake, interlock, timeout, diagnostics, and traceability layer on
top of the existing PLC integration -- `app/plc_modbus.py` rewritten with
the handshake protocol, `app/diagnostics_tab.py` (new tab), event logging
in `camera_tab.py`. **The existing YOLO/camera detection code was not
touched by any of this.**

### What's implemented and tested (against a real Modbus TCP server, not just unit-tested)

| Spec requirement | Implementation | Verified by |
|---|---|---|
| #1 Handshake (event ID → PLC ack, not fire-and-forget) | `request_reject_event()` writes event ID + command, only reports success on matching ack read back | TEST 6, full handshake suite |
| #2 PLC ready interlock | `is_ready()` checked before every send; refused + logged if not READY | TEST 7 |
| #3 Unique reject event ID | Every reject gets a compact numeric ID (Modbus-safe) + full traceable string ID | TEST 6, event log |
| #4 Fail-safe Ethernet | Comm failures never crash the app; camera/YOLO verified to keep running during a live-killed PLC connection | TEST 8 |
| #5 Timeouts | Configurable `plc_ack_timeout_s`; unacknowledged events fire `reject_timeout`, never hang forever | dedicated timeout test |
| #6 Startup sync | On connect, first PLC state read is checked -- if PLC is already mid-cycle from a previous session, detection is paused locally rather than assumed READY | TEST 10 + TEST 10b (both scenarios tested) |
| #7 Non-blocking | All PLC I/O on a background QThread; GUI never blocks | inherent to architecture, re-verified after every change |
| #8 Reject image traceability | Filename + event log both carry the same event ID | TEST 6 |
| #9 Disk space protection | Checked before every save; warns in UI + log, never blocks/crashes on failure | dedicated low-disk-space test |
| #10 Configuration | All addresses/timeouts/intervals in config.py + PLC Connection panel, none hard-coded | — |
| #11 Diagnostics view | New "Diagnostics" tab -- connection, IP, state, disconnect count, last event, last error, full session event list | screenshot-verified |
| #12 Maintenance test | Isolated Test Read/Write buttons on a separate test register -- proven never to touch the reject command register | dedicated isolation test |
| #13 Safe shutdown | Best-effort command-register clear on stop/close, wrapped so it can never block shutdown | existing shutdown tests re-run, still pass |

### A real bug caught and fixed during this work, not left for you to find
The reset-edge-detection logic (carried over from the previous update)
combined with a naive first pass at the *connection-lost* signal had a
gap: if the PLC was unreachable from the very first connection attempt,
the UI would stay stuck on "CONNECTING..." forever instead of showing
DISCONNECTED (it only emitted on True→False transitions, not on an
initial failure). Caught via TEST 8, fixed, and covered by a dedicated
regression test (`_has_signaled_status` in `plc_modbus.py`).

### TEST 9 (duplicate protection after retry) -- how it's actually guaranteed
A pending reject event holds one `(event_id, write_done, sent_at)` tuple.
If the event-ID/command write fails partway, the retry on the next poll
cycle reuses the *same* event_id -- a new one is never generated while one
is still pending. Verified by writing the event ID register once, then
polling it across several retry cycles before acknowledging, confirming
it never changes.

## Honest final status: **NOT READY FOR PRODUCTION** -- here's exactly why

Per the spec's own completion requirement, I'm not declaring this
"READY FOR PRODUCTION," and here's precisely what's still missing before
it could be:

1. **The M241 ladder/structured-text program itself does not exist yet.**
   Everything above is the PC-side Modbus TCP client, tested against a
   *software simulator* standing in for the PLC. The actual M241 program
   -- implementing the status/ack/reset word conventions this app
   expects, latching the physical output, clearing it on physical reset
   -- has not been written or verified on real Schneider hardware. This
   is real PLC programming work in EcoStruxure Machine Expert.
2. **Register addresses are illustrative, not confirmed.** They must be
   set to match whatever the real M241 program actually uses.
3. **Never tested against real Ethernet hardware/cabling** -- only
   loopback (127.0.0.1) in this environment. Real network conditions
   (latency, a managed switch, cable faults) haven't been exercised.
4. **The physical reject mechanism and physical reset button** are
   PLC-side wiring/hardware that this software assumes exists correctly.
5. Detection accuracy/tracking limitations documented earlier in this
   README (no true object tracking yet) still apply and are unrelated to,
   but still relevant alongside, this PLC work.

**What I can state confidently:** every piece of software behavior
described in the spec -- handshake, interlock, timeout, no-duplicate,
fail-safe disconnect, startup sync, diagnostics, maintenance test, safe
shutdown -- has been implemented and verified to actually work, against a
real (if software-simulated) Modbus TCP PLC, not just written and assumed
correct. The gap to production is real PLC-side commissioning work, not
unverified PC-side code.

## Arduino Mega test rig (added — testing/simulation only, while waiting on the real M241)

New: the PLC Connection panel now has a **Connection** selector —
**Ethernet (Modbus TCP)** (the M241) or **USB Serial (e.g. Arduino test
rig)**. Both use the exact same handshake/interlock/timeout logic in
`plc_modbus.py` — only the underlying transport differs (`ModbusTcpClient`
vs `ModbusSerialClient`, both from `pymodbus`), so everything already
tested against the M241 path (handshake, ready interlock, timeouts,
duplicate protection, startup sync) applies identically over serial.

**Tested for real**: the PC-side serial transport was validated end-to-end
over an actual serial link (a virtual PTY pair standing in for the USB
connection), using a hand-written Modbus RTU slave in Python as the
reference target — full handshake, event ID delivery, acknowledgment, and
physical-reset-detection all confirmed working over the real wire
protocol, not just assumed.

**Not tested**: the actual `arduino/CanFallingDetector_TestPLC.ino` sketch
itself, since I have no way to compile or run Arduino code here. It
mirrors the same protocol logic that *was* validated in Python, and I
found and fixed one real issue on review (an unbounded response buffer
that could overflow on a large read request) before finalizing it — but
verify it on real hardware per `arduino/README.md` before trusting it.

See `arduino/README.md` for wiring, upload instructions, independent
verification steps, and the register map.

## Dual PLC connections: M241 + Arduino as interchangeable hardware equivalents (added)

You can now enable **Ethernet (M241)** and **USB Serial (Arduino)**
independently, including **both at once** -- they're treated as two
interchangeable views of the same PLC I/O, not separate control systems:

- **Arduino only**: develop and trial the complete reject/reset logic now,
  without waiting on the M241.
- **M241 only**: same logic, same code path, once the real PLC arrives.
- **Both at once**: a reject event is sent to every enabled+ready
  connection; an acknowledgment from *either* completes the event; a
  physical reset from *either* resumes detection. This is what lets you
  add the M241 later with minimal changes -- just tick its checkbox
  alongside (or instead of) the Arduino's.

**Synchronization check, not blind trust**: while both are connected, the
app compares their reported states every poll cycle. If they genuinely
disagree (e.g. Ethernet says READY while Serial says WAITING_FOR_RESET),
a visible warning appears rather than silently picking one -- this is
your signal that the two aren't actually wired to reflect the same
physical process yet.

**Tested for real, not just written**: a dedicated test (`/tmp` scripts
used during development, scenario reproducible on request) ran an actual
TCP test server and an actual serial-protocol test slave simultaneously,
proving: both connect together, a reject reaches both, either can
acknowledge independently (including one arriving late after the other
already completed the event, without corrupting the record), physical
reset from either resumes detection, and a forced disagreement between
the two is correctly flagged.

Every existing single-connection behavior (M241-only, Arduino-only) was
re-verified after this change and still passes unchanged.
