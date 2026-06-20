import os
import subprocess
import json
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="InterSync VM API")

CONTAINER_BIN = "/opt/interync/bin"

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class ExecReq(BaseModel):
    container: str
    command: List[str]

class PushReq(BaseModel):
    container: str
    local_path: str
    remote_path: str

class IpcSendReq(BaseModel):
    container: str
    mechanism: str
    msg_size: int
    channel: str = "interync-ch"

class IpcBenchReq(BaseModel):
    container: str
    mechanism: str
    msg_size: int
    count: int

class SpscBenchReq(BaseModel):
    container: str
    capacity: int
    slot_size: int
    count: int

class MpmcBenchReq(BaseModel):
    container: str
    capacity: int
    slot_size: int
    count: int
    producers: int
    consumers: int

class LockAcquireReq(BaseModel):
    container: str
    primitive: str
    lock_name: str

class DeadlockInjectReq(BaseModel):
    container: str
    primitive: str
    num_threads: int

class DeadlockResolveReq(BaseModel):
    container: str
    pid: int

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def run_lxc(container: str, cmd: List[str]) -> Dict[str, Any]:
    full_cmd = ["lxc", "exec", container, "--"] + cmd
    res = subprocess.run(full_cmd, capture_output=True, text=True)
    if res.returncode != 0:
        return {"exit_code": res.returncode, "stdout": res.stdout, "stderr": res.stderr}
    return {"exit_code": 0, "stdout": res.stdout, "stderr": res.stderr}

def parse_json_output(stdout: str) -> Dict:
    lines = [l for l in stdout.splitlines() if l.strip()]
    if not lines:
        raise HTTPException(status_code=500, detail="Empty output from container")
    try:
        return json.loads(lines[-1])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Invalid JSON from container: {str(e)}")

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/lxc/list")
def list_containers():
    res = subprocess.run(["lxc", "list", "--format=json"], capture_output=True, text=True)
    if res.returncode != 0:
        raise HTTPException(status_code=500, detail=res.stderr)
    return json.loads(res.stdout)

@app.post("/api/lxc/exec")
def lxc_exec(req: ExecReq):
    return run_lxc(req.container, req.command)

@app.post("/api/lxc/push")
def lxc_push(req: PushReq):
    res = subprocess.run(["lxc", "file", "push", req.local_path, f"{req.container}/{req.remote_path.lstrip('/')}"], capture_output=True, text=True)
    if res.returncode != 0:
        raise HTTPException(status_code=500, detail=res.stderr)
    return {"status": "ok"}

@app.post("/api/ipc/send")
def ipc_send(req: IpcSendReq):
    cmd = [f"{CONTAINER_BIN}/ipc_interactive", "send", req.mechanism, str(req.msg_size), req.channel]
    res = run_lxc(req.container, cmd)
    if res["exit_code"] != 0:
        raise HTTPException(status_code=500, detail=res["stderr"])
    return parse_json_output(res["stdout"])

@app.post("/api/ipc/benchmark")
def ipc_bench(req: IpcBenchReq):
    cmd = [f"{CONTAINER_BIN}/ipc_interactive", "bench", req.mechanism, str(req.msg_size), str(req.count)]
    res = run_lxc(req.container, cmd)
    if res["exit_code"] != 0:
        raise HTTPException(status_code=500, detail=res["stderr"])
    return parse_json_output(res["stdout"])

@app.post("/api/spsc/benchmark")
def spsc_bench(req: SpscBenchReq):
    cmd = [f"{CONTAINER_BIN}/bench_spsc_interactive", "spsc", str(req.capacity), str(req.slot_size), str(req.count)]
    res = run_lxc(req.container, cmd)
    if res["exit_code"] != 0:
        raise HTTPException(status_code=500, detail=res["stderr"])
    return parse_json_output(res["stdout"])

@app.post("/api/mpmc/benchmark")
def mpmc_bench(req: MpmcBenchReq):
    cmd = [f"{CONTAINER_BIN}/bench_spsc_interactive", "mpmc", str(req.capacity), str(req.slot_size), str(req.count), str(req.producers), str(req.consumers)]
    res = run_lxc(req.container, cmd)
    if res["exit_code"] != 0:
        raise HTTPException(status_code=500, detail=res["stderr"])
    return parse_json_output(res["stdout"])

@app.post("/api/sync/lock-acquire")
def lock_acquire(req: LockAcquireReq):
    cmd = [f"{CONTAINER_BIN}/sync_interactive", "lock", req.primitive, req.lock_name]
    res = run_lxc(req.container, cmd)
    if res["exit_code"] != 0:
        raise HTTPException(status_code=500, detail=res["stderr"])
    return parse_json_output(res["stdout"])

@app.post("/api/sync/deadlock-inject")
def deadlock_inject(req: DeadlockInjectReq):
    cmd = [f"{CONTAINER_BIN}/sync_interactive", "deadlock_inject", req.primitive, str(req.num_threads)]
    res = run_lxc(req.container, cmd)
    if res["exit_code"] != 0:
        raise HTTPException(status_code=500, detail=res["stderr"])
    return parse_json_output(res["stdout"])

@app.post("/api/sync/deadlock-resolve")
def deadlock_resolve(req: DeadlockResolveReq):
    cmd = ["kill", "-9", str(req.pid)]
    res = run_lxc(req.container, cmd)
    if res["exit_code"] != 0:
        raise HTTPException(status_code=500, detail=res["stderr"])
    return {"op": "deadlock_resolve", "killed_pid": req.pid}

@app.get("/api/deadlock/detect")
def deadlock_detect(container: str):
    # Handled via pulling trace logs and running local python logic on the UI client, 
    # but could be done server side. UI handles this currently.
    pass

@app.get("/api/deadlock/graph")
def deadlock_graph(container: str):
    pass

@app.get("/api/trace/logs")
def trace_logs(container: str, n: int = 100):
    res0 = run_lxc(container, ["cat", "/tmp/interync_lock_trace.0.log"])
    res1 = run_lxc(container, ["cat", "/tmp/interync_lock_trace.1.log"])
    raw = ""
    if res0["exit_code"] == 0: raw += res0["stdout"]
    if res1["exit_code"] == 0: raw += res1["stdout"]
    return {"raw": raw}
