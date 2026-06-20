"""
dashboard_window.py
InterSync Dashboard — main window using the new SplitView layout.

Tabs (left nav bar → right canvas):
  1. Overview      — container status + CPU/RAM bars
  2. IPC Flow      — IpcVisualizer animated canvas
  3. Sync & Locks  — SyncVisualizer (lock state / wait-for graph)
  4. Benchmarks    — ChartsWidget (live throughput + bar charts)
  5. Event Log     — scrolling text feed

The window wires:
  - EventBus signals → toast overlay + status bar updates
  - InteractiveBackend instance → passed to each control panel
  - ContainerManager → used for Overview tab actions
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QAbstractListModel, QModelIndex, QSize
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QGroupBox, QProgressBar, QPushButton,
    QComboBox, QStatusBar, QScrollArea, QListView,
    QStyledItemDelegate, QStyle, QPlainTextEdit, QSplitter,
)

from .theme import (
    TEAL, PURPLE, GREEN, RED, TEXT_PRIMARY, TEXT_DIM,
    DARK_BG, SURFACE, CARD, BORDER,
)
from .split_view import SplitView
from .toast_overlay import ToastLevel
from .ipc_visualizer  import IpcVisualizer
from .sync_visualizer import SyncVisualizer
from .charts           import ChartsWidget
from dashboard.backend.event_bus import EventBus
from dashboard.interactive.ipc_controls import IpcControlPanel
from dashboard.interactive.sync_controls import SyncControlPanel
from dashboard.interactive.philo_controls import PhiloControlPanel
from dashboard.interactive.spsc_controls import SpscControlPanel
from dashboard.ui.philo_visualizer import PhiloVisualizer
from dashboard.ui.spsc_visualizer import SpscVisualizer
from dashboard.interactive.scenario_engine import ScenarioEngine
from dashboard.interactive.scenario_sidebar import ScenarioSidebar
from dashboard.ui.charts import LiveCpuChart
import glob
import os
import time
from PyQt6.QtCore import QProcess, QThread, pyqtSignal

log = logging.getLogger(__name__)

POLL_INTERVAL_MS = 2000
CONTAINER_NAMES  = ["interync-lab-1", "interync-lab-2", "interync-lab-3"]


class PollWorker(QThread):
    """Background worker thread to run slow WSL LXC commands without lagging UI."""
    result_ready = pyqtSignal(dict)

    def __init__(self, mgr, metrics, detector, parent=None):
        super().__init__(parent)
        self._mgr = mgr
        self._metrics = metrics
        self._detector = detector
        self._running = True
        self._target_container = CONTAINER_NAMES[0]

    def set_target_container(self, name: str):
        self._target_container = name

    def stop(self):
        self._running = False

    def run(self):
        while self._running:
            data = {}
            if self._mgr is None:
                if self._running:
                    self.result_ready.emit({"offline": True})
                time.sleep(2)
                continue

            # Check status and metrics for all containers
            for name in CONTAINER_NAMES:
                if not self._running:
                    return
                try:
                    status = self._mgr.container_status(name)
                    data[name] = {"status": status, "cpu": 0.0, "mem": 0.0, "error": None}
                    if status == "Running" and self._metrics:
                        m = self._metrics.collect(name)
                        data[name]["cpu"] = m.cpu_percent
                        data[name]["mem"] = m.mem_percent
                        if m.error:
                            data[name]["error"] = m.error
                except Exception as e:
                    data[name] = {"status": "Stopped", "cpu": 0.0, "mem": 0.0, "error": str(e)}

            # Check deadlock on target container
            deadlock_info = {"detected": False, "graph": None, "cycle": None, "error": None}
            if self._detector:
                target = self._target_container
                try:
                    res = self._detector.check(target)
                    if res.detected:
                        graph = self._detector.build_wait_for_graph(target)
                        deadlock_info = {
                            "detected": True,
                            "graph": graph,
                            "cycle": res.cycle,
                            "error": None
                        }
                except Exception as e:
                    deadlock_info["error"] = str(e)
            data["deadlock"] = deadlock_info
            data["target"] = self._target_container

            if self._running:
                self.result_ready.emit(data)

            # Sleep 2 seconds in small steps to be fast-quit friendly
            for _ in range(20):
                if not self._running:
                    return
                time.sleep(0.1)


class DashboardWindow(QMainWindow):
    """
    Main application window.

    Accepts optional backend objects; if None, controls are disabled
    but the visualizations still render.
    """

    def __init__(self,
                 container_manager=None,
                 metrics_collector=None,
                 deadlock_detector=None,
                 interactive_backend=None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)

        self._mgr      = container_manager
        self._metrics  = metrics_collector
        self._detector = deadlock_detector
        self._ib       = interactive_backend   # InteractiveBackend | None
        self._bus      = EventBus.instance()
        
        self._engine = ScenarioEngine(interactive_backend=self._ib)

        self._setup_window()
        self._build_ui()
        self._connect_event_bus()
        self._setup_polling()

    # ------------------------------------------------------------------ #
    # Window setup                                                          #
    # ------------------------------------------------------------------ #

    def _setup_window(self) -> None:
        self.setWindowTitle("InterSync — IPC & Synchronization Platform")
        self.setMinimumSize(1200, 750)
        self.resize(1400, 860)

    # ------------------------------------------------------------------ #
    # UI construction                                                       #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        # --- Header bar ---
        header = self._make_header()

        # --- SplitView (left nav + right canvas) ---
        self._split = SplitView()
        
        self._sidebar = ScenarioSidebar(self._engine)
        self._split.set_scenario_sidebar(self._sidebar)

        # Register each tab: (nav label, control widget, canvas widget)
        self._overview_combo = QComboBox()
        self._overview_combo.addItems(CONTAINER_NAMES)

        self._split.register_tab(
            "Overview",
            self._make_overview_controls(),
            self._make_overview_canvas(),
        )

        self._ipc_vis = IpcVisualizer(interactive_backend=self._ib)
        self._ipc_controls = self._make_ipc_controls()
        self._split.register_tab(
            "IPC Flow",
            self._ipc_controls,
            self._ipc_vis,
        )

        self._sync_vis = SyncVisualizer(interactive_backend=self._ib)
        self._sync_controls = self._make_sync_controls()
        self._split.register_tab(
            "Sync & Locks",
            self._sync_controls,
            self._sync_vis,
        )

        self._philo_vis = PhiloVisualizer(interactive_backend=self._ib)
        self._philo_controls = PhiloControlPanel(interactive_backend=self._ib)
        self._split.register_tab(
            "Dining Philosophers",
            self._philo_controls,
            self._philo_vis,
        )

        self._spsc_controls = SpscControlPanel(backend=self._ib)
        self._spsc_vis = SpscVisualizer(bus=self._bus)
        self._split.register_tab(
            "SPSC Ring Buffer",
            self._spsc_controls,
            self._spsc_vis,
        )

        from dashboard.backend.benchmark_runner import BenchmarkRunner
        runner = BenchmarkRunner(self._mgr) if self._mgr else None
        self._charts = ChartsWidget(runner, self._overview_combo)
        self._split.register_tab(
            "Benchmarks",
            self._make_bench_controls(),
            self._charts,
        )

        self._split.register_tab(
            "Event Log",
            self._make_log_controls(),
            self._make_log_canvas(),
        )

        # Initialize sync streaming for first container
        self._sync_vis.start_streaming(CONTAINER_NAMES[0])

        # --- Root layout ---
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(header)
        root.addWidget(self._split, stretch=1)

        # --- Status bar ---
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Ready — connect containers to begin.")

        # Wire toast overlay to EventBus
        self._bus.error_occurred.connect(
            lambda src, msg: self._split.show_toast(
                f"[{src}] {msg}", ToastLevel.ERROR))

    # ------------------------------------------------------------------ #
    # Header                                                                #
    # ------------------------------------------------------------------ #

    def _make_header(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(52)
        w.setStyleSheet(
            f"background-color: {DARK_BG}; border-bottom: 1px solid {BORDER};")
        layout = QHBoxLayout(w)
        layout.setContentsMargins(16, 6, 16, 6)

        title = QLabel("InterSync")
        title.setObjectName("labelHeader")
        title.setFont(QFont("Inter", 18, QFont.Weight.Bold))
        layout.addWidget(title)

        sub = QLabel("IPC & Synchronization Platform  |  Capstone 2026")
        sub.setObjectName("labelSubHeader")
        layout.addWidget(sub, stretch=1)

        # Mode toggle / Scenario selector
        self._scenario_combo = QComboBox()
        self._scenario_combo.setFixedWidth(200)
        self._scenario_combo.addItem("-- Select Scenario --", None)
        
        # Load scenario definitions
        for f in glob.glob("dashboard/scenarios/*.json"):
            try:
                scen = self._engine.load_scenario(f)
                self._scenario_combo.addItem(scen.get("name", "Unknown"), scen)
            except Exception as e:
                log.error(f"Failed to load scenario {f}: {e}")
                
        self._scenario_combo.setVisible(False)
        self._scenario_combo.currentIndexChanged.connect(self._on_scenario_selected)
        
        layout.addWidget(self._scenario_combo)

        self._mode_btn = QPushButton("Sandbox Mode")
        self._mode_btn.setFixedWidth(160)
        self._mode_btn.setCheckable(True)
        self._mode_btn.setStyleSheet(f"""
            QPushButton {{
                background: {CARD};
                color: {TEAL};
                border: 1px solid {TEAL};
                border-radius: 8px;
                padding: 6px 12px;
                font-weight: bold;
                font-size: 12px;
            }}
            QPushButton:checked {{
                background: {TEAL};
                color: {DARK_BG};
            }}
        """)
        self._mode_btn.clicked.connect(self._toggle_mode)
        layout.addWidget(self._mode_btn)

        self._btn_refresh = QPushButton("Refresh")
        self._btn_refresh.setObjectName("btnRun")
        self._btn_refresh.setFixedWidth(100)
        self._btn_refresh.clicked.connect(self._poll)
        layout.addWidget(self._btn_refresh)

        return w

    def _toggle_mode(self):
        if self._mode_btn.isChecked():
            self._mode_btn.setText("Scenario Mode")
            self._split.set_mode("scenario")
            self._scenario_combo.setVisible(True)
        else:
            self._mode_btn.setText("Sandbox Mode")
            self._split.set_mode("sandbox")
            self._scenario_combo.setVisible(False)
            self._engine.stop_scenario()
            self._scenario_combo.setCurrentIndex(0)

    def _on_scenario_selected(self, idx: int):
        scen = self._scenario_combo.itemData(idx)
        if scen:
            # Switch to the target tab for this scenario
            tab_name = scen.get("target_tab")
            if tab_name:
                clean_target = "".join(c for c in tab_name if c.isalnum()).lower()
                for i, btn in enumerate(self._split.control_panel._tab_buttons):
                    clean_btn = "".join(c for c in btn.text() if c.isalnum()).lower()
                    if clean_btn == clean_target:
                        self._split.control_panel.select_tab(i)
                        break
            self._engine.start_scenario(scen)

    def _on_scenario_event(self, event_type: str, data: dict) -> None:
        if event_type in ("win", "lose", "quit"):
            if self._mode_btn.isChecked():
                self._mode_btn.setChecked(False)
                self._toggle_mode()

    # ------------------------------------------------------------------ #
    # Overview tab                                                          #
    # ------------------------------------------------------------------ #

    def _make_overview_controls(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(12, 12, 16, 12)
        layout.setSpacing(10)

        lbl = QLabel("Container Management")
        lbl.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 13px; font-weight: bold;")
        layout.addWidget(lbl)

        self._container_groups: dict[str, dict] = {}
        for name in CONTAINER_NAMES:
            grp = QGroupBox(name)
            grp.setObjectName("perfGroup")
            g_layout = QVBoxLayout(grp)
            g_layout.setSpacing(6)

            # Make badge clickable
            badge = QPushButton("● Unknown")
            badge.setObjectName("badgeStopped")
            badge.setStyleSheet(f"text-align: left; background: transparent; color: {TEXT_PRIMARY}; font-weight: bold; border: none; padding: 0;")
            badge.setCursor(Qt.CursorShape.PointingHandCursor)
            badge.clicked.connect(lambda _, n=name: self._split.control_panel.select_tab(1)) # Switch to IPC flow


            cpu_bar = QProgressBar()
            cpu_bar.setRange(0, 100)
            cpu_bar.setValue(0)
            cpu_bar.setFormat("CPU: %p%")

            mem_bar = QProgressBar()
            mem_bar.setRange(0, 100)
            mem_bar.setValue(0)
            mem_bar.setFormat("MEM: %p%")

            btn_row = QHBoxLayout()
            btn_start = QPushButton("▶ Start")
            btn_start.setObjectName("btnRun")
            btn_stop  = QPushButton("■ Stop")
            btn_stop.setObjectName("btnStop")
            btn_ping  = QPushButton("⚡ Ping")
            btn_shell = QPushButton("🐚 Shell")

            if self._ib:
                btn_start.clicked.connect(
                    lambda _, n=name: self._ib.start_container(n))
                btn_stop.clicked.connect(
                    lambda _, n=name: self._ib.stop_container(n))
                btn_ping.clicked.connect(
                    lambda _, n=name: self._on_ping(n))
                btn_shell.clicked.connect(
                    lambda _, n=name: self._open_shell(n))
            else:
                for b in (btn_start, btn_stop, btn_ping, btn_shell):
                    b.setEnabled(False)

            btn_row.addWidget(btn_start)
            btn_row.addWidget(btn_stop)
            btn_row.addWidget(btn_ping)
            btn_row.addWidget(btn_shell)

            g_layout.addWidget(badge)
            g_layout.addWidget(cpu_bar)
            g_layout.addWidget(mem_bar)
            g_layout.addLayout(btn_row)
            layout.addWidget(grp)

            self._container_groups[name] = {
                "badge": badge, "cpu": cpu_bar, "mem": mem_bar,
            }

        layout.addStretch()
        return w

    def _make_overview_canvas(self) -> QWidget:
        """Right canvas for Overview: Live CPU Chart + Shell Output."""
        w = QWidget()
        w.setStyleSheet(f"background-color: {DARK_BG};")
        layout = QVBoxLayout(w)
        layout.setContentsMargins(16, 16, 16, 16)
        
        self._cpu_chart = LiveCpuChart(CONTAINER_NAMES)
        layout.addWidget(self._cpu_chart, stretch=1)
        
        from PyQt6.QtWidgets import QPlainTextEdit
        self._shell_output = QPlainTextEdit()
        self._shell_output.setReadOnly(True)
        self._shell_output.setStyleSheet(f"background-color: #1E1E1E; color: #00FF00; font-family: monospace; border: 1px solid {BORDER};")
        layout.addWidget(self._shell_output, stretch=1)
        
        return w

    def _open_shell(self, container_name: str):
        self._shell_output.appendPlainText(f"\n--- Starting Shell: {container_name} ---")
        process = QProcess(self)
        process.readyReadStandardOutput.connect(lambda: self._shell_output.appendPlainText(bytes(process.readAllStandardOutput()).decode()))
        process.readyReadStandardError.connect(lambda: self._shell_output.appendPlainText(bytes(process.readAllStandardError()).decode()))
        
        # Use detected lxc command path from container manager
        lxc_cmd = "lxc"
        if self._mgr and hasattr(self._mgr, "_lxc_cmd"):
            lxc_cmd = self._mgr._lxc_cmd
            
        process.start("wsl", [lxc_cmd, "exec", container_name, "--", "bash", "-c", "echo 'Welcome to container shell'; ps aux;"])
        # Keeping it simple for UI demo. Real interactive shell requires more complex pty handling.
        
    def _on_ping(self, name: str):
        if self._ib:
            # ping_container is async — result arrives via EventBus ipc_sent signal
            self._split.show_toast(f"Pinging {name}...", ToastLevel.SUCCESS)
            self._ib.ping_container(name)

    # ------------------------------------------------------------------ #
    # IPC tab                                                               #
    # ------------------------------------------------------------------ #

    def _make_ipc_controls(self) -> QWidget:
        """Create and wire the IpcControlPanel for the left IPC tab column."""
        panel = IpcControlPanel(interactive_backend=self._ib)

        # Mechanism change → update canvas accent color + animation mode
        panel.mechanism_changed.connect(self._ipc_vis.set_mechanism)

        # Send requested → toast
        def _on_send(params: dict):
            if not self._ib:
                # No backend: still animate
                self._ipc_vis.set_active(True)
                return

        panel.send_requested.connect(_on_send)

        # Burst started → activate animation
        panel.burst_started.connect(
            lambda p: self._ipc_vis.set_active(True))

        # Burst stopped → keep canvas live until send finishes
        panel.burst_stopped.connect(
            lambda: None)  # backend handles via EventBus

        # Wire EventBus toast for sends
        self._bus.ipc_sent.connect(
            lambda d: self._split.show_toast(
                f"✓ {d.get('mechanism','?')}  {d.get('bytes','?')} B "
                f"  ⏱ {d.get('send_time_us','?')} µs",
                ToastLevel.SUCCESS))
        self._bus.ipc_burst_complete.connect(
            lambda d: self._split.show_toast(
                f"Burst done  ·  {d.get('count','?')} msgs  "
                f"·  {d.get('throughput_mbs','?'):.2f} MB/s  "
                f"·  avg {d.get('avg_latency_us','?'):.1f} µs",
                ToastLevel.SUCCESS))
        self._bus.ipc_error.connect(
            lambda d: self._split.show_toast(
                f"IPC error: {d.get('error','?')}", ToastLevel.ERROR))

        return panel

    # (legacy helpers kept for compatibility — IpcControlPanel handles these now)
    def _on_ipc_send(self):
        pass

    def _toggle_ipc(self):
        active = not self._ipc_vis._active
        self._ipc_vis.set_active(active)

    # ------------------------------------------------------------------ #
    # Sync tab                                                              #
    # ------------------------------------------------------------------ #

    def _make_sync_controls(self) -> QWidget:
        """Create and wire the SyncControlPanel for the left Sync tab column."""
        panel = SyncControlPanel(interactive_backend=self._ib)

        # Expose the container combo so _poll can read the current selection
        self._sync_container_combo = panel._container_combo

        # Wire container change to restart streaming in visualizer
        panel.container_changed.connect(self._sync_vis.start_streaming)
        panel.container_changed.connect(self._on_sync_container_changed)

        # When deadlock is injected, toast it
        def _on_deadlock_inject(p: dict):
            self._split.show_toast(
                f"Injecting deadlock ({p['num_threads']} threads)...", ToastLevel.WARN)
        panel.deadlock_inject_requested.connect(_on_deadlock_inject)

        return panel

    # (legacy helpers kept for compatibility — SyncControlPanel handles these now)
    def _on_acquire(self):
        pass

    def _on_inject_deadlock(self):
        pass

    # ------------------------------------------------------------------ #
    # Benchmarks tab                                                        #
    # ------------------------------------------------------------------ #

    def _make_bench_controls(self) -> QWidget:
        """Left control panel for the Benchmarks tab (placeholder for Phase 6)."""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(12, 12, 16, 12)
        lbl = QLabel("Benchmark Controls")
        lbl.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 13px; font-weight: bold;")
        layout.addWidget(lbl)
        layout.addStretch()
        return w

    # ------------------------------------------------------------------ #
    # Event Log tab                                                         #
    # ------------------------------------------------------------------ #

    def _make_log_controls(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(12, 12, 16, 12)
        lbl = QLabel("Event Log")
        lbl.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 13px; font-weight: bold;")
        layout.addWidget(lbl)

        btn_clear = QPushButton("Clear Log")
        btn_clear.clicked.connect(self._clear_log)
        layout.addWidget(btn_clear)
        layout.addStretch()
        return w

    def _make_log_canvas(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)

        self._log_model = LogListModel()
        self._log_view  = QListView()
        self._log_view.setModel(self._log_model)
        self._log_view.setItemDelegate(LogItemDelegate())
        self._log_view.setStyleSheet(
            f"background-color: {SURFACE}; border: 1px solid {BORDER}; border-radius: 4px;")
        self._log_view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self._log_view.setUniformItemSizes(True)
        layout.addWidget(self._log_view)
        return w

    def _clear_log(self):
        self._log_model.clear()

    def _append_log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self._log_model.add_message(f"[{ts}] {msg}")
        self._log_view.scrollToBottom()

    # ------------------------------------------------------------------ #
    # EventBus connections                                                  #
    # ------------------------------------------------------------------ #

    def _connect_event_bus(self) -> None:
        bus = self._bus
        bus.ipc_sent.connect(
            lambda d: self._append_log(
                f"IPC SENT {d.get('mechanism','?')} {d.get('bytes','?')}B "
                f"in {d.get('send_time_us','?')} µs"))
        bus.lock_acquired.connect(
            lambda d: self._append_log(
                f"ACQUIRE {d.get('primitive','?')} '{d.get('lock_name','')}' "
                f"by PID {d.get('holder_pid','?')} in {d.get('acquire_time_us','?')} µs"))
        bus.lock_released.connect(
            lambda d: self._append_log(
                f"RELEASE {d.get('primitive','?')} '{d.get('lock_name','')}' "
                f"by PID {d.get('pid','?')}"))
        bus.deadlock_detected.connect(
            lambda d: (
                self._append_log(f"[DEADLOCK] {d.get('container','?')}"),
                self._status_bar.showMessage(
                    f"⚠ DEADLOCK detected in {d.get('container','?')}!")))
        bus.deadlock_resolved.connect(
            lambda d: self._append_log(
                f"[RESOLVED] Killed PID {d.get('killed_pid','?')}"))
        bus.error_occurred.connect(
            lambda src, msg: self._append_log(f"[{src} ERROR] {msg}"))
        bus.container_status_changed.connect(
            lambda name, status: self._append_log(
                f"Container {name}: {status}"))
        bus.binaries_deployed.connect(
            lambda name: self._split.show_toast(
                f"Binaries deployed to {name}", ToastLevel.SUCCESS))
        bus.scenario_event.connect(self._on_scenario_event)

    # ------------------------------------------------------------------ #
    # Polling                                                               #
    # ------------------------------------------------------------------ #

    def _setup_polling(self) -> None:
        self._worker = PollWorker(self._mgr, self._metrics, self._detector, self)
        self._worker.result_ready.connect(self._on_poll_result)
        
        # Initialize target container if combo is already created
        target = getattr(self, '_sync_container_combo', None)
        if target:
            self._worker.set_target_container(target.currentText())
            
        self._worker.start()

    def _on_sync_container_changed(self, name: str):
        if hasattr(self, "_worker"):
            self._worker.set_target_container(name)

    def _on_poll_result(self, data: dict) -> None:
        if data.get("offline"):
            self._update_containers_offline()
            return

        for name in CONTAINER_NAMES:
            cdata = data.get(name)
            if not cdata:
                continue

            status = cdata["status"]
            widgets = self._container_groups.get(name, {})
            badge: QLabel = widgets.get("badge")
            if badge:
                if status == "Running":
                    badge.setText("● Running")
                    badge.setObjectName("badgeRunning")
                elif status == "NotFound":
                    badge.setText("✗ NotFound")
                    badge.setObjectName("badgeError")
                else:
                    badge.setText("○ Stopped")
                    badge.setObjectName("badgeStopped")
                badge.style().unpolish(badge)
                badge.style().polish(badge)

            if status == "Running":
                cpu_val = cdata["cpu"]
                mem_val = cdata["mem"]
                cpu_bar: QProgressBar = widgets.get("cpu")
                mem_bar: QProgressBar = widgets.get("mem")
                if cpu_bar: cpu_bar.setValue(int(cpu_val))
                if mem_bar: mem_bar.setValue(int(mem_val))
                if hasattr(self, '_cpu_chart'):
                    self._cpu_chart.push(name, cpu_val)
                
                # Only log periodically to avoid event log spam, or log every tick
                # self._append_log(f"{name}: CPU={cpu_val:.1f}% MEM={mem_val:.1f}%")
                if cdata["error"]:
                    self._append_log(f"[WARN] {name}: {cdata['error']}")

        # Handle deadlock check results
        dl_info = data.get("deadlock")
        if dl_info:
            target = data.get("target", CONTAINER_NAMES[0])
            if dl_info["detected"]:
                self._sync_vis.set_deadlock_mode(True, dl_info["graph"], dl_info["cycle"])
                self._append_log(f"[DEADLOCK] {target} — cycle: {dl_info['cycle']}")
                self._status_bar.showMessage(f"⚠ DEADLOCK in {target}! Cycle: {dl_info['cycle']}")
            elif dl_info["error"]:
                log.debug("deadlock poll error: %s", dl_info["error"])
            else:
                self._sync_vis.set_deadlock_mode(False)

    def _update_containers_offline(self) -> None:
        for name, widgets in self._container_groups.items():
            badge: QLabel = widgets.get("badge")
            if badge:
                badge.setText("○ Offline")
                badge.setObjectName("badgeStopped")
                badge.style().unpolish(badge)
                badge.style().polish(badge)
        self._status_bar.showMessage(
            "Backend unavailable — running in preview mode (no LXD)")

    def _poll(self) -> None:
        """Manual trigger to run the background worker (handled automatically by QThread)."""
        pass

    def closeEvent(self, event) -> None:
        if hasattr(self, "_worker"):
            self._worker.stop()
            self._worker.wait()
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Log model + delegate (unchanged from original)
# ---------------------------------------------------------------------------

class LogListModel(QAbstractListModel):
    """High-performance list model for massive event logs."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = []
        self._max_rows = 5000

    def rowCount(self, parent=QModelIndex()):
        return len(self._data)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return self._data[index.row()]
        return None

    def add_message(self, msg: str):
        self.beginInsertRows(QModelIndex(), len(self._data), len(self._data))
        self._data.append(msg)
        self.endInsertRows()
        if len(self._data) > self._max_rows:
            self.beginRemoveRows(QModelIndex(), 0, 0)
            self._data.pop(0)
            self.endRemoveRows()

    def clear(self):
        self.beginResetModel()
        self._data.clear()
        self.endResetModel()


class LogItemDelegate(QStyledItemDelegate):
    """Custom painter for log items — color-coded without HTML overhead."""
    def paint(self, painter, option, index):
        text       = index.data(Qt.ItemDataRole.DisplayRole)
        bg_color   = DARK_BG
        text_color = TEXT_PRIMARY
        font_weight = QFont.Weight.Normal

        if "[DEADLOCK]" in text or "[WARN]" in text or "ERROR" in text:
            text_color  = RED
            font_weight = QFont.Weight.Bold
        elif "ACQUIRE" in text or "RELEASE" in text:
            text_color = PURPLE
        elif "IPC" in text or "MB/s" in text or "SENT" in text:
            text_color = TEAL
        elif "WAIT" in text or "Offline" in text:
            text_color = TEXT_DIM

        if option.state & QStyle.StateFlag.State_Selected:
            bg_color = CARD

        painter.fillRect(option.rect, QColor(bg_color))
        font = QFont("Consolas", 10, font_weight)
        painter.setFont(font)
        painter.setPen(QColor(text_color))
        rect = option.rect.adjusted(6, 4, -6, -4)
        painter.drawText(
            rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            text)

    def sizeHint(self, option, index):
        return QSize(200, 24)
