import httpx
from typing import Dict, Any, List

class VMClient:
    def __init__(self, vm_ip="192.168.56.101", port=5000):
        self.base_url = f"http://{vm_ip}:{port}"
        self._http = httpx.Client(timeout=30.0)
    
    def _post(self, endpoint: str, data: Dict[str, Any]) -> Dict[str, Any]:
        resp = self._http.post(f"{self.base_url}{endpoint}", json=data)
        resp.raise_for_status()
        return resp.json()

    def _get(self, endpoint: str, params: Dict[str, Any] = None) -> Any:
        resp = self._http.get(f"{self.base_url}{endpoint}", params=params)
        resp.raise_for_status()
        return resp.json()

    def ipc_send(self, container: str, mechanism: str, msg_size: int, channel: str = "interync-ch") -> Dict[str, Any]:
        return self._post("/api/ipc/send", {
            "container": container,
            "mechanism": mechanism,
            "msg_size": msg_size,
            "channel": channel
        })

    def benchmark_ipc(self, container: str, mechanism: str, msg_size: int, count: int) -> Dict[str, Any]:
        return self._post("/api/ipc/benchmark", {
            "container": container,
            "mechanism": mechanism,
            "msg_size": msg_size,
            "count": count
        })

    def benchmark_spsc(self, container: str, capacity: int, slot_size: int, count: int) -> Dict[str, Any]:
        return self._post("/api/spsc/benchmark", {
            "container": container,
            "capacity": capacity,
            "slot_size": slot_size,
            "count": count
        })

    def benchmark_mpmc(self, container: str, capacity: int, slot_size: int, count: int, producers: int, consumers: int) -> Dict[str, Any]:
        return self._post("/api/mpmc/benchmark", {
            "container": container,
            "capacity": capacity,
            "slot_size": slot_size,
            "count": count,
            "producers": producers,
            "consumers": consumers
        })

    def lock_acquire(self, container: str, primitive: str, lock_name: str) -> Dict[str, Any]:
        return self._post("/api/sync/lock-acquire", {
            "container": container,
            "primitive": primitive,
            "lock_name": lock_name
        })

    def deadlock_inject(self, container: str, primitive: str, num_threads: int) -> Dict[str, Any]:
        return self._post("/api/sync/deadlock-inject", {
            "container": container,
            "primitive": primitive,
            "num_threads": num_threads
        })

    def deadlock_resolve(self, container: str, pid: int) -> Dict[str, Any]:
        return self._post("/api/sync/deadlock-resolve", {
            "container": container,
            "pid": pid
        })

    def trace_logs(self, container: str, n: int = 100) -> str:
        data = self._get("/api/trace/logs", params={"container": container, "n": n})
        return data.get("raw", "")

    def lxc_list(self) -> List[Dict[str, Any]]:
        return self._get("/api/lxc/list")

    def lxc_exec(self, container: str, command: List[str]) -> Dict[str, Any]:
        return self._post("/api/lxc/exec", {
            "container": container,
            "command": command
        })

    def lxc_push(self, container: str, local_path: str, remote_path: str) -> Dict[str, Any]:
        return self._post("/api/lxc/push", {
            "container": container,
            "local_path": local_path,
            "remote_path": remote_path
        })
