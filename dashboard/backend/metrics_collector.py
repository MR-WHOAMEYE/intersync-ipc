"""
metrics_collector.py
InterSync — reads /proc/stat and /proc/meminfo from inside LXC containers.

Collected once per polling cycle by the dashboard's QTimer.
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Optional

from .container_manager import ContainerManager, ContainerError

log = logging.getLogger(__name__)


@dataclass
class ContainerMetrics:
    """Snapshot of resource usage for one container."""
    container_name: str
    timestamp: float             = field(default_factory=time.time)
    cpu_percent: float           = 0.0   # 0–100 per core
    mem_used_kb: int             = 0
    mem_total_kb: int            = 0
    mem_percent: float           = 0.0
    error: Optional[str]         = None


@dataclass
class _CpuSnapshot:
    """Raw CPU tick values used for delta calculation."""
    user: int = 0
    nice: int = 0
    system: int = 0
    idle: int = 0
    iowait: int = 0
    irq: int = 0
    softirq: int = 0

    @property
    def total(self) -> int:
        return (self.user + self.nice + self.system + self.idle +
                self.iowait + self.irq + self.softirq)

    @property
    def busy(self) -> int:
        return self.total - self.idle - self.iowait


class MetricsCollector:
    """
    Polls resource metrics from LXC containers by reading /proc files via
    pylxd file pull / exec+cat (no agents installed in container).

    Thread-safety: this object is NOT thread-safe.  Call from a single Qt
    thread or protect with a mutex.
    """

    def __init__(self, container_manager: ContainerManager):
        self._mgr = container_manager
        self._prev_cpu: dict[str, _CpuSnapshot] = {}

    # ------------------------------------------------------------------ #
    # Public                                                                #
    # ------------------------------------------------------------------ #

    def collect(self, container_name: str) -> ContainerMetrics:
        """Collect one metrics snapshot for `container_name`."""
        metrics = ContainerMetrics(container_name=container_name)
        try:
            metrics.cpu_percent = self._cpu_percent(container_name)
            mem = self._read_meminfo(container_name)
            metrics.mem_total_kb = mem.get("MemTotal", 0)
            avail = mem.get("MemAvailable", mem.get("MemFree", 0))
            metrics.mem_used_kb  = metrics.mem_total_kb - avail
            if metrics.mem_total_kb > 0:
                metrics.mem_percent = (
                    metrics.mem_used_kb / metrics.mem_total_kb * 100.0
                )
        except ContainerError as exc:
            metrics.error = str(exc)
            log.warning("metrics_collector: %s", exc)
        except Exception as exc:                       # noqa: BLE001
            metrics.error = f"Unexpected: {exc}"
            log.exception("metrics_collector unexpected error")
        return metrics

    def collect_all(self, container_names: list[str]) -> list[ContainerMetrics]:
        return [self.collect(n) for n in container_names]

    # ------------------------------------------------------------------ #
    # CPU                                                                   #
    # ------------------------------------------------------------------ #

    def _read_proc_stat(self, container_name: str) -> _CpuSnapshot:
        """Read the first cpu line from /proc/stat inside the container."""
        result = self._mgr.exec(container_name, ["cat", "/proc/stat"])
        if result.exit_code != 0:
            raise ContainerError(
                f"cat /proc/stat failed in {container_name}: {result.stderr}"
            )
        for line in result.stdout.splitlines():
            if line.startswith("cpu "):
                parts = line.split()
                # cpu user nice system idle iowait irq softirq ...
                snap = _CpuSnapshot(
                    user    = int(parts[1]),
                    nice    = int(parts[2]),
                    system  = int(parts[3]),
                    idle    = int(parts[4]),
                    iowait  = int(parts[5]) if len(parts) > 5 else 0,
                    irq     = int(parts[6]) if len(parts) > 6 else 0,
                    softirq = int(parts[7]) if len(parts) > 7 else 0,
                )
                return snap
        raise ContainerError(f"No 'cpu' line in /proc/stat for {container_name}")

    def _cpu_percent(self, container_name: str) -> float:
        """Return CPU utilisation % since last call (0.0 on first call)."""
        cur = self._read_proc_stat(container_name)
        prev = self._prev_cpu.get(container_name)
        self._prev_cpu[container_name] = cur

        if prev is None:
            return 0.0

        delta_total = cur.total - prev.total
        delta_busy  = cur.busy  - prev.busy
        if delta_total <= 0:
            return 0.0
        return round(delta_busy / delta_total * 100.0, 2)

    # ------------------------------------------------------------------ #
    # Memory                                                                #
    # ------------------------------------------------------------------ #

    def _read_meminfo(self, container_name: str) -> dict[str, int]:
        """Return dict of {field_name: kB} from /proc/meminfo."""
        result = self._mgr.exec(container_name, ["cat", "/proc/meminfo"])
        if result.exit_code != 0:
            raise ContainerError(
                f"cat /proc/meminfo failed in {container_name}: {result.stderr}"
            )
        out: dict[str, int] = {}
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                key = parts[0].rstrip(":")
                try:
                    out[key] = int(parts[1])
                except ValueError:
                    pass
        return out
