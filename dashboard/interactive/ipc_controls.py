"""
ipc_controls.py
InterSync Dashboard — IPC Interactive Control Panel (Phase 2).

IpcControlPanel is the left-side widget for the IPC Flow tab.
It exposes:
  • Mechanism dropdown  (PIPE / QUEUE / SOCKET / SHM)
  • Producer / Consumer container dropdowns
  • Message size slider  (8 B → 8 KB)
  • Send mode toggle     (Single Shot | Burst | Continuous)
  • Burst count spinbox  (visible in Burst mode)
  • Send rate slider     (visible in Continuous mode, msgs/sec)
  • [SEND NOW]  [START]  [STOP]  buttons
  • Log level dropdown

All user actions are forwarded to an InteractiveBackend instance.
Status updates from EventBus flow back as toast/visual cues on the canvas.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSlider, QSpinBox, QGroupBox, QButtonGroup,
    QRadioButton, QFrame, QSizePolicy,
)

from dashboard.ui.theme import (
    TEAL, TEAL_DIM, TEAL_DARK, BORDER, CARD, SURFACE,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_DIM, DARK_BG,
    PURPLE, GREEN, RED,
)
from dashboard.backend.event_bus import EventBus

CONTAINER_NAMES = ["interync-lab-1", "interync-lab-2", "interync-lab-3"]

MECHANISM_COLORS = {
    "PIPE":   TEAL,
    "QUEUE":  PURPLE,
    "SOCKET": GREEN,
    "SHM":    "#FF6D00",
}

_SECTION_STYLE = f"""
    QGroupBox {{
        background-color: {CARD};
        border: 1px solid {BORDER};
        border-radius: 8px;
        margin-top: 14px;
        padding: 10px 8px 8px 8px;
        font-size: 10px;
        font-weight: bold;
        color: {TEXT_SECONDARY};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 6px;
        color: {TEAL};
        font-size: 9px;
        text-transform: uppercase;
        letter-spacing: 1px;
    }}
"""

_SLIDER_STYLE = f"""
    QSlider::groove:horizontal {{
        height: 4px;
        background: {BORDER};
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: {TEAL};
        width: 14px;
        height: 14px;
        margin: -5px 0;
        border-radius: 7px;
    }}
    QSlider::sub-page:horizontal {{
        background: {TEAL_DARK};
        border-radius: 2px;
    }}
"""


class IpcControlPanel(QWidget):
    """
    Left control panel for the IPC Flow tab.

    Signals:
        mechanism_changed(str)   — emitted when user picks a different mechanism
        send_requested(dict)     — emitted on [SEND NOW] with params dict
        burst_started(dict)      — emitted on [START BURST]
        burst_stopped()          — emitted on [STOP]
    """

    mechanism_changed = pyqtSignal(str)
    send_requested    = pyqtSignal(dict)
    burst_started     = pyqtSignal(dict)
    burst_stopped     = pyqtSignal()

    def __init__(self, interactive_backend=None, parent=None):
        super().__init__(parent)
        self._ib   = interactive_backend
        self._bus  = EventBus.instance()
        self._cont_timer: Optional[QTimer] = None  # continuous mode timer

        self._build_ui()
        self._connect_signals()

    # ------------------------------------------------------------------ #
    # UI construction                                                       #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 16, 10)
        root.setSpacing(10)

        # Section: Mechanism
        mech_grp = QGroupBox("Mechanism")
        mech_grp.setStyleSheet(_SECTION_STYLE)
        m_layout = QVBoxLayout(mech_grp)
        m_layout.setSpacing(6)

        self._mech_combo = QComboBox()
        self._mech_combo.addItems(["PIPE", "QUEUE", "SOCKET", "SHM"])
        self._mech_combo.setToolTip("IPC mechanism to use")
        m_layout.addWidget(self._mech_combo)
        root.addWidget(mech_grp)

        # Section: Containers
        ctr_grp = QGroupBox("Containers")
        ctr_grp.setStyleSheet(_SECTION_STYLE)
        c_layout = QVBoxLayout(ctr_grp)
        c_layout.setSpacing(6)

        c_layout.addWidget(QLabel("Producer:"))
        self._producer_combo = QComboBox()
        self._producer_combo.addItems(CONTAINER_NAMES)
        c_layout.addWidget(self._producer_combo)

        c_layout.addWidget(QLabel("Consumer:"))
        self._consumer_combo = QComboBox()
        self._consumer_combo.addItems(CONTAINER_NAMES)
        self._consumer_combo.setCurrentIndex(1)
        c_layout.addWidget(self._consumer_combo)

        root.addWidget(ctr_grp)

        # Section: Message size
        size_grp = QGroupBox("Message Size")
        size_grp.setStyleSheet(_SECTION_STYLE)
        s_layout = QVBoxLayout(size_grp)

        self._size_label = QLabel("256 B")
        self._size_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._size_label.setStyleSheet(
            f"color: {TEAL}; font-size: 14px; font-weight: bold; background: transparent;")

        self._size_slider = QSlider(Qt.Orientation.Horizontal)
        self._size_slider.setRange(0, 9)          # mapped to _SIZE_STEPS
        self._size_slider.setValue(3)             # 256 B
        self._size_slider.setStyleSheet(_SLIDER_STYLE)

        s_layout.addWidget(self._size_label)
        s_layout.addWidget(self._size_slider)
        root.addWidget(size_grp)

        # Section: Send Mode
        mode_grp = QGroupBox("Send Mode")
        mode_grp.setStyleSheet(_SECTION_STYLE)
        mode_layout = QVBoxLayout(mode_grp)
        mode_layout.setSpacing(4)

        self._rb_single = QRadioButton("Single Shot")
        self._rb_burst  = QRadioButton("Burst")
        self._rb_cont   = QRadioButton("Continuous")
        self._rb_single.setChecked(True)

        for rb in (self._rb_single, self._rb_burst, self._rb_cont):
            rb.setStyleSheet(f"color: {TEXT_PRIMARY}; background: transparent;")
            mode_layout.addWidget(rb)

        self._mode_group = QButtonGroup()
        self._mode_group.addButton(self._rb_single, 0)
        self._mode_group.addButton(self._rb_burst,  1)
        self._mode_group.addButton(self._rb_cont,   2)

        # Burst count (visible when Burst selected)
        self._burst_row = QWidget()
        burst_h = QHBoxLayout(self._burst_row)
        burst_h.setContentsMargins(0, 0, 0, 0)
        burst_h.addWidget(QLabel("Count:"))
        self._burst_spin = QSpinBox()
        self._burst_spin.setRange(1, 10000)
        self._burst_spin.setValue(50)
        self._burst_spin.setSingleStep(10)
        burst_h.addWidget(self._burst_spin)
        self._burst_row.hide()
        mode_layout.addWidget(self._burst_row)

        # Rate slider (visible when Continuous selected)
        self._rate_row = QWidget()
        rate_v = QVBoxLayout(self._rate_row)
        rate_v.setContentsMargins(0, 0, 0, 0)
        self._rate_label = QLabel("Rate: 10 msg/s")
        self._rate_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; background: transparent;")
        rate_v.addWidget(self._rate_label)
        self._rate_slider = QSlider(Qt.Orientation.Horizontal)
        self._rate_slider.setRange(1, 100)
        self._rate_slider.setValue(10)
        self._rate_slider.setStyleSheet(_SLIDER_STYLE)
        rate_v.addWidget(self._rate_slider)
        self._rate_row.hide()
        mode_layout.addWidget(self._rate_row)

        root.addWidget(mode_grp)

        # Divider
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {BORDER};")
        root.addWidget(sep)

        # Action buttons
        self._btn_send  = QPushButton("Send Now")
        self._btn_send.setObjectName("btnRun")
        self._btn_send.setFixedHeight(38)

        self._btn_start = QPushButton("Start Burst")
        self._btn_start.setObjectName("btnRun")
        self._btn_start.setFixedHeight(38)
        self._btn_start.hide()

        self._btn_stop  = QPushButton("Stop")
        self._btn_stop.setObjectName("btnStop")
        self._btn_stop.setFixedHeight(38)
        self._btn_stop.hide()

        root.addWidget(self._btn_send)
        root.addWidget(self._btn_start)
        root.addWidget(self._btn_stop)

        # Log level
        log_grp = QGroupBox("Log Level")
        log_grp.setStyleSheet(_SECTION_STYLE)
        log_layout = QVBoxLayout(log_grp)
        self._log_combo = QComboBox()
        self._log_combo.addItems(["Errors Only", "All Events"])
        log_layout.addWidget(self._log_combo)
        root.addWidget(log_grp)

        root.addStretch()

        # Disable action buttons when no backend
        if not self._ib:
            self._btn_send.setEnabled(False)
            self._btn_start.setEnabled(False)

    # ------------------------------------------------------------------ #
    # Signal wiring                                                         #
    # ------------------------------------------------------------------ #

    _SIZE_STEPS = [8, 16, 64, 256, 512, 1024, 2048, 4096, 8192, 16384]

    def _connect_signals(self) -> None:
        self._mech_combo.currentTextChanged.connect(self._on_mechanism_changed)
        self._size_slider.valueChanged.connect(self._on_size_changed)
        self._rate_slider.valueChanged.connect(
            lambda v: self._rate_label.setText(f"Rate: {v} msg/s"))
        self._mode_group.idClicked.connect(self._on_mode_changed)
        self._btn_send.clicked.connect(self._on_send_now)
        self._btn_start.clicked.connect(self._on_start)
        self._btn_stop.clicked.connect(self._on_stop)

        # React to EventBus
        self._bus.ipc_burst_complete.connect(self._on_burst_done)
        self._bus.ipc_error.connect(self._on_ipc_error)

    def _on_mechanism_changed(self, mech: str) -> None:
        color = MECHANISM_COLORS.get(mech, TEAL)
        self._size_label.setStyleSheet(
            f"color: {color}; font-size: 14px; font-weight: bold; background: transparent;")
        self.mechanism_changed.emit(mech)

    def _on_size_changed(self, idx: int) -> None:
        size = self._SIZE_STEPS[idx]
        label = f"{size} B" if size < 1024 else f"{size // 1024} KB"
        self._size_label.setText(label)

    def _on_mode_changed(self, mode_id: int) -> None:
        self._burst_row.setVisible(mode_id == 1)
        self._rate_row.setVisible(mode_id == 2)
        self._btn_send.setVisible(mode_id == 0)
        self._btn_start.setVisible(mode_id in (1, 2))
        self._btn_stop.setVisible(mode_id in (1, 2))

    def _params(self) -> dict:
        return {
            "mechanism": self._mech_combo.currentText(),
            "producer":  self._producer_combo.currentText(),
            "consumer":  self._consumer_combo.currentText(),
            "size":      self._SIZE_STEPS[self._size_slider.value()],
        }

    def _on_send_now(self) -> None:
        params = self._params()
        self.send_requested.emit(params)
        if self._ib:
            self._ib.send_ipc(
                params["producer"],
                params["mechanism"],
                params["size"],
            )

    def _on_start(self) -> None:
        params = self._params()
        mode = self._mode_group.checkedId()

        if mode == 1:  # Burst
            params["count"] = self._burst_spin.value()
            self.burst_started.emit(params)
            if self._ib:
                self._btn_start.setEnabled(False)
                self._btn_stop.setEnabled(True)
                self._ib.start_burst(
                    params["producer"],
                    params["mechanism"],
                    params["size"],
                    params["count"],
                )
        else:  # Continuous
            rate = self._rate_slider.value()
            interval_ms = max(1, 1000 // rate)
            self._cont_timer = QTimer(self)
            self._cont_timer.timeout.connect(self._on_send_now)
            self._cont_timer.start(interval_ms)
            self._btn_start.setEnabled(False)
            self._btn_stop.setEnabled(True)

    def _on_stop(self) -> None:
        if self._cont_timer:
            self._cont_timer.stop()
            self._cont_timer = None
        if self._ib:
            self._ib.stop_burst()
        self.burst_stopped.emit()
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(True)

    def _on_burst_done(self, data: dict) -> None:
        self._btn_start.setEnabled(True)

    def _on_ipc_error(self, data: dict) -> None:
        self._btn_start.setEnabled(True)

    # ------------------------------------------------------------------ #
    # Accessors (for dashboard_window to query current selection)          #
    # ------------------------------------------------------------------ #

    def current_mechanism(self) -> str:
        return self._mech_combo.currentText()

    def current_producer(self) -> str:
        return self._producer_combo.currentText()
