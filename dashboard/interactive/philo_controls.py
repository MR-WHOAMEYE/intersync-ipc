"""
philo_controls.py
InterSync Dashboard — Dining Philosophers Control Panel (Phase 4).

Controls the simulation of the Dining Philosophers problem.
Exposes:
  • Container dropdown
  • Num Philosophers slider (2–10)
  • Think Time / Eat Time sliders
  • Deadlock Avoidance toggle
  • [PLAY] / [PAUSE], [STEP] buttons
  • Speed slider
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSlider, QGroupBox, QCheckBox, QFrame,
)

from dashboard.ui.theme import (
    TEAL, TEAL_DIM, TEAL_DARK, BORDER, CARD, SURFACE,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_DIM,
    PURPLE, PURPLE_DARK, PURPLE_DIM,
    GREEN, RED, DARK_BG,
)
from dashboard.backend.event_bus import EventBus

CONTAINER_NAMES = ["interync-lab-1", "interync-lab-2", "interync-lab-3"]

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

class PhiloControlPanel(QWidget):
    """
    Left control panel for the Dining Philosophers tab.
    """

    play_requested  = pyqtSignal(dict)
    pause_requested = pyqtSignal(dict)
    step_requested  = pyqtSignal(dict)

    def __init__(self, interactive_backend=None, parent=None):
        super().__init__(parent)
        self._ib = interactive_backend
        self._bus = EventBus.instance()
        self._is_playing = False

        self._build_ui()
        self._connect_signals()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 16, 10)
        root.setSpacing(10)

        # ── Target ──────────────────────────────────────────────────────
        target_grp = QGroupBox("Simulation Settings")
        target_grp.setStyleSheet(_SECTION_STYLE)
        t_layout = QVBoxLayout(target_grp)
        t_layout.setSpacing(6)

        t_layout.addWidget(QLabel("Container:"))
        self._container_combo = QComboBox()
        self._container_combo.addItems(CONTAINER_NAMES)
        t_layout.addWidget(self._container_combo)

        # Num Philosophers
        self._num_label = QLabel("Philosophers: 5")
        self._num_label.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 10px; background: transparent;")
        self._num_slider = QSlider(Qt.Orientation.Horizontal)
        self._num_slider.setRange(2, 10)
        self._num_slider.setValue(5)
        self._num_slider.setStyleSheet(_SLIDER_STYLE)
        t_layout.addWidget(self._num_label)
        t_layout.addWidget(self._num_slider)

        # Deadlock Avoidance
        self._avoidance_check = QCheckBox("Deadlock Avoidance (Dijkstra)")
        self._avoidance_check.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 11px;")
        t_layout.addWidget(self._avoidance_check)

        root.addWidget(target_grp)

        # ── Timings ─────────────────────────────────────────────────────
        time_grp = QGroupBox("Timings (ms)")
        time_grp.setStyleSheet(_SECTION_STYLE)
        time_layout = QVBoxLayout(time_grp)
        time_layout.setSpacing(6)

        self._think_label = QLabel("Think Time: 100 ms")
        self._think_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 10px; background: transparent;")
        self._think_slider = QSlider(Qt.Orientation.Horizontal)
        self._think_slider.setRange(10, 1000)
        self._think_slider.setValue(100)
        self._think_slider.setSingleStep(10)
        self._think_slider.setStyleSheet(_SLIDER_STYLE)
        time_layout.addWidget(self._think_label)
        time_layout.addWidget(self._think_slider)

        self._eat_label = QLabel("Eat Time: 100 ms")
        self._eat_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 10px; background: transparent;")
        self._eat_slider = QSlider(Qt.Orientation.Horizontal)
        self._eat_slider.setRange(10, 1000)
        self._eat_slider.setValue(100)
        self._eat_slider.setSingleStep(10)
        self._eat_slider.setStyleSheet(_SLIDER_STYLE)
        time_layout.addWidget(self._eat_label)
        time_layout.addWidget(self._eat_slider)

        root.addWidget(time_grp)

        # ── Controls ────────────────────────────────────────────────────
        ctrl_grp = QGroupBox("Execution")
        ctrl_grp.setStyleSheet(_SECTION_STYLE)
        ctrl_layout = QVBoxLayout(ctrl_grp)
        ctrl_layout.setSpacing(6)

        row = QHBoxLayout()
        self._btn_play = QPushButton("Play")
        self._btn_play.setFixedHeight(36)
        self._btn_play.setStyleSheet(
            f"QPushButton {{ background: {TEAL_DIM}; color: {TEAL}; "
            f"border: 1px solid {TEAL_DARK}; border-radius: 8px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {TEAL_DARK}; color: white; }}"
        )

        self._btn_step = QPushButton("Step")
        self._btn_step.setFixedHeight(36)
        self._btn_step.setEnabled(False) # Wait, philo_interactive in C only supports start/status/stop so far. Step is pseudo.
        self._btn_step.setStyleSheet(
            f"QPushButton {{ background: {CARD}; color: {TEXT_PRIMARY}; "
            f"border: 1px solid {BORDER}; border-radius: 8px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {SURFACE}; }}"
        )
        row.addWidget(self._btn_play)
        # We will not add step if backend doesn't support it, but let's leave it disabled.
        # row.addWidget(self._btn_step)
        ctrl_layout.addLayout(row)

        root.addWidget(ctrl_grp)
        root.addStretch()

        if not self._ib:
            self._btn_play.setEnabled(False)

    def _connect_signals(self) -> None:
        self._num_slider.valueChanged.connect(
            lambda v: self._num_label.setText(f"Philosophers: {v}"))
        self._think_slider.valueChanged.connect(
            lambda v: self._think_label.setText(f"Think Time: {v} ms"))
        self._eat_slider.valueChanged.connect(
            lambda v: self._eat_label.setText(f"Eat Time: {v} ms"))

        self._btn_play.clicked.connect(self._on_play_toggle)

        self._bus.philo_started.connect(self._on_started)
        self._bus.philo_stopped.connect(self._on_stopped)
        self._bus.deadlock_detected.connect(self._on_deadlock)

    def _params(self) -> dict:
        return {
            "container": self._container_combo.currentText(),
            "count": self._num_slider.value(),
            "avoidance": self._avoidance_check.isChecked(),
            "think_ms": self._think_slider.value(),
            "eat_ms": self._eat_slider.value(),
        }

    def _on_play_toggle(self) -> None:
        p = self._params()
        if not self._is_playing:
            self.play_requested.emit(p)
            if self._ib:
                self._ib.start_philosophers(
                    p["container"], p["count"],
                    p["think_ms"], p["eat_ms"],
                    p["avoidance"]
                )
        else:
            self.pause_requested.emit(p)
            if self._ib:
                self._ib.stop_philosophers(p["container"])

    def _on_started(self, data: dict) -> None:
        self._is_playing = True
        self._btn_play.setText("Stop")
        self._btn_play.setStyleSheet(
            f"QPushButton {{ background: {CARD}; color: {RED}; "
            f"border: 1px solid {RED}; border-radius: 8px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {RED}; color: {DARK_BG}; }}"
        )
        self._num_slider.setEnabled(False)
        self._avoidance_check.setEnabled(False)

    def _on_stopped(self, data: dict) -> None:
        self._is_playing = False
        self._btn_play.setText("Play")
        self._btn_play.setStyleSheet(
            f"QPushButton {{ background: {TEAL_DIM}; color: {TEAL}; "
            f"border: 1px solid {TEAL_DARK}; border-radius: 8px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {TEAL_DARK}; color: white; }}"
        )
        self._num_slider.setEnabled(True)
        self._avoidance_check.setEnabled(True)

    def _on_deadlock(self, data: dict) -> None:
        if self._is_playing:
            # If a deadlock is detected during philo, we can auto-stop or leave it running
            pass
