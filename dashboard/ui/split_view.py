"""
split_view.py
InterSync Dashboard — split-pane layout manager.

Replaces the old full-width tab layout with a two-column design:
  • Left:  ControlPanel (fixed 280px) with a vertical tab bar and
           dynamic control widgets that swap when the tab changes.
  • Right: Visualization canvas (stretches) + ToastOverlay.

Both columns are always visible — switching the tab bar changes the
controls on the left AND the canvas widget on the right simultaneously.

Usage:
    sv = SplitView(interactive_backend, parent=window)
    sv.set_mode("sandbox")   # or "scenario"
    window.setCentralWidget(sv)
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QStackedWidget,
    QLabel, QPushButton, QFrame, QSizePolicy, QScrollArea, QSplitter,
)

from .theme import (
    DARK_BG, SURFACE, CARD, BORDER,
    TEAL, TEAL_DIM, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_DIM,
    FONT_FAMILY,
)
from .toast_overlay import ToastOverlay, ToastLevel


class _RightPanel(QWidget):
    """Right canvas column — holds both the canvas stack and the toast overlay."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._toast: ToastOverlay | None = None

    def set_toast(self, toast: "ToastOverlay"):
        self._toast = toast

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._toast:
            self._toast.setGeometry(self.rect())


# ---------------------------------------------------------------------------
# Vertical tab button
# ---------------------------------------------------------------------------
class _TabButton(QPushButton):
    """A slim vertical navigation button used in the control panel tab bar."""

    def __init__(self, label: str, parent=None):
        super().__init__(label, parent)
        self.setCheckable(True)
        self.setFixedHeight(38)
        self.setFont(QFont("Inter", 10, QFont.Weight.Bold))
        self._update_style(False)

    def _update_style(self, active: bool):
        if active:
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: {TEAL_DIM};
                    color: {TEAL};
                    border: none;
                    border-left: 3px solid {TEAL};
                    border-radius: 0;
                    text-align: left;
                    padding-left: 14px;
                    font-weight: bold;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    color: {TEXT_SECONDARY};
                    border: none;
                    border-radius: 0;
                    text-align: left;
                    padding-left: 14px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: {CARD};
                    color: {TEXT_PRIMARY};
                }}
            """)

    def setActive(self, active: bool):
        self.setChecked(active)
        self._update_style(active)


# ---------------------------------------------------------------------------
# ControlPanel — left fixed-width column
# ---------------------------------------------------------------------------
class ControlPanel(QWidget):
    """
    Left column (resizable, min 250px).

    Contains:
      - A brand mini-header (logo + mode indicator)
      - Vertical navigation tab buttons
      - A QStackedWidget whose pages are the per-tab control widgets

    Call register_tab(name, icon, control_widget) to add tabs.
    Connect tab_changed to update the canvas on the right.
    """

    tab_changed = pyqtSignal(int)  # emits the new tab index

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(250)
        self.setObjectName("controlPanel")
        self.setStyleSheet(f"""
            #controlPanel {{
                background-color: {SURFACE};
                border-right: 1px solid {BORDER};
            }}
        """)

        self._tab_buttons: list[_TabButton] = []
        self._current_index = -1

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- Mini header ---
        header = QWidget()
        header.setFixedHeight(48)
        header.setStyleSheet(f"background-color: {DARK_BG}; border-bottom: 1px solid {BORDER};")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(14, 0, 14, 0)
        self._mode_label = QLabel("Sandbox Mode")
        self._mode_label.setStyleSheet(
            f"color: {TEAL}; font-size: 12px; font-weight: bold; background: transparent;")
        h_layout.addWidget(self._mode_label)
        root.addWidget(header)

        # --- Vertical tab bar ---
        self._nav = QWidget()
        self._nav.setStyleSheet(f"background: {DARK_BG};")
        self._nav_layout = QVBoxLayout(self._nav)
        self._nav_layout.setContentsMargins(0, 4, 0, 4)
        self._nav_layout.setSpacing(2)
        root.addWidget(self._nav)

        # --- Divider ---
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {BORDER};")
        root.addWidget(sep)

        # --- Control pages stack wrapped in scroll area ---
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollArea > QWidget > QWidget {
                background: transparent;
            }
        """)

        self._stack = QStackedWidget()
        self._stack.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._scroll.setWidget(self._stack)
        root.addWidget(self._scroll, stretch=1)

    def register_tab(self, icon_label: str, control_widget: QWidget) -> int:
        """Add a tab. Returns the assigned tab index."""
        idx = len(self._tab_buttons)
        btn = _TabButton(icon_label)
        btn.clicked.connect(lambda _, i=idx: self._select(i))
        self._tab_buttons.append(btn)
        self._nav_layout.addWidget(btn)
        self._stack.addWidget(control_widget)
        if idx == 0:
            self._select(0)
        return idx

    def _select(self, index: int):
        for i, btn in enumerate(self._tab_buttons):
            btn.setActive(i == index)
        self._stack.setCurrentIndex(index)
        self._current_index = index
        self.tab_changed.emit(index)

    def select_tab(self, index: int):
        """Programmatically switch the active tab."""
        self._select(index)

    def set_mode_label(self, text: str):
        self._mode_label.setText(text)


# ---------------------------------------------------------------------------
# SplitView — the top-level layout widget
# ---------------------------------------------------------------------------
class SplitView(QWidget):
    """
    Two-column draggable split layout:
      Left:  ControlPanel (resizable, min 250px)
      Right: Visualization canvas (stretches) with ToastOverlay

    After construction, call register_tab() for each tab.
    The canvas widget for each tab is provided by the caller.
    """

    mode_changed = pyqtSignal(str)  # "sandbox" | "scenario"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode = "sandbox"
        self._canvas_widgets: list[QWidget] = []

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Main splitter to make the sidebar draggable
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(4)
        self.splitter.setStyleSheet(f"""
            QSplitter::handle {{
                background-color: {BORDER};
            }}
            QSplitter::handle:hover {{
                background-color: {TEAL};
            }}
        """)
        root.addWidget(self.splitter)

        # Left column stack (swaps between ControlPanel and ScenarioSidebar)
        self._left_stack = QStackedWidget()
        self._left_stack.setMinimumWidth(250)
        self.control_panel = ControlPanel()
        self._left_stack.addWidget(self.control_panel)
        self.splitter.addWidget(self._left_stack)

        # Right column
        right = _RightPanel()
        right.setStyleSheet(f"background-color: {DARK_BG};")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self._canvas_stack = QStackedWidget()
        right_layout.addWidget(self._canvas_stack, stretch=1)

        self.splitter.addWidget(right)
        
        # Set initial sizes (sidebar: 280px, canvas takes the rest)
        self.splitter.setSizes([280, 1120])

        # Toast overlay — sits over the entire right column
        self._toast = ToastOverlay(right)
        right.set_toast(self._toast)

        # Wire control panel tab changes → canvas stack
        self.control_panel.tab_changed.connect(self._canvas_stack.setCurrentIndex)

    # ------------------------------------------------------------------ #
    # Public API                                                            #
    # ------------------------------------------------------------------ #

    def register_tab(self, icon_label: str,
                     control_widget: QWidget,
                     canvas_widget: QWidget) -> int:
        """
        Register a tab.

        :param icon_label:      Label for the left-panel navigation button.
        :param control_widget:  Widget placed in the left panel for this tab.
        :param canvas_widget:   Widget placed in the right canvas for this tab.
        :returns:               Tab index.
        """
        idx = self.control_panel.register_tab(icon_label, control_widget)
        self._canvas_stack.addWidget(canvas_widget)
        self._canvas_widgets.append(canvas_widget)
        return idx

    def set_mode(self, mode: str) -> None:
        """Switch between 'sandbox' and 'scenario' modes."""
        self._mode = mode
        if mode == "scenario":
            self.control_panel.set_mode_label("Scenario Mode")
            if self._left_stack.count() > 1:
                self._left_stack.setCurrentIndex(1)
        else:
            self.control_panel.set_mode_label("Sandbox Mode")
            self._left_stack.setCurrentIndex(0)
        self.mode_changed.emit(mode)

    def set_scenario_sidebar(self, sidebar: QWidget) -> None:
        """Register the scenario sidebar to be shown in scenario mode."""
        self._left_stack.addWidget(sidebar)

    def current_canvas(self) -> Optional[QWidget]:
        return self._canvas_stack.currentWidget()

    # ------------------------------------------------------------------ #
    # Toast delegation                                                      #
    # ------------------------------------------------------------------ #

    def show_toast(self, message: str, level: ToastLevel = ToastLevel.INFO) -> None:
        self._toast.show_toast(message, level)
