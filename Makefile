# InterSync: IPC & Synchronization Platform
# Makefile for development (WSL2) and production (Linux VM)
# 
# Usage:
#   make help              - Show all commands
#   make setup             - Complete setup (one-time)
#   make dev               - Start development environment
#   make benchmark         - Run all benchmarks
#   make app               - Launch Python dashboard
#   make vm-ready          - Prepare for Linux VM deployment
#   make clean             - Clean all build artifacts

.PHONY: help setup dev benchmark app vm-ready clean detect-env \
        build-interactive deploy-interactive \
        build-spsc test-spsc test-spsc-tsan deploy-spsc

# ============================================================================
# CONFIGURATION
# ============================================================================

PROJECT_NAME := interync
PYTHON_VERSION := 3.9
PYTHON_VENV := ./venv
C_BUILD_DIR := ./build
CONTAINER_PREFIX := interync-lab
NUM_CONTAINERS := 3

# Detect environment
ifeq ($(OS),Windows_NT)
    DETECTED_OS := WSL2
    IS_WSL2 := true
else
    DETECTED_OS := $(shell uname -s)
    IS_WSL2 := false
endif

# ============================================================================
# TARGETS
# ============================================================================

help:
	@echo "======================================================"
	@echo "  InterSync: IPC & Synchronization Platform"
	@echo "======================================================"
	@echo ""
	@echo "SETUP (Run once):"
	@echo "  make setup              - Complete initial setup"
	@echo "  make detect-env         - Detect current environment"
	@echo ""
	@echo "DEVELOPMENT (WSL2):"
	@echo "  make dev                - Start dev environment"
	@echo "  make python-env         - Setup Python venv"
	@echo "  make lxd-init           - Initialize LXD"
	@echo "  make build-libs         - Compile C libraries"
	@echo ""
	@echo "RUNNING:"
	@echo "  make app                - Launch Python dashboard"
	@echo "  make benchmark          - Run all benchmarks"
	@echo "  make scenario-pc         - Run producer-consumer demo"
	@echo "  make scenario-rw         - Run readers-writers demo"
	@echo "  make scenario-dp         - Run dining philosophers demo"
	@echo ""
	@echo "DEPLOYMENT (Linux VM):"
	@echo "  make vm-ready           - Prepare for VM deployment"
	@echo "  make install            - Install as system package"
	@echo ""
	@echo "SPSC/MPMC Module 2:"
	@echo "  make build-spsc          - Compile SPSC + MPMC shared library"
	@echo "  make test-spsc           - Run SPSC/MPMC correctness tests"
	@echo "  make test-spsc-tsan      - Run tests with ThreadSanitizer"
	@echo "  make deploy-spsc         - Deploy spsc library to all containers"
	@echo ""
	@echo "CLEANUP:"
	@echo "  make clean              - Remove build artifacts"
	@echo "  make clean-containers   - Destroy LXD containers"
	@echo "  make clean-all          - Full cleanup (including venv)"
	@echo ""

# ============================================================================
# ENVIRONMENT DETECTION
# ============================================================================

detect-env:
	@echo "🔍 Detecting environment..."
	@echo "  OS: $(DETECTED_OS)"
ifeq ($(IS_WSL2),true)
	@echo "  ✓ Running on WSL2"
	@echo "  ℹ️  Note: Development mode. Switch to Linux VM for final benchmarking."
else
	@echo "  ✓ Running on Linux ($(DETECTED_OS))"
	@echo "  ℹ️  Ready for production benchmarking."
endif
	@echo ""
	@which lxd > /dev/null && echo "  ✓ LXD found" || echo "  ✗ LXD not found (run 'make lxd-init')"
	@which python3 > /dev/null && echo "  ✓ Python3 found" || echo "  ✗ Python3 not found"
	@echo ""

# ============================================================================
# SETUP & INITIALIZATION
# ============================================================================

setup: detect-env lxd-init python-env build-libs containers-create
	@echo "✅ Setup complete!"
	@echo ""
	@echo "Next steps:"
	@echo "  1. Run benchmarks:  make benchmark"
	@echo "  2. Launch app:      make app"
	@echo "  3. View help:       make help"
	@echo ""

python-env:
	@echo "📦 Setting up Python virtual environment..."
	@if [ ! -d "$(PYTHON_VENV)" ]; then \
		python3 -m venv $(PYTHON_VENV); \
		echo "  ✓ Virtual environment created"; \
	fi
	@. $(PYTHON_VENV)/bin/activate && \
		pip install --upgrade pip && \
		pip install pylxd pyqt6 matplotlib pyqtgraph pandas numpy && \
		echo "  ✓ Dependencies installed"

lxd-init:
	@echo "🐳 Initializing LXD..."
	@if ! which lxd > /dev/null; then \
		echo "  Installing LXD..."; \
		sudo snap install lxd; \
	fi
	@if ! lxc storage list | grep -q "default"; then \
		echo "  Initializing LXD (interactive setup)..."; \
		sudo lxd init; \
	else \
		echo "  ✓ LXD already initialized"; \
	fi

build-libs:
	@echo "🔨 Building C libraries..."
	@mkdir -p $(C_BUILD_DIR)
	@echo "  Compiling IPC library (each source separately)..."
	@gcc -Wall -Wextra -fPIC -c lib/ipc/ipc_factory.c    -Ilib/ipc  -o $(C_BUILD_DIR)/ipc_factory.o
	@gcc -Wall -Wextra -fPIC -c lib/ipc/pipe_channel.c   -Ilib/ipc  -o $(C_BUILD_DIR)/pipe_channel.o
	@gcc -Wall -Wextra -fPIC -c lib/ipc/queue_channel.c  -Ilib/ipc  -o $(C_BUILD_DIR)/queue_channel.o
	@gcc -Wall -Wextra -fPIC -c lib/ipc/socket_channel.c -Ilib/ipc  -o $(C_BUILD_DIR)/socket_channel.o
	@gcc -Wall -Wextra -fPIC -c lib/ipc/shm_channel.c    -Ilib/ipc  -o $(C_BUILD_DIR)/shm_channel.o
	@gcc -shared -o $(C_BUILD_DIR)/libinterync-ipc.so \
		$(C_BUILD_DIR)/ipc_factory.o \
		$(C_BUILD_DIR)/pipe_channel.o \
		$(C_BUILD_DIR)/queue_channel.o \
		$(C_BUILD_DIR)/socket_channel.o \
		$(C_BUILD_DIR)/shm_channel.o \
		-lrt -pthread
	@echo "    ✓ libinterync-ipc.so built"
	@echo "  Compiling Synchronization library (each source separately)..."
	@gcc -Wall -Wextra -fPIC -c lib/sync/sync_factory.c   -Ilib/sync -o $(C_BUILD_DIR)/sync_factory.o   -pthread
	@gcc -Wall -Wextra -fPIC -c lib/sync/mutex_lock.c     -Ilib/sync -o $(C_BUILD_DIR)/mutex_lock.o     -pthread
	@gcc -Wall -Wextra -fPIC -c lib/sync/semaphore_lock.c -Ilib/sync -o $(C_BUILD_DIR)/semaphore_lock.o -pthread
	@gcc -Wall -Wextra -fPIC -c lib/sync/condvar_lock.c   -Ilib/sync -o $(C_BUILD_DIR)/condvar_lock.o   -pthread
	@gcc -Wall -Wextra -fPIC -c lib/sync/rwlock.c         -Ilib/sync -o $(C_BUILD_DIR)/rwlock.o         -pthread
	@gcc -shared -pthread -o $(C_BUILD_DIR)/libinterync-sync.so \
		$(C_BUILD_DIR)/sync_factory.o \
		$(C_BUILD_DIR)/mutex_lock.o \
		$(C_BUILD_DIR)/semaphore_lock.o \
		$(C_BUILD_DIR)/condvar_lock.o \
		$(C_BUILD_DIR)/rwlock.o \
		-pthread
	@echo "    ✓ libinterync-sync.so built"

test-libs: build-libs
	@echo "🧪 Compiling and running C library smoke tests..."
	@gcc -Wall -Wextra \
		-o $(C_BUILD_DIR)/test_ipc_sync \
		lib/ipc/test_ipc_sync.c \
		-Ilib/ipc -Ilib/sync \
		-L$(C_BUILD_DIR) \
		-linterync-ipc -linterync-sync \
		-Wl,-rpath,$(C_BUILD_DIR) \
		-lrt -pthread
	@LD_LIBRARY_PATH=$(C_BUILD_DIR) $(C_BUILD_DIR)/test_ipc_sync

build-interactive: build-libs
	@echo "🔧 Building interactive helper binaries..."
	@gcc -Wall -O2 \
		-o $(C_BUILD_DIR)/ipc_interactive \
		lib/ipc/ipc_interactive.c \
		-Ilib/ipc \
		-L$(C_BUILD_DIR) -linterync-ipc \
		-lrt -pthread \
		-Wl,-rpath,/opt/interync/lib
	@echo "    ✓ ipc_interactive built"
	@gcc -Wall -O2 \
		-o $(C_BUILD_DIR)/sync_interactive \
		lib/sync/sync_interactive.c \
		-Ilib/sync \
		-L$(C_BUILD_DIR) -linterync-sync \
		-pthread \
		-Wl,-rpath,/opt/interync/lib
	@echo "    ✓ sync_interactive built"
	@gcc -Wall -O2 \
		-o $(C_BUILD_DIR)/philo_interactive \
		lib/sync/philo_interactive.c \
		-Ilib/sync \
		-L$(C_BUILD_DIR) -linterync-sync \
		-pthread \
		-Wl,-rpath,/opt/interync/lib
	@echo "    ✓ philo_interactive built"
	@echo "✅ Interactive binaries ready in $(C_BUILD_DIR)/"

# ============================================================================
# SPSC / MPMC (Module 2)
# ============================================================================

SPSC_SRC := lib/spsc/spsc_ring_buffer.c lib/spsc/mpmc_queue.c lib/spsc/spsc_trace.c lib/spsc/spsc_io_uring.c
SPSC_INC := -Ilib/spsc

build-spsc:
	@echo "Building SPSC/MPMC lock-free library..."
	@mkdir -p $(C_BUILD_DIR)
	@gcc -Wall -Wextra -O2 -fPIC -std=c11 -pthread $(SPSC_INC) \
		-c lib/spsc/spsc_ring_buffer.c -o $(C_BUILD_DIR)/spsc_ring_buffer.o
	@gcc -Wall -Wextra -O2 -fPIC -std=c11 -pthread $(SPSC_INC) \
		-c lib/spsc/mpmc_queue.c      -o $(C_BUILD_DIR)/mpmc_queue.o
	@gcc -Wall -Wextra -O2 -fPIC -std=c11 -pthread $(SPSC_INC) \
		-c lib/spsc/spsc_trace.c      -o $(C_BUILD_DIR)/spsc_trace.o
	@gcc -Wall -Wextra -O2 -fPIC -std=c11 -pthread $(SPSC_INC) \
		-c lib/spsc/spsc_io_uring.c   -o $(C_BUILD_DIR)/spsc_io_uring.o
	@gcc -shared -pthread -o $(C_BUILD_DIR)/libinterync-spsc.so \
		$(C_BUILD_DIR)/spsc_ring_buffer.o \
		$(C_BUILD_DIR)/mpmc_queue.o \
		$(C_BUILD_DIR)/spsc_trace.o \
		$(C_BUILD_DIR)/spsc_io_uring.o
	@echo "    OK libinterync-spsc.so built"

test-spsc: build-spsc
	@echo "Building and running SPSC/MPMC tests..."
	@gcc -Wall -Wextra -O2 -std=c11 -pthread $(SPSC_INC) \
		lib/spsc/spsc_ring_buffer.c \
		lib/spsc/mpmc_queue.c \
		lib/spsc/spsc_trace.c \
		lib/spsc/spsc_io_uring.c \
		lib/spsc/test_spsc.c \
		-o $(C_BUILD_DIR)/test_spsc
	@$(C_BUILD_DIR)/test_spsc

test-spsc-tsan: 
	@echo "Building SPSC/MPMC tests with ThreadSanitizer..."
	@gcc -Wall -Wextra -O1 -std=c11 -pthread -fsanitize=thread -g $(SPSC_INC) \
		lib/spsc/spsc_ring_buffer.c \
		lib/spsc/mpmc_queue.c \
		lib/spsc/spsc_trace.c \
		lib/spsc/spsc_io_uring.c \
		lib/spsc/test_spsc.c \
		-o $(C_BUILD_DIR)/test_spsc_tsan
	@$(C_BUILD_DIR)/test_spsc_tsan

bench-spsc: build-spsc
	@echo "Building SPSC/MPMC benchmark runner..."
	@gcc -Wall -Wextra -O3 -std=c11 -pthread $(SPSC_INC) \
		lib/spsc/spsc_ring_buffer.c \
		lib/spsc/mpmc_queue.c \
		lib/spsc/spsc_trace.c \
		lib/spsc/spsc_io_uring.c \
		lib/spsc/bench_spsc.c \
		-o $(C_BUILD_DIR)/bench_spsc
	@echo "    OK bench_spsc built"

deploy-spsc: build-spsc
	@echo "Deploying SPSC library to LXD containers..."
	@for i in 1 2 3; do \
		CNAME="$(CONTAINER_PREFIX)-$$i"; \
		echo "  -> $$CNAME"; \
		lxc exec $$CNAME -- mkdir -p /opt/interync/lib /opt/interync/include 2>/dev/null || true; \
		lxc file push $(C_BUILD_DIR)/libinterync-spsc.so $$CNAME/opt/interync/lib/libinterync-spsc.so; \
		lxc file push lib/spsc/libinterync_spsc.h $$CNAME/opt/interync/include/libinterync_spsc.h; \
		echo "    OK $$CNAME deployed"; \
	done
	@echo "OK SPSC/MPMC library deployed"

deploy-interactive: build-interactive
	@echo "🚀 Deploying interactive binaries to LXD containers..."
	@for i in 1 2 3; do \
		CNAME="$(CONTAINER_PREFIX)-$$i"; \
		echo "  → $$CNAME"; \
		lxc exec $$CNAME -- mkdir -p /opt/interync/bin /opt/interync/lib 2>/dev/null || true; \
		lxc file push $(C_BUILD_DIR)/ipc_interactive     $$CNAME/opt/interync/bin/ipc_interactive; \
		lxc file push $(C_BUILD_DIR)/sync_interactive    $$CNAME/opt/interync/bin/sync_interactive; \
		lxc file push $(C_BUILD_DIR)/philo_interactive   $$CNAME/opt/interync/bin/philo_interactive; \
		lxc file push $(C_BUILD_DIR)/libinterync-ipc.so  $$CNAME/opt/interync/lib/libinterync-ipc.so; \
		lxc file push $(C_BUILD_DIR)/libinterync-sync.so $$CNAME/opt/interync/lib/libinterync-sync.so; \
		lxc exec $$CNAME -- chmod +x /opt/interync/bin/ipc_interactive; \
		lxc exec $$CNAME -- chmod +x /opt/interync/bin/sync_interactive; \
		lxc exec $$CNAME -- chmod +x /opt/interync/bin/philo_interactive; \
		echo "    ✓ $$CNAME deployed"; \
	done
	@echo "✅ Interactive binaries deployed to all containers"

containers-create:
	@echo "📦 Creating LXC containers..."
	@for i in 1 2 3; do \
		CONTAINER_NAME="$(CONTAINER_PREFIX)-$$i"; \
		if ! lxc list | grep -q "$$CONTAINER_NAME"; then \
			echo "  Creating $$CONTAINER_NAME..."; \
			lxc launch ubuntu:22.04 $$CONTAINER_NAME; \
			lxc exec $$CONTAINER_NAME -- apt-get update; \
			lxc exec $$CONTAINER_NAME -- apt-get install -y build-essential python3 python3-pip; \
			echo "    ✓ $$CONTAINER_NAME ready"; \
		else \
			echo "    ✓ $$CONTAINER_NAME already exists"; \
		fi; \
	done

# ============================================================================
# DEVELOPMENT WORKFLOW
# ============================================================================

dev: python-env
	@echo ""
	@echo "🚀 Development environment ready!"
	@echo ""
	@echo ". $(PYTHON_VENV)/bin/activate  # Activate Python venv"
	@echo "make app                       # Launch dashboard"
	@echo "make benchmark                 # Run benchmarks"
	@echo ""
	@. $(PYTHON_VENV)/bin/activate && echo "✓ Python venv activated"

# ============================================================================
# BENCHMARKING
# ============================================================================

benchmark: build-libs
	@echo "⏱️  Running comprehensive benchmarks..."
	@echo ""
	@. $(PYTHON_VENV)/bin/activate && \
		python3 benchmarks/benchmark_suite.py
	@echo ""
	@echo "✅ Benchmarks complete! Results in ./results/"

scenario-pc:
	@echo "📊 Running Producer-Consumer benchmark..."
	@. $(PYTHON_VENV)/bin/activate && \
		python3 benchmarks/scenarios/producer_consumer.py

scenario-rw:
	@echo "📖 Running Readers-Writers benchmark..."
	@. $(PYTHON_VENV)/bin/activate && \
		python3 benchmarks/scenarios/readers_writers.py

scenario-dp:
	@echo "🍽️  Running Dining Philosophers deadlock demo..."
	@. $(PYTHON_VENV)/bin/activate && \
		python3 benchmarks/scenarios/dining_philosophers.py

# ============================================================================
# APPLICATION
# ============================================================================

app:
	@echo "🎨 Launching InterSync Dashboard..."
	@. $(PYTHON_VENV)/bin/activate && \
		python3 dashboard/main.py

# ============================================================================
# DEPLOYMENT (Linux VM)
# ============================================================================

vm-ready: clean build-libs
	@echo "📋 Preparing for Linux VM deployment..."
	@echo ""
	@echo "1. Update system:"
	@echo "   sudo apt-get update && sudo apt-get upgrade"
	@echo ""
	@echo "2. Install LXD:"
	@echo "   sudo snap install lxd"
	@echo "   lxd init"
	@echo ""
	@echo "3. Install Python dependencies:"
	@echo "   pip install -r requirements.txt"
	@echo ""
	@echo "4. Run benchmarks (in vm-clean environment):"
	@echo "   make benchmark"
	@echo ""
	@echo "Hardware specs to record:"
	@lsb_release -a 2>/dev/null || uname -a
	@echo "CPU cores: $$(nproc)"
	@echo "RAM: $$(free -h | grep Mem | awk '{print $$2}')"
	@echo ""

install:
	@echo "📦 Installing InterSync system-wide..."
	@mkdir -p ~/.local/bin
	@cp dashboard/main.py ~/.local/bin/interync-dashboard
	@chmod +x ~/.local/bin/interync-dashboard
	@echo "✓ Installed to ~/.local/bin/interync-dashboard"
	@echo "Add to PATH: export PATH=~/.local/bin:$$PATH"

# ============================================================================
# CONTAINER MANAGEMENT
# ============================================================================

containers-list:
	@echo "📋 Active InterSync containers:"
	@lxc list | grep "$(CONTAINER_PREFIX)"

containers-stop:
	@echo "⏸️  Stopping all containers..."
	@for i in 1 2 3; do \
		lxc stop "$(CONTAINER_PREFIX)-$$i" 2>/dev/null || true; \
	done
	@echo "✓ Containers stopped"

containers-start:
	@echo "▶️  Starting all containers..."
	@for i in 1 2 3; do \
		lxc start "$(CONTAINER_PREFIX)-$$i" 2>/dev/null || true; \
	done
	@echo "✓ Containers started"

containers-shell:
	@echo "🐚 Opening shell in $(CONTAINER_PREFIX)-1..."
	@lxc exec "$(CONTAINER_PREFIX)-1" -- /bin/bash

# ============================================================================
# CLEANUP
# ============================================================================

clean:
	@echo "🗑️  Cleaning build artifacts..."
	@rm -rf $(C_BUILD_DIR)
	@rm -rf results/
	@rm -rf benchmarks/results
	@rm -rf dashboard/__pycache__
	@find . -name "*.pyc" -delete
	@find . -name "*.o" -delete
	@find . -name ".DS_Store" -delete
	@echo "✓ Clean complete"

clean-containers: containers-stop
	@echo "🗑️  Destroying all containers..."
	@for i in 1 2 3; do \
		lxc delete "$(CONTAINER_PREFIX)-$$i" --force 2>/dev/null || true; \
	done
	@echo "✓ Containers destroyed"

clean-all: clean clean-containers
	@echo "🗑️  Removing Python virtual environment..."
	@rm -rf $(PYTHON_VENV)
	@echo "✓ Full cleanup complete"

# ============================================================================
# DIRECTORY STRUCTURE (For Reference)
# ============================================================================

# interync/
# ├── Makefile                 (this file)
# ├── requirements.txt         (Python dependencies)
# ├── lib/
# │   ├── ipc/                (IPC library source - Module 1)
# │   │   ├── pipe.c
# │   │   ├── queue.c
# │   │   ├── socket.c
# │   │   └── libinterync.h
# │   └── sync/               (Sync library source - Module 2)
# │       ├── mutex.c
# │       ├── semaphore.c
# │       ├── condition_var.c
# │       └── sync.h
# ├── benchmarks/             (Benchmark code - Module 3)
# │   ├── benchmark_suite.py
# │   └── scenarios/
# │       ├── producer_consumer.py
# │       ├── readers_writers.py
# │       └── dining_philosophers.py
# ├── dashboard/              (Python app - Module 4)
# │   ├── main.py
# │   ├── ui/
# │   │   ├── dashboard.py
# │   │   ├── ipc_visualizer.py
# │   │   ├── sync_visualizer.py
# │   │   └── charts.py
# │   └── backend/
# │       ├── container_manager.py
# │       ├── benchmark_runner.py
# │       └── metrics_collector.py
# ├── docs/                   (Documentation)
# │   ├── SETUP.md
# │   ├── API.md
# │   └── ARCHITECTURE.md
# └── results/                (Benchmark outputs)
#     ├── latency.csv
#     ├── throughput.csv
#     └── report.html
