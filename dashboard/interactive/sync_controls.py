"""
sync_controls.py
InterSync Dashboard — Sync & Locks Interactive Control Panel (Phase 3).

SyncControlPanel is the left-side widget for the Sync & Locks tab.
It exposes:
  • Target container dropdown
  • Primitive dropdown (MUTEX / SEMAPHORE / CONDVAR / RWLOCK)
  • Lock name text input
  • [ACQUIRE]  [ACQUIRE READ]  [RELEASE]  buttons
  • [INJECT DEADLOCK]  [RESOLVE]  buttons
  • Thread count slider  (stress mode)
  • Auto-spawn toggle

All user actions are forwarded to an InteractiveBackend instance.
Status updates from EventBus flow back as canvas highlights + toasts.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSlider, QLineEdit, QGroupBox, QFrame,
    QCheckBox, QSpinBox,
)

from dashboard.ui.theme import (
    TEAL, TEAL_DIM, TEAL_DARK, BORDER, CARD,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_DIM,
    PURPLE, PURPLE_DARK, PURPLE_DIM,
    GREEN, RED, RED_DARK, DARK_BG,
)
from dashboard.backend.event_bus import EventBus

CONTAINER_NAMES = ["interync-lab-1", "interync-lab-2", "interync-lab-3"]
PRIMITIVES      = ["MUTEX", "SEMAPHORE", "CONDVAR", "RWLOCK"]

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
        color: {PURPLE};
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
        background: {PURPLE};
        width: 14px;
        height: 14px;
        margin: -5px 0;
        border-radius: 7px;
    }}
    QSlider::sub-page:horizontal {{
        background: {PURPLE_DARK};
        border-radius: 2px;
    }}
"""


class SyncControlPanel(QWidget):
    """
    Left control panel for the Sync & Locks tab.

    Signals:
        acquire_requested(dict)        — {container, primitive, lock_name}
        release_requested(dict)        — {container, holder_pid}
        deadlock_inject_requested(dict)— {container, primitive, num_threads}
        resolve_requested(dict)        — {container, pid}
        container_changed(str)         — new container name
        primitive_changed(str)         — new primitive name
    """

    acquire_requested         = pyqtSignal(dict)
    release_requested         = pyqtSignal(dict)
    deadlock_inject_requested = pyqtSignal(dict)
    resolve_requested         = pyqtSignal(dict)
    container_changed         = pyqtSignal(str)
    primitive_changed         = pyqtSignal(str)

    def __init__(self, interactive_backend=None, parent=None):
        super().__init__(parent)
        self._ib  = interactive_backend
        self._bus = EventBus.instance()

        # Track current holder_pid for release
        self._current_holder_pid: int = 0
        self._deadlock_pid:       int = 0

        self._build_ui()
        self._connect_signals()

    # ------------------------------------------------------------------ #
    # UI                                                                    #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 16, 10)
        root.setSpacing(10)

        # ── Target ──────────────────────────────────────────────────────
        target_grp = QGroupBox("Target")
        target_grp.setStyleSheet(_SECTION_STYLE)
        t_layout = QVBoxLayout(target_grp)
        t_layout.setSpacing(6)

        t_layout.addWidget(QLabel("Container:"))
        self._container_combo = QComboBox()
        self._container_combo.addItems(CONTAINER_NAMES)
        t_layout.addWidget(self._container_combo)

        t_layout.addWidget(QLabel("Primitive:"))
        self._primitive_combo = QComboBox()
        self._primitive_combo.addItems(PRIMITIVES)
        t_layout.addWidget(self._primitive_combo)

        t_layout.addWidget(QLabel("Lock Name:"))
        self._lock_name_edit = QLineEdit("my-lock-1")
        self._lock_name_edit.setStyleSheet(
            f"background: {CARD}; color: {TEXT_PRIMARY}; "
            f"border: 1px solid {BORDER}; border-radius: 4px; padding: 4px 8px;")
        self._lock_name_edit.setPlaceholderText("e.g. fork-1")
        t_layout.addWidget(self._lock_name_edit)

        root.addWidget(target_grp)

        # ── Acquire / Release ────────────────────────────────────────────
        acq_grp = QGroupBox("Lock Control")
        acq_grp.setStyleSheet(_SECTION_STYLE)
        acq_layout = QVBoxLayout(acq_grp)
        acq_layout.setSpacing(6)

        self._btn_acquire = QPushButton("Acquire")
        self._btn_acquire.setObjectName("btnRun")
        self._btn_acquire.setFixedHeight(36)

        self._btn_acquire_read = QPushButton("Acquire (Read)")
        self._btn_acquire_read.setFixedHeight(36)
        self._btn_acquire_read.setStyleSheet(
            f"QPushButton {{ background: {PURPLE_DIM}; color: {PURPLE}; "
            f"border: 1px solid {PURPLE_DARK}; border-radius: 8px; "
            f"font-weight: bold; padding: 6px 12px; }}"
            f"QPushButton:hover {{ background: {PURPLE_DARK}; color: white; }}")

        self._btn_release = QPushButton("Release")
        self._btn_release.setFixedHeight(36)
        self._btn_release.setEnabled(False)
        self._btn_release.setStyleSheet(
            f"QPushButton {{ background: {CARD}; color: {GREEN}; "
            f"border: 1px solid {GREEN}; border-radius: 8px; "
            f"font-weight: bold; padding: 6px 12px; }}"
            f"QPushButton:hover {{ background: {GREEN}; color: {DARK_BG}; }}"
            f"QPushButton:disabled {{ color: {TEXT_DIM}; border-color: {BORDER}; }}")

        # Holder info label
        self._holder_label = QLabel("No lock held")
        self._holder_label.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 10px; background: transparent;")
        self._holder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        acq_layout.addWidget(self._btn_acquire)
        acq_layout.addWidget(self._btn_acquire_read)
        acq_layout.addWidget(self._btn_release)
        acq_layout.addWidget(self._holder_label)
        root.addWidget(acq_grp)

        # ── Deadlock ─────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {BORDER};")
        root.addWidget(sep)

        dl_grp = QGroupBox("Deadlock")
        dl_grp.setStyleSheet(_SECTION_STYLE)
        dl_layout = QVBoxLayout(dl_grp)
        dl_layout.setSpacing(6)

        threads_row = QHBoxLayout()
        threads_row.addWidget(QLabel("Threads:"))
        self._thread_spin = QSpinBox()
        self._thread_spin.setRange(2, 8)
        self._thread_spin.setValue(2)
        self._thread_spin.setStyleSheet(
            f"background: {CARD}; color: {TEXT_PRIMARY}; "
            f"border: 1px solid {BORDER}; border-radius: 4px; padding: 2px;")
        threads_row.addWidget(self._thread_spin)
        dl_layout.addLayout(threads_row)

        self._btn_inject = QPushButton("Inject Deadlock")
        self._btn_inject.setObjectName("btnStop")
        self._btn_inject.setFixedHeight(36)

        self._btn_resolve = QPushButton("Resolve (Kill)")
        self._btn_resolve.setFixedHeight(36)
        self._btn_resolve.setEnabled(False)
        self._btn_resolve.setStyleSheet(
            f"QPushButton {{ background: {CARD}; color: {TEAL}; "
            f"border: 1px solid {TEAL}; border-radius: 8px; "
            f"font-weight: bold; padding: 6px 12px; }}"
            f"QPushButton:hover {{ background: {TEAL}; color: {DARK_BG}; }}"
            f"QPushButton:disabled {{ color: {TEXT_DIM}; border-color: {BORDER}; }}")

        dl_layout.addWidget(self._btn_inject)
        dl_layout.addWidget(self._btn_resolve)
        root.addWidget(dl_grp)

        # ── Stress Test ──────────────────────────────────────────────────
        stress_grp = QGroupBox("Stress Test")
        stress_grp.setStyleSheet(_SECTION_STYLE)
        stress_layout = QVBoxLayout(stress_grp)
        stress_layout.setSpacing(6)

        self._stress_label = QLabel("Threads: 4")
        self._stress_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 10px; background: transparent;")
        self._stress_slider = QSlider(Qt.Orientation.Horizontal)
        self._stress_slider.setRange(1, 16)
        self._stress_slider.setValue(4)
        self._stress_slider.setStyleSheet(_SLIDER_STYLE)

        iter_row = QHBoxLayout()
        iter_row.addWidget(QLabel("Iterations:"))
        self._iter_spin = QSpinBox()
        self._iter_spin.setRange(10, 10000)
        self._iter_spin.setValue(100)
        self._iter_spin.setSingleStep(50)
        self._iter_spin.setStyleSheet(
            f"background: {CARD}; color: {TEXT_PRIMARY}; "
            f"border: 1px solid {BORDER}; border-radius: 4px; padding: 2px;")
        iter_row.addWidget(self._iter_spin)

        self._btn_stress = QPushButton("Run Stress Test")
        self._btn_stress.setFixedHeight(34)

        stress_layout.addWidget(self._stress_label)
        stress_layout.addWidget(self._stress_slider)
        stress_layout.addLayout(iter_row)
        stress_layout.addWidget(self._btn_stress)
        root.addWidget(stress_grp)

        root.addStretch()

        # Disable all action buttons when no backend
        if not self._ib:
            for btn in (self._btn_acquire, self._btn_acquire_read,
                        self._btn_release, self._btn_inject,
                        self._btn_resolve, self._btn_stress):
                btn.setEnabled(False)

    # ------------------------------------------------------------------ #
    # Signal wiring                                                         #
    # ------------------------------------------------------------------ #

    def _connect_signals(self) -> None:
        self._container_combo.currentTextChanged.connect(
            self.container_changed.emit)
        self._primitive_combo.currentTextChanged.connect(
            self._on_primitive_changed)
        self._stress_slider.valueChanged.connect(
            lambda v: self._stress_label.setText(f"Threads: {v}"))

        self._btn_acquire.clicked.connect(self._on_acquire)
        self._btn_acquire_read.clicked.connect(self._on_acquire_read)
        self._btn_release.clicked.connect(self._on_release)
        self._btn_inject.clicked.connect(self._on_inject)
        self._btn_resolve.clicked.connect(self._on_resolve)
        self._btn_stress.clicked.connect(self._on_stress)

        # RWLOCK: enable Read button only when RWLOCK selected
        self._on_primitive_changed(self._primitive_combo.currentText())

        # EventBus reactions
        self._bus.lock_acquired.connect(self._on_bus_acquired)
        self._bus.lock_released.connect(self._on_bus_released)
        self._bus.deadlock_detected.connect(self._on_bus_deadlock)
        self._bus.deadlock_resolved.connect(self._on_bus_resolved)
        self._bus.stress_complete.connect(self._on_bus_stress)

    def _on_primitive_changed(self, prim: str) -> None:
        is_rwlock = (prim == "RWLOCK")
        self._btn_acquire_read.setEnabled(bool(self._ib) and is_rwlock)
        if not is_rwlock:
            self._btn_acquire_read.setToolTip("Only available for RWLOCK")
        self.primitive_changed.emit(prim)

    def _params(self) -> dict:
        return {
            "container": self._container_combo.currentText(),
            "primitive": self._primitive_combo.currentText(),
            "lock_name": self._lock_name_edit.text().strip() or "my-lock-1",
        }

    # ── Action handlers ─────────────────────────────────────────────────

    def _on_acquire(self) -> None:
        p = self._params()
        self.acquire_requested.emit(p)
        if self._ib:
            self._ib.acquire_lock(p["container"], p["primitive"], p["lock_name"])

    def _on_acquire_read(self) -> None:
        p = self._params()
        if self._ib:
            self._ib.acquire_lock_read(p["container"], p["lock_name"])

    def _on_release(self) -> None:
        p = self._params()
        p["holder_pid"] = self._current_holder_pid
        self.release_requested.emit(p)
        if self._ib and self._current_holder_pid:
            self._ib.release_lock(
                p["container"], self._current_holder_pid,
                p["primitive"], p["lock_name"])

    def _on_inject(self) -> None:
        p = self._params()
        p["num_threads"] = self._thread_spin.value()
        self.deadlock_inject_requested.emit(p)
        if self._ib:
            self._ib.inject_deadlock(
                p["container"], p["primitive"], p["num_threads"])

    def _on_resolve(self) -> None:
        p = self._params()
        p["pid"] = self._deadlock_pid
        self.resolve_requested.emit(p)
        if self._ib and self._deadlock_pid:
            self._ib.resolve_deadlock(p["container"], self._deadlock_pid)

    def _on_stress(self) -> None:
        p = self._params()
        threads = self._stress_slider.value()
        iters   = self._iter_spin.value()
        if self._ib:
            self._btn_stress.setEnabled(False)
            self._btn_stress.setText("Running...")
            self._ib.run_stress(p["container"], p["primitive"], threads, iters)

    # ── EventBus handlers ────────────────────────────────────────────────

    def _on_bus_acquired(self, data: dict) -> None:
        pid = data.get("holder_pid", 0)
        self._current_holder_pid = pid
        name = data.get("lock_name", "?")
        prim = data.get("primitive", "")
        us   = data.get("acquire_time_us", "?")
        self._holder_label.setText(f"Held: PID {pid}  ({us} µs)")
        self._holder_label.setStyleSheet(
            f"color: {GREEN}; font-size: 10px; font-weight: bold; background: transparent;")
        self._btn_release.setEnabled(bool(self._ib))

    def _on_bus_released(self, data: dict) -> None:
        self._current_holder_pid = 0
        self._holder_label.setText("Released — FREE")
        self._holder_label.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 10px; background: transparent;")
        self._btn_release.setEnabled(False)

    def _on_bus_deadlock(self, data: dict) -> None:
        pids = data.get("cycle_pids", [])
        self._deadlock_pid = pids[0] if pids else 0
        self._btn_resolve.setEnabled(bool(self._ib) and bool(self._deadlock_pid))
        self._btn_inject.setEnabled(False)

    def _on_bus_resolved(self, data: dict) -> None:
        self._deadlock_pid = 0
        self._btn_resolve.setEnabled(False)
        self._btn_inject.setEnabled(bool(self._ib))

    def _on_bus_stress(self, data: dict) -> None:
        contention = data.get("contention_events", "?")
        total_us   = data.get("total_time_us", "?")
        self._btn_stress.setEnabled(True)
        self._btn_stress.setText("Run Stress Test")

    # ── Accessors ────────────────────────────────────────────────────────

    def current_container(self) -> str:
        return self._container_combo.currentText()

    def current_primitive(self) -> str:
        return self._primitive_combo.currentText()
