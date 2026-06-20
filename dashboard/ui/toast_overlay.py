"""
toast_overlay.py
InterSync Dashboard — non-blocking toast notification overlay.

ToastOverlay sits transparently on top of any visualization canvas.
Toasts auto-fade after 3 seconds and stack up to 3 at a time.

Usage:
    overlay = ToastOverlay(parent=canvas_widget)
    overlay.show_toast("Sent 256B via PIPE (3.4 µs)", ToastLevel.SUCCESS)

    # Connect to EventBus:
    bus.error_occurred.connect(
        lambda src, msg: overlay.show_toast(f"[{src}] {msg}", ToastLevel.ERROR)
    )
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve,
    QRect, pyqtProperty, QPoint
)
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QFont
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel

from .theme import (
    DARK_BG, SURFACE, CARD, BORDER,
    TEAL, GREEN, RED, TEXT_PRIMARY, TEXT_SECONDARY,
    FONT_FAMILY,
)


# ---------------------------------------------------------------------------
# Toast severity levels
# ---------------------------------------------------------------------------
class ToastLevel(Enum):
    INFO    = "info"
    SUCCESS = "success"
    WARN    = "warn"
    ERROR   = "error"


_LEVEL_COLORS = {
    ToastLevel.INFO:    (TEAL,    "#0B2830"),
    ToastLevel.SUCCESS: (GREEN,   "#002712"),
    ToastLevel.WARN:    ("#FFB300","#271E00"),
    ToastLevel.ERROR:   (RED,     "#2A0009"),
}


# ---------------------------------------------------------------------------
# Single toast widget
# ---------------------------------------------------------------------------
class _Toast(QWidget):
    """One dismissible toast card."""

    def __init__(self, message: str, level: ToastLevel, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        accent, bg = _LEVEL_COLORS.get(level, (TEAL, CARD))

        self.setFixedWidth(300)

        icon = {"info": "ℹ", "success": "✓", "warn": "⚠", "error": "✗"}[level.value]

        self.setStyleSheet(f"""
            QWidget {{
                background-color: {bg};
                border: 1px solid {accent};
                border-left: 4px solid {accent};
                border-radius: 8px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)

        lbl = QLabel(f"{icon}  {message}")
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"""
            color: {accent};
            background: transparent;
            border: none;
            font-family: {FONT_FAMILY};
            font-size: 12px;
            font-weight: bold;
        """)
        layout.addWidget(lbl)
        self.adjustSize()

        self._opacity = 1.0

    # pyqtProperty for animation
    def _get_opacity(self) -> float:
        return self._opacity

    def _set_opacity(self, val: float):
        self._opacity = val
        self.setWindowOpacity(val)
        if val <= 0.0:
            self.hide()

    opacity = pyqtProperty(float, _get_opacity, _set_opacity)


# ---------------------------------------------------------------------------
# ToastOverlay
# ---------------------------------------------------------------------------
class ToastOverlay(QWidget):
    """
    Transparent overlay that stacks toast notifications in the top-right
    corner of its parent widget.

    Install by creating with the canvas widget as parent:
        overlay = ToastOverlay(parent=my_canvas)
    Then call show_toast() from any thread (via signal/slot).
    """

    MAX_TOASTS = 3
    DISPLAY_MS = 3500   # how long each toast stays fully visible
    FADE_MS    = 400    # fade-out duration

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAutoFillBackground(False)
        self._toasts: list[_Toast] = []
        self._resize_to_parent()

    # ------------------------------------------------------------------ #
    # Public API                                                            #
    # ------------------------------------------------------------------ #

    def show_toast(self, message: str,
                   level: ToastLevel = ToastLevel.INFO) -> None:
        """Add a toast. If 3 are already visible, dismiss the oldest."""
        if len(self._toasts) >= self.MAX_TOASTS:
            self._dismiss(self._toasts[0])

        toast = _Toast(message, level, self)
        self._toasts.append(toast)
        self._reposition()
        toast.show()

        # Auto-dismiss timer
        QTimer.singleShot(self.DISPLAY_MS, lambda: self._dismiss(toast))

    # ------------------------------------------------------------------ #
    # Internal                                                              #
    # ------------------------------------------------------------------ #

    def _dismiss(self, toast: _Toast) -> None:
        if toast not in self._toasts:
            return

        anim = QPropertyAnimation(toast, b"opacity", toast)
        anim.setDuration(self.FADE_MS)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def on_done():
            toast.hide()
            toast.deleteLater()
            if toast in self._toasts:
                self._toasts.remove(toast)
            self._reposition()

        anim.finished.connect(on_done)
        anim.start()

    def _reposition(self) -> None:
        """Stack toasts from top-right with 8px gap between them."""
        margin = 12
        gap    = 8
        y = margin
        for toast in self._toasts:
            x = self.width() - toast.width() - margin
            toast.move(x, y)
            y += toast.height() + gap

    def _resize_to_parent(self) -> None:
        if self.parent():
            p = self.parent()
            self.setGeometry(p.rect())  # type: ignore[union-attr]

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition()

    def paintEvent(self, _event):
        pass  # fully transparent background
