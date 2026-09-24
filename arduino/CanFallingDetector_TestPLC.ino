/*
  CanFallingDetector_TestPLC.ino
  ------------------------------
  Arduino Mega test rig standing in for the two Modbus points the app uses
  on a real Siemens LOGO! PLC, for testing/simulation ONLY while waiting
  on real PLC hardware.

  IMPORTANT, READ BEFORE USING:
  This sketch was written carefully to the standard Modbus RTU protocol
  spec (same frame format, same CRC16-Modbus algorithm) and mirrors the
  Python reference implementation in plc_modbus.py. However, this .ino
  file itself has NOT been compiled or run on physical Arduino hardware --
  there is no way to do that from this environment. Verify it works on
  your actual board (e.g. with a generic Modbus RTU master tool like
  QModMaster or Modbus Poll) BEFORE relying on it with the real app.

  WHAT IT DOES:
  - Acts as a Modbus RTU slave (server) over the native USB serial port
    (no RS485 shield needed -- this is a simple point-to-point PC<->Arduino
    link, not a multi-drop bus).
  - Implements exactly the three function codes the app uses for a LOGO!-
    style bit-oriented interface (NOT the old word/register-based scheme
    used by the previous Schneider M241 test rig):
      0x01  Read Coils              (used to read Q0.0 back for confirmation)
      0x02  Read Discrete Inputs    (used to read I0.0, the reset button)
      0x05  Write Single Coil       (used to write Q0.0, the reject output)
  - Maps coils/discrete inputs to real digital I/O:
      Coil 0 (Q0.0) written ON by the PC  -> turns ON REJECT_OUTPUT_PIN
        (Pin 7) immediately, and stays ON until the PC writes it OFF again.
      RESET_BUTTON_PIN (Pin 2) going LOW (button pressed, using
        INPUT_PULLUP) -> reported live as Discrete Input 0 (I0.0) = ON.
        The PC's app is what decides a press means "reset" and responds
        by writing Coil 0 OFF -- this sketch has no reject/reset state
        machine of its own, exactly like a real LOGO! with no ladder
        program of its own for this: it's just two plain I/O bits.
      Coil 1 is a free-standing marker bit (no physical pin attached) for
        the app's isolated maintenance Test Read/Write buttons -- it can
        never touch REJECT_OUTPUT_PIN.

  COIL / DISCRETE INPUT MAP (must match the app's PLC Connection panel):
      Coil 0            = Q0.0 (reject output)    PC <-> Arduino, read/write
      Discrete Input 0  = I0.0 (reset button)      Arduino -> PC, read-only
      Coil 1            = maintenance test marker  PC <-> Arduino, read/write, no physical pin

  WIRING (adjust pins to your actual test setup):
      Pin 7  -> REJECT_OUTPUT_PIN   (LED or relay module, standing in for
                                      the physical reject mechanism). Directly mirrors Coil 0
                                      (Q0.0) -- HIGH the instant it's written ON, LOW the instant
                                      it's written OFF. No debounce/latching logic on this side;
                                      the PC app owns all of that, same as it would with a real LOGO!.
      Pin 2  -> RESET_BUTTON_PIN    (physical pushbutton to GND, using the
                                      Mega's internal pull-up resistor). Reported live as Discrete
                                      Input 0 (I0.0); pressing it does nothing on its own here --
                                      the PC app is what reacts to it by clearing Coil 0.

  UNIT ID: this sketch responds to Modbus unit/slave ID 1 by default,
  matching the app's default "Unit ID" field.
*/

#include <Arduino.h>

const uint8_t UNIT_ID = 1;
const uint32_t BAUD_RATE = 9600;  // must match the app's "Baud rate" field

const uint8_t REJECT_OUTPUT_PIN = 7;   // physically driven by Coil 0 (Q0.0)
const uint8_t RESET_BUTTON_PIN = 2;    // physically read out as Discrete Input 0 (I0.0)

const uint16_t COIL_REJECT = 0;   // Q0.0 -- drives REJECT_OUTPUT_PIN
const uint16_t COIL_TEST = 1;     // free-standing marker bit, no physical pin attached
const uint16_t NUM_COILS = 8;

const uint16_t DISCRETE_RESET = 0;  // I0.0 -- reflects RESET_BUTTON_PIN
const uint16_t NUM_DISCRETE_INPUTS = 8;

bool coils[NUM_COILS];  // only coils[COIL_REJECT] has a physical pin attached

// ---- Modbus RTU CRC16 (standard algorithm, do not modify) ----
uint16_t crc16Modbus(const uint8_t *data, uint8_t len) {
  uint16_t crc = 0xFFFF;
  for (uint8_t i = 0; i < len; i++) {
    crc ^= data[i];
    for (uint8_t b = 0; b < 8; b++) {
      if (crc & 1) {
        crc = (crc >> 1) ^ 0xA001;
      } else {
        crc >>= 1;
      }
    }
  }
  return crc;
}

void setup() {
  Serial.begin(BAUD_RATE);
  pinMode(REJECT_OUTPUT_PIN, OUTPUT);
  digitalWrite(REJECT_OUTPUT_PIN, LOW);
  pinMode(RESET_BUTTON_PIN, INPUT_PULLUP);

  for (uint16_t i = 0; i < NUM_COILS; i++) coils[i] = false;
}

void loop() {
  handleModbusFrame();
  applyRejectOutputFromCoil();
}

// ---- The reject output pin always mirrors Coil 0 (Q0.0) directly -- no
// separate state machine on this side; the PC app owns the entire
// READY/WAITING_FOR_RESET logic, exactly matching a real LOGO! here,
// which is just two plain I/O bits with no handshake program of its own. ----
void applyRejectOutputFromCoil() {
  digitalWrite(REJECT_OUTPUT_PIN, coils[COIL_REJECT] ? HIGH : LOW);
}

// ---- Minimal Modbus RTU slave: function codes 0x01, 0x02, 0x05 only ----
void handleModbusFrame() {
  static uint8_t buf[16];
  static uint8_t bufLen = 0;
  static unsigned long lastByteAt = 0;

  while (Serial.available() > 0) {
    if (bufLen > 0 && millis() - lastByteAt > 4) {
      // Simple inter-frame gap detection (Modbus RTU frames are silence-
      // delimited); if too much time passed, the buffer is stale -- reset.
      bufLen = 0;
    }
    if (bufLen < sizeof(buf)) {
      buf[bufLen++] = Serial.read();
    } else {
      Serial.read();  // discard overflow byte, avoid buffer overrun
    }
    lastByteAt = millis();

    if (bufLen == 8) {
      uint8_t unit = buf[0];
      uint8_t func = buf[1];
      uint16_t addr = (buf[2] << 8) | buf[3];
      uint16_t recvCrc = buf[6] | (buf[7] << 8);
      uint16_t calcCrc = crc16Modbus(buf, 6);

      if (unit == UNIT_ID && recvCrc == calcCrc) {
        if (func == 0x01) {
          uint16_t count = (buf[4] << 8) | buf[5];
          respondReadBits(unit, 0x01, coils, NUM_COILS, addr, count);
        } else if (func == 0x02) {
          uint16_t count = (buf[4] << 8) | buf[5];
          bool discreteInputs[NUM_DISCRETE_INPUTS];
          for (uint16_t i = 0; i < NUM_DISCRETE_INPUTS; i++) discreteInputs[i] = false;
          discreteInputs[DISCRETE_RESET] = (digitalRead(RESET_BUTTON_PIN) == LOW);
          respondReadBits(unit, 0x02, discreteInputs, NUM_DISCRETE_INPUTS, addr, count);
        } else if (func == 0x05) {
          uint16_t rawValue = (buf[4] << 8) | buf[5];
          bool value = (rawValue == 0xFF00);  // standard Modbus coil-write encoding
          if (addr < NUM_COILS) {
            coils[addr] = value;
          }
          respondWriteSingleCoil(unit, addr, rawValue);
        }
        // Unsupported function codes are silently ignored (no response),
        // matching how the PC-side app never uses anything else.
      }
      bufLen = 0;  // frame consumed either way
    }
  }
}

void respondReadBits(uint8_t unit, uint8_t func, bool *source, uint16_t sourceLen,
                      uint16_t addr, uint16_t count) {
  // Defensive clamp: this app only ever requests count=1, but never trust
  // an incoming frame's count blindly on hardware with no bounds-checking --
  // an oversized count here would overflow the response buffer.
  const uint16_t MAX_COUNT = 32;
  if (count > MAX_COUNT) count = MAX_COUNT;
  if (addr >= sourceLen) {
    return;  // out-of-range read -- silently ignore rather than risk indexing past the array
  }
  if (addr + count > sourceLen) count = sourceLen - addr;

  uint8_t byteCount = (count + 7) / 8;
  uint8_t resp[3 + 4 + 2];  // unit+func+bytecount + up to 32 bits (4 bytes) + crc
  uint8_t idx = 0;
  resp[idx++] = unit;
  resp[idx++] = func;
  resp[idx++] = byteCount;
  for (uint8_t b = 0; b < byteCount; b++) resp[idx + b] = 0;
  for (uint16_t i = 0; i < count; i++) {
    if (source[addr + i]) {
      resp[3 + (i / 8)] |= (1 << (i % 8));
    }
  }
  idx += byteCount;
  uint16_t crc = crc16Modbus(resp, idx);
  resp[idx++] = crc & 0xFF;
  resp[idx++] = (crc >> 8) & 0xFF;
  Serial.write(resp, idx);
}

void respondWriteSingleCoil(uint8_t unit, uint16_t addr, uint16_t rawValue) {
  uint8_t resp[8];
  resp[0] = unit;
  resp[1] = 0x05;
  resp[2] = (addr >> 8) & 0xFF;
  resp[3] = addr & 0xFF;
  resp[4] = (rawValue >> 8) & 0xFF;
  resp[5] = rawValue & 0xFF;
  uint16_t crc = crc16Modbus(resp, 6);
  resp[6] = crc & 0xFF;
  resp[7] = (crc >> 8) & 0xFF;
  Serial.write(resp, 8);
}
