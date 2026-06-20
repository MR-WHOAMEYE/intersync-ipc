from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QGroupBox, QLabel, QSpinBox, 
    QPushButton, QHBoxLayout, QRadioButton, QButtonGroup
)
from PyQt6.QtCore import pyqtSignal

class SpscControlPanel(QWidget):
    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Mode Selection
        mode_group = QGroupBox("Mode")
        mode_layout = QHBoxLayout(mode_group)
        self.btn_spsc = QRadioButton("SPSC (Single)")
        self.btn_mpmc = QRadioButton("MPMC (Multi)")
        self.btn_spsc.setChecked(True)
        mode_layout.addWidget(self.btn_spsc)
        mode_layout.addWidget(self.btn_mpmc)
        layout.addWidget(mode_group)
        
        self.mode_bg = QButtonGroup()
        self.mode_bg.addButton(self.btn_spsc, 0)
        self.mode_bg.addButton(self.btn_mpmc, 1)
        self.mode_bg.buttonClicked.connect(self.toggle_mode)
        
        # Configuration
        cfg_group = QGroupBox("Configuration")
        cfg_layout = QVBoxLayout(cfg_group)
        
        # Capacity
        h1 = QHBoxLayout()
        h1.addWidget(QLabel("Capacity:"))
        self.spin_cap = QSpinBox()
        self.spin_cap.setRange(4, 65536)
        self.spin_cap.setValue(16)
        h1.addWidget(self.spin_cap)
        cfg_layout.addLayout(h1)
        
        # Slot Size
        h2 = QHBoxLayout()
        h2.addWidget(QLabel("Slot Size:"))
        self.spin_slot = QSpinBox()
        self.spin_slot.setRange(16, 4096)
        self.spin_slot.setValue(128)
        h2.addWidget(self.spin_slot)
        cfg_layout.addLayout(h2)
        
        # Producers/Consumers (MPMC only)
        self.h_p = QHBoxLayout()
        self.h_p.addWidget(QLabel("Producers:"))
        self.spin_p = QSpinBox()
        self.spin_p.setRange(1, 16)
        self.spin_p.setValue(2)
        self.h_p.addWidget(self.spin_p)
        cfg_layout.addLayout(self.h_p)
        
        self.h_c = QHBoxLayout()
        self.h_c.addWidget(QLabel("Consumers:"))
        self.spin_c = QSpinBox()
        self.spin_c.setRange(1, 16)
        self.spin_c.setValue(2)
        self.h_c.addWidget(self.spin_c)
        cfg_layout.addLayout(self.h_c)
        
        # Count
        h3 = QHBoxLayout()
        h3.addWidget(QLabel("Burst Count:"))
        self.spin_count = QSpinBox()
        self.spin_count.setRange(1, 1000000)
        self.spin_count.setValue(500)
        h3.addWidget(self.spin_count)
        cfg_layout.addLayout(h3)
        
        layout.addWidget(cfg_group)
        
        # Actions
        self.btn_start = QPushButton("Start Burst")
        self.btn_start.clicked.connect(self.start_burst)
        layout.addWidget(self.btn_start)
        
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.clicked.connect(self.stop_burst)
        layout.addWidget(self.btn_stop)
        
        layout.addStretch()
        self.toggle_mode()

    def toggle_mode(self):
        is_mpmc = self.btn_mpmc.isChecked()
        self.spin_p.setEnabled(is_mpmc)
        self.spin_c.setEnabled(is_mpmc)

    def start_burst(self):
        cap = self.spin_cap.value()
        slot = self.spin_slot.value()
        count = self.spin_count.value()
        
        if self.btn_spsc.isChecked():
            self.backend.start_spsc_burst(cap, slot, count)
        else:
            p = self.spin_p.value()
            c = self.spin_c.value()
            self.backend.start_mpmc_burst(cap, slot, count, p, c)

    def stop_burst(self):
        self.backend.stop_spsc_burst()
