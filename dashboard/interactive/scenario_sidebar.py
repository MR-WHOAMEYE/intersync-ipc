"""
scenario_sidebar.py
InterSync Dashboard — Scenario Sidebar (Phase 5).

Replaces the standard control panels when a scenario is active.
Displays objectives, progress, and instructions.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, QProgressBar, QGroupBox
)

from dashboard.ui.theme import (
    CARD, BORDER, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_DIM, TEAL, RED, PURPLE
)
from dashboard.backend.event_bus import EventBus

class ScenarioSidebar(QWidget):
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self._engine = engine
        self._bus = EventBus.instance()
        self._scenario = None

        self._build_ui()
        self._bus.scenario_event.connect(self._on_scenario_event)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 16, 12)
        layout.setSpacing(12)

        self._lbl_title = QLabel("No Scenario Active")
        self._lbl_title.setStyleSheet(f"color: {TEAL}; font-size: 14px; font-weight: bold;")
        self._lbl_title.setWordWrap(True)
        layout.addWidget(self._lbl_title)

        self._lbl_desc = QLabel("Select a scenario from the header to begin.")
        self._lbl_desc.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px;")
        self._lbl_desc.setWordWrap(True)
        layout.addWidget(self._lbl_desc)

        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.setStyleSheet(
            f"QProgressBar {{ border: 1px solid {BORDER}; border-radius: 4px; background: {CARD}; height: 8px; }}"
            f"QProgressBar::chunk {{ background-color: {PURPLE}; border-radius: 4px; }}"
        )
        layout.addWidget(self._progress)

        self._lbl_stats = QLabel("Time: 0s | Meals: 0")
        self._lbl_stats.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 12px; font-weight: bold;")
        layout.addWidget(self._lbl_stats)

        inst_grp = QGroupBox("Instructions")
        inst_grp.setStyleSheet(f"QGroupBox {{ border: 1px solid {BORDER}; border-radius: 6px; padding: 10px; margin-top: 10px; color: {TEXT_DIM}; }}")
        inst_layout = QVBoxLayout(inst_grp)
        self._lbl_instructions = QLabel("")
        self._lbl_instructions.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 11px;")
        self._lbl_instructions.setWordWrap(True)
        inst_layout.addWidget(self._lbl_instructions)
        layout.addWidget(inst_grp)

        layout.addStretch()

        self._btn_quit = QPushButton("Stop Scenario")
        self._btn_quit.setStyleSheet(
            f"QPushButton {{ background: {CARD}; color: {RED}; border: 1px solid {RED}; border-radius: 6px; padding: 8px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {RED}; color: white; }}"
        )
        self._btn_quit.clicked.connect(self._engine.stop_scenario)
        layout.addWidget(self._btn_quit)

    def _on_scenario_event(self, event_type: str, data: dict):
        if event_type == "start":
            self._scenario = data
            self._lbl_title.setText(data.get("name", "Scenario"))
            self._lbl_desc.setText(data.get("description", ""))
            
            instructions = "\n\n".join(data.get("instructions", []))
            self._lbl_instructions.setText(instructions)
            
            self._progress.setValue(0)
            self._lbl_stats.setText("Time: 0.0s | Meals: 0")
            
        elif event_type == "step_complete":
            elapsed = data.get("elapsed", 0)
            meals = data.get("meals", 0)
            self._lbl_stats.setText(f"Time: {elapsed:.1f}s | Meals: {meals}")
            
            # Update progress bar if there's a timeout
            scen = self._scenario or {}
            lose = scen.get("lose_condition", {})
            timeout = lose.get("timeout_s", 0)
            if timeout > 0:
                pct = min(100, int((elapsed / timeout) * 100))
                self._progress.setValue(pct)
                
        elif event_type in ("win", "lose", "quit"):
            self._scenario = None
