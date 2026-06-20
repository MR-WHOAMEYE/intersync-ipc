import time
import json
import subprocess
from PyQt6.QtCore import QThread
from dashboard.backend.event_bus import EventBus
import os

class SpscWorker(QThread):
    def __init__(self, capacity: int, slot_size: int, count: int, bus: EventBus, parent=None):
        super().__init__(parent)
        self.capacity = capacity
        self.slot_size = slot_size
        self.count = count
        self.bus = bus
        self._running = True
        self._proc = None

    def stop(self):
        self._running = False
        if self._proc:
            self._proc.terminate()

    def run(self):
        # We invoke the C benchmark binary via WSL. We need to modify the C binary
        # to output a live trace of operations if we want real-time visualization.
        # For now, we will simulate the events in Python so the UI can animate,
        # but in a true Windows/WSL separation, we'd stream stdout from WSL here.
        
        # Simulate pushing and popping for visualizer
        for i in range(self.count):
            if not self._running:
                break
            
            # Push
            self.bus.spsc_pushed.emit({
                "ring_name": "dash_spsc",
                "slot_idx": i % self.capacity,
                "size": self.slot_size,
                "overflow": False,
                "latency_ns": 43.0
            })
            time.sleep(0.01) # slow down so we can see it
            
            # Pop
            self.bus.spsc_popped.emit({
                "ring_name": "dash_spsc",
                "slot_idx": i % self.capacity,
                "size": self.slot_size,
                "overflow": False,
                "latency_ns": 43.0
            })
            time.sleep(0.01)

class MpmcWorker(QThread):
    def __init__(self, capacity: int, slot_size: int, count: int, producers: int, consumers: int, bus: EventBus, parent=None):
        super().__init__(parent)
        self.capacity = capacity
        self.slot_size = slot_size
        self.count = count
        self.num_producers = producers
        self.num_consumers = consumers
        self.bus = bus
        self._running = True

    def stop(self):
        self._running = False

    def run(self):
        import random
        # Simulate MPMC for the visualizer
        for i in range(self.count):
            if not self._running:
                break
            
            p = random.randint(0, self.num_producers - 1)
            self.bus.mpmc_enqueued.emit({
                "ring_name": "dash_mpmc",
                "producer_id": p,
                "size": self.slot_size,
                "latency_ns": 45.0
            })
            time.sleep(0.005)
            
            c = random.randint(0, self.num_consumers - 1)
            self.bus.mpmc_dequeued.emit({
                "ring_name": "dash_mpmc",
                "consumer_id": c,
                "size": self.slot_size,
                "latency_ns": 45.0
            })
            time.sleep(0.005)
