"""
plc_modbus.py
-------------
Real Modbus TCP communication with a Siemens LOGO! (8) logic module over
its built-in Ethernet port. Kept completely separate from camera/YOLO
code -- the detection system does not know or care how the reject signal
is actually delivered.

LOGO! only exposes two physical I/O points for this integration:
    Q0.0 (first digital output) -- the reject actuator       (coil, read/write)
    I0.0 (first digital input)  -- the physical reset button (discrete input, read-only)

Unlike the previous Schneider M241 integration -- word-based holding
registers, with a custom handshake program running on the PLC side --
LOGO! has no PLC-side program computing a status/event-ID/ack word for
us; it's just two bits. So the small state machine (READY /
WAITING_FOR_RESET) that used to live partly on the PLC now lives entirely
in this module, driven by those two bits. Every public signal and method
this class exposes is UNCHANGED from the M241 version, so camera_tab.py /
diagnostics_tab.py needed no changes at all to work with LOGO! instead --
only this file's internals (and the two addresses in config.py) changed.

ADDRESS MAPPING -- READ THIS BEFORE GOING LIVE ON A REAL LOGO!:
The addresses in config.py (plc_reject_coil_address, plc_reset_input_address,
plc_test_coil_address) are DOCUMENTED DEFAULTS (Q1 -> coil address 0,
I1 -> discrete input address 0 -- LOGO!'s Modbus addressing is 0-based on
the wire even though LOGO! itself numbers I/O starting at 1), NOT
addresses confirmed against a real LOGO! Modbus server configuration.
LOGO!'s exact address/function-code mapping for I/Q varies by firmware
version and how "Network Inputs/Outputs" are configured in LOGO! Soft
Comfort -- confirm Q1 and I1's real addresses against your actual LOGO!
project before going live. See README.md.

BEHAVIOR (replaces the old M241 handshake -- there is no PLC-side ack
register on this hardware, so the app itself now owns this entirely):
  Reject:  the app writes coil Q0.0 = ON, then reads it back to confirm
           the physical output is really active before calling the event
           acknowledged -- never assumed from a successful write alone,
           the same "never claims success it can't prove" guarantee the
           M241 handshake gave, just proven a different way here.
  Reset:   either the physical button (I0.0 rising edge, detected by
           polling) or the software RESET button (request_software_reset())
           clears things the same way: the app writes coil Q0.0 = OFF and
           returns to READY. Both paths share one internal method so
           behavior is always identical regardless of trigger.
  READY interlock: unchanged in spirit -- a new reject is refused (via
           reject_refused_not_ready) unless this module's own last-known
           state is READY, exactly like before; it's just tracked
           entirely locally now instead of partly read from a PLC status
           register.
  On (re)connect: the reject coil is read back immediately to determine
           the TRUE starting state -- if Q0.0 is already ON (e.g. left
           latched from a previous session or crash), the starting state
           is WAITING_FOR_RESET, never blindly assumed READY. This is
           what lets camera_tab.py's existing startup-sync safety check
           keep working unchanged.

SAFETY / RELIABILITY (unchanged from the M241 version):
  - All Modbus calls are wrapped in try/except; failures are logged via
    comm_error and never crash the thread or raise into the app.
  - request_reject_event() is refused (with a clear signal) if not READY,
    rather than sending a command the system isn't prepared to safely
    act on.
  - A pending event that never gets confirmed (coil write and/or readback
    keeps failing) times out (configurable) and is reported as
    reject_timeout, rather than waiting forever or silently assuming
    success.
  - reject_off()/disconnect()/stop() are safe to call even if not
    connected, and stop() makes a best-effort (non-blocking-forever)
    attempt to clear the reject coil so nothing is left latched.
  - The diagnostic-only manual reject test (request_diag_reject_test)
    writes the SAME Q0.0 coil as production (LOGO! only gives us the one
    output -- there's no separate isolated test point in hardware like
    the M241 test rig had), so THIS MODULE enforces in software that it's
    only honored while state == READY. It can never override or mask an
    in-progress real reject -- the same guarantee the old isolated test
    register gave in hardware, just enforced here instead.
"""

import threading
import time

from PyQt5.QtCore import QThread, pyqtSignal


class ModbusPLCCommunication(QThread):
    connection_changed = pyqtSignal(bool)       # True = connected
    plc_state_changed = pyqtSignal(str)         # "READY" | "WAITING_FOR_RESET" | "UNKNOWN"
    reset_detected = pyqtSignal()                # reset processed (physical I0.0 press OR software reset)
    comm_error = pyqtSignal(str)                 # human-readable error, for logging

    # --- diagnostics-only signal (never affects the production reject path) ---
    reset_button_state_changed = pyqtSignal(bool)  # live state of I0.0: True = pressed

    # --- reject handshake signals (interface unchanged from the M241
    # version -- see module docstring for what "acknowledged" means on
    # hardware with no PLC-side ack register) ---
    reject_acknowledged = pyqtSignal(int)        # event_id whose Q0.0 write was confirmed by readback
    reject_timeout = pyqtSignal(int)              # event_id that never got confirmed in time
    reject_refused_not_ready = pyqtSignal(int, str)  # event_id, current state -- request refused

    # --- maintenance test signals (never touch the reject path) ---
    test_result = pyqtSignal(str, bool, str)      # test_name, success, detail

    def __init__(self, ip=None, port=502, unit_id=1,
                 reject_coil_address=0, reset_input_address=0, test_coil_address=None,
                 timeout_s=1.0, reconnect_interval_s=3.0, poll_interval_s=0.25,
                 ack_timeout_s=5.0, transport="tcp", serial_port=None, serial_baudrate=9600):
        super().__init__()
        self.transport = transport  # "tcp" | "serial" -- everything below this
        # line is IDENTICAL regardless of transport; only _create_client()
        # differs, so the state-machine/interlock/timeout logic is exactly
        # the same whether talking to a real LOGO! over Ethernet or an
        # Arduino Mega over USB serial standing in for it.
        self.ip = ip
        self.port = port
        self.serial_port = serial_port
        self.serial_baudrate = serial_baudrate
        self.unit_id = unit_id
        self.reject_coil_address = reject_coil_address      # Q0.0
        self.reset_input_address = reset_input_address      # I0.0
        self.test_coil_address = test_coil_address           # optional internal marker/flag bit
        self.timeout_s = timeout_s
        self.reconnect_interval_s = reconnect_interval_s
        self.poll_interval_s = poll_interval_s
        self.ack_timeout_s = ack_timeout_s

        self._client = None
        self._connected = False
        self._has_signaled_status = False
        self._stop_requested = threading.Event()
        self._last_plc_state = "UNKNOWN"        # "READY" | "WAITING_FOR_RESET" | "UNKNOWN"
        self._last_reset_input_raw = None        # last polled raw I0.0 value, for edge detection

        self._lock = threading.Lock()
        # Pending reject event: [event_id, write_done, sent_at] or None.
        # write_done=False means the Q0.0=ON write itself hasn't succeeded
        # yet (will be (re)attempted); True means the write succeeded and
        # we're now confirming it by reading the coil back.
        self._pending_event = None

        # Maintenance test requests: list of dicts, drained by the thread.
        self._pending_tests = []

    # --- thread-safe public API, callable from the GUI thread ---

    def is_connected(self):
        return self._connected

    def is_ready(self):
        """True only if connected and no reject is currently latched --
        the hard interlock before sending a new reject. Tracked entirely
        locally now (LOGO! has no status register of its own), but the
        guarantee to the rest of the app is identical to before."""
        return self._connected and self._last_plc_state == "READY"

    def current_state(self):
        return self._last_plc_state

    def request_reject_event(self, event_id: int):
        """
        Request the reject for a specific event_id (a compact 16-bit-safe
        integer; the caller keeps the full traceable ID in its own log).
        Non-blocking. Refused immediately (via reject_refused_not_ready)
        if not READY -- this is the READY interlock, not a courtesy check.
        """
        if not self.is_ready():
            self.reject_refused_not_ready.emit(event_id, self._last_plc_state)
            return
        with self._lock:
            if self._pending_event is not None:
                # Should not normally happen (caller gates on is_ready()),
                # but never silently overwrite a pending event.
                self.comm_error.emit(
                    f"Reject event {event_id} requested while event "
                    f"{self._pending_event[0]} is still pending -- ignored.")
                return
            self._pending_event = [event_id, False, time.time()]

    def request_test_read(self):
        with self._lock:
            self._pending_tests.append({"type": "read"})

    def request_test_write(self, value=1):
        with self._lock:
            self._pending_tests.append({"type": "write", "value": value})

    def request_software_reset(self):
        """Send the same reset action as the physical I0.0 button (e.g.
        from the software RESET button): clears the reject coil (Q0.0)
        and returns to READY. Queued and applied on the background thread
        like everything else here."""
        with self._lock:
            self._pending_tests.append({"type": "reset_cmd"})

    def request_diag_reject_test(self, on: bool):
        """Diagnostics-only: manually drive the reject coil (Q0.0) to
        verify wiring/communication. LOGO! only gives us this one output
        -- there's no separate isolated test point in hardware -- so this
        module enforces that it's only honored while state == READY,
        which means it can never interfere with a real reject event."""
        with self._lock:
            self._pending_tests.append({"type": "diag_reject", "value": 1 if on else 0})

    def stop(self):
        self._stop_requested.set()

    # --- background thread body ---

    def _create_client(self):
        """Builds the pymodbus client for whichever transport is configured.
        This is the ONLY place transport type matters -- every other method
        calls the same client interface (.connect/.connected/.read_coils/
        .read_discrete_inputs/.write_coil) regardless of TCP vs serial."""
        if self.transport == "serial":
            from pymodbus.client import ModbusSerialClient
            return ModbusSerialClient(
                port=self.serial_port, baudrate=self.serial_baudrate,
                timeout=self.timeout_s)
        else:
            from pymodbus.client import ModbusTcpClient
            return ModbusTcpClient(self.ip, port=self.port, timeout=self.timeout_s)

    def run(self):
        while not self._stop_requested.is_set():
            if self._client is None:
                try:
                    self._client = self._create_client()
                except Exception as e:
                    self.comm_error.emit(f"Failed to create Modbus client: {e}")
                    self._client = None
                    time.sleep(self.reconnect_interval_s)
                    continue

            if not self._client.connected:
                try:
                    ok = self._client.connect()
                except Exception as e:
                    ok = False
                    self.comm_error.emit(f"PLC connect error: {e}")
                if not ok:
                    # Emit on the very first failure too, not just on a
                    # True->False transition -- otherwise a PLC that's
                    # unreachable from the start leaves the UI stuck on
                    # "CONNECTING..." forever instead of showing DISCONNECTED.
                    if self._connected or not self._has_signaled_status:
                        self._connected = False
                        self._has_signaled_status = True
                        self.connection_changed.emit(False)
                        self._last_plc_state = "UNKNOWN"
                        self.plc_state_changed.emit("UNKNOWN")
                    time.sleep(self.reconnect_interval_s)
                    continue
                else:
                    self._connected = True
                    self._has_signaled_status = True
                    self.connection_changed.emit(True)
                    self._sync_initial_state()

            if not self._service_pending_reject():
                continue

            if not self._service_pending_tests():
                continue

            if not self._poll_reset_input():
                continue

            self._check_ack_timeout()

            time.sleep(self.poll_interval_s)

        # clean shutdown -- best-effort clear of the reject coil so
        # nothing is left latched from the PC side. Never blocks longer
        # than one short attempt.
        if self._client is not None:
            try:
                if self._client.connected:
                    self._client.write_coil(self.reject_coil_address, False, device_id=self.unit_id)
            except Exception:
                pass
            try:
                self._client.close()
            except Exception:
                pass
        self._connected = False

    def _sync_initial_state(self):
        """Called once right after (re)connecting. Reads the reject coil's
        ACTUAL current value rather than assuming READY -- if Q0.0 is
        already ON (left latched from a previous session or a crash), the
        correct starting state is WAITING_FOR_RESET, matching the old M241
        integration's "never blindly resume" safety behavior exactly.
        camera_tab.py's existing startup-sync check depends on this."""
        try:
            r = self._client.read_coils(self.reject_coil_address, count=1, device_id=self.unit_id)
            if not r.isError() and bool(r.bits[0]):
                self._last_plc_state = "WAITING_FOR_RESET"
            else:
                self._last_plc_state = "READY"
        except Exception as e:
            self.comm_error.emit(f"Could not read initial Q0.0 state on connect: {e}")
            self._last_plc_state = "UNKNOWN"
        self.plc_state_changed.emit(self._last_plc_state)

    def _reconnect_after_failure(self):
        self._connected = False
        self.connection_changed.emit(False)
        self._last_plc_state = "UNKNOWN"
        self.plc_state_changed.emit("UNKNOWN")
        try:
            self._client.close()
        except Exception:
            pass
        self._client = None
        time.sleep(self.reconnect_interval_s)

    def _perform_reset(self):
        """Shared reset action -- identical whether triggered by the
        physical I0.0 button or a queued software reset request: clears
        the reject coil (Q0.0) and returns to READY. Errors are logged via
        comm_error but never raise -- a failed clear leaves Q0.0 latched
        and state unchanged, so the next reset attempt (physical or
        software) will simply retry it."""
        from pymodbus.exceptions import ModbusException
        try:
            r = self._client.write_coil(self.reject_coil_address, False, device_id=self.unit_id)
            if r.isError():
                self.comm_error.emit("Failed to clear reject coil (Q0.0) on reset -- will remain latched.")
                return False
            with self._lock:
                self._pending_event = None  # abandon any not-yet-confirmed reject on reset
            self._last_plc_state = "READY"
            self.plc_state_changed.emit("READY")
            self.reset_detected.emit()
            return True
        except (ModbusException, Exception) as e:
            self.comm_error.emit(f"Failed to clear reject coil (Q0.0) on reset: {e}")
            return False

    def _service_pending_reject(self) -> bool:
        """Returns False if a comm failure occurred (caller should loop/retry)."""
        from pymodbus.exceptions import ModbusException
        with self._lock:
            pending = self._pending_event
        if pending is None:
            return True
        event_id, write_done, sent_at = pending

        try:
            if not write_done:
                r = self._client.write_coil(self.reject_coil_address, True, device_id=self.unit_id)
                if r.isError():
                    self.comm_error.emit(f"PLC reject event {event_id} coil write rejected by device -- will retry.")
                    return True  # leave pending, retry next cycle
                with self._lock:
                    if self._pending_event is not None and self._pending_event[0] == event_id:
                        self._pending_event[1] = True  # write confirmed sent; now confirming by readback
                return True

            # write_done == True: confirm the coil actually reads back ON
            # before calling the event acknowledged -- never assume a
            # successful write alone proves the physical output is active.
            r = self._client.read_coils(self.reject_coil_address, count=1, device_id=self.unit_id)
            if r.isError():
                return True  # transient read failure -- retry next cycle, write already done
            if bool(r.bits[0]):
                with self._lock:
                    if self._pending_event is not None and self._pending_event[0] == event_id:
                        self._pending_event = None
                self._last_plc_state = "WAITING_FOR_RESET"
                self.plc_state_changed.emit("WAITING_FOR_RESET")
                self.reject_acknowledged.emit(event_id)
            # else: readback still says OFF -- leave pending, try again next cycle
            return True
        except (ModbusException, Exception) as e:
            self.comm_error.emit(f"PLC reject event {event_id} communication failed: {e}")
            self._reconnect_after_failure()
            return False

    def _service_pending_tests(self) -> bool:
        from pymodbus.exceptions import ModbusException
        with self._lock:
            tests = self._pending_tests[:]
            self._pending_tests.clear()

        for t in tests:
            try:
                if t["type"] == "read":
                    if self.test_coil_address is None:
                        self.test_result.emit("Test Read", False, "No test coil address configured.")
                        continue
                    r = self._client.read_coils(self.test_coil_address, count=1, device_id=self.unit_id)
                    if r.isError():
                        self.test_result.emit("Test Read", False, f"Device returned error: {r}")
                    else:
                        self.test_result.emit("Test Read", True, f"Coil {self.test_coil_address} = {bool(r.bits[0])}")
                elif t["type"] == "write":
                    if self.test_coil_address is None:
                        self.test_result.emit("Test Write", False, "No test coil address configured.")
                        continue
                    r = self._client.write_coil(self.test_coil_address, bool(t["value"]), device_id=self.unit_id)
                    if r.isError():
                        self.test_result.emit("Test Write", False, f"Device returned error: {r}")
                    else:
                        self.test_result.emit("Test Write", True,
                                               f"Wrote {bool(t['value'])} to coil {self.test_coil_address}")
                elif t["type"] == "reset_cmd":
                    if self._perform_reset():
                        self.test_result.emit("Reset Command", True,
                                               f"Cleared reject coil {self.reject_coil_address} (Q0.0)")
                    else:
                        self.test_result.emit("Reset Command", False,
                                               f"Failed to clear reject coil {self.reject_coil_address} (Q0.0)")
                elif t["type"] == "diag_reject":
                    if self._last_plc_state != "READY":
                        self.test_result.emit(
                            "Diagnostic Reject Test", False,
                            "Refused: a production reject is currently active -- the diagnostic "
                            "test can never override a real reject.")
                        continue
                    r = self._client.write_coil(self.reject_coil_address, bool(t["value"]), device_id=self.unit_id)
                    if r.isError():
                        self.test_result.emit("Diagnostic Reject Test", False, f"Device returned error: {r}")
                    else:
                        state_txt = "ON" if t["value"] else "OFF"
                        self.test_result.emit("Diagnostic Reject Test", True,
                                               f"Wrote {state_txt} to coil {self.reject_coil_address} (Q0.0)")
            except (ModbusException, Exception) as e:
                self.test_result.emit(t["type"], False, f"Communication error: {e}")
                self._reconnect_after_failure()
                return False
        return True

    def _poll_reset_input(self) -> bool:
        from pymodbus.exceptions import ModbusException
        try:
            r = self._client.read_discrete_inputs(self.reset_input_address, count=1, device_id=self.unit_id)
            if r.isError():
                self.comm_error.emit("PLC read (I0.0) returned an error response.")
                return True

            pressed = bool(r.bits[0])
            was_pressed = self._last_reset_input_raw

            # --- diagnostics-only: live raw state of the physical reset button ---
            if pressed != was_pressed:
                self.reset_button_state_changed.emit(pressed)

            # --- production reset action: rising edge (button just
            # pressed) while the system is latched waiting for a reset ---
            if pressed and not was_pressed and self._last_plc_state == "WAITING_FOR_RESET":
                self._perform_reset()

            self._last_reset_input_raw = pressed
            return True
        except (ModbusException, Exception) as e:
            self.comm_error.emit(f"PLC read (I0.0) failed: {e}")
            self._reconnect_after_failure()
            return False

    def _check_ack_timeout(self):
        """Covers both failure modes of a pending reject: the coil write
        itself never succeeding, or the write succeeding but the readback
        confirmation never coming back ON in time."""
        with self._lock:
            pending = self._pending_event
        if pending is None:
            return
        event_id, _, sent_at = pending
        if time.time() - sent_at > self.ack_timeout_s:
            with self._lock:
                if self._pending_event is not None and self._pending_event[0] == event_id:
                    self._pending_event = None
            self.reject_timeout.emit(event_id)
