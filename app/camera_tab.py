"""
camera_tab.py
-------------
Live USB Camera Mode with GOOD/REJECT counting, a configurable detection
zone (ROI), and automatic fault-image capture.

Reuses CanDetector and draw_detections UNCHANGED from Image Test mode
(same detection logic across all modes).

DETECTION ZONE (ROI): only detections whose center falls within the
configured vertical band count as events. This is what keeps the app from
counting stationary cans sitting elsewhere in the camera's view (upstream/
downstream of the actual fall point) -- adjust the two sliders so the band
covers only the area where cans actually fall past the camera.

IMPORTANT LIMITATION, stated plainly in the UI and here:
This does NOT implement real object tracking. Without tracking, the same
physical can crossing the zone over several consecutive frames can be
detected -- and counted -- more than once. A time-based cooldown per role
reduces (does not eliminate) this. Accurate one-can-one-count requires
proper tracking (e.g. ByteTrack/BoT-SORT), which is a larger follow-up
piece of work, not a slider setting.
"""

import os
import time
import traceback
import uuid
import json
import shutil

import cv2
import numpy as np

from PyQt5.QtCore import Qt, QTimer, QRectF, QPointF
from PyQt5.QtGui import QPainter, QPen, QColor, QKeySequence
from PyQt5.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QComboBox,
    QGroupBox, QGridLayout, QSlider, QSizePolicy, QMessageBox, QCheckBox,
    QFileDialog, QLineEdit, QSpinBox, QScrollArea, QShortcut
)

from draw import draw_detections, color_for_role
from plc_output import SimulatedPLCOutput
from plc_modbus import ModbusPLCCommunication


def cv2_to_qpixmap(frame_bgr):
    from PyQt5.QtGui import QImage, QPixmap
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb.shape
    qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())


def detect_available_cameras(max_index=5):
    """Probe camera indices 0..max_index-1, return list of indices that open."""
    available = []
    for i in range(max_index):
        try:
            cap = cv2.VideoCapture(i, cv2.CAP_DSHOW if os.name == "nt" else 0)
            if cap.isOpened():
                ok, _ = cap.read()
                if ok:
                    available.append(i)
                cap.release()
        except Exception:
            pass  # a probe failure on one index must never block the others
    return available


class VideoDisplay(QLabel):
    """
    Displays the live camera feed with a ROI rectangle drawn on top (always
    visible, as a thin reference outline). When roi_edit_mode is True, the
    rectangle grows drag handles at each edge/corner -- click-drag a handle
    to resize from that edge/corner, or click-drag inside the box to move
    it. on_roi_changed(top, bottom, left, right) (all in %) fires live
    while dragging, and on release. Coordinates are translated correctly
    even though the pixmap is letterboxed inside this label (KeepAspectRatio).
    """
    HANDLE_PX = 9

    def __init__(self, parent=None):
        super().__init__(parent)
        self.roi_edit_mode = False
        self.roi_top_pct = 0
        self.roi_bottom_pct = 100
        self.roi_left_pct = 0
        self.roi_right_pct = 100
        self.on_roi_changed = None
        self._drag_mode = None
        self._drag_start_pos = None
        self._drag_start_roi = None
        self.setMouseTracking(True)

    def _pixmap_rect(self):
        pix = self.pixmap()
        if pix is None or pix.isNull():
            return None
        lw, lh = self.width(), self.height()
        pw, ph = pix.width(), pix.height()
        if pw <= 0 or ph <= 0 or lw <= 0 or lh <= 0:
            return None
        scale = min(lw / pw, lh / ph)
        dw, dh = pw * scale, ph * scale
        return QRectF((lw - dw) / 2.0, (lh - dh) / 2.0, dw, dh)

    def _box_px(self, r):
        x1 = r.x() + r.width() * self.roi_left_pct / 100.0
        x2 = r.x() + r.width() * self.roi_right_pct / 100.0
        y1 = r.y() + r.height() * self.roi_top_pct / 100.0
        y2 = r.y() + r.height() * self.roi_bottom_pct / 100.0
        return x1, y1, x2, y2

    def _handles_px(self, r):
        x1, y1, x2, y2 = self._box_px(r)
        return {
            "nw": QPointF(x1, y1), "n": QPointF((x1 + x2) / 2, y1), "ne": QPointF(x2, y1),
            "w": QPointF(x1, (y1 + y2) / 2), "e": QPointF(x2, (y1 + y2) / 2),
            "sw": QPointF(x1, y2), "s": QPointF((x1 + x2) / 2, y2), "se": QPointF(x2, y2),
        }

    def paintEvent(self, event):
        super().paintEvent(event)
        r = self._pixmap_rect()
        if r is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        x1, y1, x2, y2 = self._box_px(r)
        pen = QPen(QColor(255, 200, 0), 2 if not self.roi_edit_mode else 3)
        painter.setPen(pen)
        painter.drawRect(QRectF(x1, y1, x2 - x1, y2 - y1))
        if self.roi_edit_mode:
            painter.setBrush(QColor(255, 200, 0))
            for pos in self._handles_px(r).values():
                painter.drawEllipse(pos, self.HANDLE_PX / 2, self.HANDLE_PX / 2)
        painter.end()

    def mousePressEvent(self, event):
        if not self.roi_edit_mode:
            return super().mousePressEvent(event)
        r = self._pixmap_rect()
        if r is None:
            return
        pos = event.pos()
        hit = None
        for name, hpos in self._handles_px(r).items():
            if (pos.x() - hpos.x()) ** 2 + (pos.y() - hpos.y()) ** 2 <= (self.HANDLE_PX + 3) ** 2:
                hit = name
                break
        if hit is None:
            x1, y1, x2, y2 = self._box_px(r)
            if x1 <= pos.x() <= x2 and y1 <= pos.y() <= y2:
                hit = "move"
        if hit is None:
            return
        self._drag_mode = hit
        self._drag_start_pos = pos
        self._drag_start_roi = (self.roi_left_pct, self.roi_top_pct, self.roi_right_pct, self.roi_bottom_pct)

    def mouseMoveEvent(self, event):
        if not self.roi_edit_mode or self._drag_mode is None:
            return super().mouseMoveEvent(event)
        r = self._pixmap_rect()
        if r is None or r.width() <= 0 or r.height() <= 0:
            return
        dx_pct = (event.pos().x() - self._drag_start_pos.x()) / r.width() * 100.0
        dy_pct = (event.pos().y() - self._drag_start_pos.y()) / r.height() * 100.0
        l0, t0, rr0, b0 = self._drag_start_roi
        l, t, rr, b = l0, t0, rr0, b0
        m = self._drag_mode
        if m == "move":
            w, h = rr0 - l0, b0 - t0
            l = max(0.0, min(100.0 - w, l0 + dx_pct))
            t = max(0.0, min(100.0 - h, t0 + dy_pct))
            rr, b = l + w, t + h
        else:
            if "w" in m:
                l = max(0.0, min(rr0 - 2, l0 + dx_pct))
            if "e" in m:
                rr = min(100.0, max(l0 + 2, rr0 + dx_pct))
            if "n" in m:
                t = max(0.0, min(b0 - 2, t0 + dy_pct))
            if "s" in m:
                b = min(100.0, max(t0 + 2, b0 + dy_pct))
        self.roi_left_pct, self.roi_top_pct = round(l), round(t)
        self.roi_right_pct, self.roi_bottom_pct = round(rr), round(b)
        self.update()
        if self.on_roi_changed:
            self.on_roi_changed(self.roi_top_pct, self.roi_bottom_pct, self.roi_left_pct, self.roi_right_pct)

    def mouseReleaseEvent(self, event):
        if self._drag_mode is not None:
            self._drag_mode = None
            if self.on_roi_changed:
                self.on_roi_changed(self.roi_top_pct, self.roi_bottom_pct, self.roi_left_pct, self.roi_right_pct)
        else:
            super().mouseReleaseEvent(event)


class LiveCameraTab(QWidget):
    def __init__(self, cfg, detector, log_fn):
        super().__init__()
        self.cfg = cfg
        self.detector = detector
        self.log = log_fn

        self.cap = None
        self.timer = QTimer()
        self.timer.timeout.connect(self._on_tick)

        self.good_count = 0
        self.reject_count = 0
        self._last_role_event_time = {"GOOD": 0.0, "REJECT": 0.0}
        self.cooldown_ms = 400  # temporary stand-in for tracking, see module docstring

        self.roi_top_pct = getattr(cfg, "roi_top_pct", 0)        # % of frame height, top of detection zone
        self.roi_bottom_pct = getattr(cfg, "roi_bottom_pct", 100)  # % of frame height, bottom
        self.roi_left_pct = getattr(cfg, "roi_left_pct", 0)      # % of frame width, left edge
        self.roi_right_pct = getattr(cfg, "roi_right_pct", 100)   # % of frame width, right edge

        # --- New: camera view adjustments (session-only; "Reset View" restores these) ---
        self.zoom_pct = 100       # 100 = no zoom, up to 300 = 3x digital zoom
        self.brightness_offset = 0   # -100..100, added to pixel values
        self.contrast_pct = 100      # 50..200, multiplies pixel values (100 = neutral)
        self._is_fullscreen = False

        self._frame_times = []  # for rolling FPS calc
        self._consecutive_read_failures = 0
        self.fault_images_dir = os.path.join(self.cfg.log_dir, "fault_images")
        os.makedirs(self.fault_images_dir, exist_ok=True)

        # --- New: camera rotation ---
        self.camera_rotation_deg = getattr(self.cfg, "camera_rotation", 0)

        # --- New: reject stop-and-latch workflow ---
        self.plc = SimulatedPLCOutput(log_fn=self.log)
        self.plc.reject_off()  # guarantee OFF at application start
        self.is_stopped_for_reject = False
        self.reject_folder = getattr(self.cfg, "reject_folder", None) or \
            os.path.join(self.cfg.log_dir, "fault_images")
        os.makedirs(self.reject_folder, exist_ok=True)

        # --- New: real PLC connection -- Ethernet (Siemens LOGO!) and/or USB
        # Serial (Arduino) can both be active at once. "simulated" (default) or
        # "modbus" (either/both real connections enabled).
        self.plc_mode = getattr(self.cfg, "plc_mode", "simulated")
        self.modbus_plc_tcp = None      # created on Connect, destroyed on Disconnect/close
        self.modbus_plc_serial = None   # same
        self.pc_state = "READY"  # display/log-only state; is_stopped_for_reject
        # remains the actual gate the tick loop checks -- unchanged from before.

        # --- New: diagnostics-only state (never affects the production reject path) ---
        self.reset_button_states = {"tcp": None, "serial": None}  # live Pin 2 state per connection
        self.diagnostic_mode_active = False
        # Set by main.py right after the Diagnostics tab is constructed, so
        # Live/Production Mode (this tab) and Diagnostic Mode (that tab) can
        # enforce the mutual-exclusion interlock in both directions.
        self.diagnostics_tab = None
        # Safety-net timer for the reset-confirmation wait -- see on_reset().
        self._reset_confirm_timer = None

        # --- New: production traceability / diagnostics state ---
        self._event_counter = 0  # source of compact (16-bit-safe) event IDs
        self.reject_events = {}  # compact_event_id -> record dict, for this session
        self.last_reject_event = None
        self.last_plc_ack_info = None
        self.last_plc_error = None
        self.plc_disconnect_count = 0
        self._was_connected_before = {"tcp": False, "serial": False}
        self._startup_sync_done = {"tcp": False, "serial": False}  # per-connection, see _on_plc_state_changed
        self.event_log_path = os.path.join(self.cfg.log_dir, "reject_events.jsonl")
        self.disk_space_warning_mb = getattr(self.cfg, "disk_space_warning_mb", 500)

        self._build_ui()

        # Per the integration spec: if the operator previously configured
        # and selected real Modbus mode, auto-connect at startup (still
        # fully non-blocking/background -- if the PLC is unreachable this
        # just shows DISCONNECTED and keeps retrying, exactly like clicking
        # Connect would). Simulated mode (the default) never auto-connects
        # to anything, so behavior for anyone who hasn't set up a PLC is
        # completely unchanged.
        if self.plc_mode == "modbus":
            self.on_plc_connect()

    def _build_ui(self):
        root = QHBoxLayout(self)

        # ---- Left: camera detection view -- given most of the page's space ----
        left = QVBoxLayout()
        self.video_label = VideoDisplay()
        self.video_label.setText("Camera stopped")
        self.video_label.setStyleSheet(
            "background:#15171a; border:1px solid #c7ccd4; border-radius:6px; color:#aab0bb; font-size:14px;")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setMinimumSize(360, 360)
        self.video_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video_label.on_roi_changed = self._on_roi_live_changed
        self._sync_roi_to_video_label()
        left.addWidget(self.video_label, stretch=1)

        # --- Minimal status strip -- stays visible in Full Screen mode too ---
        self.fs_status_row = QHBoxLayout()
        self.lbl_fs_status = QLabel("Status: STOPPED")
        self.lbl_fs_status.setStyleSheet("font-size:14px; font-weight:700;")
        self.lbl_fs_good = QLabel("GOOD: 0")
        self.lbl_fs_good.setStyleSheet("font-size:14px; font-weight:700; color:#1b8a3f;")
        self.lbl_fs_reject = QLabel("REJECT: 0")
        self.lbl_fs_reject.setStyleSheet("font-size:14px; font-weight:700; color:#c62828;")
        self.btn_exit_fullscreen = QPushButton("Exit Full Screen (Esc)")
        self.btn_exit_fullscreen.clicked.connect(self.exit_fullscreen)
        self.btn_exit_fullscreen.setVisible(False)
        self.fs_status_row.addWidget(self.lbl_fs_status)
        self.fs_status_row.addWidget(self.lbl_fs_good)
        self.fs_status_row.addWidget(self.lbl_fs_reject)
        self.fs_status_row.addStretch(1)
        self.fs_status_row.addWidget(self.btn_exit_fullscreen)
        left.addLayout(self.fs_status_row)

        self.controls_widget = QWidget()
        controls = QHBoxLayout(self.controls_widget)
        controls.setContentsMargins(0, 0, 0, 0)
        self.camera_combo = QComboBox()
        self.btn_refresh = QPushButton("Detect Cameras")
        self.btn_refresh.clicked.connect(self.on_detect_cameras)
        self.btn_start = QPushButton("Start Detection")
        self.btn_start.clicked.connect(self.on_start)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.clicked.connect(self.on_stop)
        self.btn_stop.setEnabled(False)
        self.btn_fullscreen = QPushButton("Full Screen")
        self.btn_fullscreen.clicked.connect(self.on_toggle_fullscreen)
        controls.addWidget(QLabel("Camera:"))
        controls.addWidget(self.camera_combo)
        controls.addWidget(self.btn_refresh)
        controls.addWidget(self.btn_start)
        controls.addWidget(self.btn_stop)
        controls.addWidget(self.btn_fullscreen)
        left.addWidget(self.controls_widget)

        self.rotation_widget = QWidget()
        rotation_row = QHBoxLayout(self.rotation_widget)
        rotation_row.setContentsMargins(0, 0, 0, 0)
        rotation_row.addWidget(QLabel("Camera rotation:"))
        self.rotation_combo = QComboBox()
        for deg in [0, 90, 180, 270]:
            self.rotation_combo.addItem(f"{deg}°", userData=deg)
        idx = self.rotation_combo.findData(self.camera_rotation_deg)
        self.rotation_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.rotation_combo.currentIndexChanged.connect(self._on_rotation_changed)
        rotation_row.addWidget(self.rotation_combo)
        rotation_row.addStretch(1)
        left.addWidget(self.rotation_widget)

        root.addLayout(left, stretch=1)

        # ---- Right: settings sidebar -- fixed, compact width so the camera
        # view keeps the vast majority of the page. Scrollable (and the
        # widths below stay usable down to a small industrial-panel size)
        # since there are several configuration groups. ---
        self.right_container = QWidget()
        self.right_container.setMinimumWidth(300)
        self.right_container.setMaximumWidth(400)
        right = QVBoxLayout(self.right_container)

        warn = QLabel(
            "⚠ No object tracking yet -- a time-based cooldown is used instead "
            "and may over/undercount fast or closely-spaced cans.")
        warn.setStyleSheet(
            "background:#fff3cd; color:#7a5200; padding:6px; border-radius:4px; "
            "border:1px solid #ffdf7e; font-size:11px;")
        warn.setWordWrap(True)
        right.addWidget(warn)

        params_box = QGroupBox("Settings")
        pg = QGridLayout(params_box)
        pg.addWidget(QLabel("Confidence:"), 0, 0)
        self.conf_slider = QSlider(Qt.Horizontal)
        self.conf_slider.setRange(1, 99)
        self.conf_slider.setValue(int(self.cfg.confidence * 100))
        self.conf_lbl = QLabel(f"{self.cfg.confidence:.2f}")
        self.conf_slider.valueChanged.connect(lambda v: self.conf_lbl.setText(f"{v/100:.2f}"))
        pg.addWidget(self.conf_slider, 0, 1)
        pg.addWidget(self.conf_lbl, 0, 2)

        pg.addWidget(QLabel("Cooldown (ms):"), 1, 0)
        self.cooldown_slider = QSlider(Qt.Horizontal)
        self.cooldown_slider.setRange(50, 2000)
        self.cooldown_slider.setValue(self.cooldown_ms)
        self.cooldown_lbl = QLabel(f"{self.cooldown_ms}")
        self.cooldown_slider.valueChanged.connect(self._on_cooldown_changed)
        pg.addWidget(self.cooldown_slider, 1, 1)
        pg.addWidget(self.cooldown_lbl, 1, 2)

        self.save_fault_images_chk = QCheckBox("Save image on REJECT event")
        self.save_fault_images_chk.setChecked(True)
        pg.addWidget(self.save_fault_images_chk, 2, 0, 1, 3)

        right.addWidget(params_box)

        # --- New: full rectangular ROI, with corner/edge drag-resize on the
        # video itself (VideoDisplay above) when "Edit ROI" is toggled on ---
        roi_box = QGroupBox("Detection ROI (Zone)")
        rg = QGridLayout(roi_box)

        self.btn_roi_edit = QPushButton("Edit ROI on Video")
        self.btn_roi_edit.setCheckable(True)
        self.btn_roi_edit.toggled.connect(self._on_roi_edit_toggled)
        rg.addWidget(self.btn_roi_edit, 0, 0, 1, 3)

        rg.addWidget(QLabel("Top (%):"), 1, 0)
        self.roi_top_slider = QSlider(Qt.Horizontal)
        self.roi_top_slider.setRange(0, 100)
        self.roi_top_slider.setValue(self.roi_top_pct)
        self.roi_top_lbl = QLabel(f"{self.roi_top_pct}")
        self.roi_top_slider.valueChanged.connect(self._on_roi_top_changed)
        rg.addWidget(self.roi_top_slider, 1, 1)
        rg.addWidget(self.roi_top_lbl, 1, 2)

        rg.addWidget(QLabel("Bottom (%):"), 2, 0)
        self.roi_bottom_slider = QSlider(Qt.Horizontal)
        self.roi_bottom_slider.setRange(0, 100)
        self.roi_bottom_slider.setValue(self.roi_bottom_pct)
        self.roi_bottom_lbl = QLabel(f"{self.roi_bottom_pct}")
        self.roi_bottom_slider.valueChanged.connect(self._on_roi_bottom_changed)
        rg.addWidget(self.roi_bottom_slider, 2, 1)
        rg.addWidget(self.roi_bottom_lbl, 2, 2)

        rg.addWidget(QLabel("Left (%):"), 3, 0)
        self.roi_left_slider = QSlider(Qt.Horizontal)
        self.roi_left_slider.setRange(0, 100)
        self.roi_left_slider.setValue(self.roi_left_pct)
        self.roi_left_lbl = QLabel(f"{self.roi_left_pct}")
        self.roi_left_slider.valueChanged.connect(self._on_roi_left_changed)
        rg.addWidget(self.roi_left_slider, 3, 1)
        rg.addWidget(self.roi_left_lbl, 3, 2)

        rg.addWidget(QLabel("Right (%):"), 4, 0)
        self.roi_right_slider = QSlider(Qt.Horizontal)
        self.roi_right_slider.setRange(0, 100)
        self.roi_right_slider.setValue(self.roi_right_pct)
        self.roi_right_lbl = QLabel(f"{self.roi_right_pct}")
        self.roi_right_slider.valueChanged.connect(self._on_roi_right_changed)
        rg.addWidget(self.roi_right_slider, 4, 1)
        rg.addWidget(self.roi_right_lbl, 4, 2)

        roi_btn_row = QHBoxLayout()
        self.btn_roi_save = QPushButton("Save ROI")
        self.btn_roi_save.clicked.connect(self.on_save_roi)
        self.btn_roi_restore = QPushButton("Restore Saved")
        self.btn_roi_restore.clicked.connect(self.on_restore_roi)
        roi_btn_row.addWidget(self.btn_roi_save)
        roi_btn_row.addWidget(self.btn_roi_restore)
        rg.addLayout(roi_btn_row, 5, 0, 1, 3)

        roi_hint = QLabel("Toggle \"Edit ROI on Video\" to drag the yellow box's edges/corners "
                           "directly on the camera view, or use the sliders.")
        roi_hint.setWordWrap(True)
        roi_hint.setStyleSheet("color:#5f6672; font-size:11px;")
        rg.addWidget(roi_hint, 6, 0, 1, 3)

        right.addWidget(roi_box)

        # --- New: camera view adjustments -- digital zoom, brightness,
        # contrast. Applied to the frame before both detection and display,
        # so what the operator sees is exactly what gets analyzed. ---
        adjust_box = QGroupBox("Camera View Adjustments")
        ag = QGridLayout(adjust_box)

        ag.addWidget(QLabel("Zoom:"), 0, 0)
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(100, 300)
        self.zoom_slider.setValue(self.zoom_pct)
        self.zoom_lbl = QLabel(f"{self.zoom_pct/100:.1f}x")
        self.zoom_slider.valueChanged.connect(self._on_zoom_changed)
        ag.addWidget(self.zoom_slider, 0, 1)
        ag.addWidget(self.zoom_lbl, 0, 2)

        ag.addWidget(QLabel("Brightness:"), 1, 0)
        self.brightness_slider = QSlider(Qt.Horizontal)
        self.brightness_slider.setRange(-100, 100)
        self.brightness_slider.setValue(self.brightness_offset)
        self.brightness_lbl = QLabel(f"{self.brightness_offset}")
        self.brightness_slider.valueChanged.connect(self._on_brightness_changed)
        ag.addWidget(self.brightness_slider, 1, 1)
        ag.addWidget(self.brightness_lbl, 1, 2)

        ag.addWidget(QLabel("Contrast:"), 2, 0)
        self.contrast_slider = QSlider(Qt.Horizontal)
        self.contrast_slider.setRange(50, 200)
        self.contrast_slider.setValue(self.contrast_pct)
        self.contrast_lbl = QLabel(f"{self.contrast_pct/100:.1f}x")
        self.contrast_slider.valueChanged.connect(self._on_contrast_changed)
        ag.addWidget(self.contrast_slider, 2, 1)
        ag.addWidget(self.contrast_lbl, 2, 2)

        self.btn_reset_view = QPushButton("Reset View to Default")
        self.btn_reset_view.clicked.connect(self.on_reset_view)
        ag.addWidget(self.btn_reset_view, 3, 0, 1, 3)

        right.addWidget(adjust_box)

        # --- New: reject-image folder selection ---
        reject_folder_box = QGroupBox("Reject Image Folder")
        rf_layout = QVBoxLayout(reject_folder_box)
        self.lbl_reject_folder = QLabel(f"{self.reject_folder}")
        self.lbl_reject_folder.setWordWrap(True)
        self.btn_browse_reject_folder = QPushButton("Change Folder...")
        self.btn_browse_reject_folder.clicked.connect(self.on_browse_reject_folder)
        self.lbl_disk_warning = QLabel("")
        self.lbl_disk_warning.setStyleSheet("color:#c62828; font-weight:700;")
        self.lbl_disk_warning.setVisible(False)
        rf_layout.addWidget(self.lbl_reject_folder)
        rf_layout.addWidget(self.btn_browse_reject_folder)
        rf_layout.addWidget(self.lbl_disk_warning)
        right.addWidget(reject_folder_box)

        # NOTE: the PLC/Arduino connection panel used to be built here
        # directly on the Live Camera page. It's now built by
        # _build_plc_panel() (below) and embedded into the separate,
        # admin-login-gated System / Hardware Configuration page instead --
        # see main.py and hardware_config_tab.py. The Live Camera page
        # keeps only camera/detection-facing settings, per operator-page
        # cleanup. All the connection objects/logic this page's own reject
        # workflow depends on (self.modbus_plc_tcp/self.modbus_plc_serial,
        # on_plc_connect(), etc.) are unchanged -- only where the widgets
        # are VISUALLY placed moved.
        self._build_plc_panel()

        stats_box = QGroupBox("Live Statistics")
        sg = QGridLayout(stats_box)
        self.lbl_status = QLabel("Status: STOPPED")
        self.lbl_status.setStyleSheet("font-size:15px; font-weight:700;")
        self.lbl_fps = QLabel("FPS: —")
        self.lbl_good = QLabel("GOOD: 0")
        self.lbl_good.setStyleSheet("font-size:14px; font-weight:600; color:#1b8a3f;")
        self.lbl_reject = QLabel("REJECT: 0")
        self.lbl_reject.setStyleSheet("font-size:14px; font-weight:600; color:#c62828;")
        self.lbl_rate = QLabel("Reject rate: —")
        self.lbl_last_event = QLabel("Last event: —")
        self.lbl_plc_status = QLabel("PLC reject output: OFF")
        self.lbl_plc_status.setStyleSheet("color:#1b8a3f; font-weight:700; font-size:14px;")
        for i, w in enumerate([self.lbl_status, self.lbl_fps, self.lbl_good,
                                self.lbl_reject, self.lbl_rate, self.lbl_last_event,
                                self.lbl_plc_status]):
            sg.addWidget(w, i, 0)
        right.addWidget(stats_box)

        # --- New: operator RESET button -- clears the reject latch/PLC output ---
        self.btn_reset = QPushButton("RESET (clear reject, resume detection)")
        self.btn_reset.setStyleSheet(
            "background:#c62828; color:white; font-weight:700; padding:12px; font-size:14px;")
        self.btn_reset.clicked.connect(self.on_reset)
        self.btn_reset.setEnabled(False)
        right.addWidget(self.btn_reset)

        self.btn_reset_counters = QPushButton("Reset Counters")
        self.btn_reset_counters.clicked.connect(self.on_reset_counters)
        right.addWidget(self.btn_reset_counters)

        lbl_fault_dir = QLabel(f"Fault images saved to:\n{self.fault_images_dir}")
        lbl_fault_dir.setStyleSheet("color:#5f6672; font-size:11px;")
        lbl_fault_dir.setWordWrap(True)
        right.addWidget(lbl_fault_dir)
        right.addStretch(1)

        self.right_scroll = QScrollArea()
        self.right_scroll.setWidgetResizable(True)
        self.right_scroll.setFrameShape(QScrollArea.NoFrame)
        self.right_scroll.setWidget(self.right_container)
        self.right_scroll.setMaximumWidth(420)
        root.addWidget(self.right_scroll)

        # ESC exits Full Screen mode regardless of which widget has focus.
        self._esc_shortcut = QShortcut(QKeySequence("Escape"), self)
        self._esc_shortcut.activated.connect(self.exit_fullscreen)

        self.on_detect_cameras()

    def _build_plc_panel(self):
        """
        Builds the PLC/Arduino connection panel widgets. Kept as a method
        on LiveCameraTab (not the new HardwareConfigTab) because the reject
        workflow here directly depends on self.modbus_plc_tcp/serial and
        the handler methods below -- only where this panel is DISPLAYED
        moved to the admin-gated System / Hardware Configuration page. See
        hardware_config_tab.py, which calls get_plc_panel_widget() to embed
        the exact same, fully-functional widget in its own layout.
        """
        plc_box = QGroupBox("PLC Connection")
        plcg = QGridLayout(plc_box)

        plcg.addWidget(QLabel("Mode:"), 0, 0)
        self.plc_mode_combo = QComboBox()
        self.plc_mode_combo.addItem("Simulated (no hardware)", userData="simulated")
        self.plc_mode_combo.addItem("Modbus (real PLC / test rig)", userData="modbus")
        idx = self.plc_mode_combo.findData(self.plc_mode)
        self.plc_mode_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.plc_mode_combo.currentIndexChanged.connect(self._on_plc_mode_combo_changed)
        plcg.addWidget(self.plc_mode_combo, 0, 1, 1, 2)

        plcg.addWidget(QLabel("Unit ID:"), 1, 0)
        self.plc_unit_spin = QSpinBox()
        self.plc_unit_spin.setRange(0, 255)
        self.plc_unit_spin.setValue(self.cfg.plc_unit_id)
        plcg.addWidget(self.plc_unit_spin, 1, 1, 1, 2)

        plcg.addWidget(QLabel("Reject coil addr (Q0.0):"), 2, 0)
        self.plc_reject_addr_spin = QSpinBox()
        self.plc_reject_addr_spin.setRange(0, 65535)
        self.plc_reject_addr_spin.setValue(self.cfg.plc_reject_coil_address)
        plcg.addWidget(self.plc_reject_addr_spin, 2, 1, 1, 2)

        plcg.addWidget(QLabel("Reset input addr (I0.0):"), 3, 0)
        self.plc_reset_addr_spin = QSpinBox()
        self.plc_reset_addr_spin.setRange(0, 65535)
        self.plc_reset_addr_spin.setValue(self.cfg.plc_reset_input_address)
        plcg.addWidget(self.plc_reset_addr_spin, 3, 1, 1, 2)

        note0 = QLabel("Unit ID and addresses above are shared by both connections below -- "
                        "both must be wired to the same LOGO! I/Q points.")
        note0.setWordWrap(True)
        note0.setStyleSheet("color:#5f6672; font-size:11px;")
        plcg.addWidget(note0, 4, 0, 1, 3)

        # --- Ethernet (Siemens LOGO!) sub-section ---
        self.plc_tcp_enable_chk = QCheckBox("Enable Ethernet (Siemens LOGO!)")
        self.plc_tcp_enable_chk.setChecked(getattr(self.cfg, "plc_enable_tcp", False))
        plcg.addWidget(self.plc_tcp_enable_chk, 5, 0, 1, 3)

        self.lbl_plc_ip = QLabel("IP:")
        plcg.addWidget(self.lbl_plc_ip, 6, 0)
        self.plc_ip_edit = QLineEdit(self.cfg.plc_ip)
        plcg.addWidget(self.plc_ip_edit, 6, 1, 1, 2)

        self.lbl_plc_port = QLabel("Port:")
        plcg.addWidget(self.lbl_plc_port, 7, 0)
        self.plc_port_spin = QSpinBox()
        self.plc_port_spin.setRange(1, 65535)
        self.plc_port_spin.setValue(self.cfg.plc_port)
        plcg.addWidget(self.plc_port_spin, 7, 1, 1, 2)

        self.lbl_plc_tcp_status = QLabel("Ethernet: SIMULATED / OFF")
        self.lbl_plc_tcp_status.setStyleSheet("color:#0d47a1; font-weight:600;")
        self.lbl_plc_tcp_state = QLabel("Ethernet STATE: —")
        plcg.addWidget(self.lbl_plc_tcp_status, 8, 0, 1, 3)
        plcg.addWidget(self.lbl_plc_tcp_state, 9, 0, 1, 3)

        # --- USB Serial (Arduino) sub-section ---
        self.plc_serial_enable_chk = QCheckBox("Enable USB Serial (Arduino test rig)")
        self.plc_serial_enable_chk.setChecked(getattr(self.cfg, "plc_enable_serial", False))
        plcg.addWidget(self.plc_serial_enable_chk, 10, 0, 1, 3)

        self.lbl_plc_serial_port = QLabel("COM Port:")
        plcg.addWidget(self.lbl_plc_serial_port, 11, 0)
        self.plc_serial_port_edit = QLineEdit(getattr(self.cfg, "plc_serial_port", "COM3"))
        self.plc_serial_port_edit.setPlaceholderText("e.g. COM3 (Windows) or /dev/ttyUSB0 (Linux)")
        plcg.addWidget(self.plc_serial_port_edit, 11, 1, 1, 2)

        self.lbl_plc_baud = QLabel("Baud rate:")
        plcg.addWidget(self.lbl_plc_baud, 12, 0)
        self.plc_baud_spin = QSpinBox()
        self.plc_baud_spin.setRange(300, 115200)
        self.plc_baud_spin.setValue(getattr(self.cfg, "plc_serial_baudrate", 9600))
        plcg.addWidget(self.plc_baud_spin, 12, 1, 1, 2)

        self.lbl_plc_serial_status = QLabel("Serial: SIMULATED / OFF")
        self.lbl_plc_serial_status.setStyleSheet("color:#0d47a1; font-weight:600;")
        self.lbl_plc_serial_state = QLabel("Serial STATE: —")
        plcg.addWidget(self.lbl_plc_serial_status, 13, 0, 1, 3)
        plcg.addWidget(self.lbl_plc_serial_state, 14, 0, 1, 3)

        self.btn_plc_connect = QPushButton("Connect Enabled")
        self.btn_plc_connect.clicked.connect(self.on_plc_connect)
        self.btn_plc_disconnect = QPushButton("Disconnect All")
        self.btn_plc_disconnect.clicked.connect(self.on_plc_disconnect)
        self.btn_plc_disconnect.setEnabled(False)
        plcg.addWidget(self.btn_plc_connect, 15, 0, 1, 2)
        plcg.addWidget(self.btn_plc_disconnect, 15, 2)

        self.lbl_plc_sync_warning = QLabel("")
        self.lbl_plc_sync_warning.setStyleSheet("color:#c62828; font-weight:700;")
        self.lbl_plc_sync_warning.setWordWrap(True)
        self.lbl_plc_sync_warning.setVisible(False)
        plcg.addWidget(self.lbl_plc_sync_warning, 16, 0, 1, 3)

        note = QLabel("Q0.0/I0.0 addresses above are illustrative defaults -- "
                       "confirm against your actual Siemens LOGO!/Arduino program before going live. "
                       "Both connections can be enabled at once for side-by-side testing.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#5f6672; font-size:11px;")
        plcg.addWidget(note, 17, 0, 1, 3)

        self.plc_box = plc_box  # kept off the Live Camera page's own layout;
        # hardware_config_tab.py pulls this out via get_plc_panel_widget()

    def get_plc_panel_widget(self):
        """Returns the fully-built, fully-functional PLC Connection panel
        for embedding into the System / Hardware Configuration page."""
        return self.plc_box

    def _on_cooldown_changed(self, v):
        self.cooldown_ms = v
        self.cooldown_lbl.setText(str(v))

    def _sync_roi_to_video_label(self):
        self.video_label.roi_top_pct = self.roi_top_pct
        self.video_label.roi_bottom_pct = self.roi_bottom_pct
        self.video_label.roi_left_pct = self.roi_left_pct
        self.video_label.roi_right_pct = self.roi_right_pct
        self.video_label.update()

    def _on_roi_live_changed(self, top, bottom, left, right):
        """Called live while dragging a handle/the box directly on the
        video (VideoDisplay.on_roi_changed callback). Keeps the sliders in
        sync without re-triggering their own change handlers redundantly."""
        self.roi_top_pct, self.roi_bottom_pct = top, bottom
        self.roi_left_pct, self.roi_right_pct = left, right
        for slider, val in ((self.roi_top_slider, top), (self.roi_bottom_slider, bottom),
                             (self.roi_left_slider, left), (self.roi_right_slider, right)):
            slider.blockSignals(True)
            slider.setValue(val)
            slider.blockSignals(False)
        self.roi_top_lbl.setText(str(top))
        self.roi_bottom_lbl.setText(str(bottom))
        self.roi_left_lbl.setText(str(left))
        self.roi_right_lbl.setText(str(right))

    def _on_roi_edit_toggled(self, checked):
        self.video_label.roi_edit_mode = checked
        self.video_label.update()
        self.btn_roi_edit.setText("Stop Editing ROI" if checked else "Edit ROI on Video")

    def _on_roi_top_changed(self, v):
        if v >= self.roi_bottom_slider.value():
            self.roi_top_slider.setValue(max(0, self.roi_bottom_slider.value() - 1))
            return
        self.roi_top_pct = v
        self.roi_top_lbl.setText(str(v))
        self._sync_roi_to_video_label()

    def _on_roi_bottom_changed(self, v):
        if v <= self.roi_top_slider.value():
            self.roi_bottom_slider.setValue(min(100, self.roi_top_slider.value() + 1))
            return
        self.roi_bottom_pct = v
        self.roi_bottom_lbl.setText(str(v))
        self._sync_roi_to_video_label()

    def _on_roi_left_changed(self, v):
        if v >= self.roi_right_slider.value():
            self.roi_left_slider.setValue(max(0, self.roi_right_slider.value() - 1))
            return
        self.roi_left_pct = v
        self.roi_left_lbl.setText(str(v))
        self._sync_roi_to_video_label()

    def _on_roi_right_changed(self, v):
        if v <= self.roi_left_slider.value():
            self.roi_right_slider.setValue(min(100, self.roi_left_slider.value() + 1))
            return
        self.roi_right_pct = v
        self.roi_right_lbl.setText(str(v))
        self._sync_roi_to_video_label()

    def on_save_roi(self):
        self.cfg.roi_top_pct = self.roi_top_pct
        self.cfg.roi_bottom_pct = self.roi_bottom_pct
        self.cfg.roi_left_pct = self.roi_left_pct
        self.cfg.roi_right_pct = self.roi_right_pct
        try:
            self.cfg.save()
            self.log(f"ROI saved: top={self.roi_top_pct}% bottom={self.roi_bottom_pct}% "
                     f"left={self.roi_left_pct}% right={self.roi_right_pct}%")
        except Exception as e:
            self.log(f"WARNING: failed to save ROI: {e}")

    def on_restore_roi(self):
        self.roi_top_pct = getattr(self.cfg, "roi_top_pct", 0)
        self.roi_bottom_pct = getattr(self.cfg, "roi_bottom_pct", 100)
        self.roi_left_pct = getattr(self.cfg, "roi_left_pct", 0)
        self.roi_right_pct = getattr(self.cfg, "roi_right_pct", 100)
        for slider, val in ((self.roi_top_slider, self.roi_top_pct), (self.roi_bottom_slider, self.roi_bottom_pct),
                             (self.roi_left_slider, self.roi_left_pct), (self.roi_right_slider, self.roi_right_pct)):
            slider.blockSignals(True)
            slider.setValue(val)
            slider.blockSignals(False)
        self.roi_top_lbl.setText(str(self.roi_top_pct))
        self.roi_bottom_lbl.setText(str(self.roi_bottom_pct))
        self.roi_left_lbl.setText(str(self.roi_left_pct))
        self.roi_right_lbl.setText(str(self.roi_right_pct))
        self._sync_roi_to_video_label()
        self.log("ROI restored to last saved configuration.")

    def _on_zoom_changed(self, v):
        self.zoom_pct = v
        self.zoom_lbl.setText(f"{v/100:.1f}x")

    def _on_brightness_changed(self, v):
        self.brightness_offset = v
        self.brightness_lbl.setText(str(v))

    def _on_contrast_changed(self, v):
        self.contrast_pct = v
        self.contrast_lbl.setText(f"{v/100:.1f}x")

    def on_reset_view(self):
        self.zoom_slider.setValue(100)
        self.brightness_slider.setValue(0)
        self.contrast_slider.setValue(100)
        self.log("Camera view adjustments reset to default (no zoom, neutral brightness/contrast).")

    def on_toggle_fullscreen(self):
        self._is_fullscreen = True
        self.right_scroll.setVisible(False)
        self.rotation_widget.setVisible(False)
        self.btn_exit_fullscreen.setVisible(True)
        win = self.window()
        if hasattr(win, "menuBar"):
            win.menuBar().setVisible(False)
        if hasattr(win, "tabs"):
            win.tabs.tabBar().setVisible(False)
        win.showFullScreen()

    def exit_fullscreen(self):
        if not self._is_fullscreen:
            return
        self._is_fullscreen = False
        self.right_scroll.setVisible(True)
        self.rotation_widget.setVisible(True)
        self.btn_exit_fullscreen.setVisible(False)
        win = self.window()
        if hasattr(win, "menuBar"):
            win.menuBar().setVisible(True)
        if hasattr(win, "tabs"):
            win.tabs.tabBar().setVisible(True)
        win.showNormal()

    def _on_rotation_changed(self, _idx):
        deg = self.rotation_combo.currentData()
        self.camera_rotation_deg = deg
        self.cfg.camera_rotation = deg
        try:
            self.cfg.save()
        except Exception as e:
            self.log(f"WARNING: failed to save rotation setting: {e}")
        self.log(f"Camera rotation set to {deg}°.")

    def _apply_rotation(self, frame):
        code = {
            90: cv2.ROTATE_90_CLOCKWISE,
            180: cv2.ROTATE_180,
            270: cv2.ROTATE_90_COUNTERCLOCKWISE,
        }.get(self.camera_rotation_deg)
        if code is None:
            return frame
        return cv2.rotate(frame, code)

    def on_browse_reject_folder(self):
        path = QFileDialog.getExistingDirectory(
            self, "Select Reject Image Folder", self.reject_folder)
        if not path:
            return
        try:
            os.makedirs(path, exist_ok=True)
        except Exception as e:
            QMessageBox.critical(self, "Folder Error", f"Could not use that folder:\n{e}")
            self.log(f"ERROR: failed to create/use reject folder '{path}': {e}")
            return
        self.reject_folder = path
        self.cfg.reject_folder = path
        try:
            self.cfg.save()
        except Exception as e:
            self.log(f"WARNING: failed to save reject folder setting: {e}")
        self.lbl_reject_folder.setText(self.reject_folder)
        self.log(f"Reject image folder set to: {path}")

    def _on_plc_mode_combo_changed(self, _idx):
        self.plc_mode = self.plc_mode_combo.currentData()
        self.cfg.plc_mode = self.plc_mode
        try:
            self.cfg.save()
        except Exception as e:
            self.log(f"WARNING: failed to save PLC mode setting: {e}")
        if self.plc_mode == "simulated":
            self.lbl_plc_tcp_status.setText("Ethernet: SIMULATED / OFF")
            self.lbl_plc_tcp_status.setStyleSheet("color:#0d47a1; font-weight:600;")
            self.lbl_plc_serial_status.setText("Serial: SIMULATED / OFF")
            self.lbl_plc_serial_status.setStyleSheet("color:#0d47a1; font-weight:600;")
        self.log(f"PLC mode set to: {self.plc_mode}")

    def on_plc_connect(self):
        """
        Connect whichever of Ethernet (Siemens LOGO!) / USB Serial (Arduino) are
        enabled -- both can be active at once. They are treated as
        interchangeable equivalents of the same PLC I/O: a reject event is
        sent to every enabled+ready connection, a physical reset from
        EITHER resumes detection, and the app watches for their reported
        states disagreeing (see _check_plc_sync). Non-blocking -- each
        connection attempt happens on its own background thread.
        """
        if self.plc_mode != "modbus":
            self.plc_mode = "modbus"
            idx = self.plc_mode_combo.findData("modbus")
            if idx >= 0:
                self.plc_mode_combo.setCurrentIndex(idx)

        enable_tcp = self.plc_tcp_enable_chk.isChecked()
        enable_serial = self.plc_serial_enable_chk.isChecked()
        if not enable_tcp and not enable_serial:
            QMessageBox.warning(self, "No connection selected",
                                 "Enable Ethernet (Siemens LOGO!) and/or USB Serial (Arduino) first.")
            return

        unit_id = self.plc_unit_spin.value()
        reject_addr = self.plc_reject_addr_spin.value()
        reset_addr = self.plc_reset_addr_spin.value()

        self.cfg.plc_enable_tcp = enable_tcp
        self.cfg.plc_enable_serial = enable_serial
        self.cfg.plc_unit_id = unit_id
        self.cfg.plc_reject_coil_address = reject_addr
        self.cfg.plc_reset_input_address = reset_addr
        if enable_tcp:
            self.cfg.plc_ip = self.plc_ip_edit.text().strip()
            self.cfg.plc_port = self.plc_port_spin.value()
        if enable_serial:
            self.cfg.plc_serial_port = self.plc_serial_port_edit.text().strip()
            self.cfg.plc_serial_baudrate = self.plc_baud_spin.value()
        try:
            self.cfg.save()
        except Exception as e:
            self.log(f"WARNING: failed to save PLC settings: {e}")

        if enable_tcp and self.modbus_plc_tcp is None:
            self._connect_one("tcp", unit_id, reject_addr, reset_addr)
        if enable_serial and self.modbus_plc_serial is None:
            self._connect_one("serial", unit_id, reject_addr, reset_addr)

        self.btn_plc_connect.setEnabled(False)
        self.btn_plc_disconnect.setEnabled(True)

    def _connect_one(self, source, unit_id, reject_addr, reset_addr):
        name = "Ethernet" if source == "tcp" else "Serial"
        try:
            comm = ModbusPLCCommunication(
                transport=source,
                ip=self.cfg.plc_ip, port=self.cfg.plc_port,
                serial_port=self.cfg.plc_serial_port, serial_baudrate=self.cfg.plc_serial_baudrate,
                unit_id=unit_id,
                reject_coil_address=reject_addr,
                reset_input_address=reset_addr,
                test_coil_address=self.cfg.plc_test_coil_address,
                timeout_s=self.cfg.plc_timeout_s,
                reconnect_interval_s=self.cfg.plc_reconnect_interval_s,
                poll_interval_s=self.cfg.plc_poll_interval_s,
                ack_timeout_s=self.cfg.plc_ack_timeout_s,
            )
            # functools.partial-style binding via default-arg lambdas so each
            # signal callback knows which connection (source) it came from.
            comm.connection_changed.connect(lambda c, s=source: self._on_plc_connection_changed(s, c))
            comm.plc_state_changed.connect(lambda st, s=source: self._on_plc_state_changed(s, st))
            comm.reset_detected.connect(lambda s=source: self._on_plc_reset_detected(s))
            comm.comm_error.connect(lambda m, s=source: self._on_plc_comm_error(s, m))
            comm.reject_acknowledged.connect(lambda eid, s=source: self._on_reject_acknowledged(s, eid))
            comm.reject_timeout.connect(lambda eid, s=source: self._on_reject_timeout(s, eid))
            comm.reject_refused_not_ready.connect(lambda eid, st, s=source: self._on_reject_refused(s, eid, st))
            comm.test_result.connect(lambda n, ok, d, s=source: self._on_plc_test_result(s, n, ok, d))
            comm.reset_button_state_changed.connect(lambda pressed, s=source: self._on_plc_reset_button_state(s, pressed))
            comm.start()

            if source == "tcp":
                self.modbus_plc_tcp = comm
                self.lbl_plc_tcp_status.setText("Ethernet: CONNECTING...")
                self.lbl_plc_tcp_status.setStyleSheet("color:#b36b00; font-weight:600;")
                self.log(f"PLC(Ethernet): connecting to {self.cfg.plc_ip}:{self.cfg.plc_port}...")
            else:
                self.modbus_plc_serial = comm
                self.lbl_plc_serial_status.setText("Serial: CONNECTING...")
                self.lbl_plc_serial_status.setStyleSheet("color:#b36b00; font-weight:600;")
                self.log(f"PLC(Serial): connecting via {self.cfg.plc_serial_port} "
                         f"@ {self.cfg.plc_serial_baudrate} baud...")
        except Exception as e:
            self.log(f"ERROR: failed to start PLC communication ({name}): {e}")
            if source == "tcp":
                self.modbus_plc_tcp = None
            else:
                self.modbus_plc_serial = None
            QMessageBox.critical(self, "PLC Connection Error", f"{name}: {e}")

    def on_plc_disconnect(self):
        for source in ("tcp", "serial"):
            comm = self.modbus_plc_tcp if source == "tcp" else self.modbus_plc_serial
            if comm is not None:
                try:
                    comm.stop()
                    comm.wait(3000)
                except Exception as e:
                    self.log(f"WARNING: error stopping PLC communication ({source}): {e}")
                if source == "tcp":
                    self.modbus_plc_tcp = None
                else:
                    self.modbus_plc_serial = None
            self._startup_sync_done[source] = False  # force a fresh sync check on next connect
            self.reset_button_states[source] = None

        self.btn_plc_connect.setEnabled(True)
        self.btn_plc_disconnect.setEnabled(False)
        self.lbl_plc_tcp_status.setText("Ethernet: DISCONNECTED")
        self.lbl_plc_tcp_status.setStyleSheet("color:#c62828; font-weight:600;")
        self.lbl_plc_tcp_state.setText("Ethernet STATE: —")
        self.lbl_plc_serial_status.setText("Serial: DISCONNECTED")
        self.lbl_plc_serial_status.setStyleSheet("color:#c62828; font-weight:600;")
        self.lbl_plc_serial_state.setText("Serial STATE: —")
        self.lbl_plc_sync_warning.setVisible(False)
        self.log("PLC disconnected (all connections).")

    def _on_plc_connection_changed(self, source, connected):
        name = "Ethernet" if source == "tcp" else "Serial"
        lbl = self.lbl_plc_tcp_status if source == "tcp" else self.lbl_plc_serial_status
        if connected:
            lbl.setText(f"{name}: CONNECTED")
            lbl.setStyleSheet("color:#1b8a3f; font-weight:600;")
            self.log(f"PLC({name}): connection established.")
            self._was_connected_before[source] = True
        else:
            lbl.setText(f"{name}: DISCONNECTED")
            lbl.setStyleSheet("color:#c62828; font-weight:600;")
            if self._was_connected_before[source]:
                self.plc_disconnect_count += 1
                self.log(f"PLC({name}): connection lost (disconnect #{self.plc_disconnect_count}). "
                         f"Will keep retrying automatically.")
            else:
                self.log(f"PLC({name}): not yet connected. Will keep retrying automatically.")
        self._check_plc_sync()

    def _on_plc_state_changed(self, source, state):
        name = "Ethernet" if source == "tcp" else "Serial"
        lbl = self.lbl_plc_tcp_state if source == "tcp" else self.lbl_plc_serial_state
        lbl.setText(f"{name} STATE: {state}")
        self.log(f"PLC({name}) state changed: {state}")

        if not self._startup_sync_done[source]:
            self._startup_sync_done[source] = True
            if state not in ("READY",):
                # Spec TEST 10, applied per-connection: on (re)connect, if
                # this PLC/rig is already mid-cycle from a previous session,
                # the app must NOT blindly resume as if nothing happened --
                # it pauses locally too, until a genuine RESET arrives. No
                # image is saved and no new event ID is created here.
                self.is_stopped_for_reject = True
                self.pc_state = "WAITING_FOR_PLC_RESET"
                self.btn_reset.setEnabled(True)
                self._set_status(
                    f"Status: PAUSED - PLC({name}) already in '{state}' at connect (press RESET)")
                self.log(f"STARTUP SYNC ({name}): PLC reported state='{state}' (not READY) on connect -- "
                         f"detection paused for safety until RESET, rather than assuming READY.")
            else:
                self.log(f"STARTUP SYNC ({name}): PLC reported READY on connect -- "
                         f"detection may proceed normally.")

        self._check_plc_sync()
        self._maybe_complete_reset()

    def _check_plc_sync(self):
        """
        When both Ethernet and Serial are connected, they're supposed to be
        interchangeable equivalents of the same PLC I/O -- if they ever
        report different states, that's worth surfacing loudly rather than
        silently trusting one, since it usually means they're not actually
        wired/tested against the same logic yet.
        """
        tcp_ok = self.modbus_plc_tcp is not None and self.modbus_plc_tcp.is_connected()
        serial_ok = self.modbus_plc_serial is not None and self.modbus_plc_serial.is_connected()
        if tcp_ok and serial_ok:
            tcp_state = self.modbus_plc_tcp.current_state()
            serial_state = self.modbus_plc_serial.current_state()
            if tcp_state != "UNKNOWN" and serial_state != "UNKNOWN" and tcp_state != serial_state:
                self.lbl_plc_sync_warning.setText(
                    f"⚠ PLC STATE MISMATCH: Ethernet={tcp_state}, Serial={serial_state} -- "
                    f"verify both are reflecting the same physical process before trusting either.")
                self.lbl_plc_sync_warning.setVisible(True)
                self.log(f"WARNING: PLC state mismatch -- Ethernet={tcp_state}, Serial={serial_state}")
                return
        self.lbl_plc_sync_warning.setVisible(False)

    def _on_plc_reset_detected(self, source):
        # This fires once the PLC has ACTUALLY confirmed the reject coil
        # is cleared (see plc_modbus.py: _perform_reset() only emits this
        # after a successful write) -- so it's the real confirmation
        # signal that gates resuming detection, not a re-trigger of the
        # reset request itself. Covers both paths uniformly: a physical
        # I0.0 button press (which never went through on_reset() at all)
        # and the tail end of a software-requested reset.
        name = "Ethernet" if source == "tcp" else "Serial"
        self.log(f"PLC({name}): reset confirmed (physical input or software request).")
        self._maybe_complete_reset()

    def _on_plc_comm_error(self, source, msg):
        name = "Ethernet" if source == "tcp" else "Serial"
        self.last_plc_error = f"{time.strftime('%H:%M:%S')}  [{name}] {msg}"
        self.log(f"PLC({name}) ERROR: {msg}")

    def _on_reject_acknowledged(self, source, compact_event_id):
        name = "Ethernet" if source == "tcp" else "Serial"
        record = self.reject_events.get(compact_event_id)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        if record is not None:
            record.setdefault("ack_from", [])
            if name not in record["ack_from"]:
                record["ack_from"].append(name)
            record["plc_ack_received"] = True
            record["plc_ack_timestamp"] = ts
            record["final_status"] = "COMPLETED"
            self.last_plc_ack_info = f"{record['event_id']} acknowledged by {name} @ {ts}"
            self._log_reject_event(record)  # append updated snapshot -- log is append-only
            self.log(f"PLC({name}) ACKNOWLEDGED reject event {record['event_id']} -- confirmed COMPLETED.")
        else:
            self.log(f"WARNING: received PLC({name}) acknowledgment for unknown event id {compact_event_id}.")

    def _on_reject_timeout(self, source, compact_event_id):
        name = "Ethernet" if source == "tcp" else "Serial"
        record = self.reject_events.get(compact_event_id)
        if record is not None:
            # Only downgrade to ACK_TIMEOUT if NEITHER connection has
            # acknowledged it yet -- if the other connection already
            # confirmed the event, that confirmation stands.
            if not record.get("plc_ack_received", False):
                record["final_status"] = "ACK_TIMEOUT"
                self._log_reject_event(record)
                self.log(f"WARNING: PLC({name}) did NOT acknowledge reject event {record['event_id']} "
                         f"within the configured timeout. Event marked ACK_TIMEOUT. "
                         f"System remains stopped locally and still requires RESET.")
            else:
                self.log(f"PLC({name}) ack timeout for event {record['event_id']}, but it was "
                         f"already confirmed via {record.get('ack_from')} -- no change to final status.")
        else:
            self.log(f"WARNING: ack timeout ({name}) for unknown event id {compact_event_id}.")

    def _on_reject_refused(self, source, compact_event_id, plc_state):
        name = "Ethernet" if source == "tcp" else "Serial"
        self.log(f"WARNING: PLC({name}) refused reject event {compact_event_id} -- "
                 f"not READY (state: {plc_state}). No command sent via {name}.")

    def _on_plc_test_result(self, source, test_name, success, detail):
        name = "Ethernet" if source == "tcp" else "Serial"
        status = "OK" if success else "FAILED"
        self.log(f"PLC({name}) MAINTENANCE TEST [{test_name}]: {status} -- {detail}")
        if hasattr(self, "lbl_maint_result"):
            color = "#1b8a3f" if success else "#c62828"
            self.lbl_maint_result.setText(f"[{name}] {test_name}: {status} -- {detail}")
            self.lbl_maint_result.setStyleSheet(f"color:{color};")

    def _on_plc_reset_button_state(self, source, pressed):
        """Diagnostics-only: live raw state of the physical reset button
        (I0.0, or Pin 2 on the Arduino test rig). Never touches the
        reject/reset production logic."""
        self.reset_button_states[source] = pressed

    def on_detect_cameras(self):
        self.camera_combo.clear()
        self.log("Scanning for cameras...")
        cams = detect_available_cameras()
        if not cams:
            self.camera_combo.addItem("No cameras found")
            self.log("No cameras detected.")
            return
        for idx in cams:
            self.camera_combo.addItem(f"Camera {idx}", userData=idx)
        self.log(f"Found {len(cams)} camera(s): {cams}")

    def on_start(self):
        # --- Diagnostic Mode / Live Mode interlock: Live/Production Mode
        # starting here always wins -- if Diagnostic Mode is currently
        # active, turn it off automatically first so the two modes are
        # never active at the same time. ---
        if self.diagnostic_mode_active and self.diagnostics_tab is not None:
            self.diagnostics_tab.set_diagnostic_mode(False)

        if not self.detector.is_loaded():
            QMessageBox.warning(self, "Model not loaded", "Load a model on the Image Test tab first.")
            return
        idx = self.camera_combo.currentData()
        if idx is None:
            QMessageBox.warning(self, "No camera", "No camera selected/detected.")
            return

        try:
            self.cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW if os.name == "nt" else 0)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg.camera_width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg.camera_height)
        except Exception as e:
            self.log(f"ERROR opening camera {idx}: {e}")
            QMessageBox.critical(self, "Camera Error", f"Could not open Camera {idx}:\n{e}")
            self.cap = None
            return

        if self.cap is None or not self.cap.isOpened():
            QMessageBox.critical(self, "Camera Error", f"Could not open Camera {idx}.")
            self.cap = None
            return

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.camera_combo.setEnabled(False)
        self._set_status("Status: RUNNING")
        self._frame_times = []
        self._consecutive_read_failures = 0
        self.log(f"Camera {idx} started.")
        self.timer.start(1)  # tick as fast as possible; actual pace is capture+inference bound

    def on_stop(self):
        self.timer.stop()
        self._cancel_reset_confirm_timeout()
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass  # never let a release failure block shutdown
            self.cap = None
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.camera_combo.setEnabled(True)
        self._set_status("Status: STOPPED")
        self.video_label.setText("Camera stopped")
        self.log("Camera stopped.")

    def on_reset_counters(self):
        self.good_count = 0
        self.reject_count = 0
        self._update_stats_labels()
        self.log("Counters reset.")

    def on_reset(self):
        """
        ROOT-CAUSE FIX (previously: detection resumed immediately after
        firing an async reset command, before the PLC had actually cleared
        the reject coil -- if the next can arrived in that ~poll-interval
        window, is_ready() was still False, so the reject was detected and
        counted by the software but never sent to the PLC. Q0.0 never
        turned on for that can. This is now fixed at the source: detection
        does NOT resume until the PLC(s) actually confirm the reset
        completed -- see _complete_reset() / _maybe_complete_reset() --
        not by adding a delay, but by gating resume on real confirmation.)

        Requests a reset (software RESET button, or automatically when the
        real PLC reports its physical reset input was pressed -- see
        _on_plc_reset_detected). If no real PLC is connected (simulated
        mode), there's nothing to confirm, so this resumes immediately, as
        before. If a real PLC is connected, this only REQUESTS the reset;
        _maybe_complete_reset() actually resumes once every connected PLC
        confirms it's READY.
        """
        any_comm_connected = False
        for comm in (self.modbus_plc_tcp, self.modbus_plc_serial):
            if comm is not None and comm.is_connected():
                any_comm_connected = True
                comm.request_software_reset()

        self.btn_reset.setEnabled(False)

        if not any_comm_connected:
            # Nothing to wait for -- simulated mode, or no PLC connected.
            self._complete_reset()
            return

        self.pc_state = "WAITING_FOR_PLC_RESET_CONFIRM"
        self._set_status("Status: RESETTING... (waiting for PLC confirmation)")
        self.log("RESET requested -- waiting for the PLC to confirm the reject "
                 "output is actually cleared before resuming detection.")
        self._start_reset_confirm_timeout()

    def _start_reset_confirm_timeout(self):
        """Safety net: if PLC confirmation never arrives (comms failure,
        PLC fault, etc.), don't leave the app silently stuck forever --
        surface it and let the operator retry, while staying safely
        stopped (never auto-resumes without real confirmation)."""
        if self._reset_confirm_timer is not None:
            self._reset_confirm_timer.stop()
        timeout_ms = int(max(self.cfg.plc_ack_timeout_s, 1.0) * 1000) + 2000
        self._reset_confirm_timer = QTimer(self)
        self._reset_confirm_timer.setSingleShot(True)
        self._reset_confirm_timer.timeout.connect(self._on_reset_confirm_timeout)
        self._reset_confirm_timer.start(timeout_ms)

    def _cancel_reset_confirm_timeout(self):
        if self._reset_confirm_timer is not None:
            self._reset_confirm_timer.stop()
            self._reset_confirm_timer = None

    def _on_reset_confirm_timeout(self):
        if not self.is_stopped_for_reject:
            return  # already completed via the normal path -- nothing to do
        self._reset_confirm_timer = None
        self.btn_reset.setEnabled(True)
        self._set_status("Status: RESET NOT CONFIRMED (press RESET to retry)")
        self.log("WARNING: reset was requested but no PLC confirmed it within the timeout. "
                 "Detection remains stopped for safety -- check the PLC connection and press "
                 "RESET again.")

    def _all_enabled_plcs_ready(self) -> bool:
        """True only if every currently connected PLC reports READY, or
        trivially True if none are connected. This is the actual gate on
        resuming detection -- see on_reset()."""
        any_connected = False
        for comm in (self.modbus_plc_tcp, self.modbus_plc_serial):
            if comm is not None and comm.is_connected():
                any_connected = True
                if comm.current_state() != "READY":
                    return False
        return True

    def _maybe_complete_reset(self):
        """Called whenever a connected PLC's state changes or reports a
        confirmed reset. Only actually resumes detection if we were
        genuinely waiting on a reset AND every connected PLC now agrees
        it's READY -- this is what closes the race that caused the
        second-reject bug."""
        if not self.is_stopped_for_reject:
            return
        if self._all_enabled_plcs_ready():
            self._complete_reset()

    def _complete_reset(self):
        """Actually clears the local latch and resumes live detection.
        Only ever called once a reset is either confirmed complete by the
        PLC, or -- in simulated mode -- immediately, since there's nothing
        to confirm."""
        self._cancel_reset_confirm_timeout()
        self.plc.reject_off()
        self.is_stopped_for_reject = False
        self.pc_state = "READY"
        self.btn_reset.setEnabled(False)
        self.lbl_plc_status.setText("PLC reject output: OFF")
        self.lbl_plc_status.setStyleSheet("color:#1b8a3f; font-weight:700; font-size:14px;")
        self._set_status("Status: RUNNING" if self.cap is not None else "Status: STOPPED")
        self.log("RESET confirmed: PLC output OFF. Detection resumed.")

    def _in_zone(self, det, frame_h, frame_w):
        """True if this detection's center falls inside the configured
        rectangular zone (top/bottom/left/right)."""
        y1, y2 = det.xyxy[1], det.xyxy[3]
        x1, x2 = det.xyxy[0], det.xyxy[2]
        cy_pct = ((y1 + y2) / 2.0 / frame_h) * 100.0
        cx_pct = ((x1 + x2) / 2.0 / frame_w) * 100.0
        return (self.roi_top_pct <= cy_pct <= self.roi_bottom_pct
                and self.roi_left_pct <= cx_pct <= self.roi_right_pct)

    def _apply_view_adjustments(self, frame):
        """Digital zoom + brightness/contrast, applied to the frame before
        BOTH detection and display, so what the operator sees on the
        Camera View Adjustments controls is exactly what gets analyzed."""
        if self.brightness_offset != 0 or self.contrast_pct != 100:
            alpha = self.contrast_pct / 100.0
            beta = self.brightness_offset
            frame = cv2.convertScaleAbs(frame, alpha=alpha, beta=beta)
        if self.zoom_pct > 100:
            h, w = frame.shape[:2]
            zoom = self.zoom_pct / 100.0
            new_w, new_h = max(1, int(w / zoom)), max(1, int(h / zoom))
            x0 = (w - new_w) // 2
            y0 = (h - new_h) // 2
            cropped = frame[y0:y0 + new_h, x0:x0 + new_w]
            frame = cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LINEAR)
        return frame

    def _on_tick(self):
        if self.cap is None:
            return
        if self.is_stopped_for_reject:
            # System is latched waiting for operator RESET -- do not read new
            # frames or run detection. The video pane stays frozen on the
            # frame that triggered the reject, which is itself useful
            # operator feedback alongside the dedicated Last Reject panel.
            return
        try:
            ok, frame = self.cap.read()
        except Exception as e:
            ok, frame = False, None
            self.log(f"ERROR reading camera frame: {e}")

        if not ok or frame is None:
            self._consecutive_read_failures += 1
            if self._consecutive_read_failures == 1:
                self.log("WARNING: failed to read frame from camera (will keep retrying).")
            if self._consecutive_read_failures >= 30:
                # ~sustained failure -- camera likely disconnected. Stop cleanly
                # instead of spinning forever or crashing.
                self.log("ERROR: camera appears disconnected. Stopping detection.")
                self._set_status("Status: CAMERA DISCONNECTED")
                self.on_stop()
                QMessageBox.warning(self, "Camera Disconnected",
                                     "Lost connection to the camera. Detection stopped.\n"
                                     "Check the camera and click Start Detection again.")
            return
        self._consecutive_read_failures = 0

        frame = self._apply_rotation(frame)
        frame = self._apply_view_adjustments(frame)

        conf = self.conf_slider.value() / 100.0
        try:
            result = self.detector.predict(frame, conf=conf)
        except Exception as e:
            self.log(f"ERROR during inference: {e}\n{traceback.format_exc()}")
            self._set_status("Status: INFERENCE ERROR (stopped)")
            self.on_stop()
            QMessageBox.critical(self, "Detection Error",
                                  f"Inference failed and detection was stopped:\n{e}")
            return

        h, w = frame.shape[0], frame.shape[1]
        now = time.time()
        for det in result.detections:
            if det.role not in ("GOOD", "REJECT"):
                continue
            if not self._in_zone(det, h, w):
                continue  # outside the configured detection zone -- ignore
            last = self._last_role_event_time[det.role]
            if (now - last) * 1000.0 >= self.cooldown_ms:
                self._last_role_event_time[det.role] = now
                self._fire_event(det, frame)
                if det.role == "REJECT":
                    self._latch_reject(det, frame)
                    break  # system is now stopped; ignore any further detections this tick

        annotated = draw_detections(frame.copy(), result.detections)
        pix = cv2_to_qpixmap(annotated)
        scaled = pix.scaled(self.video_label.width(), self.video_label.height(),
                             Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.video_label.setPixmap(scaled)  # ROI rectangle/handles are drawn on top by VideoDisplay.paintEvent

        self._frame_times.append(now)
        self._frame_times = [t for t in self._frame_times if now - t < 2.0]
        fps = len(self._frame_times) / 2.0
        self.lbl_fps.setText(f"FPS: {fps:.1f}  (inference {result.inference_ms:.0f} ms)")

    def _fire_event(self, det, frame):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        if det.role == "GOOD":
            self.good_count += 1
        else:
            self.reject_count += 1
            if self.save_fault_images_chk.isChecked():
                self._save_fault_image(frame, det, ts)

        self.lbl_last_event.setText(f"Last event: {det.role} ({det.confidence*100:.0f}%) @ {ts}")
        self._update_stats_labels()
        self.log(f"EVENT: {det.role}  conf={det.confidence*100:.1f}%  @ {ts}")

    def _save_fault_image(self, frame, det, ts):
        fname = f"REJECT_{ts.replace(':', '-').replace(' ', '_')}_{int(det.confidence*100)}pct.jpg"
        path = os.path.join(self.fault_images_dir, fname)
        annotated = draw_detections(frame.copy(), [det])
        try:
            cv2.imwrite(path, annotated)
        except Exception as e:
            self.log(f"WARNING: failed to save fault image: {e}")

    def _check_disk_space(self):
        """Returns (ok: bool, free_mb: float). Never raises."""
        try:
            usage = shutil.disk_usage(self.reject_folder if os.path.isdir(self.reject_folder) else self.cfg.log_dir)
            free_mb = usage.free / (1024 * 1024)
            return free_mb >= self.disk_space_warning_mb, free_mb
        except Exception as e:
            self.log(f"WARNING: could not check disk space: {e}")
            return True, -1  # unknown -- don't block on a check failure

    def _log_reject_event(self, record):
        """Append one JSON-line snapshot of a reject event's current state.
        Append-only, never rewrites -- safe even if the app crashes mid-event.
        Never raises."""
        try:
            os.makedirs(os.path.dirname(self.event_log_path), exist_ok=True)
            with open(self.event_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            self.log(f"WARNING: failed to write reject event log: {e}")

    def _latch_reject(self, det, frame):
        """
        Reject workflow: create a unique traceable event, check disk space,
        save a confirmed-reject-only image, show it in the Last Reject
        panel, turn the (simulated) PLC reject output ON, and stop
        detection until the operator presses RESET. In Modbus mode, this
        also runs the full event-ID/acknowledgment handshake (see
        plc_modbus.py) rather than a fire-and-forget write. Never raises --
        a folder, disk-space, or PLC failure here must not crash the app.
        """
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        self.pc_state = "REJECT_DETECTED"

        self._event_counter += 1
        compact_event_id = (self._event_counter % 65535) + 1  # 1..65535, Modbus-register-safe
        full_event_id = f"EVT_{ts.replace(':', '-').replace(' ', '_')}_{compact_event_id:05d}"

        annotated = draw_detections(frame.copy(), [det])
        cv2.putText(annotated, "REJECT - MACHINE STOPPED", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2, cv2.LINE_AA)

        record = {
            "event_id": full_event_id,
            "compact_event_id": compact_event_id,
            "timestamp": ts,
            "confidence": det.confidence,
            "class_name": getattr(det, "class_name", None),
            "image_path": None,
            "plc_mode": self.plc_mode,
            "plc_command_sent": False,
            "plc_command_sent_to": [],
            "plc_ack_received": False,
            "plc_ack_timestamp": None,
            "ack_from": [],
            "final_status": "PENDING",
        }

        self.pc_state = "SAVE_REJECT_IMAGE"
        disk_ok, free_mb = self._check_disk_space()
        if not disk_ok:
            self.log(f"WARNING: LOW DISK SPACE on reject image drive -- "
                     f"{free_mb:.0f} MB free (warning threshold: {self.disk_space_warning_mb} MB). "
                     f"Still attempting to save.")
            self.lbl_disk_warning.setText(f"⚠ LOW DISK SPACE: {free_mb:.0f} MB free")
            self.lbl_disk_warning.setVisible(True)
        else:
            self.lbl_disk_warning.setVisible(False)

        try:
            os.makedirs(self.reject_folder, exist_ok=True)
            fname = f"{ts.replace(':', '-').replace(' ', '_')}_REJECT_{compact_event_id:05d}.jpg"
            path = os.path.join(self.reject_folder, fname)
            cv2.imwrite(path, annotated)
            record["image_path"] = path
            self.log(f"Saved confirmed reject image [{full_event_id}]: {path}")
        except Exception as e:
            self.log(f"WARNING: failed to save reject image to '{self.reject_folder}': {e}")
            record["image_path"] = f"SAVE_FAILED: {e}"

        # The full-size video pane freezes on the reject frame (below) so the
        # operator sees it immediately in the large camera view; the complete,
        # permanent picture record lives in the Rejection History tab.
        try:
            pix2 = cv2_to_qpixmap(annotated)
            scaled2 = pix2.scaled(self.video_label.width(), self.video_label.height(),
                                   Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.video_label.setPixmap(scaled2)
        except Exception as e:
            self.log(f"WARNING: failed to freeze video pane on reject frame: {e}")

        self.plc.reject_on()  # simulated output, always safe to call regardless of mode
        self.is_stopped_for_reject = True
        self.pc_state = "WAITING_FOR_PLC_RESET"
        self.btn_reset.setEnabled(True)
        self.lbl_plc_status.setText("PLC reject output: ON (latched)")
        self.lbl_plc_status.setStyleSheet("color:#c62828; font-weight:700; font-size:14px;")
        self._set_status(f"Status: STOPPED - REJECT [{full_event_id}] (press RESET)")

        self.reject_events[compact_event_id] = record
        self.last_reject_event = record

        if self.plc_mode == "modbus":
            self.pc_state = "SEND_REJECT_TO_PLC"
            sent_to = []
            not_ready = []
            for source, comm in (("Ethernet", self.modbus_plc_tcp), ("Serial", self.modbus_plc_serial)):
                if comm is None:
                    continue
                if comm.is_connected():
                    if comm.is_ready():
                        comm.request_reject_event(compact_event_id)
                        sent_to.append(source)
                    else:
                        not_ready.append(f"{source}({comm.current_state()})")
                else:
                    not_ready.append(f"{source}(disconnected)")

            if sent_to:
                record["plc_command_sent"] = True
                record["final_status"] = "AWAITING_ACK"
                record["plc_command_sent_to"] = sent_to
                self.log(f"Reject event {full_event_id} (id {compact_event_id}) sent to: "
                         f"{', '.join(sent_to)}, awaiting acknowledgment...")
            elif not_ready:
                record["final_status"] = "PLC_NOT_READY"
                self.log(f"WARNING: no enabled PLC connection was ready -- {', '.join(not_ready)}. "
                         f"Reject event {full_event_id} was NOT sent to any PLC. "
                         f"System remains stopped locally and will wait for RESET.")
            else:
                record["final_status"] = "PLC_DISCONNECTED"
                self.log(f"WARNING: PLC mode is Modbus but no connection is enabled/connected -- "
                         f"reject event {full_event_id} could NOT be sent to any PLC. "
                         f"System is still stopped locally and will wait for RESET.")
        else:
            record["final_status"] = "SIMULATED_OK"

        self._log_reject_event(record)
        self.log(f"REJECT CONFIRMED [{full_event_id}] conf={det.confidence*100:.1f}% @ {ts} -> "
                 f"system stopped, waiting for operator/PLC RESET.")

    def _set_status(self, text):
        self.lbl_status.setText(text)
        self.lbl_fs_status.setText(text)

    def _update_stats_labels(self):
        self.lbl_good.setText(f"GOOD: {self.good_count}")
        self.lbl_reject.setText(f"REJECT: {self.reject_count}")
        total = self.good_count + self.reject_count
        rate = (self.reject_count / total * 100.0) if total else 0.0
        self.lbl_rate.setText(f"Reject rate: {rate:.1f}%  (total {total})")
        self.lbl_fs_good.setText(f"GOOD: {self.good_count}")
        self.lbl_fs_reject.setText(f"REJECT: {self.reject_count}")
