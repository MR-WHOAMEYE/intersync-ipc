"""
deadlock_detector.py
InterSync — parses /tmp/interync_lock_trace.log from inside containers,
builds a wait-for graph, and detects cycles (potential deadlocks).

Log format (written by lib/sync/mutex_lock.c):
    <timestamp_ns>,<pid>,<lock_ptr_hex>,<ACQUIRE|RELEASE|WAIT>

Algorithm:
    1. Tail the log file from the container.
    2. Replay events to reconstruct which PID holds which lock.
    3. When PID A is WAIT-ing on lock L and PID B already holds L,
       add edge A → B to the wait-for graph.
    4. Run DFS cycle detection on the wait-for graph.
    5. Emit a deadlock event (with the cycle) to the dashboard.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Optional

import networkx as nx

from .container_manager import ContainerManager, ContainerError

log = logging.getLogger(__name__)

TRACE_LOG_PATH = "/tmp/interync_lock_trace.log"

# Regex: timestamp_ns , pid , 0xADDR , EVENT
_LINE_RE = re.compile(
    r"^(\d+),(\d+),(0x[0-9a-fA-F]+|[0-9a-fA-F]+),(ACQUIRE|RELEASE|WAIT)\s*$"
)


@dataclass
class LockEvent:
    timestamp_ns: int
    pid: int
    lock_id: str   # normalised hex string, e.g. "0x7f3a10"
    event: str     # ACQUIRE | RELEASE | WAIT


@dataclass
class DeadlockResult:
    detected: bool
    cycle: list[int] = field(default_factory=list)   # list of PIDs in the cycle
    container_name: str = ""
    raw_log_lines: int = 0


class DeadlockDetector:
    """
    Pulls the trace log from a container and analyses it for deadlocks.

    Usage:
        detector = DeadlockDetector(container_manager)
        result = detector.check("interync-lab-1")
        if result.detected:
            print("DEADLOCK CYCLE:", result.cycle)
    """

    def __init__(self, container_manager: ContainerManager,
                 on_deadlock: Optional[Callable[[DeadlockResult], None]] = None):
        self._mgr = container_manager
        self._on_deadlock = on_deadlock
        # Per-container state: { container_name → { lock_id → holder_pid } }
        self._lock_holders: dict[str, dict[str, int]] = defaultdict(dict)

    # ------------------------------------------------------------------ #
    # Public                                                                #
    # ------------------------------------------------------------------ #

    def check(self, container_name: str) -> DeadlockResult:
        """
        Pull the trace log and return a DeadlockResult.
        This is safe to call repeatedly — it re-reads the full log each time
        (suitable for polling intervals of 1–5 s).
        """
        try:
            raw = self._pull_log(container_name)
        except ContainerError as exc:
            log.debug("deadlock_detector: cannot pull log from %s: %s",
                      container_name, exc)
            return DeadlockResult(detected=False, container_name=container_name)

        events = self._parse(raw)
        result = self._analyse(events, container_name)
        result.raw_log_lines = len(events)

        if result.detected and self._on_deadlock:
            self._on_deadlock(result)

        return result

    def check_all(self, container_names: list[str]) -> list[DeadlockResult]:
        return [self.check(n) for n in container_names]

    def build_wait_for_graph(self, container_name: str) -> nx.DiGraph:
        """
        Return the current wait-for graph as a networkx DiGraph for
        rendering in the dashboard's sync_visualizer.
        """
        try:
            raw = self._pull_log(container_name)
        except ContainerError:
            return nx.DiGraph()
        events = self._parse(raw)
        graph, _ = self._build_graph(events)
        return graph

    # ------------------------------------------------------------------ #
    # Log pulling                                                           #
    # ------------------------------------------------------------------ #

    def _pull_log(self, container_name: str) -> str:
        """Pull the entire trace log from the container as a string."""
        raw = ""
        for path in ["/tmp/interync_lock_trace.0.log", "/tmp/interync_lock_trace.1.log"]:
            res = self._mgr.exec(container_name, ["cat", path])
            if res.exit_code == 0:
                raw += res.stdout + "\n"
        return raw

    # ------------------------------------------------------------------ #
    # Parsing                                                               #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse(raw: str) -> list[LockEvent]:
        events: list[LockEvent] = []
        seen = set()
        
        for line in raw.splitlines():
            m = _LINE_RE.match(line.strip())
            if not m:
                continue
            ts, pid_s, lock_id, event = m.groups()
            
            # Deduplicate
            uniq_key = (ts, pid_s, lock_id, event)
            if uniq_key in seen:
                continue
            seen.add(uniq_key)
            
            # Normalise lock_id to lowercase 0x... form
            if not lock_id.startswith("0x"):
                lock_id = "0x" + lock_id
            events.append(LockEvent(
                timestamp_ns=int(ts),
                pid=int(pid_s),
                lock_id=lock_id.lower(),
                event=event,
            ))
            
        # Sort by timestamp since we concatenated two separate files
        events.sort(key=lambda e: e.timestamp_ns)
        return events

    # ------------------------------------------------------------------ #
    # Graph construction & cycle detection                                  #
    # ------------------------------------------------------------------ #

    def _build_graph(self, events: list[LockEvent]
                     ) -> tuple[nx.DiGraph, dict[str, int]]:
        """
        Replay the event stream.

        Returns:
            graph       — wait-for DiGraph (edge A→B: A waits for B)
            holders     — { lock_id → current_holder_pid }
        """
        holders: dict[str, int] = {}   # lock_id → pid
        waiters: dict[str, list[int]] = defaultdict(list)  # lock_id → [pids waiting]
        graph = nx.DiGraph()

        for ev in events:
            pid  = ev.pid
            lid  = ev.lock_id

            if ev.event == "ACQUIRE":
                holders[lid] = pid
                # Remove this pid from waiters list if it was waiting
                if pid in waiters.get(lid, []):
                    waiters[lid].remove(pid)
                graph.add_node(pid)

            elif ev.event == "RELEASE":
                if holders.get(lid) == pid:
                    del holders[lid]

            elif ev.event == "WAIT":
                graph.add_node(pid)
                current_holder = holders.get(lid)
                if current_holder is not None and current_holder != pid:
                    graph.add_edge(pid, current_holder,
                                   lock=lid)
                waiters[lid].append(pid)

        return graph, holders

    def _analyse(self, events: list[LockEvent],
                 container_name: str) -> DeadlockResult:
        graph, _ = self._build_graph(events)

        try:
            cycle = nx.find_cycle(graph, orientation="original")
            # cycle is a list of (u, v, key, direction) tuples
            cycle_pids = list(dict.fromkeys(
                node for edge in cycle for node in edge[:2]
            ))
            log.warning(
                "DEADLOCK detected in %s — cycle: %s",
                container_name, cycle_pids
            )
            return DeadlockResult(
                detected=True,
                cycle=cycle_pids,
                container_name=container_name,
            )
        except nx.NetworkXNoCycle:
            return DeadlockResult(detected=False, container_name=container_name)

class VmDeadlockDetector(DeadlockDetector):
    """
    Subclass that fetches the rotating trace logs via the VM HTTP client 
    instead of running `cat` via `lxc exec`.
    """
    def _pull_log(self, container_name: str) -> str:
        # self._mgr is a VmContainerManager, which has .client
        if hasattr(self._mgr, "client"):
            return self._mgr.client.trace_logs(container_name, n=5000)
        return super()._pull_log(container_name)
