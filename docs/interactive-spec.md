# InterSync Interactive Dashboard — Specification

> **Status:** Draft Specification
> **Target:** Transform the current passive-simulation PyQt6 dashboard into a fully interactive, real-time control interface backed by LXD containers and real C libraries.
> **Timeline:** No rush — spec-driven implementation over multiple sessions.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Layout & Navigation](#2-layout--navigation)
3. [IPC Flow Tab — Interactive Controls](#3-ipc-flow-tab--interactive-controls)
4. [Sync & Locks Tab — Interactive Controls](#4-sync--locks-tab--interactive-controls)
5. [Dining Philosophers Tab — Interactive Controls](#5-dining-philosophers-tab--interactive-controls)
6. [Benchmarks Tab — Improvements](#6-benchmarks-tab--improvements)
7. [Overview Tab — Improvements](#7-overview-tab--improvements)
8. [Guided Scenarios Mode](#8-guided-scenarios-mode)
9. [Backend Architecture Changes](#9-backend-architecture-changes)
10. [Interaction & Feedback System](#10-interaction--feedback-system)
11. [File Layout & New Files](#11-file-layout--new-files)
12. [Implementation Roadmap](#12-implementation-roadmap)

---

## 1. Architecture Overview

### Current Limitation
The dashboard today is a **passive viewer**: it polls containers, reads logs, and animates pre-recorded or simulated data. The user clicks "Start" and watches — there is no way to influence the live system.

### Target Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                        Dashboard (PyQt6)                            │
│                                                                     │
│  ┌────────────────────┐   ┌──────────────────────────────────────┐ │
│  │   CONTROL PANEL     │   │        VISUALIZATION CANVAS          │ │
│  │   (Left, fixed)     │   │        (Right, stretch)              │ │
│  │                     │   │                                       │ │
│  │  • Sliders          │   │  • IPC flow animation                 │ │
│  │  • Buttons          │   │  • Lock state boxes                   │ │
│  │  • Dropdowns        │   │  • Wait-for graph                     │ │
│  │  • Step controls    │   │  • Philosopher table                  │ │
│  │  • Scenario player  │   │  • Toast notifications                │ │
│  └────────┬────────────┘   └──────────────────┬────────────────────┘ │
│           │                                    │                    │
└───────────┼────────────────────────────────────┼────────────────────┘
            │                                    │
            ▼                                    ▼
  ┌─────────────────────┐         ┌─────────────────────────────┐
  │  InteractiveBackend  │         │  EventBus (in-process)       │
  │  (new)               │         │  Routes:                     │
  │                     │         │  • User actions → Backend     │
  │  • Sends commands    │         │  • Backend responses → UI    │
  │    to LXD containers │         │  • Deadlock alerts → UI      │
  │  • Calls C programs  │         └─────────────────────────────┘
  │  via lxc exec        │
  └──────────┬───────────┘
             │
             ▼
  ┌─────────────────────────────────────┐
  │  LXD Containers                      │
  │  interync-lab-1, -2, -3             │
  │                                     │
  │  • C test harness (interactive mode)│
  │  • Real ipc_send / ipc_receive      │
  │  • Real sync_lock / sync_unlock     │
  │  • Real-time lock trace output      │
  └─────────────────────────────────────┘
```

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Backend** | Real C libraries via LXD | Authentic IPC/sync behavior, not Python simulation |
| **Layout** | Split view | Left: controls, Right: visualization — always both visible |
| **Input** | GUI buttons + sliders + dropdowns | No keyboard shortcuts or CLI overlay |
| **Feedback** | Both toasts + inline canvas | Toasts for events, inline for real-time state |
| **Modes** | Both sandbox + guided scenarios | Two top-level modes selectable from a toggle |
| **Session recording** | Architecture-aware, not implemented | Leave hooks for future `recording.py` module |
| **State persistence** | None | Start fresh every launch |
| **Container selection** | Yes, user picks which container per role | Dropdowns for producer/consumer container choice |

---

## 2. Layout & Navigation

### Global Layout Change

Current: Tab-based, each tab is full-width with no split.

New: **Split-view layout** applied globally.

```
┌─────────────────────────────────────────────────────────────┐
│  [⚡ InterSync — Interactive Mode]            [🔬 Scenarios] │
├──────────────┬──────────────────────────────────────────────┤
│  CONTROL      │  VISUALIZATION CANVAS                       │
│  PANEL        │                                              │
│  (280px fixed)│  (stretches to fill)                         │
│              │                                              │
│  ┌─────────┐ │  ┌──────────────────────────────────────────┐│
│  │ Tab bar  │ │  │  (content changes per tab)               ││
│  │ [IPC]    │ │  │                                          ││
│  │ [Sync]   │ │  │                                          ││
│  │ [Philo]  │ │  │  • IPC: animated flow + packets          ││
│  │ [Bench]  │ │  │  • Sync: lock boxes + wait-for graph     ││
│  │ [Overview│ │  │  • Philo: table + philosophers           ││
│  └─────────┘ │  └──────────────────────────────────────────┘│
│              │                                              │
│  ┌─────────┐ │  ┌──────────────────────────────────────────┐│
│  │ Controls │ │  │  Feedback overlay (toasts, alerts)       ││
│  │ (dynamic)│ │  └──────────────────────────────────────────┘│
│  └─────────┘ │                                              │
└──────────────┴──────────────────────────────────────────────┘
│  Status Bar: [Container: interync-lab-1 ● Running] [CPU: 12%]│
└─────────────────────────────────────────────────────────────┘
```

### Split View Implementation

- **Control Panel** (QWidget, left, fixed 280px width):
  - Tab bar at top (Overview | IPC | Sync | Philosophers | Benchmarks)
  - Below tab bar: dynamic control widgets specific to the active tab
  - Controls are always visible regardless of what's on the canvas

- **Visualization Canvas** (QWidget, right, stretch):
  - Renders the tab-specific visualization
  - Responsible for all QPainter / matplotlib / pyqtgraph output
  - Overlays toast notifications for feedback

- **Status Bar** (bottom):
  - Currently selected container and its status
  - Global connection indicator (LXD connected/disconnected)
  - Last action timestamp

---

## 3. IPC Flow Tab — Interactive Controls

### 3.1 Control Panel (Left)

| Control | Type | Description |
|---------|------|-------------|
| IPC Mechanism | Dropdown | PIPE / QUEUE / SOCKET / SHM |
| Producer Container | Dropdown | interync-lab-1, -2, -3 |
| Consumer Container | Dropdown | interync-lab-1, -2, -3 |
| Message Size | Slider | 1 byte – 8 KB (default: 256 B) |
| Send Mode | Toggle | `Single Shot` \| `Burst` \| `Continuous` |
| Send Rate | Slider | (only in Continuous mode) 1–1000 msg/s |
| Burst Count | Spinbox | (only in Burst mode) 1–1000 messages |
| **[SEND NOW]** | Button | Sends one message with current settings |
| **[START BURST]** | Button | Sends burst of N messages |
| **[STOP]** | Button | Stops continuous/burst mid-way |
| Log Level | Dropdown | None / Errors / All (controls toast verbosity) |

### 3.2 Canvas Behavior

- **Producer box** (left): Shows container name, PID, and a "message counter"
- **Channel tube** (center): Animated, shows current message flowing. Color-coded by mechanism:
  - PIPE: teal, QUEUE: purple, SOCKET: green, SHM: orange
- **Consumer box** (right): Shows container name, PID, received message count, byte total
- **Click on Producer**: Sends one message (same as SEND NOW button)
- **Inline stats**: Real-time throughput (MB/s) and latency (µs) drawn on the channel tube
- **Errors**: Red flash on the channel tube if `ipc_send()` / `ipc_receive()` fails

### 3.3 Backend Workflow (SEND NOW example)

```
User clicks [SEND NOW] with mechanism=PIPE, msg_size=256,
producer=interync-lab-1, consumer=interync-lab-2

→ InteractiveBackend.send_ipc("interync-lab-1", "interync-lab-2", {
      mechanism: "PIPE",
      msg_size: 256,
      data: random_bytes(256)
  })

→ LXD exec on interync-lab-1:
    /opt/interync/bin/ipc_interactive send PIPE 256 <base64_data>

→ LXD exec on interync-lab-2:
    /opt/interync/bin/ipc_interactive receive PIPE 256

→ Results returned to dashboard as JSON:
    {
      "send_time_us": 12.3,
      "recv_time_us": 15.7,
      "latency_us": 3.4,
      "bytes": 256
    }

→ Canvas updates: packet animation plays, stats update, toast shows "Sent 256B via PIPE (3.4 µs)"
```

### 3.4 New C Helper Binary

A new C program `ipc_interactive` will be compiled and deployed to containers:

```c
// Usage inside container:
//   ipc_interactive send PIPE 256    # sends 256 bytes, returns timing JSON
//   ipc_interactive recv PIPE 256    # receives 256 bytes, returns timing JSON
//   ipc_interactive burst PIPE 256 50  # sends 50 messages of 256 bytes
//   ipc_interactive listen PIPE 256  # blocking, receives one message
```

This binary links against `libinterync-ipc.so` and writes JSON timing output to stdout.

---

## 4. Sync & Locks Tab — Interactive Controls

### 4.1 Control Panel (Left)

| Control | Type | Description |
|---------|------|-------------|
| Target Container | Dropdown | interync-lab-1, -2, -3 |
| Primitive | Dropdown | MUTEX / SEMAPHORE / CONDVAR / RWLOCK |
| Lock Name | Text Input | Optional label for the lock (e.g., "Fork-1") |
| **[ACQUIRE]** | Button | Acquire the selected lock (blocking call via LXD) |
| **[ACQUIRE (READ)]** | Button | (RWLOCK only) Acquire shared read lock |
| **[RELEASE]** | Button | Release the currently held lock |
| **[INJECT DEADLOCK]** | Button | Spawns a second thread that tries to acquire locks in opposite order |
| **[RESOLVE]** | Button | Force-release the lock causing the deadlock (kill the holding PID) |
| Thread Count | Slider | 1–16 threads contending for the lock |
| Auto-spawn | Toggle | On: threads automatically contend; Off: manual control only |

### 4.2 Canvas Behavior

- **Lock boxes**: One box per active lock. Shows:
  - Lock type (MUTEX, SEMAPHORE, etc.)
  - Current holder PID (or "FREE" in green)
  - Wait queue: red dots for waiting PIDs
- **Click on a FREE lock**: Acquire it (same as ACQUIRE button)
- **Click on a held lock**: Release it (same as RELEASE button)
- **Pulse animation**: Lock box glows cyan briefly on ACQUIRE/RELEASE
- **Deadlock visualization**: When INJECT DEADLOCK is clicked:
  - Canvas switches to wait-for graph view
  - Cycle highlighted in red
  - Status bar shows "⚠ DEADLOCK — resolve by clicking the holding lock"
- **Step controls** (when step-through mode enabled):
  - ⏮ ⏸ ⏭ buttons to step through the event timeline
  - Timeline scrubber at bottom of canvas

### 4.3 Backend Workflow

A new C binary `sync_interactive`:

```c
// Usage:
//   sync_interactive acquire MUTEX lock_name
//   sync_interactive acquire_read RWLOCK lock_name    // non-blocking attempt
//   sync_interactive release <lock_handle_hex>
//   sync_interactive stress MUTEX 8 100               // 8 threads, 100 iterations each
//   sync_interactive deadlock MUTEX 2                 // 2 threads, opposite order → deadlock
```

Each call returns JSON with timing, success/failure, and current wait queue info.

The **TraceStreamer** (existing `sync_visualizer.py` QThread) will continue to tail the lock trace log from the container to provide real-time state updates alongside manual actions.

---

## 5. Dining Philosophers Tab — Interactive Controls

### 5.1 Control Panel (Left)

| Control | Type | Description |
|---------|------|-------------|
| Num Philosophers | Slider | 2–10 |
| Container | Dropdown | interync-lab-1, -2, -3 |
| Think Time | Slider | 0–1000 ms |
| Eat Time | Slider | 0–1000 ms |
| Deadlock Avoidance | Toggle | On: resource ordering; Off: naive (deadlock-prone) |
| **[PLAY/PAUSE]** | Button | Start/pause the simulation |
| **[STEP]** | Button | Advance one step (each philosopher attempts one action) |
| Speed | Slider | 0.1x – 10x simulation speed |
| Click philosopher | Interactive | Click a philosopher → toggle between thinking/hungry/eating |

### 5.2 Canvas Layout

A round table rendered with QPainter:

```
               🧑 P0
         🥄          🥄
     🧑 P4              🧑 P1
         🥄          🥄
     🧑 P3              🧑 P2
         🥄          🥄
               🧑 P5
```

- Each philosopher is a circle with:
  - PID number
  - State color: 💤 thinking (dim blue), 🍽️ hungry (yellow), 🍝 eating (green), 💀 deadlocked (red)
- Each fork (🥄) is drawn between two philosophers
  - Green glow when held, gray when free
- Clicking a philosopher's circle toggles their state (only if simulation is paused)
- Fork state shown as tooltip on hover

### 5.3 Game-Like Scenario Mode

When "Scenarios" mode is active (toggle in header):

| Scenario | Goal | Constraints |
|----------|------|-------------|
| **"Avoid the Deadlock"** | Keep all philosophers eating for 30s | Deadlock avoidance OFF, user must manually intervene |
| **"Speed Feast"** | Maximize total meals in 20s | User adjusts think/eat times live |
| **"Fair Share"** | Achieve fairness ratio > 0.9 | User intervenes to prevent starvation |
| **"Triage"** | Resolve 3 deadlocks in a row | System auto-injects deadlocks, user must resolve each |

When a scenario completes, a results dialog shows score/statistics.

---

## 6. Benchmarks Tab — Improvements

### 6.1 Changes

The Benchmarks tab is already somewhat interactive (sliders + Run button). Enhancements:

- Add a **Live Stream toggle**: when on, benchmark results stream to the IPC Flow canvas in real-time instead of waiting for completion
- Add **Comparison mode**: run two mechanisms side-by-side with matched parameters
- Add a **Save Snapshot** button: export current chart as PNG
- The left control panel can show: scenario selector, parameter sliders, run/stop buttons

---

## 7. Overview Tab — Improvements

### 7.1 Changes

- Add **container action buttons** in the Overview tab:
  - [START] / [STOP] next to each container name
  - [SHELL] opens a terminal emulator widget (QProcess running `wsl lxc exec ... bash`)
- Add a **Connection health** indicator: green/yellow/red based on last poll time
- Make container status badges **clickable** to jump to the relevant tab for that container
- Add a **mini IPC test** button per container: sends a ping message and measures round-trip

---

## 8. Guided Scenarios Mode

### 8.1 Mode Toggle

A toggle in the header bar:

```
[🔬 Sandbox Mode]  <──toggle──>  [🎯 Scenario Mode]
```

### 8.2 Scenario System Architecture

- `dashboard/scenarios/` directory containing JSON scenario definitions:

```json
{
  "id": "avoid-deadlock-1",
  "name": "Avoid the Deadlock",
  "description": "Keep all 5 philosophers eating for 30 seconds without deadlock...",
  "tab": "philosophers",
  "initial_params": {
    "num_philosophers": 5,
    "deadlock_avoidance": false,
    "think_ms": 20,
    "eat_ms": 15
  },
  "win_condition": {
    "type": "survive_seconds",
    "value": 30,
    "metric": "no_deadlock"
  },
  "constraints": {
    "allow_manual_intervention": true,
    "max_resolves": 3
  },
  "steps": [
    {"instruction": "Click each philosopher to help them eat when they're hungry", "timeout_s": 10},
    {"instruction": "A deadlock is forming! Click the fork held by P2 to release it", "timeout_s": 5}
  ]
}
```

- `dashboard/scenario_engine.py` — runs the scenario lifecycle:
  - Loads scenario JSON
  - Sets initial params on the relevant tab
  - Shows step-by-step instructions in a sidebar
  - Evaluates win/lose conditions
  - Shows results dialog on completion

### 8.3 UI for Scenarios

When Scenario Mode is active:
- A **Scenario Sidebar** appears on the far left (overlaying the normal control panel)
- Shows: scenario name, progress bar, current objective, time remaining
- Bottom: [QUIT SCENARIO] button

---

## 9. Backend Architecture Changes

### 9.1 New Module: `dashboard/backend/interactive_backend.py`

This is the central class that translates user interactions into LXD commands and parses results.

```python
class InteractiveBackend:
    """
    Transforms UI interactions into real IPC/sync operations
    executed inside LXD containers via the C helper binaries.
    """

    def __init__(self, container_manager: ContainerManager):
        ...

    # IPC
    def send_ipc(self, producer_cont: str, consumer_cont: str,
                 mechanism: str, msg_size: int, data: bytes) -> dict:
        """Returns {send_time_us, recv_time_us, latency_us, bytes, error}"""
        ...

    def start_burst(self, producer_cont: str, consumer_cont: str,
                    mechanism: str, msg_size: int, count: int):
        """Starts a burst in a background thread, yields progress via callback"""
        ...

    def stop_burst(self):
        ...

    # Sync
    def acquire_lock(self, container: str, primitive: str,
                     lock_name: str) -> dict:
        """Returns {success, holder_pid, wait_queue, acquire_time_us}"""
        ...

    def release_lock(self, container: str, lock_handle: str) -> dict:
        ...

    def inject_deadlock(self, container: str, primitive: str) -> dict:
        """Spawns competing threads in opposite order, returns cycle info"""
        ...

    def resolve_deadlock(self, container: str, pid_to_kill: int) -> dict:
        """Kills the holding PID to break deadlock"""
        ...

    # Philosophers
    def run_philosopher_step(self, container: str, params: dict) -> dict:
        """Advances the philosopher simulation by one tick"""
        ...
```

### 9.2 New Module: `dashboard/backend/event_bus.py`

An in-process pub-sub bus for decoupling UI from backend:

```python
class EventBus(QObject):
    """
    Singleton event bus. UI components emit events here,
    backend components subscribe and respond.
    """
    ipc_sent = pyqtSignal(dict)
    ipc_received = pyqtSignal(dict)
    lock_acquired = pyqtSignal(dict)
    lock_released = pyqtSignal(dict)
    deadlock_detected = pyqtSignal(dict)
    deadlock_resolved = pyqtSignal(dict)
    error_occurred = pyqtSignal(str, str)  # (source, message)
    scenario_event = pyqtSignal(str, dict)  # (event_type, data)
```

### 9.3 New C Helper Binaries

| Binary | Source | Purpose |
|--------|--------|---------|
| `ipc_interactive` | `lib/ipc/ipc_interactive.c` | Send/receive/burst IPC messages with JSON timing output |
| `sync_interactive` | `lib/sync/sync_interactive.c` | Acquire/release locks, stress test, deadlock injection |
| `philo_interactive` | `benchmarks/scenarios/philo_interactive.c` | Single-tick philosopher simulation for step-by-step control |

Each binary:
- Links against the corresponding `.so` library
- Accepts CLI arguments for operation + parameters
- Outputs JSON to stdout
- Uses `clock_gettime(CLOCK_MONOTONIC)` for microsecond timing
- Returns non-zero exit code on error with error JSON on stderr

### 9.4 Build System Updates

Add to `Makefile`:

```makefile
build-interactive: build-libs
	@gcc -Wall -O2 -o $(C_BUILD_DIR)/ipc_interactive \
		lib/ipc/ipc_interactive.c \
		-Ilib/ipc -L$(C_BUILD_DIR) -linterync-ipc -lrt -pthread \
		-Wl,-rpath,$(C_BUILD_DIR)
	@gcc -Wall -O2 -o $(C_BUILD_DIR)/sync_interactive \
		lib/sync/sync_interactive.c \
		-Ilib/sync -L$(C_BUILD_DIR) -linterync-sync -pthread \
		-Wl,-rpath,$(C_BUILD_DIR)
	@gcc -Wall -O2 -o $(C_BUILD_DIR)/philo_interactive \
		lib/sync/philo_interactive.c \
		-Ilib/sync -L$(C_BUILD_DIR) -linterync-sync -pthread \
		-Wl,-rpath,$(C_BUILD_DIR)
```

---

## 10. Interaction & Feedback System

### 10.1 Toast Notification System

```python
class ToastOverlay(QWidget):
    """
    Overlays on the visualization canvas. Shows non-blocking
    toast notifications that auto-fade after 2–5 seconds.
    """
    # Severity levels: INFO, SUCCESS, WARN, ERROR
    # Toast types: IPC sent, IPC received, lock acquired, deadlock, error
    # Position: top-right of canvas
    # Max visible: 3 toasts at a time (oldest fades first)
```

### 10.2 Inline Feedback

- **Lock boxes**: Pulse animation on state change
- **IPC channel tube**: Red flash on error, green flash on success
- **Philosopher circles**: Scale animation on state change
- **Wait-for graph**: Highlight changing edges briefly

### 10.3 Response Time Expectations

| Operation | Expected latency | UX treatment |
|-----------|-----------------|--------------|
| Single IPC send | < 50 ms | Immediate packet animation |
| Lock acquire | < 100 ms | Instant state update |
| Burst start | < 200 ms | "Burst started" toast |
| Deadlock inject | < 1 s | Loading spinner + "Injecting..." |
| Container exec | < 2 s | Brief loading indicator |

For longer operations (burst, stress test), run in `QThread` / `asyncio` to keep UI responsive.

### 10.4 Error Handling

- **Container unreachable**: Red badge in status bar, disabled controls, "Container offline" toast
- **IPC send failure**: Red flash on channel, error toast with errno description
- **Lock timeout**: Yellow toast, lock box shows "TIMEOUT" state
- **Scenario failure**: Results dialog with failure reason + retry option

---

## 11. File Layout & New Files

### 11.1 New Files

```
dashboard/
├── interactive/
│   ├── __init__.py
│   ├── ipc_controls.py          # Control panel widgets for IPC tab
│   ├── sync_controls.py         # Control panel widgets for Sync tab
│   ├── philo_controls.py        # Control panel widgets for Philosophers tab
│   ├── control_panel.py         # Container widget: tab bar + dynamic controls
│   ├── toast_overlay.py         # Toast notification overlay widget
│   ├── scenario_sidebar.py      # Scenario mode instruction sidebar
│   └── scenario_engine.py       # Scenario lifecycle manager
├── backend/
│   ├── interactive_backend.py   # NEW: translates UI actions → LXD commands
│   └── event_bus.py             # NEW: in-process pub-sub event bus
├── scenarios/
│   ├── avoid_deadlock.json      # Scenario definition
│   ├── speed_feast.json
│   ├── fair_share.json
│   └── triage.json
├── ui/
│   ├── split_view.py            # NEW: split view layout manager
│   ├── dashboard_window.py      # MODIFIED: uses split_view instead of tabs
│   ├── ipc_visualizer.py        # MODIFIED: supports interactive state
│   ├── sync_visualizer.py       # MODIFIED: supports manual lock control
│   └── charts.py                # MODIFIED: supports comparison mode
└── main.py                      # MODIFIED: initializes InteractiveBackend

lib/
├── ipc/
│   ├── ipc_interactive.c        # NEW: interactive IPC helper binary
│   └── libinterync_ipc.h
└── sync/
    ├── sync_interactive.c       # NEW: interactive sync helper binary
    ├── philo_interactive.c      # NEW: interactive philosophers helper
    └── libinterync_sync.h
```

### 11.2 Modified Files

| File | Changes |
|------|---------|
| `dashboard/ui/dashboard_window.py` | Replace tab-based layout with split-pane; add mode toggle; integrate control panel |
| `dashboard/ui/ipc_visualizer.py` | Add click-to-send, inline stats, error flash, mechanism-specific colors |
| `dashboard/ui/sync_visualizer.py` | Add click-to-acquire/release, deadlock injection UI, step controls |
| `dashboard/main.py` | Initialize `InteractiveBackend` and `EventBus`, wire them to window |
| `Makefile` | Add `build-interactive` target with new C binaries, add `deploy-interactive` target |

---

## 12. Implementation Roadmap

### Phase 1 — Foundation (Session 1)
1. Create `event_bus.py` — the pub-sub core
2. Create `interactive_backend.py` — command translation layer
3. Create `split_view.py` — split pane layout
4. Modify `dashboard_window.py` — use split view, add mode toggle
5. Create C helper `ipc_interactive.c` — compile and test

### Phase 2 — IPC Interactive (Session 2)
1. Create `ipc_controls.py` — buttons, sliders, dropdowns for IPC tab
2. Modify `ipc_visualizer.py` — click-to-send, inline feedback, error handling
3. Create `toast_overlay.py` — notification system
4. Wire IPC controls → InteractiveBackend → LXD → canvas update

### Phase 3 — Sync Interactive (Session 3)
1. Create `sync_controls.py` — acquire/release/deadlock injection controls
2. Create C helper `sync_interactive.c` — compile and test
3. Modify `sync_visualizer.py` — click on locks, deadlock UI, step controls
4. Wire sync controls → InteractiveBackend → LXD → canvas update

### Phase 4 — Philosophers Interactive (Session 4)
1. Create `philo_controls.py` — philosopher controls
2. Create C helper `philo_interactive.c` — compile and test
3. Render round table with QPainter — clickable philosopher circles
4. Implement step-through simulation mode

### Phase 5 — Scenarios System (Session 5)
1. Create `scenario_engine.py` — scenario lifecycle manager
2. Create `scenario_sidebar.py` — instruction overlay
3. Write 4 scenario JSON files
4. Wire scenario engine to all tabs

### Phase 6 — Polish (Session 6)
1. Benchmark tab enhancements
2. Overview tab improvements
3. Thorough error handling
4. Performance optimization
5. Final testing with LXD containers
