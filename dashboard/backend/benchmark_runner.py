"""
benchmark_runner.py
InterSync — orchestrates scenario execution inside containers and
writes results to ./results/{latency.csv,throughput.csv,report.json}.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from .container_manager import ContainerManager, ContainerError

log = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).parents[2] / "results"
BUILD_DIR   = Path(__file__).parents[2] / "build"

# Paths inside the container
REMOTE_LIB_DIR  = "/opt/interync/lib"
REMOTE_BIN_DIR  = "/opt/interync/bin"
REMOTE_LOG      = "/tmp/interync_lock_trace.log"


class BenchmarkRunner:
    """
    High-level benchmark orchestrator used by both the CLI
    (benchmarks/benchmark_suite.py) and the dashboard.
    """

    def __init__(self, container_manager: ContainerManager):
        self._mgr = container_manager
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    # Library deployment                                                    #
    # ------------------------------------------------------------------ #

    def deploy_libs(self, container_name: str) -> None:
        """
        Push the compiled .so files into the container under REMOTE_LIB_DIR.
        Raises ContainerError if the .so files haven't been built yet.
        """
        ipc_so  = BUILD_DIR / "libinterync-ipc.so"
        sync_so = BUILD_DIR / "libinterync-sync.so"

        for so_path in (ipc_so, sync_so):
            if not so_path.exists():
                raise ContainerError(
                    f"Missing shared library: {so_path}. "
                    f"Run 'make build-libs' first."
                )

        # Ensure remote dir exists
        self._mgr.exec(container_name, ["mkdir", "-p", REMOTE_LIB_DIR])
        self._mgr.exec(container_name, ["mkdir", "-p", REMOTE_BIN_DIR])

        self._mgr.push_file(
            container_name, ipc_so, f"{REMOTE_LIB_DIR}/libinterync-ipc.so"
        )
        self._mgr.push_file(
            container_name, sync_so, f"{REMOTE_LIB_DIR}/libinterync-sync.so"
        )
        # Update linker cache so the test binaries can find the libraries
        self._mgr.exec(container_name,
                       ["bash", "-c",
                        f"echo '{REMOTE_LIB_DIR}' > /etc/ld.so.conf.d/interync.conf"
                        f" && ldconfig"])
        log.info("Libraries deployed to %s:%s", container_name, REMOTE_LIB_DIR)

    # ------------------------------------------------------------------ #
    # Running scenarios                                                     #
    # ------------------------------------------------------------------ #

    def run_scenario(self,
                     container_name: str,
                     scenario_script: str,
                     params: dict[str, Any] | None = None,
                     stream_callback: callable = None) -> dict[str, Any]:
        """
        Execute a scenario Python script inside the container.

        The script is expected to stream JSON results to stdout.
        Intermediate throughputs trigger the `stream_callback(tput)`.
        The final result object is returned.
        """
        params = params or {}
        self._mgr.ensure_running(container_name)
        self.deploy_libs(container_name)

        # Push the scenario script
        local_script = (Path(__file__).parents[2]
                        / "benchmarks" / "scenarios" / scenario_script)
        remote_script = f"/tmp/{scenario_script}"
        self._mgr.push_file(container_name, local_script, remote_script)

        env = {
            "LD_LIBRARY_PATH": REMOTE_LIB_DIR,
            "INTERYNC_PARAMS": json.dumps(params),
        }

        final_report = None
        for line in self._mgr.exec_stream(
            container_name,
            ["python3", remote_script],
            env=env
        ):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if "stream_tput" in data:
                    if stream_callback:
                        stream_callback(data["stream_tput"])
                elif "scenario" in data:
                    final_report = data
            except json.JSONDecodeError:
                pass # Ignore non-JSON logs like warnings

        if final_report is None:
            raise ContainerError(f"Scenario {scenario_script} produced no final JSON report")
            
        return final_report

    # ------------------------------------------------------------------ #
    # Results persistence                                                   #
    # ------------------------------------------------------------------ #

    def save_results(self, report: dict[str, Any]) -> None:
        """
        Persist the benchmark report dict to:
          results/report.json
          results/latency.csv
          results/throughput.csv
        """
        import csv

        # JSON report (dashboard reads this)
        report_path = RESULTS_DIR / "report.json"
        report_path.write_text(json.dumps(report, indent=2))
        log.info("Report saved to %s", report_path)

        # Latency CSV
        latency_rows = report.get("latency", [])
        if latency_rows:
            lat_path = RESULTS_DIR / "latency.csv"
            with open(lat_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=latency_rows[0].keys())
                writer.writeheader()
                writer.writerows(latency_rows)

        # Throughput CSV
        tp_rows = report.get("throughput", [])
        if tp_rows:
            tp_path = RESULTS_DIR / "throughput.csv"
            with open(tp_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=tp_rows[0].keys())
                writer.writeheader()
                writer.writerows(tp_rows)

    def load_report(self) -> dict[str, Any]:
        """Load the latest saved report.json (empty dict if not found)."""
        report_path = RESULTS_DIR / "report.json"
        if not report_path.exists():
            return {}
        return json.loads(report_path.read_text())
