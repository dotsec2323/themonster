"""
rejection_history_tab.py
-------------------------
Rejection History page: shows every REJECT event that has been logged to
disk, each with its saved picture, Date, Time, and class/confidence
information, so operators and maintenance can review past rejects without
digging through the raw image folder or log file.

Clicking a thumbnail opens a large, zoomable viewer with Previous/Next
navigation across all loaded records, an info panel, and a Close button.

Read-only, and completely decoupled from the detection/PLC/reject logic --
it only reads the same append-only JSONL log that camera_tab.py already
writes on every reject event (see camera_tab._log_reject_event /
event_log_path). Nothing here can affect the reject/reset workflow.
"""

import os
import json

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QGroupBox, QScrollArea, QDialog
)

MAX_HISTORY_ENTRIES = 300  # most recent N events shown, newest first (perf cap)


class ClickableThumb(QLabel):
    """A QLabel that reports clicks -- used for the reject-history
    thumbnails so clicking one opens the large viewer."""
    def __init__(self, on_click, parent=None):
        super().__init__(parent)
        self._on_click = on_click
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        if self._on_click:
            self._on_click()
        super().mousePressEvent(event)


class RejectedImageViewerDialog(QDialog):
    """
    Large preview of a single rejected-can image, with Previous/Next
    navigation across the full set of currently-loaded records, zoom
    in/out/fit, an info panel (date, time, class, confidence, status,
    event ID), and a Close button. Left/Right arrow keys also navigate;
    Escape closes.
    """
    def __init__(self, records, start_index, parent=None):
        super().__init__(parent)
        self.records = records
        self.index = start_index
        self.fit_mode = True
        self.zoom_factor = 1.0
        self._orig_pix = None
        self.setWindowTitle("Rejected Image Viewer")
        self.resize(1000, 720)
        self._build_ui()
        self._load_current()

    def _build_ui(self):
        root = QVBoxLayout(self)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.setStyleSheet("background:#15171a; border:1px solid #c7ccd4; border-radius:6px;")
        self.img_label = QLabel()
        self.img_label.setAlignment(Qt.AlignCenter)
        self.scroll.setWidget(self.img_label)
        root.addWidget(self.scroll, stretch=1)

        self.lbl_info = QLabel("")
        self.lbl_info.setWordWrap(True)
        self.lbl_info.setStyleSheet("font-size:13px; padding:4px 0;")
        root.addWidget(self.lbl_info)

        btn_row = QHBoxLayout()
        self.btn_prev = QPushButton("← Previous")
        self.btn_prev.clicked.connect(self.on_prev)
        self.btn_zoom_out = QPushButton("Zoom −")
        self.btn_zoom_out.clicked.connect(self.on_zoom_out)
        self.btn_zoom_fit = QPushButton("Fit")
        self.btn_zoom_fit.clicked.connect(self.on_zoom_reset)
        self.btn_zoom_in = QPushButton("Zoom +")
        self.btn_zoom_in.clicked.connect(self.on_zoom_in)
        self.btn_next = QPushButton("Next →")
        self.btn_next.clicked.connect(self.on_next)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.close)
        for b in (self.btn_prev, self.btn_zoom_out, self.btn_zoom_fit, self.btn_zoom_in, self.btn_next):
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_close)
        root.addLayout(btn_row)

    def _load_current(self):
        rec = self.records[self.index]
        self.setWindowTitle(f"Rejected Image Viewer  —  {self.index + 1} of {len(self.records)}")

        self._orig_pix = None
        img_path = rec.get("image_path")
        if img_path and os.path.exists(img_path):
            pix = QPixmap(img_path)
            if not pix.isNull():
                self._orig_pix = pix

        self.fit_mode = True
        self.zoom_factor = 1.0
        self._render_image()

        ts = rec.get("timestamp", "") or ""
        date_part, _, time_part = ts.partition(" ")
        class_name = rec.get("class_name") or "REJECT"
        conf = rec.get("confidence")
        conf_txt = f"{conf*100:.0f}%" if isinstance(conf, (int, float)) else "—"
        self.lbl_info.setText(
            f"<b>Class / Reject reason:</b> {class_name} &nbsp;&nbsp; "
            f"<b>Confidence:</b> {conf_txt}<br>"
            f"<b>Date:</b> {date_part or '—'} &nbsp;&nbsp; <b>Time:</b> {time_part or '—'}<br>"
            f"<b>Status:</b> {rec.get('final_status', '—')} &nbsp;&nbsp; "
            f"<b>Event ID:</b> {rec.get('event_id', '—')}"
        )

        self.btn_prev.setEnabled(self.index > 0)
        self.btn_next.setEnabled(self.index < len(self.records) - 1)

    def _render_image(self):
        if self._orig_pix is None:
            self.img_label.setPixmap(QPixmap())
            self.img_label.setText("(image not available)")
            self.img_label.setStyleSheet("color:#aab0bb; font-size:13px;")
            return
        if self.fit_mode:
            avail = self.scroll.viewport().size()
            pix = self._orig_pix.scaled(avail, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        else:
            w = max(1, int(self._orig_pix.width() * self.zoom_factor))
            h = max(1, int(self._orig_pix.height() * self.zoom_factor))
            pix = self._orig_pix.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.img_label.setPixmap(pix)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fit_mode:
            self._render_image()

    def on_prev(self):
        if self.index > 0:
            self.index -= 1
            self._load_current()

    def on_next(self):
        if self.index < len(self.records) - 1:
            self.index += 1
            self._load_current()

    def on_zoom_in(self):
        self.fit_mode = False
        self.zoom_factor = min(5.0, self.zoom_factor * 1.25)
        self._render_image()

    def on_zoom_out(self):
        self.fit_mode = False
        self.zoom_factor = max(0.2, self.zoom_factor / 1.25)
        self._render_image()

    def on_zoom_reset(self):
        self.fit_mode = True
        self._render_image()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Left:
            self.on_prev()
        elif event.key() == Qt.Key_Right:
            self.on_next()
        elif event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)


class RejectionHistoryTab(QWidget):
    def __init__(self, camera_tab, log_fn):
        super().__init__()
        self.camera_tab = camera_tab
        self.log = log_fn
        self._last_mtime = None
        self._records = []
        self._build_ui()

        # Auto-refresh, same pattern as diagnostics_tab.py -- but the
        # mtime check in refresh() skips any real work when the log file
        # hasn't changed, so this stays cheap between actual reject events.
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start(3000)
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)

        top_row = QHBoxLayout()
        self.lbl_title = QLabel("Rejection History")
        self.lbl_title.setStyleSheet("font-size:18px; font-weight:700; color:#0d47a1;")
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh)
        top_row.addWidget(self.lbl_title)
        top_row.addStretch(1)
        top_row.addWidget(self.btn_refresh)
        root.addLayout(top_row)

        note = QLabel("Every confirmed rejection is recorded here permanently, newest first. "
                       "Click a picture to open a large viewer with zoom and Previous/Next navigation.")
        note.setStyleSheet("color:#5f6672; font-size:11px;")
        note.setWordWrap(True)
        root.addWidget(note)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.list_container)
        root.addWidget(self.scroll, stretch=1)

    def _load_records(self):
        """Read the append-only JSONL log and collapse it to the latest
        snapshot per event, newest first. Never raises -- a read/parse
        failure here must not affect the rest of the app."""
        path = self.camera_tab.event_log_path
        if not os.path.exists(path):
            return []
        records = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    key = rec.get("compact_event_id", rec.get("event_id"))
                    records[key] = rec  # later lines overwrite earlier ones -- keeps latest status
        except Exception as e:
            self.log(f"WARNING: Rejection History could not read the event log: {e}")
            return []

        ordered = sorted(records.values(), key=lambda r: r.get("timestamp", ""), reverse=True)
        return ordered[:MAX_HISTORY_ENTRIES]

    def refresh(self):
        path = self.camera_tab.event_log_path
        try:
            mtime = os.path.getmtime(path) if os.path.exists(path) else None
        except Exception:
            mtime = None
        if mtime is not None and mtime == self._last_mtime:
            return  # log hasn't changed since last refresh -- nothing to rebuild
        self._last_mtime = mtime

        self._records = self._load_records()

        # Clear all existing cards, keeping the trailing stretch item.
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        count_txt = f"({len(self._records)} shown, most recent first)" if self._records else "(none yet)"
        self.lbl_title.setText(f"Rejection History  {count_txt}")

        if not self._records:
            empty = QLabel("No rejection events recorded yet.")
            empty.setStyleSheet("color:#5f6672; padding:12px;")
            self.list_layout.insertWidget(0, empty)
            return

        for i, rec in enumerate(self._records):
            card = self._build_card(rec, i)
            self.list_layout.insertWidget(self.list_layout.count() - 1, card)

    def open_viewer(self, index):
        if not self._records or not (0 <= index < len(self._records)):
            return
        dlg = RejectedImageViewerDialog(self._records, index, parent=self)
        dlg.exec_()

    def _build_card(self, rec, index):
        card = QGroupBox()
        card.setStyleSheet(
            "QGroupBox { border:1px solid #c7ccd4; border-radius:8px; "
            "background-color:#ffffff; margin-top:6px; }")
        row = QHBoxLayout(card)

        thumb = ClickableThumb(on_click=lambda idx=index: self.open_viewer(idx))
        thumb.setFixedSize(170, 128)
        thumb.setAlignment(Qt.AlignCenter)
        img_path = rec.get("image_path")
        if img_path and os.path.exists(img_path):
            pix = QPixmap(img_path)
            if not pix.isNull():
                thumb.setPixmap(pix.scaled(170, 128, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                thumb.setStyleSheet("background:#15171a; border:1px solid #c7ccd4; border-radius:4px;")
                thumb.setToolTip("Click to open a large preview")
            else:
                thumb.setText("(image unreadable)")
                thumb.setStyleSheet(
                    "background:#15171a; border:1px solid #c7ccd4; border-radius:4px; "
                    "color:#aab0bb; font-size:11px;")
        else:
            thumb.setText("(image not found)")
            thumb.setStyleSheet(
                "background:#15171a; border:1px solid #c7ccd4; border-radius:4px; "
                "color:#aab0bb; font-size:11px;")
        row.addWidget(thumb)

        info = QVBoxLayout()
        ts = rec.get("timestamp", "") or ""
        date_part, _, time_part = ts.partition(" ")
        class_name = rec.get("class_name") or "REJECT"
        conf = rec.get("confidence")
        conf_txt = f"{conf*100:.0f}%" if isinstance(conf, (int, float)) else "—"

        lbl_top = QLabel(f"<b>{class_name}</b> &nbsp;&nbsp; confidence: {conf_txt}")
        lbl_top.setStyleSheet("font-size:14px; color:#1a1d21;")
        lbl_date = QLabel(f"Date: {date_part or '—'}")
        lbl_date.setStyleSheet("font-size:13px; color:#1a1d21;")
        lbl_time = QLabel(f"Time: {time_part or '—'}")
        lbl_time.setStyleSheet("font-size:13px; color:#1a1d21;")
        lbl_status = QLabel(f"Status: {rec.get('final_status', '—')}")
        lbl_status.setStyleSheet("font-size:12px; color:#5f6672;")
        lbl_id = QLabel(f"Event ID: {rec.get('event_id', '—')}")
        lbl_id.setStyleSheet("font-size:11px; color:#5f6672;")
        for w in (lbl_top, lbl_date, lbl_time, lbl_status, lbl_id):
            info.addWidget(w)
        info.addStretch(1)
        row.addLayout(info, stretch=1)

        view_btn = QPushButton("View Large")
        view_btn.clicked.connect(lambda _=False, idx=index: self.open_viewer(idx))
        btn_col = QVBoxLayout()
        btn_col.addWidget(view_btn)
        btn_col.addStretch(1)
        row.addLayout(btn_col)

        return card
