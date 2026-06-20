"""
scenario_engine.py
InterSync Dashboard — Scenarios Engine (Phase 5).

Loads JSON scenarios and orchestrates them.
Monitors the EventBus to check win/lose conditions.
Emits `scenario_event` with state updates.
"""

from __future__ import annotations

import json
import os
import time
from PyQt6.QtCore import QObject, pyqtSignal, QTimer
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout

from dashboard.backend.event_bus import EventBus
from dashboard.ui.theme import DARK_BG, CARD, TEXT_PRIMARY, TEAL, RED, BORDER

class ScenarioResultsDialog(QDialog):
    def __init__(self, title, message, success=True, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Scenario Result")
        self.setFixedSize(300, 150)
        self.setStyleSheet(f"background: {DARK_BG}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER};")

        layout = QVBoxLayout(self)
        
        lbl_title = QLabel(title)
        lbl_title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {TEAL if success else RED}; border: none;")
        layout.addWidget(lbl_title)

        lbl_msg = QLabel(message)
        lbl_msg.setStyleSheet("font-size: 12px; border: none;")
        layout.addWidget(lbl_msg)

        btn_layout = QHBoxLayout()
        self.btn_retry = QPushButton("Retry")
        self.btn_quit = QPushButton("Quit")
        for btn in (self.btn_retry, self.btn_quit):
            btn.setStyleSheet(f"background: {CARD}; padding: 6px; border: 1px solid {BORDER}; border-radius: 4px;")
        
        self.btn_retry.clicked.connect(self.accept)
        self.btn_quit.clicked.connect(self.reject)

        btn_layout.addWidget(self.btn_retry)
        btn_layout.addWidget(self.btn_quit)
        layout.addLayout(btn_layout)


class ScenarioEngine(QObject):
    def __init__(self, interactive_backend=None, parent=None):
        super().__init__(parent)
        self._ib = interactive_backend
        self._bus = EventBus.instance()
        self._active_scenario = None
        
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._evaluate)
        
        self._start_time = 0
        self._meals = 0
        self._deadlocked = False
        self._deadlock_resolved = False

        self._bus.philo_state_update.connect(self._on_philo_state)
        self._bus.deadlock_detected.connect(self._on_deadlock_detected)
        self._bus.deadlock_resolved.connect(self._on_deadlock_resolved)

    def load_scenario(self, filepath: str) -> dict:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)

    def start_scenario(self, scenario: dict):
        self._active_scenario = scenario
        self._start_time = time.time()
        self._meals = 0
        self._deadlocked = False
        self._deadlock_resolved = False
        
        # Emit start event
        self._bus.scenario_event.emit("start", scenario)
        
        # Inject initial state if needed
        init = scenario.get("initial_state", {})
        if init.get("action") == "inject_deadlock" and self._ib:
            self._ib.inject_deadlock(init.get("container", "interync-lab-1"), 
                                     init.get("primitive", "MUTEX"), 
                                     init.get("threads", 3))
        
        self._timer.start()

    def stop_scenario(self):
        self._timer.stop()
        self._active_scenario = None
        self._bus.scenario_event.emit("quit", {})

    def _on_philo_state(self, data: dict):
        if self._active_scenario:
            self._meals = data.get("total_meals", 0)
            self._deadlocked = data.get("deadlocked", False)

    def _on_deadlock_detected(self, data: dict):
        if self._active_scenario:
            self._deadlocked = True

    def _on_deadlock_resolved(self, data: dict):
        if self._active_scenario:
            self._deadlock_resolved = True

    def _evaluate(self):
        if not self._active_scenario:
            return

        elapsed = time.time() - self._start_time
        win_cond = self._active_scenario.get("win_condition", {})
        lose_cond = self._active_scenario.get("lose_condition", {})
        timeout_s = win_cond.get("timeout_s") or lose_cond.get("timeout_s") or 9999

        # Win checks
        won = False
        if win_cond.get("type") == "philo_meals" and self._meals >= win_cond.get("target", 0):
            won = True
        elif win_cond.get("type") == "deadlock_resolved" and self._deadlock_resolved:
            won = True

        # Lose checks
        lost = False
        lose_reason = ""
        if lose_cond.get("type") == "deadlock" and self._deadlocked:
            lost = True
            lose_reason = "Deadlock occurred!"
        elif lose_cond.get("type") == "timeout" and elapsed >= timeout_s:
            lost = True
            lose_reason = "Time's up!"
        elif elapsed >= timeout_s and not won:
            lost = True
            lose_reason = "Time's up!"

        if won:
            self._timer.stop()
            self._bus.scenario_event.emit("win", {"time": elapsed, "meals": self._meals})
            self._show_result(True, f"You won in {elapsed:.1f}s!")
        elif lost:
            self._timer.stop()
            self._bus.scenario_event.emit("lose", {"reason": lose_reason})
            self._show_result(False, f"You lost: {lose_reason}")
        else:
            self._bus.scenario_event.emit("step_complete", {"elapsed": elapsed, "meals": self._meals})

    def _show_result(self, success: bool, message: str):
        dialog = ScenarioResultsDialog("Victory!" if success else "Defeat", message, success)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Retry
            self.start_scenario(self._active_scenario)
        else:
            self.stop_scenario()
