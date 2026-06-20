import time
from collections import deque
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QSplitter
from PyQt6.QtCore import Qt, QTimer, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush

import pyqtgraph as pg
from dashboard.backend.event_bus import EventBus

class RingBufferCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(150)
        self.capacity = 16
        self.slots = ["EMPTY"] * self.capacity
        self.write_idx = 0
        self.read_idx = 0

    def update_state(self, w_idx, r_idx, capacity):
        if capacity != self.capacity:
            self.capacity = capacity
            self.slots = ["EMPTY"] * self.capacity
        self.write_idx = w_idx
        self.read_idx = r_idx
        
        # Simple heuristic to color filled slots
        # In a real system, the slots state is explicit. Here we approximate for visuals.
        for i in range(self.capacity):
            self.slots[i] = "EMPTY"
        
        c = self.read_idx
        while c != self.write_idx:
            idx = c % self.capacity
            self.slots[idx] = "FILLED"
            c += 1

        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        painter.fillRect(self.rect(), QColor("#1e1e1e"))
        
        w = self.width()
        h = self.height()
        
        # Draw slots
        slot_w = min(40, (w - 20) / max(1, self.capacity))
        slot_h = 60
        start_x = 10
        start_y = (h - slot_h) // 2
        
        for i in range(self.capacity):
            x = start_x + i * slot_w
            rect = QRectF(x, start_y, slot_w - 2, slot_h)
            
            if self.slots[i] == "FILLED":
                painter.setBrush(QColor("#00bcd4")) # Teal
            else:
                painter.setBrush(QColor("#333333"))
                
            pen = QPen(QColor("#555555"), 1)
            
            # Highlight cursors
            if i == self.write_idx % self.capacity:
                pen = QPen(QColor("#ff9800"), 3) # Orange write cursor
            elif i == self.read_idx % self.capacity:
                pen = QPen(QColor("#9c27b0"), 3) # Purple read cursor
                
            painter.setPen(pen)
            painter.drawRect(rect)
            
        # Draw labels
        painter.setPen(QColor("#ffffff"))
        painter.drawText(10, start_y + slot_h + 20, f"Capacity: {self.capacity}")
        painter.drawText(10, start_y + slot_h + 35, f"Write Cursor: {self.write_idx}")
        painter.drawText(10, start_y + slot_h + 50, f"Read Cursor: {self.read_idx}")


class SpscVisualizer(QWidget):
    def __init__(self, bus: EventBus, parent=None):
        super().__init__(parent)
        self.bus = bus
        self.setup_ui()
        
        self.latencies = deque(maxlen=1000)
        self.throughput_data = deque(maxlen=100)
        self.last_tp_time = time.time()
        self.msg_count = 0
        
        # State tracking for the canvas
        self.w_idx = 0
        self.r_idx = 0
        self.cap = 16
        
        self.bus.spsc_pushed.connect(self.on_push)
        self.bus.spsc_popped.connect(self.on_pop)
        self.bus.mpmc_enqueued.connect(self.on_push)
        self.bus.mpmc_dequeued.connect(self.on_pop)
        
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_plots)
        self.timer.start(100)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        splitter = QSplitter(Qt.Orientation.Vertical)
        
        self.canvas = RingBufferCanvas()
        splitter.addWidget(self.canvas)
        
        # Plots
        self.plot_widget = pg.GraphicsLayoutWidget()
        splitter.addWidget(self.plot_widget)
        
        # Latency Histogram
        self.p1 = self.plot_widget.addPlot(title="Latency Distribution (ns)")
        self.hist_curve = self.p1.plot(stepMode="center", fillLevel=0, brush=(0,188,212,150))
        
        self.plot_widget.nextRow()
        
        # Throughput
        self.p2 = self.plot_widget.addPlot(title="Throughput (msgs/sec)")
        self.tp_curve = self.p2.plot(pen=pg.mkPen(color=(156,39,176), width=2))
        
        layout.addWidget(splitter)

    def on_push(self, payload):
        self.latencies.append(payload.get("latency_ns", 0))
        self.msg_count += 1
        self.w_idx += 1
        self.canvas.update_state(self.w_idx, self.r_idx, self.cap)

    def on_pop(self, payload):
        self.latencies.append(payload.get("latency_ns", 0))
        self.r_idx += 1
        self.canvas.update_state(self.w_idx, self.r_idx, self.cap)

    def update_plots(self):
        # Update Histogram
        if len(self.latencies) > 2:
            import numpy as np
            y, x = np.histogram(list(self.latencies), bins=20)
            self.hist_curve.setData(x, y)
            
        # Update Throughput
        now = time.time()
        dt = now - self.last_tp_time
        if dt > 0.5:
            tp = self.msg_count / dt
            self.throughput_data.append(tp)
            self.tp_curve.setData(list(self.throughput_data))
            self.msg_count = 0
            self.last_tp_time = now
