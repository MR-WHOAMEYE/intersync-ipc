"""
event_bus.py
InterSync Dashboard — in-process pub-sub event bus.

A QObject singleton that decouples UI components from the backend.
All backend operations emit signals here; all UI components subscribe here.

Usage:
    from dashboard.backend.event_bus import EventBus
    bus = EventBus.instance()
    bus.ipc_sent.connect(my_slot)
    bus.ipc_sent.emit({"mechanism": "PIPE", "bytes": 256, "latency_us": 12.3})
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal


class EventBus(QObject):
    """
    Singleton in-process event bus.

    All signals carry a dict payload for forward compatibility.
    """

    # ------------------------------------------------------------------ #
    # IPC events                                                            #
    # ------------------------------------------------------------------ #
    ipc_sent = pyqtSignal(dict)
    """Emitted after a successful IPC send.
    Keys: mechanism, bytes, send_time_us, error (None on success)."""

    ipc_received = pyqtSignal(dict)
    """Emitted after a successful IPC receive.
    Keys: mechanism, bytes, recv_time_us, error."""

    ipc_burst_progress = pyqtSignal(dict)
    """Emitted per-message during a burst.
    Keys: sent, total, mechanism."""

    ipc_burst_complete = pyqtSignal(dict)
    """Emitted when a burst finishes.
    Keys: mechanism, count, bytes_total, total_time_us,
          avg_latency_us, throughput_mbs, error."""

    ipc_error = pyqtSignal(dict)
    """Emitted when any IPC operation fails.
    Keys: op, mechanism, error."""

    # ------------------------------------------------------------------ #
    # Sync / lock events                                                    #
    # ------------------------------------------------------------------ #
    lock_acquired = pyqtSignal(dict)
    """Emitted when a lock is acquired.
    Keys: primitive, lock_name, lock_handle, holder_pid, acquire_time_us."""

    lock_released = pyqtSignal(dict)
    """Emitted when a lock is released.
    Keys: primitive, lock_name, pid."""

    lock_error = pyqtSignal(dict)
    """Emitted when a lock operation fails.
    Keys: op, primitive, lock_name, error."""

    deadlock_detected = pyqtSignal(dict)
    """Emitted when deadlock injection completes or is detected.
    Keys: primitive, cycle_pids, container."""

    deadlock_resolved = pyqtSignal(dict)
    """Emitted after a deadlock is resolved.
    Keys: killed_pid, container."""

    stress_complete = pyqtSignal(dict)
    """Emitted when a stress test finishes.
    Keys: primitive, threads, iterations, total_time_us, contention_events."""

    # ------------------------------------------------------------------ #
    # Philosopher events                                                    #
    # ------------------------------------------------------------------ #
    philo_state_update = pyqtSignal(dict)
    """Emitted every ~500ms with full philosopher state.
    Keys: philosophers (list of dicts), deadlocked, total_meals."""

    philo_state_changed = pyqtSignal(dict) # { "philo_id": 0, "state": "EATING" }
    fork_state_changed  = pyqtSignal(dict) # { "fork_id": 0, "owner": 1 }

    # SPSC/MPMC Events
    spsc_pushed = pyqtSignal(dict)  # { "ring_name": str, "slot_idx": int, "size": int, "overflow": bool, "latency_ns": float }
    spsc_popped = pyqtSignal(dict)  # { "ring_name": str, "slot_idx": int, "size": int, "overflow": bool, "latency_ns": float }
    mpmc_enqueued = pyqtSignal(dict)
    mpmc_dequeued = pyqtSignal(dict)
    spsc_trace_event = pyqtSignal(dict)

    # Global System
    global_tick = pyqtSignal()

    philo_started = pyqtSignal(dict)
    """Emitted when simulation starts.
    Keys: session_id, num_philosophers, avoidance."""

    philo_stopped = pyqtSignal(dict)
    """Emitted when simulation stops.
    Keys: session_id."""

    # ------------------------------------------------------------------ #
    # Container events                                                      #
    # ------------------------------------------------------------------ #
    container_status_changed = pyqtSignal(str, str)
    """Emitted when a container's status changes.
    Args: container_name, new_status (Running/Stopped/NotFound)."""

    binaries_deployed = pyqtSignal(str)
    """Emitted when binaries are successfully deployed to a container.
    Args: container_name."""

    # ------------------------------------------------------------------ #
    # General error                                                         #
    # ------------------------------------------------------------------ #
    error_occurred = pyqtSignal(str, str)
    """General error signal for the toast overlay.
    Args: source (e.g. 'IPC', 'Sync'), message."""

    # ------------------------------------------------------------------ #
    # Scenario events                                                       #
    # ------------------------------------------------------------------ #
    scenario_event = pyqtSignal(str, dict)
    """Scenario lifecycle events.
    Args: event_type ('start'|'step_complete'|'win'|'lose'|'quit'), data."""

    # ------------------------------------------------------------------ #
    # Singleton management                                                  #
    # ------------------------------------------------------------------ #
    _instance: "EventBus | None" = None

    def __init__(self, parent=None):
        super().__init__(parent)

    @classmethod
    def instance(cls) -> "EventBus":
        """Return the global EventBus singleton, creating it if needed."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Destroy and recreate the singleton (useful for testing)."""
        if cls._instance is not None:
            cls._instance.deleteLater()
            cls._instance = None
