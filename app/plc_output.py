"""
plc_output.py
-------------
Reject-signal output interface, currently simulated.

Kept as its own small class specifically so a real PLC protocol
implementation (Siemens S7, Modbus TCP, digital I/O) can later replace
SimulatedPLCOutput without touching any detection/UI code -- callers only
ever use .reject_on() / .reject_off() / .is_on, never anything
protocol-specific.

SAFETY: reject_on()/reject_off() never raise. A PLC communication failure
must never crash the detection application -- it's logged and swallowed,
matching the project's explicit requirement that PLC errors can't take
down the app. The real (non-simulated) implementation this gets swapped
for later must preserve that same guarantee.
"""

import time


class SimulatedPLCOutput:
    def __init__(self, log_fn=None):
        self.log = log_fn or (lambda msg: None)
        self.is_on = False
        self._last_change_ts = None

    def reject_on(self):
        """Turn the reject output ON and latch it. Never raises."""
        try:
            self.is_on = True
            self._last_change_ts = time.time()
            self.log("PLC OUTPUT: REJECT signal -> ON (latched)")
        except Exception as e:
            self.log(f"WARNING: PLC output error (reject_on): {e}")

    def reject_off(self):
        """Turn the reject output OFF (operator RESET, or app shutdown). Never raises."""
        try:
            self.is_on = False
            self._last_change_ts = time.time()
            self.log("PLC OUTPUT: REJECT signal -> OFF")
        except Exception as e:
            self.log(f"WARNING: PLC output error (reject_off): {e}")
