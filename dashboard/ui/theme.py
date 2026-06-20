"""
theme.py
InterSync Dashboard — centralised QSS stylesheet.

Colour convention:
  Teal   (#00bcd4 / #00838f) — IPC channels and data-flow elements
  Purple (#9c27b0 / #6a1b9a) — Synchronisation / lock primitives
  Green  (#4caf50 / #2e7d32) — Performance / throughput / healthy state
  Red    (#f44336 / #b71c1c) — Deadlock / error / critical state
  Dark   (#1a1a2e / #16213e) — Background layers
  Surface (#0f3460)           — Panel / card backgrounds
"""

DARK_BG      = "#080B10"
SURFACE      = "#0F131A"
CARD         = "#151B26"
BORDER       = "#222E40"

TEAL         = "#00E5FF"
TEAL_DARK    = "#00B4D8"
TEAL_DIM     = "#0B2830"

PURPLE       = "#B026FF"
PURPLE_DARK  = "#8A2BE2"
PURPLE_DIM   = "#240046"

GREEN        = "#00E676"
GREEN_DARK   = "#00A86B"

RED          = "#FF1744"
RED_DARK     = "#A30022"

TEXT_PRIMARY   = "#F0F4F8"
TEXT_SECONDARY = "#8F9CAE"
TEXT_DIM       = "#5C6B73"

FONT_FAMILY = "Inter, Outfit, Segoe UI, Arial, sans-serif"
FONT_SIZE   = "13px"


def get_stylesheet() -> str:
    """Return the complete QSS stylesheet for the InterSync application."""
    return f"""
/* =====================================================================
   InterSync Dashboard — Cyberpunk Obsidian Dark Theme
   ===================================================================== */

QMainWindow, QDialog {{
    background-color: {DARK_BG};
    color: {TEXT_PRIMARY};
    font-family: {FONT_FAMILY};
    font-size: {FONT_SIZE};
}}

QWidget {{
    background-color: transparent;
    color: {TEXT_PRIMARY};
    font-family: {FONT_FAMILY};
    font-size: {FONT_SIZE};
}}

/* --- Central / main content area --- */
QFrame#centralFrame, QFrame#panel {{
    background-color: {SURFACE};
    border-radius: 10px;
    border: 1px solid {BORDER};
}}

/* --- Tab bar --- */
QTabWidget::pane {{
    border: 1px solid {BORDER};
    background-color: {SURFACE};
    border-radius: 8px;
}}

QTabBar::tab {{
    background-color: {CARD};
    color: {TEXT_SECONDARY};
    padding: 10px 20px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    margin-right: 4px;
    font-weight: bold;
}}

QTabBar::tab:selected {{
    background-color: {SURFACE};
    color: {TEAL};
    border-bottom: 2px solid {TEAL};
}}

QTabBar::tab:hover:!selected {{
    background-color: {TEAL_DIM};
    color: {TEAL};
}}

/* --- Buttons --- */
QPushButton {{
    background-color: {CARD};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 18px;
    font-weight: bold;
}}

QPushButton:hover {{
    background-color: {TEAL_DIM};
    border-color: {TEAL};
    color: {TEAL};
}}

QPushButton:pressed {{
    background-color: {TEAL_DARK};
    color: {DARK_BG};
}}

QPushButton#btnRun {{
    background-color: {TEAL_DARK};
    color: {DARK_BG};
    border-color: {TEAL};
}}

QPushButton#btnRun:hover {{
    background-color: {TEAL};
    color: {DARK_BG};
}}

QPushButton#btnStop {{
    background-color: {RED_DARK};
    color: white;
    border-color: {RED};
}}

QPushButton#btnStop:hover {{
    background-color: {RED};
}}

/* --- Group boxes (panels) --- */
QGroupBox {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
    margin-top: 16px;
    padding: 12px;
    font-weight: bold;
    color: {TEXT_SECONDARY};
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 8px;
    color: {TEAL};
    font-size: 11px;
    font-weight: bold;
    text-transform: uppercase;
    letter-spacing: 1.5px;
}}

/* --- IPC-specific group boxes --- */
QGroupBox#ipcGroup {{
    border-color: {TEAL_DARK};
}}

QGroupBox#ipcGroup::title {{
    color: {TEAL};
}}

/* --- Sync-specific group boxes --- */
QGroupBox#syncGroup {{
    border-color: {PURPLE_DARK};
}}

QGroupBox#syncGroup::title {{
    color: {PURPLE};
}}

/* --- Status / performance group boxes --- */
QGroupBox#perfGroup {{
    border-color: {GREEN_DARK};
}}

QGroupBox#perfGroup::title {{
    color: {GREEN};
}}

/* --- Deadlock group boxes --- */
QGroupBox#deadlockGroup {{
    border-color: {RED_DARK};
}}

QGroupBox#deadlockGroup::title {{
    color: {RED};
}}

/* --- Labels --- */
QLabel {{
    color: {TEXT_PRIMARY};
    background-color: transparent;
}}

QLabel#labelHeader {{
    font-size: 22px;
    font-weight: bold;
    color: {TEAL};
}}

QLabel#labelSubHeader {{
    font-size: 13px;
    color: {TEXT_SECONDARY};
}}

QLabel#labelMetric {{
    font-size: 24px;
    font-weight: bold;
    color: {GREEN};
}}

QLabel#labelStatus {{
    font-size: 12px;
    color: {TEXT_DIM};
}}

QLabel#labelDeadlock {{
    color: {RED};
    font-weight: bold;
}}

/* --- Progress bars --- */
QProgressBar {{
    background-color: {CARD};
    border: 1px solid {BORDER};
    border-radius: 6px;
    text-align: center;
    color: {TEXT_PRIMARY};
    height: 20px;
    font-weight: bold;
}}

QProgressBar::chunk {{
    background-color: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 {TEAL_DARK}, stop:1 {TEAL}
    );
    border-radius: 5px;
}}

/* --- Combo boxes --- */
QComboBox {{
    background-color: {CARD};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 12px;
}}

QComboBox:hover {{
    border-color: {TEAL};
}}

QComboBox QAbstractItemView {{
    background-color: {CARD};
    color: {TEXT_PRIMARY};
    selection-background-color: {TEAL_DIM};
    selection-color: {TEAL};
    border: 1px solid {BORDER};
}}

/* --- Scroll bars --- */
QScrollBar:vertical {{
    background-color: {SURFACE};
    width: 10px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background-color: {BORDER};
    border-radius: 5px;
    min-height: 25px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: {TEAL_DIM};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

/* --- Text / log areas --- */
QTextEdit, QPlainTextEdit {{
    background-color: {CARD};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 6px;
    font-family: "Cascadia Code", "Consolas", monospace;
    font-size: 12px;
    padding: 8px;
}}

/* --- Splitter handle --- */
QSplitter::handle {{
    background-color: {BORDER};
}}

/* --- Status bar --- */
QStatusBar {{
    background-color: {SURFACE};
    color: {TEXT_DIM};
    border-top: 1px solid {BORDER};
    font-size: 11px;
}}

/* --- Toolbar --- */
QToolBar {{
    background-color: {SURFACE};
    border-bottom: 1px solid {BORDER};
    spacing: 6px;
    padding: 6px;
}}

/* --- Menu bar --- */
QMenuBar {{
    background-color: {SURFACE};
    color: {TEXT_PRIMARY};
}}

QMenuBar::item:selected {{
    background-color: {TEAL_DIM};
    color: {TEAL};
}}

QMenu {{
    background-color: {CARD};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
}}

QMenu::item:selected {{
    background-color: {TEAL_DIM};
    color: {TEAL};
}}

/* --- Matplotlib canvas embed --- */
QWidget#matplotlibCanvas {{
    background-color: {DARK_BG};
    border-radius: 8px;
    border: 1px solid {BORDER};
}}

/* --- Container status badges --- */
QLabel#badgeRunning {{
    color: {GREEN};
    background-color: {SURFACE};
    border: 1px solid {GREEN};
    border-radius: 10px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: bold;
}}

QLabel#badgeStopped {{
    color: {TEXT_DIM};
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: bold;
}}

QLabel#badgeError {{
    color: {RED};
    background-color: {SURFACE};
    border: 1px solid {RED};
    border-radius: 10px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: bold;
}}
"""


# Convenience colour accessors for Python-side drawing (QPainter, etc.)
COLOURS = {
    "ipc":      TEAL,
    "sync":     PURPLE,
    "perf":     GREEN,
    "deadlock": RED,
    "bg":       DARK_BG,
    "surface":  SURFACE,
    "card":     CARD,
    "border":   BORDER,
    "text":     TEXT_PRIMARY,
    "dim":      TEXT_DIM,
}

