"""
container_manager.py
InterSync — ONLY file that imports pylxd directly.

Wraps all LXD REST API calls: create, start, stop, destroy containers,
push/pull files, and exec commands.
"""

from __future__ import annotations

import os
import io
import time
import logging
import stat
from pathlib import Path
from typing import Optional

import logging

log = logging.getLogger(__name__)

try:
    import pylxd
    from pylxd.exceptions import LXDAPIException, NotFound
    _PYLXD_AVAILABLE = True
except Exception as exc:
    _PYLXD_AVAILABLE = False
    class NotFound(Exception): pass
    class LXDAPIException(Exception): pass
    log.debug("pylxd not available: %s", exc)

log = logging.getLogger(__name__)

CONTAINER_PREFIX = "interync-lab"
DEFAULT_IMAGE    = "ubuntu:22.04"
NUM_CONTAINERS   = 3


class ContainerError(RuntimeError):
    """Raised when a container operation fails."""


class ContainerManager:
    """
    Manages the lifecycle of interync-lab-{1,2,3} LXD containers.

    Usage:
        mgr = ContainerManager()
        mgr.ensure_running("interync-lab-1")
        result = mgr.exec("interync-lab-1", ["echo", "hello"])
        print(result.stdout)
    """

    def __init__(self, endpoint: str = "unix:///var/snap/lxd/common/lxd/unix.socket"):
        if not _PYLXD_AVAILABLE:
            raise ContainerError(
                "pylxd is not installed. Run: pip install pylxd"
            )
        try:
            self._client = pylxd.Client()
            log.info("Connected to LXD daemon")
        except Exception as exc:
            raise ContainerError(
                f"Cannot connect to LXD. Is lxd running? ({exc})"
            ) from exc

    @staticmethod
    def create_best() -> "ContainerManager":
        """Factory: returns VmContainerManager if IP set, WslContainerManager on Windows, else ContainerManager."""
        import sys
        import os
        vm_ip = os.environ.get("INTERSYNC_VM_IP")
        if vm_ip:
            log.info("INTERSYNC_VM_IP detected: using VmContainerManager for remote execution via FastAPI")
            return VmContainerManager(vm_ip)
        
        if sys.platform == "win32":
            log.info("Windows detected: using WSL CLI backend for LXD")
            return WslContainerManager()
        return ContainerManager()


class VmContainerManager(ContainerManager):
    """
    Delegates all LXD container management and execution commands over HTTP 
    to a FastAPI server running inside a VirtualBox VM (Parrot OS).
    """
    def __init__(self, vm_ip: str):
        from dashboard.backend.vm_client import VMClient
        self.client = VMClient(vm_ip)
        
    def list_containers(self) -> list[str]:
        # Parse output from `lxc list --format=json` which the VM returns
        try:
            data = self.client.lxc_list()
            return [c["name"] for c in data if c["name"].startswith("interync-lab")]
        except Exception as exc:
            raise ContainerError(f"VM lxc_list failed: {exc}")

    def get_container(self, name: str):
        pass # Not used in VM mode, mostly we just pass names

    def ensure_running(self, name: str) -> None:
        pass # In VM mode, we assume the VM already started its nested containers
        
    def exec(self, name: str, command: list[str], env: Optional[dict[str, str]] = None) -> "ExecResult":
        try:
            res = self.client.lxc_exec(name, command)
            class _Res:
                def __init__(self, c, o, e):
                    self.exit_code = c
                    self.stdout = o
                    self.stderr = e
            return _Res(res["exit_code"], res["stdout"], res["stderr"])
        except Exception as exc:
            raise ContainerError(f"VM exec failed for {command}: {exc}")

    def push_file(self, container_name: str, local_path: str, remote_path: str, mode: int = 0o644) -> None:
        try:
            self.client.lxc_push(container_name, local_path, remote_path)
        except Exception as exc:
            raise ContainerError(f"VM push failed: {exc}")


    # ------------------------------------------------------------------ #
    # Container lifecycle                                                   #
    # ------------------------------------------------------------------ #

    def list_containers(self) -> list[str]:
        """Return names of all interync-lab-* containers."""
        try:
            return [
                c.name for c in self._client.containers.all()
                if c.name.startswith(CONTAINER_PREFIX)
            ]
        except LXDAPIException as exc:
            raise ContainerError(f"list_containers failed: {exc}") from exc

    def get_container(self, name: str):
        """Return a pylxd container object or raise ContainerError."""
        try:
            return self._client.containers.get(name)
        except NotFound:
            raise ContainerError(f"Container '{name}' not found") from None
        except LXDAPIException as exc:
            raise ContainerError(f"get_container({name}) failed: {exc}") from exc

    def create_container(self, name: str, image: str = DEFAULT_IMAGE) -> None:
        """Create and start a new container with build-essential + python3."""
        log.info("Creating container %s from %s", name, image)
        try:
            existing = self._client.containers.all()
            if any(c.name == name for c in existing):
                log.info("Container %s already exists", name)
                return
        except LXDAPIException:
            pass

        config = {
            "name": name,
            "source": {
                "type": "image",
                "alias": image,
            },
        }
        try:
            container = self._client.containers.create(config, wait=True)
            container.start(wait=True)
            time.sleep(2)   # let networking settle

            log.info("Installing build tools in %s", name)
            self._exec_or_raise(container, ["apt-get", "update", "-qq"])
            self._exec_or_raise(
                container,
                ["apt-get", "install", "-y", "-qq",
                 "build-essential", "python3", "python3-pip"]
            )
            log.info("Container %s ready", name)
        except LXDAPIException as exc:
            raise ContainerError(f"create_container({name}) failed: {exc}") from exc

    def start_container(self, name: str) -> None:
        c = self.get_container(name)
        if c.status != "Running":
            c.start(wait=True)
            log.info("Started %s", name)

    def stop_container(self, name: str) -> None:
        c = self.get_container(name)
        if c.status == "Running":
            c.stop(wait=True)
            log.info("Stopped %s", name)

    def destroy_container(self, name: str) -> None:
        c = self.get_container(name)
        if c.status == "Running":
            c.stop(wait=True)
        c.delete(wait=True)
        log.info("Destroyed %s", name)

    def ensure_running(self, name: str) -> None:
        """Ensure the named container exists and is running."""
        try:
            c = self.get_container(name)
        except ContainerError:
            self.create_container(name)
            return
        if c.status != "Running":
            c.start(wait=True)
            time.sleep(1)

    def container_status(self, name: str) -> str:
        """Return 'Running', 'Stopped', or 'NotFound'."""
        try:
            return self.get_container(name).status
        except ContainerError:
            return "NotFound"

    # ------------------------------------------------------------------ #
    # File transfer                                                         #
    # ------------------------------------------------------------------ #

    def push_file(self, container_name: str,
                  local_path: str | Path,
                  remote_path: str) -> None:
        """Copy a local file into the container."""
        c = self.get_container(container_name)
        data = Path(local_path).read_bytes()
        try:
            c.files.put(remote_path, data)
            log.debug("Pushed %s → %s:%s", local_path, container_name, remote_path)
        except LXDAPIException as exc:
            raise ContainerError(
                f"push_file {local_path} → {container_name}:{remote_path} failed: {exc}"
            ) from exc

    def pull_file(self, container_name: str, remote_path: str) -> bytes:
        """Pull a file from the container and return its contents."""
        c = self.get_container(container_name)
        try:
            return c.files.get(remote_path)
        except LXDAPIException as exc:
            raise ContainerError(
                f"pull_file {container_name}:{remote_path} failed: {exc}"
            ) from exc

    # ------------------------------------------------------------------ #
    # Command execution                                                     #
    # ------------------------------------------------------------------ #

    def exec(self, container_name: str,
             command: list[str],
             cwd: str = "/root",
             env: Optional[dict] = None,
             timeout: int = 120) -> "ExecResult":
        """
        Execute a command inside the container.

        Returns an ExecResult with .exit_code, .stdout, .stderr.
        Raises ContainerError if the container is unreachable.
        Does NOT raise on non-zero exit codes — callers decide.
        """
        c = self.get_container(container_name)
        try:
            result = c.execute(
                command,
                environment=env or {},
                cwd=cwd,
            )
            return ExecResult(
                exit_code=result.exit_code,
                stdout=result.stdout if isinstance(result.stdout, str)
                       else result.stdout.decode("utf-8", errors="replace"),
                stderr=result.stderr if isinstance(result.stderr, str)
                       else result.stderr.decode("utf-8", errors="replace"),
            )
        except LXDAPIException as exc:
            raise ContainerError(
                f"exec {command} in {container_name} failed: {exc}"
            ) from exc

    def exec_stream(self, container_name: str, command: list[str], cwd: str = "/root", env: Optional[dict] = None):
        """Yields stdout lines as they arrive. Returns (exit_code, final_stderr) at the end, but python generators don't return easily so we'll just yield lines."""
        # For base pylxd, we'd need websocket streaming. For now, just fallback to blocking.
        res = self.exec(container_name, command, cwd, env)
        for line in res.stdout.splitlines():
            yield line


    def _exec_or_raise(self, container, command: list[str]) -> None:
        """Execute and raise ContainerError on non-zero exit."""
        result = container.execute(command)
        if result.exit_code != 0:
            raise ContainerError(
                f"Command {command} exited {result.exit_code}: "
                f"{result.stderr}"
            )

    def create_all_containers(self) -> None:
        """Create all three interync-lab containers."""
        for i in range(1, NUM_CONTAINERS + 1):
            self.create_container(f"{CONTAINER_PREFIX}-{i}")

    def stop_all_containers(self) -> None:
        for i in range(1, NUM_CONTAINERS + 1):
            name = f"{CONTAINER_PREFIX}-{i}"
            try:
                self.stop_container(name)
            except ContainerError as exc:
                log.warning("Could not stop %s: %s", name, exc)

    def start_all_containers(self) -> None:
        for i in range(1, NUM_CONTAINERS + 1):
            name = f"{CONTAINER_PREFIX}-{i}"
            try:
                self.start_container(name)
            except ContainerError as exc:
                log.warning("Could not start %s: %s", name, exc)


class ExecResult:
    """Result of a container command execution."""
    __slots__ = ("exit_code", "stdout", "stderr")

    def __init__(self, exit_code: int, stdout: str, stderr: str):
        self.exit_code = exit_code
        self.stdout    = stdout
        self.stderr    = stderr

    def __repr__(self) -> str:
        return (f"ExecResult(exit_code={self.exit_code}, "
                f"stdout={self.stdout!r:.60}, stderr={self.stderr!r:.60})")


class WslContainerManager(ContainerManager):
    """
    Windows-native backend that delegates LXD operations to WSL2 via `wsl lxc ...`.
    Allows the PyQt6 dashboard to run on Windows while controlling LXD in WSL.
    """

    def __init__(self):
        import subprocess
        # Test if wsl lxc is available, checking snap path if needed
        try:
            subprocess.run(["wsl", "lxc", "--version"], capture_output=True, check=True)
            self._lxc_cmd = "lxc"
            log.info("Connected to LXD daemon via WSL CLI (lxc)")
        except Exception:
            try:
                subprocess.run(["wsl", "/snap/bin/lxc", "--version"], capture_output=True, check=True)
                self._lxc_cmd = "/snap/bin/lxc"
                log.info("Connected to LXD daemon via WSL CLI (/snap/bin/lxc)")
            except Exception as exc:
                raise ContainerError(f"Cannot run 'lxc' or '/snap/bin/lxc' via wsl. Is LXD installed in WSL? {exc}")

    def _run_wsl(self, args: list[str], input_bytes: Optional[bytes] = None) -> ExecResult:
        import subprocess
        
        # Replace the generic 'lxc' command with the resolved path
        if args[0] == "lxc":
            args[0] = self._lxc_cmd
            
        try:
            res = subprocess.run(
                ["wsl"] + args,
                input=input_bytes,
                capture_output=True
            )
            return ExecResult(
                exit_code=res.returncode,
                stdout=res.stdout.decode("utf-8", errors="replace"),
                stderr=res.stderr.decode("utf-8", errors="replace"),
            )
        except Exception as exc:
            raise ContainerError(f"wsl command failed: {exc}")

    def container_status(self, name: str) -> str:
        res = self._run_wsl(["lxc", "list", name, "-c", "s", "--format", "csv"])
        if res.exit_code != 0 or not res.stdout.strip():
            return "NotFound"
        # stdout is like "RUNNING\n"
        status = res.stdout.strip().title()
        return status

    def start_container(self, name: str) -> None:
        res = self._run_wsl(["lxc", "start", name])
        if res.exit_code != 0:
            raise ContainerError(f"start_container failed: {res.stderr}")

    def stop_container(self, name: str) -> None:
        res = self._run_wsl(["lxc", "stop", name])
        if res.exit_code != 0:
            raise ContainerError(f"stop_container failed: {res.stderr}")

    def ensure_running(self, name: str) -> None:
        status = self.container_status(name)
        if status == "NotFound":
            # create container? Wsl backend assumes containers are created via Makefile
            raise ContainerError(f"Container {name} not found. Run 'make containers-create' in WSL.")
        if status != "Running":
            self.start_container(name)

    def push_file(self, container_name: str, local_path: str | Path, remote_path: str) -> None:
        data = Path(local_path).read_bytes()
        # push via stdin: lxc file push - <container>/<path>
        res = self._run_wsl(["lxc", "file", "push", "-", f"{container_name}/{remote_path}"], input_bytes=data)
        if res.exit_code != 0:
            raise ContainerError(f"push_file failed: {res.stderr}")

    def pull_file(self, container_name: str, remote_path: str) -> bytes:
        import subprocess
        # pull to stdout
        try:
            res = subprocess.run(
                ["wsl", "lxc", "file", "pull", f"{container_name}/{remote_path}", "-"],
                capture_output=True
            )
            if res.returncode != 0:
                raise ContainerError(f"pull_file failed: {res.stderr.decode('utf-8', errors='replace')}")
            return res.stdout
        except Exception as exc:
            raise ContainerError(f"pull_file exception: {exc}")

    def exec(self, container_name: str, command: list[str], cwd: str = "/root", env: Optional[dict] = None, timeout: int = 120) -> ExecResult:
        args = ["lxc", "exec", container_name]
        if env:
            for k, v in env.items():
                args.extend(["--env", f"{k}={v}"])
        args.extend(["--cwd", cwd, "--"])
        args.extend(command)
        return self._run_wsl(args)

    def exec_stream(self, container_name: str, command: list[str], cwd: str = "/root", env: Optional[dict] = None):
        import subprocess
        args = ["lxc", "exec", container_name]
        if env:
            for k, v in env.items():
                args.extend(["--env", f"{k}={v}"])
        args.extend(["--cwd", cwd, "--"])
        args.extend(command)
        
        if args[0] == "lxc":
            args[0] = self._lxc_cmd
            
        try:
            proc = subprocess.Popen(
                ["wsl"] + args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )
            for line in iter(proc.stdout.readline, ''):
                yield line
            proc.wait()
        except Exception as exc:
            raise ContainerError(f"wsl command failed: {exc}")
