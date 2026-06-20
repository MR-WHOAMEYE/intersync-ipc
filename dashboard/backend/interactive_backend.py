"""
interactive_backend.py
InterSync Dashboard — translates UI actions into real LXD container commands.

Each public method:
  1. Builds a command list for the appropriate C helper binary.
  2. Executes it via ContainerManager.exec() (or exec_stream() for bursts).
  3. Parses JSON from stdout.
  4. Emits the matching EventBus signal.
  5. Returns the parsed dict to the caller.

All blocking operations run in QThread workers so the Qt event loop
is never blocked.

Binary locations inside each container:
  /opt/interync/bin/ipc_interactive
  /opt/interync/bin/sync_interactive
  /opt/interync/bin/philo_interactive
  /opt/interync/lib/libinterync-ipc.so
  /opt/interync/lib/libinterync-sync.so
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import QThread, pyqtSignal

from dashboard.backend.container_manager import ContainerManager, ContainerError
from dashboard.backend.event_bus import EventBus

log = logging.getLogger(__name__)

# Path inside each container where binaries + libs are deployed
CONTAINER_BIN = "/opt/interync/bin"
CONTAINER_LIB = "/opt/interync/lib"

# Local build output directory (relative to project root)
_HERE = Path(__file__).parents[2]  # repo root
BUILD_DIR = _HERE / "build"


# ---------------------------------------------------------------------------
# Helper: parse JSON from a string, return dict on failure with error key
# ---------------------------------------------------------------------------
def _parse_json(text: str, fallback_op: str = "unknown") -> dict:
    text = text.strip()
    # Take last non-empty line (burst emits progress lines then a final JSON)
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return {"op": fallback_op, "error": "empty output from binary"}
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        return {"op": fallback_op, "error": f"JSON parse error: {exc}", "raw": text[:200]}


# ---------------------------------------------------------------------------
# Generic Async Worker
# ---------------------------------------------------------------------------
class _AsyncWorker(QThread):
    """Generic worker that runs a zero-argument callable in a background thread."""
    finished = pyqtSignal(dict)
    error    = pyqtSignal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            result = self._fn()
            self.finished.emit(result if isinstance(result, dict) else {})
        except ContainerError as exc:
            self.error.emit(str(exc))
        except Exception as exc:
            self.error.emit(f"Unexpected error: {exc}")


# ---------------------------------------------------------------------------
# Worker thread: runs a burst command and streams progress
# ---------------------------------------------------------------------------
class _BurstWorker(QThread):
    progress = pyqtSignal(dict)
    finished = pyqtSignal(dict)
    error    = pyqtSignal(dict)

    def __init__(self, mgr: ContainerManager, container: str,
                 command: list[str], env: dict, parent=None):
        super().__init__(parent)
        self._mgr       = mgr
        self._container = container
        self._command   = command
        self._env       = env
        self._stop      = False

    def request_stop(self):
        self._stop = True

    def run(self):
        try:
            for raw_line in self._mgr.exec_stream(
                self._container, self._command,
                cwd=CONTAINER_BIN, env=self._env
            ):
                if self._stop:
                    break
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Progress lines have "progress" key; final result has "op":"burst"
                if "progress" in data:
                    self.progress.emit(data)
                elif data.get("op") == "burst":
                    self.finished.emit(data)
                    return

            # If we exited without a final burst result
            self.error.emit({"op": "burst", "error": "stream ended without result"})

        except ContainerError as exc:
            self.error.emit({"op": "burst", "error": str(exc)})


# ---------------------------------------------------------------------------
# Worker thread: runs a philosopher simulation and streams status updates
# ---------------------------------------------------------------------------
class _PhiloWorker(QThread):
    state_update = pyqtSignal(dict)
    started_sig  = pyqtSignal(dict)
    error        = pyqtSignal(dict)

    def __init__(self, mgr: ContainerManager, container: str,
                 command: list[str], env: dict, parent=None):
        super().__init__(parent)
        self._mgr       = mgr
        self._container = container
        self._command   = command
        self._env       = env
        self._stop      = False

    def request_stop(self):
        self._stop = True

    def run(self):
        try:
            for raw_line in self._mgr.exec_stream(
                self._container, self._command,
                cwd=CONTAINER_BIN, env=self._env
            ):
                if self._stop:
                    break
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                op = data.get("op", "")
                if op == "start":
                    self.started_sig.emit(data)
                elif op == "status":
                    self.state_update.emit(data)

        except ContainerError as exc:
            self.error.emit({"error": str(exc)})


# ---------------------------------------------------------------------------
# InteractiveBackend
# ---------------------------------------------------------------------------
class InteractiveBackend:
    """
    Translates UI actions into real IPC/sync operations executed inside
    LXD containers via the C helper binaries.

    All methods are safe to call from the main Qt thread.
    Long-running operations (burst, philosopher) spawn QThread workers.
    """

    def __init__(self, container_manager: ContainerManager):
        self._mgr   = container_manager
        self._bus   = EventBus.instance()
        self._burst_worker: Optional[_BurstWorker]  = None
        self._philo_worker: Optional[_PhiloWorker]  = None
        self._philo_session: Optional[str] = None
        # Keep references so workers aren't GC'd before they finish
        self._workers: list[_AsyncWorker] = []

    # ------------------------------------------------------------------ #
    # Deployment                                                            #
    # ------------------------------------------------------------------ #

    def deploy_binaries(self, container_name: str) -> bool:
        """
        Push compiled binaries and .so files into the container.
        Returns True on success, False if build directory is missing
        or container is unreachable.
        """
        binaries = [
            ("ipc_interactive",    f"{CONTAINER_BIN}/ipc_interactive"),
            ("sync_interactive",   f"{CONTAINER_BIN}/sync_interactive"),
            ("philo_interactive",  f"{CONTAINER_BIN}/philo_interactive"),
            ("libinterync-ipc.so", f"{CONTAINER_LIB}/libinterync-ipc.so"),
            ("libinterync-sync.so",f"{CONTAINER_LIB}/libinterync-sync.so"),
        ]

        if not BUILD_DIR.exists():
            self._bus.error_occurred.emit(
                "Deploy",
                f"Build directory '{BUILD_DIR}' not found. "
                "Run: make build-interactive"
            )
            return False

        try:
            # Ensure target directories exist
            self._mgr.exec(container_name,
                           ["mkdir", "-p", CONTAINER_BIN, CONTAINER_LIB])

            for local_name, remote_path in binaries:
                local_path = BUILD_DIR / local_name
                if not local_path.exists():
                    self._bus.error_occurred.emit(
                        "Deploy",
                        f"'{local_name}' not found in build/. "
                        "Run: make build-interactive"
                    )
                    return False
                self._mgr.push_file(container_name, local_path, remote_path)

            # Make executables runnable
            self._mgr.exec(container_name,
                           ["chmod", "+x",
                            f"{CONTAINER_BIN}/ipc_interactive",
                            f"{CONTAINER_BIN}/sync_interactive",
                            f"{CONTAINER_BIN}/philo_interactive"])

            log.info("Deployed interactive binaries to %s", container_name)
            self._bus.binaries_deployed.emit(container_name)
            return True

        except ContainerError as exc:
            log.warning("deploy_binaries(%s) failed: %s", container_name, exc)
            self._bus.error_occurred.emit("Deploy", str(exc))
            return False

    # ------------------------------------------------------------------ #
    # IPC operations                                                        #
    # ------------------------------------------------------------------ #

    def _ipc_env(self) -> dict:
        return {"LD_LIBRARY_PATH": CONTAINER_LIB}

    def _run_async(self, fn) -> _AsyncWorker:
        """Spawn a background worker for a blocking fn() -> dict call."""
        worker = _AsyncWorker(fn)
        self._workers.append(worker)

        def _cleanup():
            try:
                self._workers.remove(worker)
            except ValueError:
                pass

        worker.finished.connect(lambda _: _cleanup())
        worker.error.connect(lambda _: _cleanup())
        worker.start()
        return worker

    def send_ipc(self, producer_container: str,
                 mechanism: str, msg_size: int,
                 channel_name: str = "interync-ch") -> None:
        """
        Send one IPC message in a background thread.
        Result emitted via EventBus (ipc_sent or ipc_error).
        """
        cmd = [f"{CONTAINER_BIN}/ipc_interactive",
               "send", mechanism, str(msg_size), channel_name]

        def _do():
            try:
                result = self._mgr.exec(producer_container, cmd,
                                        cwd=CONTAINER_BIN, env=self._ipc_env())
                data = _parse_json(result.stdout, "send")
                if result.exit_code != 0 or data.get("error"):
                    err = data.get("error") or result.stderr.strip()
                    self._bus.ipc_error.emit(
                        {"op": "send", "mechanism": mechanism, "error": err})
                    self._bus.error_occurred.emit("IPC", f"Send failed: {err}")
                else:
                    self._bus.ipc_sent.emit(data)
                return data
            except ContainerError as exc:
                err = str(exc)
                self._bus.ipc_error.emit(
                    {"op": "send", "mechanism": mechanism, "error": err})
                self._bus.error_occurred.emit("IPC", err)
                return {"op": "send", "error": err}

        self._run_async(_do)

    # ----------------------------------------------------------------------- #
    # SPSC / MPMC Controls (Phase 4)                                        #
    # ----------------------------------------------------------------------- #
    def start_spsc_burst(self, capacity: int, slot_size: int, count: int) -> dict:
        """Starts a background Python thread that uses spsc_bindings to push/pop."""
        import dashboard.backend.spsc_worker as spsc_worker
        
        self.stop_spsc_burst()
        self._spsc_worker = spsc_worker.SpscWorker(capacity, slot_size, count, self.bus)
        self._spsc_worker.start()
        return {"op": "start_spsc_burst", "status": "started"}

    def stop_spsc_burst(self):
        if hasattr(self, '_spsc_worker') and self._spsc_worker is not None:
            self._spsc_worker.stop()
            self._spsc_worker.wait()
            self._spsc_worker = None
        if hasattr(self, '_mpmc_worker') and self._mpmc_worker is not None:
            self._mpmc_worker.stop()
            self._mpmc_worker.wait()
            self._mpmc_worker = None

    def start_mpmc_burst(self, capacity: int, slot_size: int, count: int, producers: int, consumers: int) -> dict:
        """Starts a background Python thread that uses spsc_bindings to enqueue/dequeue."""
        import dashboard.backend.spsc_worker as spsc_worker
        
        self.stop_spsc_burst()
        self._mpmc_worker = spsc_worker.MpmcWorker(capacity, slot_size, count, producers, consumers, self.bus)
        self._mpmc_worker.start()
        return {"op": "start_mpmc_burst", "status": "started"}

    def start_burst(self, producer_container: str,
                    mechanism: str, msg_size: int, count: int,
                    channel_name: str = "interync-ch") -> None:
        """
        Start a burst of `count` messages in a background QThread.
        Progress is emitted via EventBus.ipc_burst_progress.
        Completion is emitted via EventBus.ipc_burst_complete.
        """
        if self._burst_worker and self._burst_worker.isRunning():
            log.warning("Burst already running; ignoring new start_burst call")
            return

        cmd = [f"{CONTAINER_BIN}/ipc_interactive",
               "burst", mechanism, str(msg_size), str(count), channel_name]

        worker = _BurstWorker(self._mgr, producer_container, cmd, self._ipc_env())

        def on_progress(data: dict):
            data["mechanism"] = mechanism
            self._bus.ipc_burst_progress.emit(data)

        def on_finished(data: dict):
            self._bus.ipc_burst_complete.emit(data)

        def on_error(data: dict):
            self._bus.ipc_error.emit(data)
            self._bus.error_occurred.emit("IPC", data.get("error", "Burst failed"))

        worker.progress.connect(on_progress)
        worker.finished.connect(on_finished)
        worker.error.connect(on_error)

        self._burst_worker = worker
        worker.start()

    def stop_burst(self) -> None:
        """Request the running burst worker to stop after its current message."""
        if self._burst_worker:
            self._burst_worker.request_stop()

    # ------------------------------------------------------------------ #
    # Sync / lock operations                                               #
    # ------------------------------------------------------------------ #

    def _sync_env(self) -> dict:
        return {"LD_LIBRARY_PATH": CONTAINER_LIB}

    def acquire_lock(self, container: str,
                     primitive: str, lock_name: str) -> None:
        """
        Acquire a lock in a background thread.
        Result emitted via EventBus (lock_acquired or lock_error).
        """
        cmd = [f"{CONTAINER_BIN}/sync_interactive",
               "acquire", primitive, lock_name]

        def _do():
            try:
                result = self._mgr.exec(container, cmd,
                                        cwd=CONTAINER_BIN, env=self._sync_env())
                data = _parse_json(result.stdout, "acquire")
                if result.exit_code != 0 or data.get("error"):
                    err = data.get("error") or result.stderr.strip()
                    self._bus.lock_error.emit(
                        {"op": "acquire", "primitive": primitive,
                         "lock_name": lock_name, "error": err})
                    self._bus.error_occurred.emit("Sync", f"Acquire failed: {err}")
                else:
                    data.setdefault("lock_name", lock_name)
                    self._bus.lock_acquired.emit(data)
                return data
            except ContainerError as exc:
                err = str(exc)
                self._bus.lock_error.emit(
                    {"op": "acquire", "primitive": primitive,
                     "lock_name": lock_name, "error": err})
                self._bus.error_occurred.emit("Sync", err)
                return {"op": "acquire", "error": err}

        self._run_async(_do)

    def acquire_lock_read(self, container: str, lock_name: str) -> None:
        """Acquire a shared read lock (RWLOCK only) in a background thread."""
        cmd = [f"{CONTAINER_BIN}/sync_interactive",
               "acquire_read", "RWLOCK", lock_name]

        def _do():
            try:
                result = self._mgr.exec(container, cmd,
                                        cwd=CONTAINER_BIN, env=self._sync_env())
                data = _parse_json(result.stdout, "acquire_read")
                if data.get("error"):
                    self._bus.lock_error.emit(data)
                    self._bus.error_occurred.emit(
                        "Sync", f"AcquireRead failed: {data['error']}")
                else:
                    self._bus.lock_acquired.emit(data)
                return data
            except ContainerError as exc:
                self._bus.error_occurred.emit("Sync", str(exc))
                return {"op": "acquire_read", "error": str(exc)}

        self._run_async(_do)

    def release_lock(self, container: str, holder_pid: int,
                     primitive: str = "", lock_name: str = "") -> None:
        """Send SIGTERM to the holder PID to release the lock in a background thread."""
        cmd = [f"{CONTAINER_BIN}/sync_interactive",
               "release", str(holder_pid)]

        def _do():
            try:
                result = self._mgr.exec(container, cmd,
                                        cwd=CONTAINER_BIN, env=self._sync_env())
                data = _parse_json(result.stdout, "release")
                if data.get("error"):
                    self._bus.lock_error.emit(data)
                    self._bus.error_occurred.emit(
                        "Sync", f"Release failed: {data['error']}")
                else:
                    data.update({"primitive": primitive, "lock_name": lock_name,
                                 "pid": holder_pid})
                    self._bus.lock_released.emit(data)
                return data
            except ContainerError as exc:
                self._bus.error_occurred.emit("Sync", str(exc))
                return {"op": "release", "error": str(exc)}

        self._run_async(_do)

    def inject_deadlock(self, container: str, primitive: str = "MUTEX",
                        num_threads: int = 2) -> None:
        """Inject a deadlock in a background thread."""
        cmd = [f"{CONTAINER_BIN}/sync_interactive",
               "deadlock", primitive, str(num_threads)]

        def _do():
            try:
                result = self._mgr.exec(container, cmd,
                                        cwd=CONTAINER_BIN, env=self._sync_env(),
                                        timeout=5)
                data = _parse_json(result.stdout, "deadlock")
                data["container"] = container
                self._bus.deadlock_detected.emit(data)
                return data
            except ContainerError as exc:
                self._bus.error_occurred.emit("Sync", str(exc))
                return {"op": "deadlock", "error": str(exc)}

        self._run_async(_do)

    def resolve_deadlock(self, container: str, pid_to_kill: int) -> None:
        """Kill the holding PID in a background thread."""
        cmd = [f"{CONTAINER_BIN}/sync_interactive",
               "kill", str(pid_to_kill)]

        def _do():
            try:
                result = self._mgr.exec(container, cmd,
                                        cwd=CONTAINER_BIN, env=self._sync_env())
                data = _parse_json(result.stdout, "kill")
                data["container"] = container
                data["killed_pid"] = pid_to_kill
                self._bus.deadlock_resolved.emit(data)
                return data
            except ContainerError as exc:
                self._bus.error_occurred.emit("Sync", str(exc))
                return {"op": "kill", "error": str(exc)}

        self._run_async(_do)

    def run_stress(self, container: str, primitive: str,
                   num_threads: int, iterations: int) -> None:
        """Run a stress test in a background thread."""
        cmd = [f"{CONTAINER_BIN}/sync_interactive",
               "stress", primitive, str(num_threads), str(iterations)]

        def _do():
            try:
                result = self._mgr.exec(container, cmd,
                                        cwd=CONTAINER_BIN, env=self._sync_env(),
                                        timeout=60)
                data = _parse_json(result.stdout, "stress")
                self._bus.stress_complete.emit(data)
                return data
            except ContainerError as exc:
                self._bus.error_occurred.emit("Sync", str(exc))
                return {"op": "stress", "error": str(exc)}

        self._run_async(_do)

    # ------------------------------------------------------------------ #
    # Dining philosophers                                                   #
    # ------------------------------------------------------------------ #

    def _philo_env(self) -> dict:
        return {"LD_LIBRARY_PATH": CONTAINER_LIB}

    def start_philosophers(self, container: str, num_philosophers: int,
                           think_ms: int, eat_ms: int,
                           avoidance: bool) -> None:
        """
        Start the philosopher simulation in a background QThread.
        State updates arrive via EventBus.philo_state_update every ~500ms.
        """
        if self._philo_worker and self._philo_worker.isRunning():
            log.warning("Philosopher simulation already running")
            return

        cmd = [f"{CONTAINER_BIN}/philo_interactive",
               "start",
               str(num_philosophers),
               str(think_ms),
               str(eat_ms),
               "1" if avoidance else "0"]

        worker = _PhiloWorker(self._mgr, container, cmd, self._philo_env())

        def on_started(data: dict):
            self._philo_session = data.get("session_id")
            self._bus.philo_started.emit(data)

        def on_update(data: dict):
            self._bus.philo_state_update.emit(data)

        def on_error(data: dict):
            self._bus.error_occurred.emit("Philosophers", data.get("error", "Unknown"))

        worker.started_sig.connect(on_started)
        worker.state_update.connect(on_update)
        worker.error.connect(on_error)

        self._philo_worker = worker
        worker.start()

    def stop_philosophers(self, container: str) -> None:
        """Stop the running philosopher simulation."""
        if self._philo_worker:
            self._philo_worker.request_stop()

        if self._philo_session:
            cmd = [f"{CONTAINER_BIN}/philo_interactive",
                   "stop", self._philo_session]
            try:
                self._mgr.exec(container, cmd,
                               cwd=CONTAINER_BIN, env=self._philo_env())
            except ContainerError as exc:
                log.warning("stop_philosophers: %s", exc)
            finally:
                self._bus.philo_stopped.emit({"session_id": self._philo_session})
                self._philo_session = None

    # ------------------------------------------------------------------ #
    # Container control                                                     #
    # ------------------------------------------------------------------ #

    def start_container(self, name: str) -> None:
        """Start a stopped container and emit status change."""
        try:
            self._mgr.start_container(name)
            self._bus.container_status_changed.emit(name, "Running")
        except ContainerError as exc:
            self._bus.error_occurred.emit("Container", str(exc))

    def stop_container(self, name: str) -> None:
        """Stop a running container and emit status change."""
        try:
            self._mgr.stop_container(name)
            self._bus.container_status_changed.emit(name, "Stopped")
        except ContainerError as exc:
            self._bus.error_occurred.emit("Container", str(exc))

    def ping_container(self, name: str, mechanism: str = "PIPE") -> None:
        """
        Send a tiny 64-byte IPC message (ping) in a background thread.
        Result emitted via EventBus (ipc_sent or ipc_error).
        """
        self.send_ipc(name, mechanism, 64, "interync-ping")
