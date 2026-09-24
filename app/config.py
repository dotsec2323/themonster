"""
config.py
---------
Central configuration for CanFallingDetector.

All tunable parameters live here and are persisted to config/settings.json
so the app reloads the same setup on restart (Requirement #23).

Nothing in this file is hard-coded into the detection logic elsewhere —
other modules always read values via AppConfig, never literals.
"""

import json
import os
from dataclasses import dataclass, asdict, field
from typing import Optional


# Project root = parent of the "app" folder this file lives in
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(ROOT_DIR, "config")
CONFIG_PATH = os.path.join(CONFIG_DIR, "settings.json")
LOGS_DIR = os.path.join(ROOT_DIR, "logs")
MODELS_DIR = os.path.join(ROOT_DIR, "models")
# Uses ONNX Runtime (not torch) for inference -- see detector.py for why.
DEFAULT_MODEL_PATH = os.path.join(MODELS_DIR, "best.onnx")


@dataclass
class AppConfig:
    # --- Model / inference settings (Requirement #17) ---
    model_path: str = DEFAULT_MODEL_PATH
    confidence: float = 0.25
    iou: float = 0.50
    imgsz: int = 640
    device: str = "auto"  # "auto" | "cpu" | "0" (GPU index)

    # --- Detection ROI (zone), saved/restored from the Live Camera page ---
    roi_top_pct: int = 0
    roi_bottom_pct: int = 100
    roi_left_pct: int = 0
    roi_right_pct: int = 100

    # --- Admin gate for the System / Hardware Configuration page ---
    admin_username: str = "root"
    admin_password: str = "3322"

    # --- Class role mapping ---
    # The trained model's actual class names (from best.pt) are:
    #   0: "fall can"   -> currently mapped to REJECT
    #   1: "Good can"   -> currently mapped to GOOD
    # This mapping is intentionally NOT hard-coded into detection logic —
    # it is read from here, so if class semantics change (or a retrained
    # model swaps indices/names), only this config needs to change.
    good_class_names: list = field(default_factory=lambda: ["Good can", "good_can"])
    reject_class_names: list = field(default_factory=lambda: ["fall can", "reject_can"])

    # --- Camera settings (Stage 5+) ---
    camera_index: int = 0
    camera_width: int = 1280
    camera_height: int = 720
    camera_fps_target: int = 60
    camera_rotation: int = 0  # 0, 90, 180, or 270 degrees

    # --- Reject workflow (stop-and-latch until operator RESET) ---
    reject_folder: Optional[str] = None  # None -> defaults to logs/fault_images

    # --- Real PLC connection: Siemens LOGO! (8), Modbus TCP ---
    # LOGO! only exposes two physical I/O points for this integration:
    #   Q0.0 (first digital output) -- the reject actuator
    #   I0.0 (first digital input)  -- the physical reset button
    # Both are bit-level Modbus points (coil / discrete input), NOT the
    # word-based holding-register scheme used by the old M241 integration
    # -- LOGO! has no PLC-side program computing a status/ack/event-ID
    # word, so the app now owns that small state machine itself (see the
    # module docstring in plc_modbus.py for the full mapping/behavior).
    #
    # DEFAULT ADDRESSES BELOW ARE NOT CONFIRMED AGAINST A REAL UNIT.
    # LOGO!'s exact Modbus function-code/address mapping for I/Q varies by
    # firmware version and how the "Network Inputs/Outputs" are configured
    # in LOGO! Soft Comfort -- confirm Q1 -> coil address and I1 -> discrete
    # input address against your actual LOGO! project (and its Modbus
    # server documentation) before going live. See plc_modbus.py.
    plc_mode: str = "simulated"  # "simulated" | "modbus"
    # Both can be enabled at once -- the app treats them as interchangeable
    # equivalents of the same PLC I/O, not two separate control systems.
    # This is what lets you develop/trial fully on the Arduino now, add the
    # LOGO! later, or run both side by side during the transition.
    plc_enable_tcp: bool = False      # Ethernet -- e.g. the real LOGO!
    plc_enable_serial: bool = False   # USB Serial -- e.g. an Arduino test rig
    plc_ip: str = "192.168.1.20"
    plc_port: int = 502
    plc_serial_port: str = "COM3" if os.name == "nt" else "/dev/ttyUSB0"
    plc_serial_baudrate: int = 9600
    plc_unit_id: int = 1
    plc_reject_coil_address: int = 0        # Q0.0 (Q1): PC -> LOGO!, reject actuator (coil, read/write)
    plc_reset_input_address: int = 0        # I0.0 (I1): LOGO! -> PC, physical reset button (discrete input, read-only)
    plc_test_coil_address: Optional[int] = None  # optional internal marker/flag bit (e.g. M1) for the
                                                  # isolated maintenance Test Read/Write buttons --
                                                  # leave as None until you've wired one up; never a
                                                  # real physical I/O point. See plc_modbus.py.
    plc_timeout_s: float = 1.0
    plc_reconnect_interval_s: float = 3.0
    plc_poll_interval_s: float = 0.25
    plc_ack_timeout_s: float = 5.0          # how long to wait for the reject coil write to be
                                             # confirmed before reject_timeout (see plc_modbus.py)

    # --- Production safety ---
    disk_space_warning_mb: int = 500        # warn when the reject folder's drive has less free space than this

    # --- ROI / event line settings (Stage 7+) placeholder ---
    roi_points: Optional[list] = None      # polygon points, normalized 0-1
    event_line: Optional[list] = None      # [(x1,y1),(x2,y2)] normalized 0-1

    # --- Tracking settings (Stage 6+) placeholder ---
    tracker: str = "bytetrack.yaml"
    track_id_cooldown_events: int = 1  # events per track id (not time-based)

    # --- Output pulse (Stage 9+) placeholder ---
    reject_pulse_ms: int = 100
    good_pulse_ms: int = 50

    # --- Logging ---
    log_dir: str = LOGS_DIR

    def save(self, path: str = CONFIG_PATH):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls, path: str = CONFIG_PATH) -> "AppConfig":
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Merge with defaults so new fields added in later stages
                # don't break loading of an older settings.json
                defaults = asdict(cls())
                defaults.update(data)
                return cls(**defaults)
            except Exception:
                # Corrupt config -> fall back to defaults rather than crash
                return cls()
        return cls()


def ensure_dirs(cfg: AppConfig):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(cfg.log_dir, exist_ok=True)
    os.makedirs(MODELS_DIR, exist_ok=True)
