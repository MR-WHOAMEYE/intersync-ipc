"""
philo_visualizer.py
InterSync Dashboard — Dining Philosophers Visualizer (Phase 4).

Renders a round table with philosophers and forks.
Colors:
  Thinking = Blue (TEAL)
  Hungry = Yellow
  Eating = Neon Green
  Deadlock = Red

Listens to EventBus.philo_state_update for real-time visualization.
"""

from __future__ import annotations

import math
from typing import Optional

from PyQt6.QtCore import Qt, QRectF, QPointF, QTimer
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QRadialGradient,
)
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel

from dashboard.ui.theme import (
    DARK_BG, SURFACE, CARD, BORDER,
    TEAL, TEAL_DIM, PURPLE, GREEN, RED, TEXT_PRIMARY, TEXT_DIM,
)
from dashboard.backend.event_bus import EventBus

# Colors
COLOR_THINKING = QColor(TEAL_DIM)
COLOR_HUNGRY   = QColor("#FFC107") # Amber/Yellow
COLOR_EATING   = QColor(GREEN)
COLOR_DEADLOCK = QColor(RED)
COLOR_FORK_FREE = QColor(BORDER)
COLOR_FORK_HELD = QColor(GREEN)

class PhiloVisualizer(QWidget):
    """
    QPainter-based canvas for the Dining Philosophers.
    """

    def __init__(self, interactive_backend=None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._ib = interactive_backend
        self._bus = EventBus.instance()

        self._philosophers: list[dict] = []
        self._deadlocked = False
        self._total_meals = 0

        self.setMinimumSize(400, 400)

        self._build_ui()
        self._connect_bus()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._status_label = QLabel("Waiting to start...")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setFixedHeight(28)
        self._status_label.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 11px; "
            f"background: {SURFACE}; border-bottom: 1px solid {BORDER};"
        )
        layout.addWidget(self._status_label)

        self._canvas = QWidget() # We will override paintEvent in the parent class or make a sub-widget.
        # Actually, let's just use self for painting and set layout alignment.
        # However, to avoid painting over the label, let's make a dedicated Canvas widget.
        self._canvas = PhiloCanvas()
        layout.addWidget(self._canvas, stretch=1)

    def _connect_bus(self) -> None:
        self._bus.philo_state_update.connect(self._on_state_update)
        self._bus.philo_started.connect(self._on_started)
        self._bus.philo_stopped.connect(self._on_stopped)

    def _on_started(self, data: dict) -> None:
        self._status_label.setText(f"Simulation running: {data.get('num_philosophers', 5)} philosophers")
        self._status_label.setStyleSheet(
            f"color: {TEAL}; font-size: 11px; background: {SURFACE}; border-bottom: 1px solid {BORDER};"
        )
        self._canvas.reset()

    def _on_stopped(self, data: dict) -> None:
        self._status_label.setText("Simulation stopped.")
        self._status_label.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 11px; background: {SURFACE}; border-bottom: 1px solid {BORDER};"
        )
        self._canvas.reset()

    def _on_state_update(self, data: dict) -> None:
        philos = data.get("philosophers", [])
        deadlocked = data.get("deadlocked", False)
        meals = data.get("total_meals", 0)

        if deadlocked:
            self._status_label.setText("⚠ DEADLOCK DETECTED")
            self._status_label.setStyleSheet(
                f"color: {RED}; font-weight: bold; font-size: 12px; "
                f"background: {SURFACE}; border-bottom: 1px solid {RED};"
            )
        else:
            self._status_label.setText(f"Simulation running  —  Total meals: {meals}")
            self._status_label.setStyleSheet(
                f"color: {TEAL}; font-size: 11px; background: {SURFACE}; border-bottom: 1px solid {BORDER};"
            )

        self._canvas.update_state(philos, deadlocked)


class PhiloCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._philos = []
        self._deadlocked = False

    def reset(self):
        self._philos = []
        self._deadlocked = False
        self.update()

    def update_state(self, philos: list[dict], deadlocked: bool):
        self._philos = philos
        self._deadlocked = deadlocked
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, QColor(DARK_BG))

        if not self._philos:
            painter.setPen(QColor(TEXT_DIM))
            painter.setFont(QFont("Inter", 11))
            painter.drawText(
                QRectF(0, 0, w, h),
                Qt.AlignmentFlag.AlignCenter,
                "Press Play to begin Dining Philosophers simulation"
            )
            painter.end()
            return

        cx, cy = w / 2, h / 2
        r_table = min(w, h) * 0.25
        r_philo = min(w, h) * 0.08
        n = len(self._philos)

        # Draw Table
        painter.setPen(QPen(QColor(BORDER), 3))
        painter.setBrush(QBrush(QColor(CARD)))
        painter.drawEllipse(QPointF(cx, cy), r_table, r_table)

        # Draw Philosophers and Forks
        for i, philo in enumerate(self._philos):
            angle = i * (2 * math.pi / n) - (math.pi / 2) # Start at top
            
            # Fork (between this philo and the next)
            fork_angle = angle + (math.pi / n)
            fx = cx + (r_table * 0.8) * math.cos(fork_angle)
            fy = cy + (r_table * 0.8) * math.sin(fork_angle)

            # Determine fork state. In the C code, a fork might be held by left or right philo.
            # We'll just look at whether the philos are eating to highlight forks.
            # Simple approximation: if philo i or i+1 is eating, their respective fork is held.
            # Actually, C backend output should tell us who holds what, but we will guess from state for now.
            state = philo.get("state", "THINKING")
            next_philo = self._philos[(i + 1) % n]
            next_state = next_philo.get("state", "THINKING")

            # In standard setup, Philo i needs Fork i and Fork (i+1)%n.
            fork_held = False
            if state == "EATING" or next_state == "EATING":
                fork_held = True

            f_color = COLOR_FORK_HELD if fork_held else COLOR_FORK_FREE
            painter.setPen(QPen(f_color, 4))
            # Draw a line for the fork
            painter.drawLine(
                QPointF(cx + (r_table * 0.6) * math.cos(fork_angle), cy + (r_table * 0.6) * math.sin(fork_angle)),
                QPointF(cx + (r_table * 0.9) * math.cos(fork_angle), cy + (r_table * 0.9) * math.sin(fork_angle))
            )

            # Philosopher Circle
            px = cx + (r_table + r_philo + 10) * math.cos(angle)
            py = cy + (r_table + r_philo + 10) * math.sin(angle)

            p_color = COLOR_THINKING
            if state == "HUNGRY":
                p_color = COLOR_HUNGRY
            elif state == "EATING":
                p_color = COLOR_EATING
            if self._deadlocked:
                p_color = COLOR_DEADLOCK

            painter.setPen(QPen(QColor(BORDER), 2))
            
            # Gradient glow
            grad = QRadialGradient(px, py, r_philo)
            grad.setColorAt(0, p_color.lighter(130))
            grad.setColorAt(1, p_color.darker(150))
            painter.setBrush(QBrush(grad))

            painter.drawEllipse(QPointF(px, py), r_philo, r_philo)

            # ID text
            painter.setPen(QColor(TEXT_PRIMARY))
            painter.setFont(QFont("Inter", 10, QFont.Weight.Bold))
            painter.drawText(
                QRectF(px - r_philo, py - r_philo, r_philo * 2, r_philo * 2),
                Qt.AlignmentFlag.AlignCenter,
                str(i)
            )

            # Meals text
            meals = philo.get("meals", 0)
            painter.setPen(QColor(TEXT_DIM))
            painter.setFont(QFont("Inter", 8))
            painter.drawText(
                QRectF(px - r_philo, py + r_philo + 2, r_philo * 2, 14),
                Qt.AlignmentFlag.AlignCenter,
                f"{meals} meals"
            )

        painter.end()
