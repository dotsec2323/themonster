# Arduino Mega Test Rig — Setup & Verification

This stands in for the two Modbus points the app uses on a real **Siemens
LOGO!** PLC (Q0.0 reject output, I0.0 reset input) **for testing only**,
while you wait on the real hardware. It uses the Arduino Mega's USB
connection directly (Modbus RTU over serial) — no Ethernet shield needed.

## ⚠️ Important — verify this before trusting it with the app

`CanFallingDetector_TestPLC.ino` was written carefully to the standard
Modbus RTU protocol spec, and the same coil/discrete-input logic is what
`plc_modbus.py` uses against a real LOGO! over Ethernet — same code path,
different transport.

**What could NOT be done here: compile or run this .ino file on actual
Arduino hardware.** There's no AVR toolchain or physical board access in
this environment. Verify it independently before relying on it — see
"Verify independently" below.

## Wiring

| Arduino Mega pin | Connect to |
|---|---|
| Pin 7 | LED (with resistor) or relay module — stands in for the physical reject mechanism (Q0.0) |
| Pin 2 | Pushbutton, other side to GND — stands in for the physical reset button (I0.0) |
| USB | Straight to the PC running CanFallingDetector.exe |

The sketch uses `INPUT_PULLUP` on pin 2, so no external resistor is
needed for the button — just button-to-GND.

## Upload

1. Open `CanFallingDetector_TestPLC.ino` in the Arduino IDE
2. Select **Tools → Board → Arduino Mega or Mega 2560**
3. Select the correct **Port**
4. Click **Upload**

## Verify independently (do this before connecting the app)

Before pointing the CanFallingDetector app at it, confirm the sketch
actually behaves like a Modbus RTU slave using a generic tool:

- **QModMaster** (free) or **Modbus Poll** (trial available) on Windows
- Connect to the Arduino's COM port at **9600 baud**, Unit/Slave ID **1**
- Try reading **Coil 0** (Q0.0) — should read back **OFF**
- Try writing **Coil 0** ON — pin 7's LED/relay should turn on immediately;
  write it OFF again — it should turn off immediately
- Try reading/writing **Coil 1** (the free-standing maintenance test
  marker) — should read back whatever you last wrote, with no effect on
  pin 7
- Press the physical reset button — reading **Discrete Input 0** (I0.0)
  should show **ON** while held, **OFF** when released

If any of that doesn't behave as expected, the sketch needs debugging
before use — don't skip this step.

## Using it with the app

1. In the app's **Live Camera** tab → **PLC Connection** panel:
   - **Mode**: Modbus (real PLC / test rig)
   - **Connection**: USB Serial (Arduino test rig)
   - **COM Port**: whatever port the Arduino shows as (check Device Manager on Windows, or `/dev/ttyUSB0`/`/dev/ttyACM0` on Linux)
   - **Baud rate**: 9600 (matches the sketch's `BAUD_RATE`)
   - **Unit ID**: 1
   - Leave **Reject coil addr (Q0.0)** and **Reset input addr (I0.0)** at
     their defaults (0 / 0) — they match this sketch exactly
2. Click **Connect**
3. Trigger a REJECT (or wait for one from live detection) — the LED/relay
   on pin 7 should turn on, and the app should show the event confirmed
4. Press the physical button on pin 2 — the app should auto-clear the
   reject state (pin 7 turns off), same as the software RESET button

## Coil / discrete input map (must match the app's config)

| Address | Type | Direction | Meaning |
|---|---|---|---|
| Coil 0 | Coil (read/write) | PC ↔ Arduino | Q0.0 — reject output, drives pin 7 directly |
| Coil 1 | Coil (read/write) | PC ↔ Arduino | Maintenance test marker — no physical pin, isolated from the reject path |
| Discrete Input 0 | Discrete input (read-only) | Arduino → PC | I0.0 — reflects pin 2 (the physical reset button) live |

Unlike the earlier register-based test rig, this sketch has **no internal
reject/reset state machine of its own** — it's just two plain I/O bits.
The PC app decides what a reset button press means and reacts by writing
Coil 0 off, exactly as it would with a real LOGO! that has no ladder
program of its own for this.

## Known simplification vs. a real PLC

This test rig has **zero processing delay** between a coil write and the
physical pin changing — a real LOGO! (or any real PLC I/O) has real
electrical/scan-cycle timing that this simple rig doesn't model. It's
good enough to validate the app's own state machine and timing logic
(readback confirmation, timeouts, the reset edge-detection), but don't
mistake its instant response for what real hardware timing will look
like.
