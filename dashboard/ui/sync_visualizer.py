"""
sync_visualizer.py
InterSync Dashboard — lock ownership and wait-for graph visualiser (Phase 3).

New in Phase 3:
  • LockStateCanvas: click FREE lock → acquire; click HELD lock → release
  • Rect hit-testing via mousePressEvent on rendered lock positions
  • Step controls bar (⏮ ⏸ ⏭) drawn at bottom of canvas
  • QSlider timeline scrubber for replaying lock history
  • EventBus.lock_acquired / lock_released / deadlock_detected / deadlock_resolved
    update the canvas in real-time without polling
  • Hover tooltip on lock boxes (holder PID, wait count, acquire time)
  • Per-primitive accent colours:
      MUTEX=purple, SEMAPHORE=teal, CONDVAR=orange, RWLOCK=green

Two display modes (QStackedWidget):
  0 - LockStateCanvas  (normal — interactive lock boxes)
  1 - WaitForGraphCanvas (deadlock — matplotlib wait-for graph)
"""

from __future__ import annotations

import math
import re
import subprocess
import time
from typing import Optional

import networkx as nx
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from PyQt6.QtCore import Qt, QRectF, QPointF, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QPainterPath,
    QLinearGradient, QRadialGradient,
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QStackedWidget, QPushButton, QSlider, QToolTip,
)

from .theme import (
    DARK_BG, SURFACE, CARD, BORDER,
    PURPLE, PURPLE_DARK, PURPLE_DIM,
    TEAL, TEAL_DARK, TEAL_DIM,
    GREEN, RED, RED_DARK, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_DIM,
    COLOURS,
)
from dashboard.backend.event_bus import EventBus

# --------------------------------------------------------------------------
# TraceStreamer — unchanged from original (tails lock trace log via lxc)
# --------------------------------------------------------------------------
_LINE_RE = re.compile(
    r"^(\d+),(\d+),(0x[0-9a-fA-F]+|[0-9a-fA-F]+),(ACQUIRE|RELEASE|WAIT)\s*$")


class TraceStreamer(QThread):
    event_received = pyqtSignal(int, str, str)   # pid, lock_id, event_type

    def __init__(self, container_name: str, parent=None):
        super().__init__(parent)
        self.container_name = container_name
        self._running = True
        self._proc    = None

    def run(self):
        try:
            self._proc = subprocess.Popen(
                ["wsl", "lxc", "exec", self.container_name,
                 "--", "tail", "-F", "-n", "+1",
                 "/tmp/interync_lock_trace.log"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True, bufsize=1
            )
            for line in iter(self._proc.stdout.readline, ""):
                if not self._running:
                    break
                m = _LINE_RE.match(line.strip())
                if m:
                    _, pid_s, lock_id, event = m.groups()
                    if not lock_id.startswith("0x"):
                        lock_id = "0x" + lock_id
                    self.event_received.emit(int(pid_s), lock_id.lower(), event)
        except Exception as exc:
            pass
        finally:
            if self._proc:
                self._proc.terminate()

    def stop(self):
        self._running = False
        if self._proc:
            self._proc.terminate()
        self.wait()


# --------------------------------------------------------------------------
# Per-primitive accent colors
# --------------------------------------------------------------------------
PRIMITIVE_COLORS: dict[str, str] = {
    "MUTEX":     PURPLE,
    "SEMAPHORE": TEAL,
    "CONDVAR":   "#FF6D00",
    "RWLOCK":    GREEN,
}

LOCK_W, LOCK_H = 130, 54
QUEUE_DOT_R    = 11
ROW_GAP        = 90
STEP_BAR_H     = 48   # height of the step controls bar at bottom


# --------------------------------------------------------------------------
# LockStateCanvas — interactive QPainter lock visualiser
# --------------------------------------------------------------------------
class LockStateCanvas(QWidget):
    """
    Draws lock boxes with owner PIDs and waiting queues.

    Interactive:
      • Click a FREE lock box → emits lock_clicked(lock_id, "free")
      • Click a HELD lock box → emits lock_clicked(lock_id, "held")
      • Hover → QToolTip with PID info

    Callers connect lock_clicked to SyncControlPanel actions.
    """

    lock_clicked = pyqtSignal(str, str)   # (lock_id, "free"|"held")

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._locks:  list[dict] = []
        self._pulses: dict[str, float] = {}    # lock_id → pulse radius (0–20)

        # For hit-testing: list of (QRectF, lock_dict) populated in paintEvent
        self._rendered_rects: list[tuple[QRectF, dict]] = []

        # Flash state per lock_id: (type, alpha)
        self._flashes: dict[str, tuple[str, float]] = {}

        # Step history and playback
        self._history:      list[list[dict]] = []   # snapshots
        self._history_pos:  int = -1
        self._step_mode:    bool = False

        self.setMinimumSize(420, 220)
        self.setMouseTracking(True)

        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._animate)
        self._anim_timer.start(40)  # 25 fps

    # ── Public API ────────────────────────────────────────────────────────

    def update_locks(self, locks: list[dict]) -> None:
        self._locks = locks
        # Record snapshot for step scrubber
        if locks:
            import copy
            self._history.append(copy.deepcopy(locks))
            if len(self._history) > 200:
                self._history.pop(0)
            self._history_pos = len(self._history) - 1
        self.update()

    def pulse_lock(self, lock_id: str) -> None:
        self._pulses[lock_id] = 20.0
        self.update()

    def flash_lock(self, lock_id: str, flash_type: str) -> None:
        """flash_type: 'acquire' (green) | 'release' (teal) | 'error' (red)"""
        self._flashes[lock_id] = (flash_type, 1.0)
        self.update()

    def set_step_mode(self, active: bool) -> None:
        self._step_mode = active
        self.update()

    def step_back(self) -> None:
        if self._history and self._history_pos > 0:
            self._history_pos -= 1
            self._locks = self._history[self._history_pos]
            self.update()

    def step_forward(self) -> None:
        if self._history and self._history_pos < len(self._history) - 1:
            self._history_pos += 1
            self._locks = self._history[self._history_pos]
            self.update()

    def seek_to(self, pos: int) -> None:
        if self._history:
            self._history_pos = max(0, min(pos, len(self._history) - 1))
            self._locks = self._history[self._history_pos]
            self.update()

    def history_length(self) -> int:
        return len(self._history)

    # ── Internal animation ────────────────────────────────────────────────

    def _animate(self) -> None:
        changed = False
        # Pulse decay
        for lid in list(self._pulses):
            self._pulses[lid] -= 1.5
            if self._pulses[lid] <= 0:
                del self._pulses[lid]
            changed = True
        # Flash decay
        for lid in list(self._flashes):
            ft, alpha = self._flashes[lid]
            alpha = max(0.0, alpha - 0.06)
            if alpha <= 0:
                del self._flashes[lid]
            else:
                self._flashes[lid] = (ft, alpha)
            changed = True
        if changed:
            self.update()

    # ── Mouse events ──────────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = QPointF(event.position())
            for rect, lock in self._rendered_rects:
                if rect.contains(pos):
                    state = "free" if lock.get("holder") is None else "held"
                    self.lock_clicked.emit(lock.get("id", ""), state)
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        pos = QPointF(event.position())
        for rect, lock in self._rendered_rects:
            if rect.contains(pos):
                holder = lock.get("holder")
                waiters = lock.get("waiters", [])
                acq_us  = lock.get("acquire_time_us", "")
                tip = (
                    f"🔒 {lock.get('type','LOCK')}  |  "
                    f"{'FREE' if not holder else f'Holder: PID {holder}'}"
                    f"  |  Waiters: {len(waiters)}"
                    + (f"  |  Acq: {acq_us} µs" if acq_us else "")
                )
                QToolTip.showText(event.globalPosition().toPoint(), tip, self)
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                return
        QToolTip.hideText()
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    # ── Paint ─────────────────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        canvas_h = h - (STEP_BAR_H if self._step_mode else 0)

        # Background
        painter.fillRect(0, 0, w, h, QColor(DARK_BG))
        self._draw_grid(painter, w, canvas_h)

        # Clear hit rects
        self._rendered_rects.clear()

        if not self._locks:
            painter.setPen(QColor(TEXT_DIM))
            painter.setFont(QFont("Inter", 11))
            painter.drawText(
                QRectF(0, 0, w, canvas_h),
                Qt.AlignmentFlag.AlignCenter,
                "No active locks\n\nAcquire a lock via the control panel →\nor click on a container in the Overview tab")
            painter.end()
            return

        # Layout: auto-wrap into rows of up to 4 locks
        locks_per_row = min(4, len(self._locks))
        total_w = locks_per_row * (LOCK_W + 90)
        start_x = max(10, (w - total_w) / 2)
        start_y = 24

        for i, lock in enumerate(self._locks):
            row = i // locks_per_row
            col = i % locks_per_row
            x = start_x + col * (LOCK_W + 90)
            y = start_y + row * ROW_GAP

            rect = QRectF(x, y, LOCK_W, LOCK_H)
            self._rendered_rects.append((rect, lock))
            self._draw_lock_box(painter, x, y, lock)
            self._draw_queue(painter, x + LOCK_W + 12, y, lock.get("waiters", []))

        # Step controls bar
        if self._step_mode:
            self._draw_step_bar(painter, w, h)

        painter.end()

    # ── Draw helpers ──────────────────────────────────────────────────────

    def _draw_grid(self, p: QPainter, w: int, h: int) -> None:
        p.setPen(QPen(QColor(BORDER), 1))
        for x in range(0, w, 36):
            for y in range(0, h, 36):
                p.drawPoint(x, y)

    def _draw_lock_box(self, p: QPainter, x: float, y: float,
                       lock: dict) -> None:
        prim  = lock.get("type", "MUTEX")
        held  = lock.get("holder") is not None
        accent = PRIMITIVE_COLORS.get(prim, PURPLE)
        lid    = lock.get("id", "")
        rect   = QRectF(x, y, LOCK_W, LOCK_H)

        # ── Pulse ring ────────────────────────────────────────────────────
        pulse_r = self._pulses.get(lid, 0)
        if pulse_r > 0:
            alpha = int((pulse_r / 20.0) * 120)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(QColor(accent).red(),
                              QColor(accent).green(),
                              QColor(accent).blue(), alpha))
            pr = rect.adjusted(-pulse_r * 0.4, -pulse_r * 0.4,
                                pulse_r * 0.4,  pulse_r * 0.4)
            p.drawRoundedRect(pr, 10 + pulse_r * 0.2, 10 + pulse_r * 0.2)

        # ── Flash overlay ─────────────────────────────────────────────────
        flash = self._flashes.get(lid)

        # ── Box fill ──────────────────────────────────────────────────────
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        if held:
            grad.setColorAt(0, QColor(accent).darker(220))
            grad.setColorAt(1, QColor(accent).darker(280))
        else:
            grad.setColorAt(0, QColor(CARD))
            grad.setColorAt(1, QColor(SURFACE if SURFACE else CARD))

        border_w = 2.0 if held else 1.5
        p.setPen(QPen(QColor(accent if held else BORDER), border_w))
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(rect, 10, 10)

        # Flash overlay
        if flash:
            ft, alpha = flash
            fc = {"acquire": GREEN, "release": TEAL, "error": RED}.get(ft, PURPLE)
            c = QColor(fc)
            c.setAlpha(int(alpha * 180))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(c))
            p.drawRoundedRect(rect, 10, 10)

        # ── Primitive type badge ───────────────────────────────────────────
        p.setPen(QColor(accent))
        p.setFont(QFont("Inter", 7, QFont.Weight.Bold))
        p.drawText(QRectF(x + 6, y + 4, LOCK_W - 12, 14),
                   Qt.AlignmentFlag.AlignLeft, prim)

        # Lock icon + name
        p.setPen(QColor(TEXT_SECONDARY))
        p.setFont(QFont("Inter", 8))
        name = lock.get("name", lid[-6:] if lid else "?")
        p.drawText(QRectF(x + 6, y + 4, LOCK_W - 12, 14),
                   Qt.AlignmentFlag.AlignRight, name)

        # ── Status line ───────────────────────────────────────────────────
        holder = lock.get("holder")
        if holder:
            status_text  = f"🔒 PID {holder}"
            status_color = accent
        else:
            status_text  = "🔓 FREE  (click to acquire)"
            status_color = TEXT_DIM

        p.setPen(QColor(status_color))
        p.setFont(QFont("Inter", 9, QFont.Weight.Bold if held else QFont.Weight.Normal))
        p.drawText(QRectF(x + 4, y + 20, LOCK_W - 8, 28),
                   Qt.AlignmentFlag.AlignCenter, status_text)

    def _draw_queue(self, p: QPainter, x: float, y: float,
                    waiters: list[int]) -> None:
        if not waiters:
            return
        cy = y + LOCK_H / 2
        p.setPen(QPen(QColor(BORDER), 1.5, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(x - 6, cy), QPointF(x, cy))

        for idx, pid in enumerate(waiters[:5]):
            cx = x + idx * (QUEUE_DOT_R * 2 + 5) + QUEUE_DOT_R
            # Gradient dot
            grad = QRadialGradient(cx - 3, cy - 3, QUEUE_DOT_R)
            c1 = RED if idx == 0 else PURPLE
            grad.setColorAt(0, QColor("white"))
            grad.setColorAt(0.4, QColor(c1))
            grad.setColorAt(1.0, QColor(c1).darker(160))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(grad))
            p.drawEllipse(QPointF(cx, cy), QUEUE_DOT_R, QUEUE_DOT_R)
            p.setPen(QColor(TEXT_PRIMARY))
            p.setFont(QFont("Inter", 7, QFont.Weight.Bold))
            p.drawText(QRectF(cx - QUEUE_DOT_R, cy - QUEUE_DOT_R,
                              QUEUE_DOT_R * 2, QUEUE_DOT_R * 2),
                       Qt.AlignmentFlag.AlignCenter, str(pid % 1000))

        if len(waiters) > 5:
            ex = x + 5 * (QUEUE_DOT_R * 2 + 5)
            p.setPen(QColor(TEXT_DIM))
            p.setFont(QFont("Inter", 8))
            p.drawText(QRectF(ex, cy - 10, 40, 20),
                       Qt.AlignmentFlag.AlignCenter, f"+{len(waiters)-5}")

    def _draw_step_bar(self, p: QPainter, w: int, h: int) -> None:
        bar_y = h - STEP_BAR_H
        p.fillRect(0, bar_y, w, STEP_BAR_H, QColor(SURFACE))
        p.setPen(QPen(QColor(BORDER), 1))
        p.drawLine(0, bar_y, w, bar_y)

        # Buttons drawn here are purely cosmetic — actual buttons are in SyncVisualizer
        cx = w / 2
        p.setPen(QColor(TEAL))
        p.setFont(QFont("Inter", 9))
        total = max(1, len(self._history))
        pos   = self._history_pos + 1
        p.drawText(QRectF(cx - 80, bar_y + 8, 160, 20),
                   Qt.AlignmentFlag.AlignCenter,
                   f"Step {pos} / {total}")


# --------------------------------------------------------------------------
# WaitForGraphCanvas — enhanced matplotlib deadlock graph
# --------------------------------------------------------------------------
class WaitForGraphCanvas(FigureCanvas):
    """
    Renders a wait-for graph using networkx + matplotlib.
    Cycle nodes are highlighted in red; edges in the cycle are bold red.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        import matplotlib as mpl
        mpl.rcParams["figure.facecolor"] = DARK_BG
        mpl.rcParams["axes.facecolor"]   = DARK_BG

        self.fig = Figure(figsize=(5, 4), dpi=96, tight_layout=True)
        super().__init__(self.fig)
        if parent:
            self.setParent(parent)
        self.setStyleSheet(f"background-color: {DARK_BG};")
        self._graph: nx.DiGraph  = nx.DiGraph()
        self._cycle: list[int]   = []

    def update_graph(self, graph: nx.DiGraph,
                     cycle: Optional[list[int]] = None) -> None:
        self._graph = graph
        self._cycle = cycle or []
        self._redraw()

    def _redraw(self) -> None:
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        ax.set_facecolor(DARK_BG)
        ax.axis("off")

        G = self._graph
        if len(G) == 0:
            ax.text(0.5, 0.5, "No wait-for relationships\n(locks free)",
                    transform=ax.transAxes, ha="center", va="center",
                    color=TEXT_DIM, fontsize=12)
            self.draw()
            return

        try:
            pos = nx.spring_layout(G, seed=42, k=2.5)
        except Exception:
            pos = nx.circular_layout(G)

        cycle_set  = set(self._cycle)
        node_cols  = [RED if n in cycle_set else PURPLE for n in G.nodes]
        edge_cols  = []
        edge_widths= []
        for u, v in G.edges:
            if u in cycle_set and v in cycle_set:
                edge_cols.append(RED)
                edge_widths.append(3.0)
            else:
                edge_cols.append(BORDER)
                edge_widths.append(1.5)

        nx.draw_networkx_nodes(G, pos, ax=ax,
                               node_color=node_cols, node_size=700,
                               linewidths=2)
        nx.draw_networkx_labels(G, pos, ax=ax,
                                labels={n: f"PID\n{n}" for n in G.nodes},
                                font_color=TEXT_PRIMARY, font_size=8,
                                font_weight="bold")
        nx.draw_networkx_edges(G, pos, ax=ax,
                               edge_color=edge_cols,
                               width=edge_widths,
                               arrows=True,
                               arrowsize=22,
                               connectionstyle="arc3,rad=0.12")
        if self._cycle:
            ax.set_title("⚠  DEADLOCK DETECTED — circular wait",
                         color=RED, fontsize=12, fontweight="bold", pad=8)
        else:
            ax.set_title("Wait-for Graph",
                         color=TEXT_SECONDARY, fontsize=12, pad=8)
        self.draw()


# --------------------------------------------------------------------------
# SyncVisualizer — top-level widget
# --------------------------------------------------------------------------
class SyncVisualizer(QWidget):
    """
    Top-level sync widget with:
      - Normal mode: LockStateCanvas (click to acquire / release)
      - Deadlock mode: WaitForGraphCanvas (matplotlib wait-for graph)
      - Step controls (⏮ ⏸ ⏭ + QSlider) at the bottom
    """

    def __init__(self, interactive_backend=None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._ib     = interactive_backend
        self._bus    = EventBus.instance()
        self._step_mode = False

        self._build_ui()
        self._connect_bus()

        self._locks_data: dict[str, dict] = {}
        self._streamer:   Optional[TraceStreamer] = None

    # ── Build ─────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Status bar at top
        self._status_label = QLabel("Normal operation  —  streaming lock trace log")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setFixedHeight(28)
        self._status_label.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 11px; "
            f"background: {SURFACE}; border-bottom: 1px solid {BORDER};")
        root.addWidget(self._status_label)

        # Canvas stack
        self._stack = QStackedWidget()
        self._lock_canvas  = LockStateCanvas()
        self._graph_canvas = WaitForGraphCanvas()
        self._stack.addWidget(self._lock_canvas)   # 0: normal
        self._stack.addWidget(self._graph_canvas)  # 1: deadlock
        root.addWidget(self._stack, stretch=1)

        # Step controls bar (hidden by default)
        self._step_bar = self._make_step_bar()
        self._step_bar.setVisible(False)
        root.addWidget(self._step_bar)

        # Wire lock canvas clicks → backend actions
        self._lock_canvas.lock_clicked.connect(self._on_lock_clicked)

    def _make_step_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(48)
        bar.setStyleSheet(
            f"background: {SURFACE}; border-top: 1px solid {BORDER};")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(8)

        self._btn_step_back = QPushButton("⏮")
        self._btn_step_back.setFixedSize(34, 34)
        self._btn_step_back.clicked.connect(self._lock_canvas.step_back)

        self._btn_step_pause = QPushButton("⏸")
        self._btn_step_pause.setFixedSize(34, 34)
        self._btn_step_pause.setCheckable(True)
        self._btn_step_pause.clicked.connect(self._toggle_step_mode)

        self._btn_step_fwd = QPushButton("⏭")
        self._btn_step_fwd.setFixedSize(34, 34)
        self._btn_step_fwd.clicked.connect(self._lock_canvas.step_forward)

        for btn in (self._btn_step_back, self._btn_step_pause, self._btn_step_fwd):
            btn.setStyleSheet(
                f"QPushButton {{ background: {CARD}; color: {TEAL}; "
                f"border: 1px solid {BORDER}; border-radius: 6px; "
                f"font-size: 14px; }}"
                f"QPushButton:hover {{ background: {TEAL_DIM}; }}"
                f"QPushButton:checked {{ background: {TEAL}; color: {DARK_BG}; }}")

        self._step_slider = QSlider(Qt.Orientation.Horizontal)
        self._step_slider.setRange(0, 0)
        self._step_slider.setValue(0)
        self._step_slider.setStyleSheet(
            f"QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}"
            f"QSlider::handle:horizontal {{ background: {TEAL}; width: 14px; "
            f"height: 14px; margin: -5px 0; border-radius: 7px; }}"
            f"QSlider::sub-page:horizontal {{ background: {TEAL_DARK}; border-radius: 2px; }}")
        self._step_slider.sliderMoved.connect(self._lock_canvas.seek_to)

        self._step_pos_label = QLabel("Step 0 / 0")
        self._step_pos_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 10px; min-width: 70px;")

        layout.addWidget(self._btn_step_back)
        layout.addWidget(self._btn_step_pause)
        layout.addWidget(self._btn_step_fwd)
        layout.addWidget(self._step_slider, stretch=1)
        layout.addWidget(self._step_pos_label)

        return bar

    def _toggle_step_mode(self, checked: bool) -> None:
        self._step_mode = checked
        self._lock_canvas.set_step_mode(checked)
        self._btn_step_pause.setText("▶" if checked else "⏸")

    # ── EventBus ─────────────────────────────────────────────────────────

    def _connect_bus(self) -> None:
        bus = self._bus
        bus.lock_acquired.connect(self._on_bus_acquired)
        bus.lock_released.connect(self._on_bus_released)
        bus.deadlock_detected.connect(self._on_bus_deadlock)
        bus.deadlock_resolved.connect(self._on_bus_resolved)

    def _on_bus_acquired(self, data: dict) -> None:
        lid  = data.get("lock_handle", "")
        pid  = data.get("holder_pid")
        prim = data.get("primitive", "MUTEX")
        name = data.get("lock_name", "")
        acq_us = data.get("acquire_time_us", "")

        if lid not in self._locks_data:
            self._locks_data[lid] = {
                "id": lid, "type": prim, "name": name,
                "holder": None, "waiters": [],
                "acquire_time_us": acq_us,
            }
        lock = self._locks_data[lid]
        lock["holder"] = pid
        lock["acquire_time_us"] = acq_us
        if pid in lock["waiters"]:
            lock["waiters"].remove(pid)

        self._lock_canvas.pulse_lock(lid)
        self._lock_canvas.flash_lock(lid, "acquire")
        self.update_lock_states()
        self._update_step_slider()
        self._status_label.setText(
            f"🔒 Acquired {prim} '{name}'  by PID {pid}  ({acq_us} µs)")
        self._status_label.setStyleSheet(
            f"color: {GREEN}; font-size: 11px; "
            f"background: {SURFACE}; border-bottom: 1px solid {BORDER}; font-weight: bold;")

    def _on_bus_released(self, data: dict) -> None:
        # Find lock by holder PID
        pid  = data.get("pid")
        prim = data.get("primitive", "")
        name = data.get("lock_name", "")
        for lid, lock in self._locks_data.items():
            if lock.get("holder") == pid:
                lock["holder"] = None
                self._lock_canvas.pulse_lock(lid)
                self._lock_canvas.flash_lock(lid, "release")
                break
        self.update_lock_states()
        self._update_step_slider()
        self._status_label.setText(f"🔓 Released {prim} '{name}' by PID {pid}")
        self._status_label.setStyleSheet(
            f"color: {TEAL}; font-size: 11px; "
            f"background: {SURFACE}; border-bottom: 1px solid {BORDER};")

    def _on_bus_deadlock(self, data: dict) -> None:
        self.set_deadlock_mode(True)
        self._status_label.setText(
            f"⚠  DEADLOCK detected in {data.get('container','?')}  "
            f"— click [RESOLVE] in control panel")
        self._status_label.setStyleSheet(
            f"color: {RED}; font-weight: bold; font-size: 12px; "
            f"background: {SURFACE}; border-bottom: 1px solid {RED};")

    def _on_bus_resolved(self, data: dict) -> None:
        self.set_deadlock_mode(False)
        self._locks_data.clear()
        self.update_lock_states()
        self._status_label.setText(
            f"✓ Deadlock resolved  (PID {data.get('killed_pid','?')} terminated)")
        self._status_label.setStyleSheet(
            f"color: {GREEN}; font-size: 11px; "
            f"background: {SURFACE}; border-bottom: 1px solid {BORDER};")

    # ── TraceStreamer ─────────────────────────────────────────────────────

    def start_streaming(self, container_name: str) -> None:
        if self._streamer:
            self._streamer.stop()
        self._locks_data.clear()
        self.update_lock_states()
        self._streamer = TraceStreamer(container_name, self)
        self._streamer.event_received.connect(self._handle_trace_event)
        self._streamer.start()

    def stop_streaming(self) -> None:
        if self._streamer:
            self._streamer.stop()
            self._streamer = None

    def _handle_trace_event(self, pid: int, lock_id: str, event: str) -> None:
        if lock_id not in self._locks_data:
            self._locks_data[lock_id] = {
                "id": lock_id, "type": "LOCK",
                "name": lock_id[-6:],
                "holder": None, "waiters": [],
            }
        lock = self._locks_data[lock_id]
        if event == "ACQUIRE":
            lock["holder"] = pid
            if pid in lock["waiters"]:
                lock["waiters"].remove(pid)
            self._lock_canvas.pulse_lock(lock_id)
            self._lock_canvas.flash_lock(lock_id, "acquire")
        elif event == "RELEASE":
            if lock["holder"] == pid:
                lock["holder"] = None
            self._lock_canvas.flash_lock(lock_id, "release")
            self._lock_canvas.pulse_lock(lock_id)
        elif event == "WAIT":
            if pid not in lock["waiters"]:
                lock["waiters"].append(pid)
        self.update_lock_states()
        self._update_step_slider()

    # ── Interactive: click on lock box ───────────────────────────────────

    def _on_lock_clicked(self, lock_id: str, state: str) -> None:
        """Called when user clicks a lock box on the canvas."""
        lock = self._locks_data.get(lock_id, {})
        if state == "free":
            # Acquire it — let the control panel know via EventBus
            if self._ib:
                container = (self._streamer.container_name
                             if self._streamer else "interync-lab-1")
                prim = lock.get("type", "MUTEX")
                name = lock.get("name", lock_id)
                self._ib.acquire_lock(container, prim, name)
        else:
            # Release it
            holder_pid = lock.get("holder")
            if holder_pid and self._ib:
                container = (self._streamer.container_name
                             if self._streamer else "interync-lab-1")
                self._ib.release_lock(container, holder_pid,
                                      lock.get("type", "MUTEX"),
                                      lock.get("name", lock_id))

    # ── Public API ────────────────────────────────────────────────────────

    def update_lock_states(self) -> None:
        self._lock_canvas.update_locks(list(self._locks_data.values()))

    def set_deadlock_mode(self, active: bool,
                          graph: Optional[nx.DiGraph] = None,
                          cycle: Optional[list[int]] = None) -> None:
        if active:
            self._stack.setCurrentIndex(1)
            if graph is not None:
                self._graph_canvas.update_graph(graph, cycle)
            # Show step bar for reviewing the lock history
            self._step_bar.setVisible(True)
        else:
            self._stack.setCurrentIndex(0)
            self._step_bar.setVisible(False)

    def set_interactive_backend(self, ib) -> None:
        self._ib = ib

    # ── Step slider sync ─────────────────────────────────────────────────

    def _update_step_slider(self) -> None:
        total = self._lock_canvas.history_length()
        pos   = self._lock_canvas._history_pos
        self._step_slider.setRange(0, max(0, total - 1))
        self._step_slider.setValue(pos)
        self._step_pos_label.setText(f"Step {pos+1} / {total}")
