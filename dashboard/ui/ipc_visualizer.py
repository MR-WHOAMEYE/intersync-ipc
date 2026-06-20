"""
ipc_visualizer.py
InterSync Dashboard — animated IPC flow visualiser (Phase 2 interactive).

New in Phase 2:
  • Per-mechanism accent colors (PIPE=cyan, QUEUE=purple, SOCKET=green, SHM=orange)
  • Click on Producer box → triggers a real IPC send via InteractiveBackend
  • Inline latency + throughput text drawn on the channel tube
  • Red error flash on channel tube when ipc_error is emitted
  • Green success glow pulse when ipc_sent is emitted
  • Burst progress bar drawn beneath the tube
  • Real-time stats update from EventBus signals

Layout (IPC_PIPE example):

   ┌──────────┐      ══════════>      ┌──────────┐
   │ Producer │  ─── pipe fd[1] ───>  │ Consumer │
   └──────────┘   [▓▓▓▓░░ 3.4µs]     └──────────┘
       ↑ click here to send

Packets animate left→right inside the tube.
"""

from __future__ import annotations

import math
import time
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QLinearGradient,
    QPainterPath, QRadialGradient,
)
from PyQt6.QtWidgets import QWidget

from .theme import (
    COLOURS, TEAL, TEAL_DARK, TEAL_DIM, BORDER, DARK_BG, CARD,
    TEXT_PRIMARY, TEXT_DIM, TEXT_SECONDARY,
    PURPLE, GREEN, RED,
)
from dashboard.backend.event_bus import EventBus

IPC_NAMES = {
    "PIPE":   "Anonymous Pipe  (fd pair)",
    "QUEUE":  "POSIX Message Queue",
    "SOCKET": "UNIX Domain Socket",
    "SHM":    "Shared Memory  +  Semaphore",
}

# Per-mechanism accent colors
MECHANISM_COLORS: dict[str, str] = {
    "PIPE":   TEAL,
    "QUEUE":  PURPLE,
    "SOCKET": GREEN,
    "SHM":    "#FF6D00",
}

# Visual constants
BOX_W, BOX_H  = 136, 58
TUBE_H        = 26
ARROW_SPEED   = 0.28   # fraction of tube width per second
PACKET_RADIUS = 9
FLASH_DURATION = 0.45  # seconds for success/error flash


class IpcVisualizer(QWidget):
    """
    Animated, interactive IPC flow canvas.

    • Shows packet animation between Producer and Consumer boxes.
    • Clicking the Producer box sends a real IPC message.
    • Subscribes to EventBus for live latency/throughput overlays.
    """

    def __init__(self, interactive_backend=None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._ib      = interactive_backend
        self._bus     = EventBus.instance()
        self._mechanism     = "PIPE"
        self._active        = False

        # Packet animation state
        self._packets: list[float] = []       # 0.0 → 1.0 progress

        # SHM grid
        self._shm_cols  = 8
        self._shm_rows  = 4
        self._shm_cells = [0.0] * (self._shm_cols * self._shm_rows)

        # Timing
        self._last_ts   = time.monotonic()

        # Live stats from EventBus
        self._last_latency_us:   float = 0.0
        self._last_throughput:   float = 0.0
        self._msg_count:         int   = 0
        self._bytes_sent:        int   = 0
        self._burst_progress:    float = 0.0   # 0.0 → 1.0

        # Flash state
        self._flash_timer:  float = 0.0        # > 0 while flashing
        self._flash_type:   str   = "none"     # "success" | "error"

        # Hover state
        self._producer_hovered = False

        # Rect cache for hit-testing (computed in paintEvent)
        self._producer_rect: Optional[QRectF] = None
        self._consumer_rect: Optional[QRectF] = None
        self._tube_rect:     Optional[QRectF] = None

        # Timer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)   # ~30 fps

        self.setMinimumSize(440, 240)
        self.setMouseTracking(True)

        self._connect_bus()

    # ------------------------------------------------------------------ #
    # Public API                                                            #
    # ------------------------------------------------------------------ #

    def set_mechanism(self, name: str) -> None:
        """Switch mechanism; resets packet animation."""
        self._mechanism  = name.upper()
        self._packets.clear()
        self._burst_progress = 0.0
        self.update()

    def set_active(self, active: bool) -> None:
        """Start / stop packet animation."""
        self._active = active
        if active:
            self._last_ts = time.monotonic()
            if not self._packets and self._mechanism != "SHM":
                self._packets = [0.0]

    def set_interactive_backend(self, ib) -> None:
        self._ib = ib

    def record_transfer(self, byte_count: int) -> None:
        """Manual call to record a transfer (used if not using EventBus)."""
        self._bytes_sent += byte_count
        self._msg_count  += 1

    # ------------------------------------------------------------------ #
    # EventBus connections                                                  #
    # ------------------------------------------------------------------ #

    def _connect_bus(self) -> None:
        self._bus.ipc_sent.connect(self._on_ipc_sent)
        self._bus.ipc_error.connect(self._on_ipc_error)
        self._bus.ipc_burst_progress.connect(self._on_burst_progress)
        self._bus.ipc_burst_complete.connect(self._on_burst_complete)

    def _on_ipc_sent(self, data: dict) -> None:
        mech = data.get("mechanism", "")
        if mech and mech != self._mechanism:
            return  # different tab — ignore
        self._last_latency_us = float(data.get("send_time_us", 0))
        size = int(data.get("bytes", 256))
        self._bytes_sent += size
        self._msg_count  += 1
        # Compute rough throughput (MB/s) from last latency
        if self._last_latency_us > 0:
            self._last_throughput = (size / (1024 * 1024)) / (self._last_latency_us / 1e6)
        if self._mechanism == "SHM":
            # Light up the next SHM cell in sequence — driven by real data only
            cell_idx = self._msg_count % len(self._shm_cells)
            self._shm_cells[cell_idx] = 1.0
        else:
            # Inject a packet into the animation
            self._packets.insert(0, 0.0)
        # Trigger success flash
        self._flash_type  = "success"
        self._flash_timer = FLASH_DURATION
        self.update()

    def _on_ipc_error(self, data: dict) -> None:
        self._flash_type  = "error"
        self._flash_timer = FLASH_DURATION
        self.update()

    def _on_burst_progress(self, data: dict) -> None:
        sent  = int(data.get("progress", 0))
        total = int(data.get("total", 1))
        self._burst_progress = sent / max(total, 1)
        # Inject packet every few messages for visual effect
        if sent % 3 == 0 and self._mechanism != "SHM":
            self._packets.insert(0, 0.0)
        self.update()

    def _on_burst_complete(self, data: dict) -> None:
        self._burst_progress = 1.0
        self._last_latency_us  = float(data.get("avg_latency_us", 0))
        self._last_throughput  = float(data.get("throughput_mbs", 0))
        QTimer.singleShot(1500, lambda: setattr(self, "_burst_progress", 0.0))

    # ------------------------------------------------------------------ #
    # Mouse events                                                          #
    # ------------------------------------------------------------------ #

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._producer_rect and self._producer_rect.contains(
                    QPointF(event.position())):
                self._send_one()
            else:
                super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._producer_rect:
            hovered = self._producer_rect.contains(QPointF(event.position()))
            if hovered != self._producer_hovered:
                self._producer_hovered = hovered
                self.setCursor(
                    Qt.CursorShape.PointingHandCursor if hovered
                    else Qt.CursorShape.ArrowCursor)
                self.update()
        super().mouseMoveEvent(event)

    def _send_one(self) -> None:
        """Trigger one IPC send via the backend."""
        if self._ib:
            from dashboard.ui.dashboard_window import CONTAINER_NAMES
            producer = CONTAINER_NAMES[0]
            self._ib.send_ipc(producer, self._mechanism, 256)
        else:
            # No backend — still animate
            self._flash_type  = "success"
            self._flash_timer = FLASH_DURATION
            if self._mechanism != "SHM":
                self._packets.insert(0, 0.0)
            self.update()

    # ------------------------------------------------------------------ #
    # Animation tick                                                        #
    # ------------------------------------------------------------------ #

    def _tick(self) -> None:
        now = time.monotonic()
        dt  = now - self._last_ts
        self._last_ts = now

        # Advance flash timer
        if self._flash_timer > 0:
            self._flash_timer = max(0.0, self._flash_timer - dt)

        if self._active or self._packets:
            if self._mechanism == "SHM":
                # Decay all cells — only real ipc_sent events light them up
                for i in range(len(self._shm_cells)):
                    if self._shm_cells[i] > 0:
                        self._shm_cells[i] = max(0.0, self._shm_cells[i] - dt * 2.5)
            else:
                # Move packets
                self._packets = [p + ARROW_SPEED * dt for p in self._packets]
                # Spawn new ones when active
                if self._active:
                    if not self._packets or self._packets[-1] > 0.25:
                        self._packets.append(0.0)
                # Remove finished
                self._packets = [p for p in self._packets if p <= 1.02]
        else:
            for i in range(len(self._shm_cells)):
                if self._shm_cells[i] > 0:
                    self._shm_cells[i] = max(0.0, self._shm_cells[i] - dt * 2.5)

        self.update()

    # ------------------------------------------------------------------ #
    # Geometry helpers                                                      #
    # ------------------------------------------------------------------ #

    def _compute_geometry(self) -> tuple:
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2

        left_box  = QRectF(cx - BOX_W - 90, cy - BOX_H / 2, BOX_W, BOX_H)
        right_box = QRectF(cx + 90,          cy - BOX_H / 2, BOX_W, BOX_H)

        ax1 = left_box.right()  + 8
        ax2 = right_box.left() - 8
        ay  = cy

        tube_rect = QRectF(ax1, ay - TUBE_H / 2, ax2 - ax1, TUBE_H)

        return w, h, cx, cy, left_box, right_box, ax1, ax2, ay, tube_rect

    # ------------------------------------------------------------------ #
    # Painting                                                              #
    # ------------------------------------------------------------------ #

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        geo = self._compute_geometry()
        w, h, cx, cy, left_box, right_box, ax1, ax2, ay, tube_rect = geo

        # Cache rects for hit-testing
        self._producer_rect = left_box
        self._consumer_rect = right_box
        self._tube_rect     = tube_rect

        accent = MECHANISM_COLORS.get(self._mechanism, TEAL)

        # --- Background ---
        painter.fillRect(0, 0, w, h, QColor(DARK_BG))

        # --- Subtle grid dots ---
        self._draw_grid(painter, w, h)

        # --- Channel label ---
        mech_label = IPC_NAMES.get(self._mechanism, self._mechanism)
        self._draw_channel_label(painter, cx, ay - 36, mech_label, accent)

        # --- Tube or SHM grid ---
        if self._mechanism == "SHM":
            self._draw_shm_grid(painter, cx, cy, accent)
        else:
            self._draw_tube(painter, tube_rect, accent)
            # Flash overlay
            if self._flash_timer > 0:
                self._draw_flash(painter, tube_rect, accent)
            # Packets
            for pos in self._packets:
                px = ax1 + (ax2 - ax1) * min(pos, 1.0)
                self._draw_packet(painter, px, ay, accent)

        # --- Burst progress bar ---
        if self._burst_progress > 0:
            self._draw_burst_bar(painter, w, ay, ax1, ax2, accent)

        # --- Inline stats on tube ---
        if self._last_latency_us > 0 and self._mechanism != "SHM":
            self._draw_tube_stats(painter, tube_rect, accent)

        # --- Arrow head ---
        if self._mechanism != "SHM":
            self._draw_arrowhead(painter, ax2, ay, accent)

        # --- Producer / Consumer boxes ---
        self._draw_box(painter, left_box,  "Producer",
                       accent=accent, is_sender=True,
                       hovered=self._producer_hovered)
        self._draw_box(painter, right_box, "Consumer",
                       accent=accent, is_sender=False)

        # --- Hint text on Producer when hovered ---
        if self._producer_hovered:
            self._draw_click_hint(painter, left_box, accent)

        # --- Bottom stats bar ---
        self._draw_stats(painter, w, h, accent)

        painter.end()

    # ------------------------------------------------------------------ #
    # Draw helpers                                                          #
    # ------------------------------------------------------------------ #

    def _draw_grid(self, p: QPainter, w: int, h: int) -> None:
        p.setPen(QPen(QColor(BORDER), 1))
        step = 32
        for x in range(0, w, step):
            for y in range(0, h, step):
                p.drawPoint(x, y)

    def _draw_box(self, p: QPainter, rect: QRectF, label: str,
                  accent: str = TEAL, is_sender: bool = True,
                  hovered: bool = False) -> None:
        # Glow behind box when hovered
        if hovered:
            glow = QRadialGradient(rect.center(), BOX_W * 0.7)
            glow.setColorAt(0, QColor(accent).darker(80))
            glow.setColorAt(1, QColor(0, 0, 0, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(glow))
            p.drawEllipse(rect.adjusted(-20, -20, 20, 20))

        # Box fill: gradient
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        if is_sender:
            grad.setColorAt(0, QColor(accent).darker(250))
            grad.setColorAt(1, QColor(accent).darker(200))
        else:
            grad.setColorAt(0, QColor(CARD))
            grad.setColorAt(1, QColor(SURFACE if hasattr(self, '_surface') else CARD))

        border_color = QColor(accent) if hovered else QColor(accent).darker(140)
        pen_width    = 2.5 if hovered else 1.5
        p.setPen(QPen(border_color, pen_width))
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(rect, 10, 10)

        # Container icon + label
        p.setPen(QColor(TEXT_PRIMARY))
        p.setFont(QFont("Inter", 11, QFont.Weight.Bold))
        p.drawText(rect.adjusted(0, -8, 0, 0), Qt.AlignmentFlag.AlignCenter, label)

        # Sub-label
        p.setPen(QColor(accent))
        p.setFont(QFont("Inter", 8))
        sub = "click to send →" if is_sender and hovered else (
              "interync-lab-1" if is_sender else "interync-lab-2")
        p.drawText(rect.adjusted(0, 10, 0, 0), Qt.AlignmentFlag.AlignCenter, sub)

    def _draw_click_hint(self, p: QPainter, rect: QRectF, accent: str) -> None:
        p.setPen(QColor(accent))
        p.setFont(QFont("Inter", 8, QFont.Weight.Bold))
        hint_rect = QRectF(rect.left(), rect.bottom() + 4,
                           rect.width(), 20)
        p.drawText(hint_rect, Qt.AlignmentFlag.AlignCenter, "↑ click to send")

    def _draw_tube(self, p: QPainter, rect: QRectF, accent: str) -> None:
        # Outer shadow
        shadow_rect = rect.adjusted(-2, 2, 2, 2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(0, 0, 0, 60)))
        p.drawRoundedRect(shadow_rect, TUBE_H / 2, TUBE_H / 2)

        # Inner gradient fill
        grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        grad.setColorAt(0, QColor(CARD).lighter(130))
        grad.setColorAt(1, QColor(CARD))
        p.setPen(QPen(QColor(accent).darker(160), 1.5))
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(rect, TUBE_H / 2, TUBE_H / 2)

        # Shimmer line along top of tube
        shimmer = QLinearGradient(rect.topLeft(), rect.topRight())
        shimmer.setColorAt(0.0, QColor(255, 255, 255, 0))
        shimmer.setColorAt(0.4, QColor(255, 255, 255, 25))
        shimmer.setColorAt(0.6, QColor(255, 255, 255, 25))
        shimmer.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(shimmer))
        p.drawRoundedRect(
            QRectF(rect.left(), rect.top(), rect.width(), TUBE_H * 0.35),
            TUBE_H / 2, TUBE_H / 2)

    def _draw_flash(self, p: QPainter, rect: QRectF, accent: str) -> None:
        """Draw a color flash overlay on the tube (success=green, error=red)."""
        ratio = self._flash_timer / FLASH_DURATION
        alpha = int(ratio * 160)
        if self._flash_type == "success":
            color = QColor(GREEN)
        else:
            color = QColor(RED)
        color.setAlpha(alpha)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(color))
        p.drawRoundedRect(rect, TUBE_H / 2, TUBE_H / 2)

    def _draw_packet(self, p: QPainter, cx: float, cy: float,
                     accent: str) -> None:
        """Draw an animated data packet dot."""
        # Glow halo
        halo = QRadialGradient(cx, cy, PACKET_RADIUS * 2.5)
        halo.setColorAt(0, QColor(accent + "60"))
        halo.setColorAt(1, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(halo))
        p.drawEllipse(QPointF(cx, cy), PACKET_RADIUS * 2.5, PACKET_RADIUS * 2.5)

        # Core
        grad = QRadialGradient(cx - 3, cy - 3, PACKET_RADIUS)
        grad.setColorAt(0, QColor("white"))
        grad.setColorAt(0.3, QColor(accent))
        grad.setColorAt(1, QColor(accent).darker(180))
        p.setBrush(QBrush(grad))
        p.drawEllipse(QPointF(cx, cy), PACKET_RADIUS, PACKET_RADIUS)

    def _draw_arrowhead(self, p: QPainter, x: float, y: float,
                        accent: str) -> None:
        aw, ah = 12, 8
        path = QPainterPath()
        path.moveTo(x + aw, y)
        path.lineTo(x, y - ah)
        path.lineTo(x, y + ah)
        path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(accent)))
        p.drawPath(path)

    def _draw_tube_stats(self, p: QPainter, tube_rect: QRectF,
                         accent: str) -> None:
        """Draw latency and throughput inline on the tube."""
        lat  = self._last_latency_us
        tp   = self._last_throughput
        if lat >= 1000:
            lat_str = f"{lat/1000:.2f} ms"
        else:
            lat_str = f"{lat:.1f} µs"
        tp_str = f"{tp:.2f} MB/s" if tp > 0 else ""
        text = f"⏱ {lat_str}  {tp_str}".strip()

        p.setPen(QColor(TEXT_PRIMARY))
        p.setFont(QFont("Inter", 8, QFont.Weight.Bold))
        p.drawText(tube_rect, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_burst_bar(self, p: QPainter, w: int, cy: float,
                        ax1: float, ax2: float, accent: str) -> None:
        bar_h  = 6
        bar_y  = cy + TUBE_H / 2 + 6
        bar_w  = ax2 - ax1
        filled = bar_w * self._burst_progress

        # Background
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(BORDER)))
        p.drawRoundedRect(QRectF(ax1, bar_y, bar_w, bar_h), 3, 3)

        # Fill
        if filled > 0:
            grad = QLinearGradient(ax1, 0, ax1 + filled, 0)
            grad.setColorAt(0, QColor(accent).darker(130))
            grad.setColorAt(1, QColor(accent))
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(QRectF(ax1, bar_y, filled, bar_h), 3, 3)

        # Percent label
        pct = f"{self._burst_progress * 100:.0f}%"
        p.setPen(QColor(TEXT_SECONDARY))
        p.setFont(QFont("Inter", 8))
        p.drawText(QRectF(ax1, bar_y + 8, bar_w, 14),
                   Qt.AlignmentFlag.AlignCenter, f"Burst: {pct}")

    def _draw_shm_grid(self, p: QPainter, cx: float, cy: float,
                       accent: str) -> None:
        cell_size = 20
        gap       = 4
        total_w   = self._shm_cols * cell_size + (self._shm_cols - 1) * gap
        total_h   = self._shm_rows * cell_size + (self._shm_rows - 1) * gap
        sx = cx - total_w / 2
        sy = cy - total_h / 2

        for row in range(self._shm_rows):
            for col in range(self._shm_cols):
                idx   = row * self._shm_cols + col
                alpha = self._shm_cells[idx]
                x = sx + col * (cell_size + gap)
                y = sy + row * (cell_size + gap)
                rect = QRectF(x, y, cell_size, cell_size)

                p.setPen(QPen(QColor(BORDER), 1))
                if alpha > 0:
                    c = QColor(accent)
                    c.setAlpha(int(alpha * 220))
                    p.setBrush(QBrush(c))
                else:
                    p.setBrush(QBrush(QColor(CARD)))
                p.drawRoundedRect(rect, 4, 4)

    def _draw_channel_label(self, p: QPainter, cx: float, y: float,
                            text: str, accent: str) -> None:
        p.setPen(QColor(accent))
        p.setFont(QFont("Inter", 9, QFont.Weight.Bold))
        p.drawText(QRectF(cx - 220, y - 14, 440, 28),
                   Qt.AlignmentFlag.AlignCenter, text)

    def _draw_stats(self, p: QPainter, w: int, h: int, accent: str) -> None:
        p.setFont(QFont("Inter", 10))
        status = "● ACTIVE" if self._active else "○ IDLE"
        colour = QColor(accent) if self._active else QColor(TEXT_DIM)
        p.setPen(colour)
        lat_str = (f"  |  Last: {self._last_latency_us:.1f} µs"
                   if self._last_latency_us > 0 else "")
        tp_str  = (f"  |  {self._last_throughput:.2f} MB/s"
                   if self._last_throughput > 0 else "")
        p.drawText(
            QRectF(12, h - 28, w - 24, 24),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"{status}   Msgs: {self._msg_count}"
            f"   Bytes: {self._bytes_sent:,}{lat_str}{tp_str}"
        )
