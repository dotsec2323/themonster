"""
hardware_config_tab.py
------------------------
System / Hardware Configuration page: Siemens LOGO! PLC and Arduino/USB
serial test-rig connection settings, moved off the operator-facing Live
Camera page and behind a simple administrator login, per spec.

The actual connection objects/logic (self.modbus_plc_tcp/serial,
on_plc_connect(), the reject/reset state machine, etc.) still live on
camera_tab.py, completely unchanged -- the reject workflow there depends
on them directly, and moving them would risk the very handshake logic
that was just fixed. This page only relocates WHERE the PLC Connection
panel widget is DISPLAYED (via camera_tab.get_plc_panel_widget(), which
hands over the exact same, fully-functional widget built in
camera_tab.py), and adds the login gate in front of it.

Credentials are the simple username/password pair specified for this
internal tool (root / 3322, stored in config.py) -- not a real
authentication system. This restricts casual access from the operator
view, not a security boundary against a determined user with access to
the source or config file.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QLineEdit, QStackedWidget, QGroupBox, QScrollArea
)


class HardwareConfigTab(QWidget):
    def __init__(self, camera_tab, log_fn):
        super().__init__()
        self.camera_tab = camera_tab
        self.log = log_fn
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        self.stack = QStackedWidget()
        root.addWidget(self.stack)

        # ---- Login page ----
        login_page = QWidget()
        lg = QVBoxLayout(login_page)
        lg.addStretch(1)
        login_box = QGroupBox("Administrator Login Required")
        login_box.setMaximumWidth(380)
        bl = QVBoxLayout(login_box)
        info = QLabel("System / Hardware Configuration (PLC and Arduino settings) "
                       "is restricted to administrators.")
        info.setWordWrap(True)
        bl.addWidget(info)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Username:"))
        self.login_user_edit = QLineEdit()
        row1.addWidget(self.login_user_edit)
        bl.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Password:"))
        self.login_pass_edit = QLineEdit()
        self.login_pass_edit.setEchoMode(QLineEdit.Password)
        self.login_pass_edit.returnPressed.connect(self.on_login)
        row2.addWidget(self.login_pass_edit)
        bl.addLayout(row2)

        self.lbl_login_error = QLabel("")
        self.lbl_login_error.setStyleSheet("color:#c62828; font-weight:600;")
        bl.addWidget(self.lbl_login_error)

        self.btn_login = QPushButton("Login")
        self.btn_login.clicked.connect(self.on_login)
        bl.addWidget(self.btn_login)

        login_wrap = QHBoxLayout()
        login_wrap.addStretch(1)
        login_wrap.addWidget(login_box)
        login_wrap.addStretch(1)
        lg.addLayout(login_wrap)
        lg.addStretch(2)
        self.stack.addWidget(login_page)

        # ---- Settings page (shown only after a successful login) ----
        settings_page = QWidget()
        sg = QVBoxLayout(settings_page)

        top_row = QHBoxLayout()
        title = QLabel("System / Hardware Configuration")
        title.setStyleSheet("font-size:18px; font-weight:700; color:#0d47a1;")
        self.btn_logout = QPushButton("Log Out")
        self.btn_logout.clicked.connect(self.on_logout)
        top_row.addWidget(title)
        top_row.addStretch(1)
        top_row.addWidget(self.btn_logout)
        sg.addLayout(top_row)

        note = QLabel("Changes here affect PLC (Siemens LOGO!) and Arduino/serial test-rig "
                       "communication. Only administrators should access this page.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#5f6672; font-size:11px;")
        sg.addWidget(note)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        # This is the SAME widget object built (and fully wired) in
        # camera_tab.py -- reparented here, not rebuilt, so every existing
        # connection/handler keeps working exactly as before.
        content_layout.addWidget(self.camera_tab.get_plc_panel_widget())
        content_layout.addStretch(1)
        scroll.setWidget(content)
        sg.addWidget(scroll, stretch=1)

        self.stack.addWidget(settings_page)
        self.stack.setCurrentIndex(0)

    def on_login(self):
        user = self.login_user_edit.text().strip()
        pw = self.login_pass_edit.text()
        cfg = self.camera_tab.cfg
        expected_user = getattr(cfg, "admin_username", "root")
        expected_pass = getattr(cfg, "admin_password", "3322")
        if user == expected_user and pw == expected_pass:
            self.lbl_login_error.setText("")
            self.login_user_edit.clear()
            self.login_pass_edit.clear()
            self.stack.setCurrentIndex(1)
            self.log("Administrator logged in to System / Hardware Configuration.")
        else:
            self.lbl_login_error.setText("Incorrect username or password.")
            self.login_pass_edit.clear()
            self.log("Failed administrator login attempt on System / Hardware Configuration.")

    def on_logout(self):
        self.stack.setCurrentIndex(0)
        self.log("Administrator logged out of System / Hardware Configuration.")
