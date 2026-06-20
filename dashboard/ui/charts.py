"""
charts.py
InterSync Dashboard — renders results/report.json as bar + line charts
embedded inside PyQt6 widgets using matplotlib.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSlider, QGroupBox, QFormLayout, QComboBox, QCheckBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtGui import QColor

from .theme import (
    DARK_BG, SURFACE, CARD, BORDER,
    TEAL, TEAL_DARK, TEAL_DIM, PURPLE, GREEN, RED,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_DIM,
)

log = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).parents[2] / "results"

# Convert theme hex to QColor for pyqtgraph
def to_color(hex_str: str, alpha: int = 255) -> QColor:
    c = QColor(hex_str)
    c.setAlpha(alpha)
    return c

class LiveThroughputChart(pg.PlotWidget):
    """Scrolling line chart for real-time throughput during benchmark run."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.setBackground(DARK_BG)
        self.setTitle("Live Throughput (MB/s)", color=TEXT_PRIMARY, size="12pt")
        self.setLabel('left', 'MB/s', color=TEXT_SECONDARY)
        self.setLabel('bottom', 'Time', color=TEXT_SECONDARY)
        self.showGrid(x=True, y=True, alpha=0.3)
        self.curve = self.plot(pen=pg.mkPen(color=TEAL, width=2.5))
        self.data = []

    def push(self, val: float):
        self.data.append(val)
        if len(self.data) > 100:
            self.data.pop(0)
        self.curve.setData(self.data)

    def reset(self):
        self.data = []
        self.curve.setData([])


class BaseBarChart(pg.PlotWidget):
    """Base class for Bar Charts in PyQtGraph"""
    def __init__(self, title: str, ylabel: str, brush_color: str, **kwargs):
        super().__init__(**kwargs)
        self.setBackground(DARK_BG)
        self.setTitle(title, color=TEXT_PRIMARY, size="12pt")
        self.setLabel('left', ylabel, color=TEXT_SECONDARY)
        self.showGrid(y=True, alpha=0.3)
        self.brush_color = brush_color
        self.getAxis('bottom').setPen(to_color(BORDER))
        self.getAxis('left').setPen(to_color(BORDER))

    def draw_bars(self, labels: list[str], values: list[float]):
        self.clear()
        if not values:
            return
        
        x = np.arange(len(labels))
        ax = self.getAxis('bottom')
        ax.setTicks([list(zip(x, labels))])
        
        bar_item = pg.BarGraphItem(x=x, height=values, width=0.6, 
                                   brush=to_color(self.brush_color, 200),
                                   pen=to_color(self.brush_color))
        self.addItem(bar_item)


class LatencyBarChart(BaseBarChart):
    def __init__(self):
        super().__init__("IPC Latency Comparison", "Latency (µs)", TEAL)

    def update_data(self, report: dict[str, Any]):
        latency_data = report.get("latency", [])
        labels = [r.get("mechanism", "?") for r in latency_data]
        values = [r.get("latency_us", 0) for r in latency_data]
        self.draw_bars(labels, values)


class ThroughputBarChart(BaseBarChart):
    def __init__(self):
        super().__init__("IPC Throughput Comparison", "Throughput (MB/s)", PURPLE)

    def update_data(self, report: dict[str, Any]):
        tp_data = report.get("throughput", [])
        labels = [r.get("mechanism", "?") for r in tp_data]
        values = [r.get("throughput_mbs", 0) for r in tp_data]
        self.draw_bars(labels, values)


class LiveCpuChart(pg.PlotWidget):
    """Scrolling line chart for live CPU utilisation per container."""
    HISTORY = 60

    def __init__(self, container_names: list[str], **kwargs):
        super().__init__(**kwargs)
        self.setBackground(DARK_BG)
        self.setTitle("Live CPU Utilisation", color=TEXT_PRIMARY, size="12pt")
        self.setLabel('left', 'CPU %', color=TEXT_SECONDARY)
        self.setLabel('bottom', 'Time (polls)', color=TEXT_SECONDARY)
        self.showGrid(y=True, alpha=0.3)
        self.setYRange(0, 105)
        
        self.addLegend(offset=(10, 10))
        
        self._names = container_names
        self._history = {n: [] for n in container_names}
        colors = [TEAL, GREEN, PURPLE]
        
        self.curves = {}
        for i, name in enumerate(container_names):
            c = colors[i % len(colors)]
            self.curves[name] = self.plot(pen=pg.mkPen(color=c, width=2), name=name)

    def push(self, container_name: str, cpu_pct: float):
        hist = self._history.setdefault(container_name, [])
        hist.append(cpu_pct)
        if len(hist) > self.HISTORY:
            hist.pop(0)
        
        self.curves[container_name].setData(hist)


class BenchmarkStreamer(QThread):
    """Runs the benchmark scenario in a background thread to prevent UI freezing."""
    throughput_ready = pyqtSignal(float)
    finished_report = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, runner, container_name: str, params: dict):
        super().__init__()
        self.runner = runner
        self.container_name = container_name
        self.params = params

    def run(self):
        try:
            report = self.runner.run_scenario(
                self.container_name,
                "producer_consumer.py",
                self.params,
                stream_callback=self.throughput_ready.emit
            )
            self.runner.save_results(report)
            self.finished_report.emit(report)
        except Exception as exc:
            self.error_occurred.emit(str(exc))

class ChartsWidget(QWidget):
    """
    Main Benchmarks tab layout. Left: Controls. Right: Live + Bar Charts.
    """
    def __init__(self, runner, container_combo: QComboBox, parent=None):
        super().__init__(parent)
        self._runner = runner
        self._combo = container_combo
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)
        
        # Left Panel - Controls
        control_panel = QWidget()
        control_panel.setFixedWidth(280)
        control_panel.setObjectName("glassCard") # styling from theme.py
        ctrl_layout = QVBoxLayout(control_panel)
        ctrl_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        title = QLabel("Benchmark Controls")
        title.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 16px; font-weight: bold;")
        ctrl_layout.addWidget(title)
        
        # Form for sliders
        form = QFormLayout()
        
        self.msg_size_slider = QSlider(Qt.Orientation.Horizontal)
        self.msg_size_slider.setRange(1, 1024)
        self.msg_size_slider.setValue(64)
        self.msg_size_lbl = QLabel("64 KB")
        self.msg_size_slider.valueChanged.connect(lambda v: self.msg_size_lbl.setText(f"{v} KB"))
        form.addRow("Message Size:", self.msg_size_lbl)
        form.addRow(self.msg_size_slider)
        
        self.thread_slider = QSlider(Qt.Orientation.Horizontal)
        self.thread_slider.setRange(1, 32)
        self.thread_slider.setValue(4)
        self.thread_lbl = QLabel("4 Threads")
        self.thread_slider.valueChanged.connect(lambda v: self.thread_lbl.setText(f"{v} Threads"))
        form.addRow("Concurrency:", self.thread_lbl)
        form.addRow(self.thread_slider)
        
        ctrl_layout.addLayout(form)
        
        # Run Button
        self.run_btn = QPushButton("RUN SCENARIOS")
        self.run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.run_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {TEAL};
                color: #000000;
                font-weight: bold;
                border-radius: 8px;
                padding: 12px;
                margin-top: 20px;
            }}
            QPushButton:hover {{ background-color: {TEAL_DARK}; }}
            QPushButton:pressed {{ background-color: {TEAL_DIM}; color: {TEAL}; }}
        """)
        self.run_btn.clicked.connect(self._on_run_clicked)
        ctrl_layout.addWidget(self.run_btn)
        
        # Live Stream Toggle
        self.live_stream_cb = QCheckBox("Stream to IPC Canvas")
        self.live_stream_cb.setStyleSheet(f"color: {TEXT_PRIMARY}; margin-top: 10px;")
        ctrl_layout.addWidget(self.live_stream_cb)
        
        # Save Snapshot Button
        self.snapshot_btn = QPushButton("Save Snapshot")
        self.snapshot_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {CARD};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 8px;
                padding: 8px;
                margin-top: 10px;
            }}
            QPushButton:hover {{ background-color: {SURFACE}; }}
        """)
        self.snapshot_btn.clicked.connect(self._save_snapshot)
        ctrl_layout.addWidget(self.snapshot_btn)
        
        ctrl_layout.addStretch()
        
        # Right Panel - Charts
        chart_panel = QWidget()
        chart_layout = QVBoxLayout(chart_panel)
        chart_layout.setContentsMargins(0, 0, 0, 0)
        
        self.live_chart = LiveThroughputChart()
        self.latency_chart = LatencyBarChart()
        self.tp_chart = ThroughputBarChart()
        
        chart_layout.addWidget(self.live_chart, stretch=2)
        
        bottom_charts = QHBoxLayout()
        bottom_charts.addWidget(self.latency_chart)
        bottom_charts.addWidget(self.tp_chart)
        chart_layout.addLayout(bottom_charts, stretch=3)
        
        layout.addWidget(control_panel)
        layout.addWidget(chart_panel, stretch=1)
        
        # Load initial data
        self.refresh()
        self._streamer = None

    def _on_run_clicked(self):
        if not self._runner:
            return
            
        target = self._combo.currentText()
        if not target:
            return
            
        self.run_btn.setEnabled(False)
        self.run_btn.setText("RUNNING...")
        self.live_chart.reset()
        
        params = {
            "message_size_bytes": self.msg_size_slider.value() * 1024,
            "num_messages": 1000,
            "mechanisms": ["PIPE", "QUEUE", "SOCKET", "SHM"],
        }
        
        self._streamer = BenchmarkStreamer(self._runner, target, params)
        self._streamer.throughput_ready.connect(self.live_chart.push)
        self._streamer.finished_report.connect(self._on_run_finished)
        self._streamer.error_occurred.connect(self._on_run_error)
        self._streamer.start()

    def _on_run_finished(self, report):
        self.run_btn.setEnabled(True)
        self.run_btn.setText("RUN SCENARIOS")
        self.latency_chart.update_data(report)
        self.tp_chart.update_data(report)
        
    def _on_run_error(self, err_msg):
        log.error("Benchmark failed: %s", err_msg)
        self.run_btn.setEnabled(True)
        self.run_btn.setText("RUN SCENARIOS")

    def refresh(self) -> None:
        """Reload report.json and update all bar charts."""
        report_path = RESULTS_DIR / "report.json"
        if not report_path.exists():
            report: dict[str, Any] = {}
        else:
            try:
                report = json.loads(report_path.read_text())
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("Cannot read report.json: %s", exc)
                report = {}

        self.latency_chart.update_data(report)
        self.tp_chart.update_data(report)

    def _save_snapshot(self):
        import time
        from PyQt6.QtGui import QPixmap
        import os
        
        # Ensure results directory exists
        os.makedirs(RESULTS_DIR, exist_ok=True)
        
        filename = RESULTS_DIR / f"snapshot_{int(time.time())}.png"
        
        # Grab the entire charts widget or just the right panel
        pixmap = self.grab()
        pixmap.save(str(filename), "PNG")
        
        # Inform the user (if we had access to the toast, but we can just log for now)
        log.info(f"Saved snapshot to {filename}")
        # Let's emit a signal or assume parent will handle toast if needed.
        # For now, it just saves the file.
