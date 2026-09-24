"""
main.py
-------
CanFallingDetector - Windows desktop application.

STAGE 1 (current): Model loading + single-image test mode.
  - Browse & load a local .pt YOLO model (no internet at any point)
  - Browse & load a single test image
  - Run inference with configurable Confidence / IoU / Image size
  - Show bounding boxes, class, confidence, detection count,
    image resolution, and processing time (Requirement #8)

Later stages (video test, live camera, tracking, ROI/event line,
GOOD/REJECT event log, simulated output, PLC prep) are added as
additional tabs on top of this same window WITHOUT changing this
stage's code, per the project's staged build plan.

Run:  python main.py
"""

import os
import sys
import time
import traceback

import cv2
import numpy as np

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap, QFont
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QVBoxLayout,
    QHBoxLayout, QFileDialog, QSlider, QSpinBox, QComboBox, QGroupBox,
    QGridLayout, QMessageBox, QSizePolicy, QFrame, QTabWidget, QTextEdit
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import AppConfig, ensure_dirs, CONFIG_PATH  # noqa: E402
from detector import CanDetector, ModelLoadError  # noqa: E402
from draw import draw_detections  # noqa: E402
from camera_tab import LiveCameraTab  # noqa: E402
from diagnostics_tab import DiagnosticsTab  # noqa: E402
from rejection_history_tab import RejectionHistoryTab  # noqa: E402
from hardware_config_tab import HardwareConfigTab  # noqa: E402


LIGHT_STYLE = """
QMainWindow, QWidget { background-color: #eef1f5; color: #1a1d21; font-family: 'Segoe UI', Arial; font-size: 13px; }
QGroupBox { background-color: #ffffff; border: 1px solid #c7ccd4; border-radius: 8px; margin-top: 14px; font-weight: 700; color: #0d47a1; padding-top: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }
QPushButton { background-color: #1565c0; border: 1px solid #0d47a1; border-radius: 5px; padding: 9px 16px; color: #ffffff; font-weight: 600; }
QPushButton:hover { background-color: #1976d2; }
QPushButton:pressed { background-color: #0d47a1; }
QPushButton:disabled { background-color: #d5d9de; border-color: #d5d9de; color: #8a919c; }
QLabel#imageLabel { background-color: #15171a; border: 1px solid #c7ccd4; border-radius: 6px; color: #aab0bb; }
QLabel#statusOk { color: #1b8a3f; font-weight: 700; }
QLabel#statusBad { color: #c62828; font-weight: 700; }
QLabel#statusWarn { color: #b36b00; font-weight: 700; }
QSlider::groove:horizontal { height: 6px; background: #c7ccd4; border-radius: 3px; }
QSlider::handle:horizontal { background: #1565c0; width: 16px; margin: -6px 0; border-radius: 8px; }
QTabWidget::pane { border: 1px solid #c7ccd4; background: #ffffff; top: -1px; }
QTabBar::tab { background: #dde2e8; padding: 10px 22px; color: #3a4048; font-weight: 600; border: 1px solid #c7ccd4; border-bottom: none; border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; }
QTabBar::tab:selected { background: #1565c0; color: #ffffff; }
QTabBar::tab:hover:!selected { background: #c9d1db; }
QTextEdit { background-color: #ffffff; border: 1px solid #c7ccd4; color: #1a1d21; font-family: Consolas, monospace; }
QComboBox, QSpinBox, QLineEdit { background-color: #ffffff; border: 1px solid #c7ccd4; border-radius: 4px; padding: 5px 6px; color: #1a1d21; }
QComboBox:disabled, QSpinBox:disabled, QLineEdit:disabled { background-color: #eceff2; color: #8a919c; }
QCheckBox { color: #1a1d21; spacing: 8px; }
QScrollArea { background-color: transparent; border: none; }
QScrollBar:vertical { background: #eef1f5; width: 12px; }
QScrollBar::handle:vertical { background: #c7ccd4; border-radius: 5px; min-height: 24px; }
"""



class ModelLoadWorker(QThread):
    """Loads the YOLO model on a background thread so the GUI stays
    responsive instead of showing 'Not Responding' during load."""
    finished_ok = pyqtSignal(dict)   # class_names dict
    finished_err = pyqtSignal(str)   # error message

    def __init__(self, detector: CanDetector, model_path: str):
        super().__init__()
        self.detector = detector
        self.model_path = model_path

    def run(self):
        try:
            classes = self.detector.load(self.model_path)
            self.finished_ok.emit(dict(classes))
        except ModelLoadError as e:
            self.finished_err.emit(str(e))
        except Exception as e:
            self.finished_err.emit(f"Unexpected error: {e}")


def cv2_to_qpixmap(frame_bgr: np.ndarray) -> QPixmap:
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb.shape
    bytes_per_line = ch * w
    qimg = QImage(rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())  # copy: rgb buffer is a local var


class ImageTestTab(QWidget):
    """Stage 1 / Requirement #8: Image Test Mode."""

    def __init__(self, cfg: AppConfig, detector: CanDetector, log_fn):
        super().__init__()
        self.cfg = cfg
        self.detector = detector
        self.log = log_fn
        self.current_image_path = None
        self.current_image_bgr = None
        self._build_ui()

    def _build_ui(self):
        root = QHBoxLayout(self)

        # ---- Left: image display ----
        left = QVBoxLayout()
        self.image_label = QLabel("No image loaded")
        self.image_label.setObjectName("imageLabel")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumSize(480, 480)
        self.image_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        left.addWidget(self.image_label, stretch=1)

        btn_row = QHBoxLayout()
        self.btn_load_image = QPushButton("Load Test Image")
        self.btn_load_image.clicked.connect(self.on_load_image)
        self.btn_run = QPushButton("Run Detection")
        self.btn_run.clicked.connect(self.on_run_detection)
        self.btn_run.setEnabled(False)
        btn_row.addWidget(self.btn_load_image)
        btn_row.addWidget(self.btn_run)
        left.addLayout(btn_row)

        root.addLayout(left, stretch=3)

        # ---- Right: controls + results ----
        right = QVBoxLayout()

        params_box = QGroupBox("Inference Settings (Requirement #17 — configurable, not hard-coded)")
        pg = QGridLayout(params_box)

        pg.addWidget(QLabel("Confidence:"), 0, 0)
        self.conf_slider = QSlider(Qt.Horizontal)
        self.conf_slider.setRange(1, 99)
        self.conf_slider.setValue(int(self.cfg.confidence * 100))
        self.conf_value_lbl = QLabel(f"{self.cfg.confidence:.2f}")
        self.conf_slider.valueChanged.connect(
            lambda v: self.conf_value_lbl.setText(f"{v/100:.2f}"))
        pg.addWidget(self.conf_slider, 0, 1)
        pg.addWidget(self.conf_value_lbl, 0, 2)

        pg.addWidget(QLabel("IoU:"), 1, 0)
        self.iou_slider = QSlider(Qt.Horizontal)
        self.iou_slider.setRange(1, 99)
        self.iou_slider.setValue(int(self.cfg.iou * 100))
        self.iou_value_lbl = QLabel(f"{self.cfg.iou:.2f}")
        self.iou_slider.valueChanged.connect(
            lambda v: self.iou_value_lbl.setText(f"{v/100:.2f}"))
        pg.addWidget(self.iou_slider, 1, 1)
        pg.addWidget(self.iou_value_lbl, 1, 2)

        pg.addWidget(QLabel("Image size:"), 2, 0)
        self.imgsz_combo = QComboBox()
        for sz in [320, 480, 544, 640, 736, 960, 1280]:
            self.imgsz_combo.addItem(str(sz))
        self.imgsz_combo.setCurrentText(str(self.cfg.imgsz))
        pg.addWidget(self.imgsz_combo, 2, 1, 1, 2)

        right.addWidget(params_box)

        status_box = QGroupBox("Status")
        sg = QGridLayout(status_box)
        self.lbl_model_status = QLabel("Model: not loaded")
        self.lbl_model_status.setObjectName("statusBad")
        self.lbl_device_status = QLabel("Device: —")
        self.lbl_classes = QLabel("Classes: —")
        sg.addWidget(self.lbl_model_status, 0, 0)
        sg.addWidget(self.lbl_device_status, 1, 0)
        sg.addWidget(self.lbl_classes, 2, 0)
        right.addWidget(status_box)

        results_box = QGroupBox("Detection Results")
        rg = QGridLayout(results_box)
        self.lbl_resolution = QLabel("Resolution: —")
        self.lbl_proc_time = QLabel("Processing time: —")
        self.lbl_count = QLabel("Detections: —")
        self.lbl_good = QLabel("GOOD: —")
        self.lbl_reject = QLabel("REJECT: —")
        for i, w in enumerate([self.lbl_resolution, self.lbl_proc_time,
                                self.lbl_count, self.lbl_good, self.lbl_reject]):
            rg.addWidget(w, i, 0)
        right.addWidget(results_box)

        detail_box = QGroupBox("Per-Detection Detail")
        db = QVBoxLayout(detail_box)
        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        self.detail_text.setMinimumHeight(160)
        db.addWidget(self.detail_text)
        right.addWidget(detail_box, stretch=1)

        root.addLayout(right, stretch=2)

    def set_model_status(self, ok: bool, text: str, classes: dict = None):
        self.lbl_model_status.setText(text)
        self.lbl_model_status.setObjectName("statusOk" if ok else "statusBad")
        self.lbl_model_status.setStyle(self.lbl_model_status.style())
        if classes:
            self.lbl_classes.setText("Classes: " + ", ".join(f"{k}:{v}" for k, v in classes.items()))
        self.lbl_device_status.setText(f"Device: {self.detector.active_device_label()}")
        self.btn_run.setEnabled(ok and self.current_image_bgr is not None)

    def on_load_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Test Image", self.cfg.model_path and os.path.dirname(self.cfg.model_path) or "",
            "Images (*.jpg *.jpeg *.png *.bmp)"
        )
        if not path:
            return
        img = cv2.imread(path)
        if img is None:
            QMessageBox.critical(self, "Error", f"Could not read image:\n{path}")
            return
        self.current_image_path = path
        self.current_image_bgr = img
        self._show_frame(img)
        self.log(f"Loaded test image: {path}  ({img.shape[1]}x{img.shape[0]})")
        self.btn_run.setEnabled(self.detector.is_loaded())

    def _show_frame(self, frame_bgr):
        pix = cv2_to_qpixmap(frame_bgr)
        scaled = pix.scaled(self.image_label.width(), self.image_label.height(),
                             Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.image_label.setPixmap(scaled)

    def on_run_detection(self):
        if self.current_image_bgr is None or not self.detector.is_loaded():
            return
        conf = self.conf_slider.value() / 100.0
        iou = self.iou_slider.value() / 100.0
        imgsz = int(self.imgsz_combo.currentText())

        frame = self.current_image_bgr.copy()
        try:
            result = self.detector.predict(frame, conf=conf, iou=iou, imgsz=imgsz)
        except Exception as e:
            QMessageBox.critical(self, "Inference Error", str(e))
            return

        annotated = draw_detections(frame.copy(), result.detections)
        self._show_frame(annotated)

        h, w = result.image_shape
        n_good = sum(1 for d in result.detections if d.role == "GOOD")
        n_reject = sum(1 for d in result.detections if d.role == "REJECT")
        n_unknown = sum(1 for d in result.detections if d.role == "UNKNOWN")

        self.lbl_resolution.setText(f"Resolution: {w} x {h}")
        self.lbl_proc_time.setText(f"Processing time: {result.inference_ms:.1f} ms")
        self.lbl_count.setText(f"Detections: {len(result.detections)}")
        self.lbl_good.setText(f"GOOD: {n_good}")
        self.lbl_reject.setText(f"REJECT: {n_reject}" + (f"   (UNKNOWN: {n_unknown})" if n_unknown else ""))

        lines = []
        for i, d in enumerate(result.detections, 1):
            lines.append(f"[{i}] {d.role}_CAN  '{d.class_name}'  conf={d.confidence*100:.1f}%  "
                         f"box=({d.xyxy[0]:.0f},{d.xyxy[1]:.0f})-({d.xyxy[2]:.0f},{d.xyxy[3]:.0f})")
        self.detail_text.setPlainText("\n".join(lines) if lines else "(no detections at current settings)")

        self.log(f"Inference: {os.path.basename(self.current_image_path)} | "
                 f"conf={conf:.2f} iou={iou:.2f} imgsz={imgsz} | "
                 f"{len(result.detections)} dets (GOOD {n_good} / REJECT {n_reject}) | "
                 f"{result.inference_ms:.1f} ms")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CanFallingDetector — Stage 1: Image Test Mode  [OFFLINE / SIMULATION]")
        self.resize(1200, 760)

        # --- Help menu / About dialog (also carries the copyright notice) ---
        help_menu = self.menuBar().addMenu("Help")
        about_action = help_menu.addAction("About")
        about_action.triggered.connect(self._show_about_dialog)

        self.cfg = AppConfig.load(CONFIG_PATH)
        ensure_dirs(self.cfg)
        self._log_file_path = os.path.join(self.cfg.log_dir, "app.log")
        self.detector = CanDetector(self.cfg)

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)

        # Top bar: model load + banner
        top = QHBoxLayout()
        self.btn_load_model = QPushButton("Browse Model (.onnx)")
        self.btn_load_model.clicked.connect(self.on_browse_model)
        self.lbl_model_path = QLabel(self._short_path(self.cfg.model_path))
        top.addWidget(self.btn_load_model)
        top.addWidget(self.lbl_model_path)
        top.addStretch(1)

        banner = QLabel("SIMULATION MODE — no PLC / machine output connected")
        banner.setStyleSheet(
            "background:#fff3cd; color:#7a5200; padding:6px 12px; border-radius:4px; "
            "border:1px solid #ffdf7e; font-weight:600;")
        top.addWidget(banner)
        outer.addLayout(top)

        # Log panel (created BEFORE tabs, since tab construction calls self.append_log)
        log_box = QGroupBox("Log")
        log_layout = QVBoxLayout(log_box)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(120)
        log_layout.addWidget(self.log_text)

        # Tabs
        self.tabs = QTabWidget()
        self.image_tab = ImageTestTab(self.cfg, self.detector, self.append_log)
        self.tabs.addTab(self.image_tab, "Image Test")
        self.camera_tab = LiveCameraTab(self.cfg, self.detector, self.append_log)
        self.tabs.addTab(self.camera_tab, "Live Camera")
        self.rejection_history_tab = RejectionHistoryTab(self.camera_tab, self.append_log)
        self.tabs.addTab(self.rejection_history_tab, "Rejection History")
        self.diagnostics_tab = DiagnosticsTab(self.camera_tab, self.append_log)
        # Back-reference so the Live Camera tab can enforce the Diagnostic
        # Mode / Live Mode interlock (see camera_tab.on_start()).
        self.camera_tab.diagnostics_tab = self.diagnostics_tab
        self.tabs.addTab(self.diagnostics_tab, "Diagnostics")
        self.hardware_config_tab = HardwareConfigTab(self.camera_tab, self.append_log)
        self.tabs.addTab(self.hardware_config_tab, "System Configuration")
        outer.addWidget(self.tabs, stretch=1)
        outer.addWidget(log_box)

        self.append_log("Application started. Fully offline — no network calls are made.")
        self._try_autoload_model()

    def _short_path(self, p):
        return p if len(p) < 70 else "..." + p[-67:]

    def _show_about_dialog(self):
        QMessageBox.about(
            self, "About CanFallingDetector",
            "<h3>CanFallingDetector</h3>"
            "<p>Automated vision-based can-falling detection with "
            "PLC-controlled reject/reset integration.</p>"
            "<p>Software designed, directed, generated, and created by "
            "<b>RAMI HAFIENE</b>.</p>"
        )

    def closeEvent(self, event):
        # Ensure the camera is released cleanly if the window is closed
        # while Live Camera mode is running, and that the PLC reject
        # output is guaranteed OFF on shutdown regardless of latch state.
        if hasattr(self, "camera_tab"):
            self.camera_tab.on_stop()
            try:
                self.camera_tab.plc.reject_off()
            except Exception:
                pass  # shutdown must never be blocked by a PLC error
            try:
                self.camera_tab.on_plc_disconnect()
            except Exception:
                pass  # same guarantee for the real Modbus connection, if active
        event.accept()

    def append_log(self, text):
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {text}"
        self.log_text.append(line)
        try:
            with open(self._log_file_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass  # a logging failure must never interrupt the app itself

    def _try_autoload_model(self):
        if os.path.exists(self.cfg.model_path):
            self._load_model(self.cfg.model_path)
        else:
            self.append_log(f"No model found at default path: {self.cfg.model_path}. Use 'Browse Model'.")

    def on_browse_model(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select YOLO model", "", "ONNX model (*.onnx);;PyTorch model (*.pt)")
        if not path:
            return
        if path.lower().endswith(".pt"):
            QMessageBox.warning(
                self, "PyTorch model not supported in this build",
                "This application runs on ONNX Runtime only (not PyTorch), specifically to "
                "avoid the DLL crashes that PyTorch causes on some Windows PCs.\n\n"
                "Please convert your .pt file to .onnx first, then select the .onnx file here.\n\n"
                "If you don't have a way to convert it, send the .pt file back to whoever "
                "provided this application for conversion."
            )
            self.append_log(f"Rejected .pt file (not supported in this build): {path}")
            return
        self._load_model(path)

    def _load_model(self, path):
        self.append_log(f"Loading model: {path} ... (running in background, window stays responsive)")
        self.btn_load_model.setEnabled(False)
        self.image_tab.set_model_status(False, "Model: LOADING...")

        self._pending_model_path = path
        self._load_worker = ModelLoadWorker(self.detector, path)
        self._load_worker.finished_ok.connect(self._on_model_loaded)
        self._load_worker.finished_err.connect(self._on_model_load_failed)
        self._load_worker.start()

    def _on_model_loaded(self, classes: dict):
        self._load_worker.wait()  # ensure OS thread fully joins before continuing
        path = self._pending_model_path
        self.cfg.model_path = path
        self.cfg.save()
        self.lbl_model_path.setText(self._short_path(path))
        self.append_log(f"Model loaded OK. Classes: {classes}. Device: {self.detector.active_device_label()}.")
        self.image_tab.set_model_status(True, "Model: LOADED", classes)
        self.btn_load_model.setEnabled(True)

    def _on_model_load_failed(self, error_msg: str):
        self._load_worker.wait()  # ensure OS thread fully joins before continuing
        self.append_log(f"ERROR: {error_msg}")
        self.image_tab.set_model_status(False, "Model: FAILED TO LOAD")
        self.btn_load_model.setEnabled(True)
        QMessageBox.critical(self, "Model Load Failed", error_msg)


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(LIGHT_STYLE)
    win = MainWindow()

    # Global safety net: log any uncaught exception to a file and show a
    # dialog instead of letting the whole application silently disappear.
    def handle_exception(exc_type, exc_value, exc_tb):
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            win.append_log(f"UNCAUGHT ERROR: {exc_value}\n{tb_text}")
        except Exception:
            pass
        try:
            QMessageBox.critical(win, "Unexpected Error",
                                  f"An unexpected error occurred and was logged:\n\n{exc_value}\n\n"
                                  f"The application will try to keep running.")
        except Exception:
            pass

    sys.excepthook = handle_exception

    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
