"""
main.py
InterSync Dashboard — PyQt6 application entry point.

Run via:
    make app
    # or directly:
    python3 dashboard/main.py
"""

from __future__ import annotations

import logging
import sys
import os

# ---------------------------------------------------------------------------
# Ensure the repo root is on sys.path so "dashboard.ui.*" imports work when
# running from the repo root or from inside the dashboard/ directory.
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QFontDatabase

from dashboard.ui.theme import get_stylesheet
from dashboard.ui.dashboard_window import DashboardWindow
from dashboard.backend.event_bus import EventBus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


def _load_backends():
    """
    Attempt to initialise the backend objects.
    Returns (container_manager, metrics_collector, deadlock_detector,
             interactive_backend).
    All may be None — the dashboard handles offline gracefully.
    """
    try:
        from dashboard.backend.container_manager import ContainerManager, ContainerError
        from dashboard.backend.metrics_collector import MetricsCollector
        from dashboard.backend.deadlock_detector import DeadlockDetector
        from dashboard.backend.interactive_backend import InteractiveBackend

        mgr      = ContainerManager.create_best()
        metrics  = MetricsCollector(mgr)
        detector = DeadlockDetector(mgr)
        ib       = InteractiveBackend(mgr)
        log.info("Backend connected to LXD")
        return mgr, metrics, detector, ib

    except Exception as exc:  # noqa: BLE001
        log.warning("Backend unavailable (%s) — running in preview mode.", exc)
        return None, None, None, None


def _deploy_binaries_async(ib, mgr, container_names: list[str]) -> None:
    """
    Deploy interactive binaries to all running containers in the background.
    Uses a single-shot QTimer so it runs after the window is shown.
    """
    from PyQt6.QtCore import QTimer

    def deploy():
        for name in container_names:
            try:
                status = mgr.container_status(name)
                if status == "Running":
                    log.info("Deploying binaries to %s …", name)
                    ib.deploy_binaries(name)
            except Exception as exc:
                log.warning("deploy_binaries(%s): %s", name, exc)

    QTimer.singleShot(1500, deploy)


def main() -> int:
    # High-DPI support
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("InterSync")
    app.setApplicationDisplayName("InterSync Dashboard")
    app.setOrganizationName("InterSync Capstone Team")

    # Apply global dark stylesheet
    app.setStyleSheet(get_stylesheet())

    # Prefer Inter font if available
    for family in ("Inter", "Outfit", "Segoe UI", "Roboto"):
        if family in QFontDatabase.families():
            app.setFont(QFont(family, 12))
            break

    # Initialise EventBus singleton
    EventBus.instance()

    # Initialise backends (fails gracefully)
    mgr, metrics, detector, ib = _load_backends()

    # Create and show main window
    window = DashboardWindow(
        container_manager=mgr,
        metrics_collector=metrics,
        deadlock_detector=detector,
        interactive_backend=ib,
    )
    window.show()

    # Deploy binaries to running containers after window is visible
    if ib and mgr:
        from dashboard.ui.dashboard_window import CONTAINER_NAMES
        _deploy_binaries_async(ib, mgr, CONTAINER_NAMES)

    log.info("InterSync Dashboard started")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
