"""
diagnostics_tab.py
-------------------
Production diagnostics view (spec item #11) and a protected maintenance
test function (spec item #12), kept in their own tab so the normal
operator interface (Live Camera tab) isn't overloaded with technical
detail.

This tab is read-only/observational except for the clearly-separated
Maintenance Test section, which only ever touches the isolated test
coil (plc_test_coil_address) -- it never writes to the reject coil
path, so it cannot accidentally trigger a production reject event.
"""

import time

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QGroupBox, QGridLayout, QTextEdit, QCheckBox
)


class DiagnosticsTab(QWidget):
    def __init__(self, camera_tab, log_fn):
        super().__init__()
        self.camera_tab = camera_tab
        self.log = log_fn
        self._build_ui()

        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start(1000)
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)

        conn_box = QGroupBox("PLC Connection Status")
        cg = QGridLayout(conn_box)
        self.lbl_conn_tcp = QLabel("Ethernet: —")
        self.lbl_state_tcp = QLabel("Ethernet STATE: —")
        self.lbl_ip = QLabel("IP: —")
        self.lbl_conn_serial = QLabel("Serial: —")
        self.lbl_state_serial = QLabel("Serial STATE: —")
        self.lbl_com_port = QLabel("COM Port: —")
        self.lbl_sync_status = QLabel("Sync: —")
        self.lbl_disconnect_count = QLabel("Disconnections this session: 0")
        for i, w in enumerate([self.lbl_conn_tcp, self.lbl_state_tcp, self.lbl_ip,
                                self.lbl_conn_serial, self.lbl_state_serial, self.lbl_com_port,
                                self.lbl_sync_status, self.lbl_disconnect_count]):
            cg.addWidget(w, i, 0)
        root.addWidget(conn_box)

        event_box = QGroupBox("Last Reject Event")
        eg = QGridLayout(event_box)
        self.lbl_event_id = QLabel("Event ID: —")
        self.lbl_event_ts = QLabel("Detected: —")
        self.lbl_event_image = QLabel("Image: —")
        self.lbl_event_image.setWordWrap(True)
        self.lbl_event_cmd = QLabel("PLC command sent: —")
        self.lbl_event_ack = QLabel("PLC acknowledgment: —")
        self.lbl_event_status = QLabel("Final status: —")
        for i, w in enumerate([self.lbl_event_id, self.lbl_event_ts, self.lbl_event_image,
                                self.lbl_event_cmd, self.lbl_event_ack, self.lbl_event_status]):
            eg.addWidget(w, i, 0)
        root.addWidget(event_box)

        error_box = QGroupBox("Last Communication Error")
        er = QVBoxLayout(error_box)
        self.lbl_last_error = QLabel("None")
        self.lbl_last_error.setWordWrap(True)
        er.addWidget(self.lbl_last_error)
        root.addWidget(error_box)

        maint_box = QGroupBox("Maintenance Test (manual, does NOT affect production reject signaling)")
        mg = QVBoxLayout(maint_box)
        warn = QLabel("These buttons only touch the isolated test register -- "
                       "they never send a reject command. Safe to use during normal operation.")
        warn.setWordWrap(True)
        warn.setStyleSheet("color:#5f6672; font-size:11px;")
        mg.addWidget(warn)
        btn_row = QHBoxLayout()
        self.btn_test_read = QPushButton("Test Read")
        self.btn_test_read.clicked.connect(self.on_test_read)
        self.btn_test_write = QPushButton("Test Write")
        self.btn_test_write.clicked.connect(self.on_test_write)
        btn_row.addWidget(self.btn_test_read)
        btn_row.addWidget(self.btn_test_write)
        mg.addLayout(btn_row)
        self.lbl_maint_result = QLabel("No test run yet.")
        self.lbl_maint_result.setWordWrap(True)
        mg.addWidget(self.lbl_maint_result)
        root.addWidget(maint_box)

        # --- New: live Reset Button (Pin 2) monitor -- read-only, always on ---
        reset_box = QGroupBox("Reset Button Monitor (Arduino Pin 2)")
        rbg = QGridLayout(reset_box)
        self.lbl_reset_btn_tcp = QLabel("Ethernet: —")
        self.lbl_reset_btn_serial = QLabel("Serial (Arduino): —")
        rbg.addWidget(self.lbl_reset_btn_tcp, 0, 0)
        rbg.addWidget(self.lbl_reset_btn_serial, 1, 0)
        root.addWidget(reset_box)

        # --- New: Diagnostic Mode + Reject Actuator (Pin 7) manual test ---
        diag_box = QGroupBox("Reject Actuator Test (Arduino Pin 7)")
        dg = QVBoxLayout(diag_box)
        diag_warn = QLabel(
            "Enable Diagnostic Mode to manually drive the reject actuator ON/OFF and "
            "verify Arduino communication and wiring. Diagnostic Mode and Live/Production "
            "Mode can never both be active -- starting Live/Production Mode automatically "
            "turns Diagnostic Mode off, and enabling Diagnostic Mode automatically stops "
            "Live/Production Mode.")
        diag_warn.setWordWrap(True)
        diag_warn.setStyleSheet("color:#5f6672; font-size:11px;")
        dg.addWidget(diag_warn)

        self.chk_diagnostic_mode = QCheckBox("Diagnostic Mode")
        self.chk_diagnostic_mode.toggled.connect(self.on_diagnostic_mode_toggled)
        dg.addWidget(self.chk_diagnostic_mode)

        diag_btn_row = QHBoxLayout()
        self.btn_diag_reject_on = QPushButton("Reject Actuator ON (HIGH)")
        self.btn_diag_reject_on.clicked.connect(lambda: self.on_diag_reject_test(True))
        self.btn_diag_reject_on.setEnabled(False)
        self.btn_diag_reject_off = QPushButton("Reject Actuator OFF (LOW)")
        self.btn_diag_reject_off.clicked.connect(lambda: self.on_diag_reject_test(False))
        self.btn_diag_reject_off.setEnabled(False)
        diag_btn_row.addWidget(self.btn_diag_reject_on)
        diag_btn_row.addWidget(self.btn_diag_reject_off)
        dg.addLayout(diag_btn_row)

        self.lbl_diag_result = QLabel("Diagnostic Mode is off.")
        self.lbl_diag_result.setWordWrap(True)
        dg.addWidget(self.lbl_diag_result)
        root.addWidget(diag_box)

        events_box = QGroupBox("All Reject Events This Session")
        evb = QVBoxLayout(events_box)
        self.events_text = QTextEdit()
        self.events_text.setReadOnly(True)
        evb.addWidget(self.events_text)
        root.addWidget(events_box, stretch=1)

        # Let camera_tab's PLC test-result handler update this label directly
        # (see camera_tab._on_plc_test_result) without needing to resubscribe
        # every time the Modbus connection object is recreated.
        self.camera_tab.lbl_maint_result = self.lbl_maint_result

    def on_test_read(self):
        ct = self.camera_tab
        ran_any = False
        for comm in (ct.modbus_plc_tcp, ct.modbus_plc_serial):
            if comm is not None and comm.is_connected():
                comm.request_test_read()
                ran_any = True
        if not ran_any:
            self.lbl_maint_result.setText("Cannot test: no PLC connection is connected.")
            self.lbl_maint_result.setStyleSheet("color:#c62828;")

    def on_test_write(self):
        ct = self.camera_tab
        ran_any = False
        for comm in (ct.modbus_plc_tcp, ct.modbus_plc_serial):
            if comm is not None and comm.is_connected():
                comm.request_test_write(1)
                ran_any = True
        if not ran_any:
            self.lbl_maint_result.setText("Cannot test: no PLC connection is connected.")
            self.lbl_maint_result.setStyleSheet("color:#c62828;")

    def on_diagnostic_mode_toggled(self, checked):
        self.set_diagnostic_mode(checked)

    def set_diagnostic_mode(self, enabled):
        """Enable/disable Diagnostic Mode, enforcing the mutual-exclusion
        interlock with Live/Production Mode (camera_tab.on_start() calls
        this the same way when Live/Production Mode needs to win)."""
        ct = self.camera_tab

        # Keep the checkbox in sync without re-triggering this method.
        self.chk_diagnostic_mode.blockSignals(True)
        self.chk_diagnostic_mode.setChecked(enabled)
        self.chk_diagnostic_mode.blockSignals(False)

        if enabled:
            if ct.cap is not None:
                ct.on_stop()
                self.log("Diagnostic Mode enabled -- Live/Production Mode stopped automatically.")
            ct.diagnostic_mode_active = True
            self.btn_diag_reject_on.setEnabled(True)
            self.btn_diag_reject_off.setEnabled(True)
            self.lbl_diag_result.setText("Diagnostic Mode is ON. Manual actuator test is available.")
        else:
            ct.diagnostic_mode_active = False
            self.btn_diag_reject_on.setEnabled(False)
            self.btn_diag_reject_off.setEnabled(False)
            # Safety: make sure any manual override left on is cleared.
            for comm in (ct.modbus_plc_tcp, ct.modbus_plc_serial):
                if comm is not None and comm.is_connected():
                    comm.request_diag_reject_test(False)
            self.lbl_diag_result.setText("Diagnostic Mode is off.")

    def on_diag_reject_test(self, on):
        ct = self.camera_tab
        ran_any = False
        for comm in (ct.modbus_plc_tcp, ct.modbus_plc_serial):
            if comm is not None and comm.is_connected():
                comm.request_diag_reject_test(on)
                ran_any = True
        if not ran_any:
            self.lbl_diag_result.setText("Cannot test: no PLC connection is connected.")
            self.lbl_diag_result.setStyleSheet("color:#c62828;")
        else:
            self.lbl_diag_result.setText(f"Reject actuator test: requested {'ON' if on else 'OFF'}.")
            self.lbl_diag_result.setStyleSheet("")

    def refresh(self):
        ct = self.camera_tab
        self.lbl_conn_tcp.setText(f"Ethernet: {ct.lbl_plc_tcp_status.text().split(': ', 1)[-1]}")
        self.lbl_state_tcp.setText(ct.lbl_plc_tcp_state.text())
        self.lbl_ip.setText(f"IP: {ct.plc_ip_edit.text()}:{ct.plc_port_spin.value()}"
                             if hasattr(ct, "plc_ip_edit") else "IP: —")
        self.lbl_conn_serial.setText(f"Serial: {ct.lbl_plc_serial_status.text().split(': ', 1)[-1]}")
        self.lbl_state_serial.setText(ct.lbl_plc_serial_state.text())
        self.lbl_com_port.setText(f"COM Port: {ct.plc_serial_port_edit.text()} @ {ct.plc_baud_spin.value()} baud"
                                   if hasattr(ct, "plc_serial_port_edit") else "COM Port: —")
        if ct.lbl_plc_sync_warning.isVisible():
            self.lbl_sync_status.setText("Sync: ⚠ MISMATCH -- see Live Camera tab for detail")
            self.lbl_sync_status.setStyleSheet("color:#c62828; font-weight:700;")
        else:
            self.lbl_sync_status.setText("Sync: OK (or only one connection active)")
            self.lbl_sync_status.setStyleSheet("")
        self.lbl_disconnect_count.setText(f"Disconnections this session: {ct.plc_disconnect_count}")

        def _btn_text(pressed):
            if pressed is None:
                return "—"
            return "Pressed / ON" if pressed else "Released / OFF"
        self.lbl_reset_btn_tcp.setText(f"Ethernet: {_btn_text(ct.reset_button_states.get('tcp'))}")
        self.lbl_reset_btn_serial.setText(f"Serial (Arduino): {_btn_text(ct.reset_button_states.get('serial'))}")

        ev = ct.last_reject_event
        if ev is not None:
            self.lbl_event_id.setText(f"Event ID: {ev['event_id']}")
            self.lbl_event_ts.setText(f"Detected: {ev['timestamp']}")
            self.lbl_event_image.setText(f"Image: {ev['image_path']}")
            sent_to = ev.get("plc_command_sent_to", [])
            self.lbl_event_cmd.setText(f"PLC command sent: {ev['plc_command_sent']}"
                                        + (f"  (to: {', '.join(sent_to)})" if sent_to else ""))
            ack_from = ev.get("ack_from", [])
            ack_txt = f"Yes, by {', '.join(ack_from)} @ {ev['plc_ack_timestamp']}" \
                if ev["plc_ack_received"] else "No"
            self.lbl_event_ack.setText(f"PLC acknowledgment: {ack_txt}")
            self.lbl_event_status.setText(f"Final status: {ev['final_status']}")

        if ct.last_plc_error:
            self.lbl_last_error.setText(ct.last_plc_error)

        lines = []
        for eid in sorted(ct.reject_events.keys()):
            r = ct.reject_events[eid]
            lines.append(f"[{r['timestamp']}] {r['event_id']}  status={r['final_status']}  "
                         f"conf={r['confidence']*100:.0f}%  sent_to={r.get('plc_command_sent_to', [])}  "
                         f"acked_by={r.get('ack_from', [])}")
        self.events_text.setPlainText("\n".join(lines) if lines else "(no reject events yet this session)")
