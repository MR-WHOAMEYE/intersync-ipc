import ctypes
import os
import sys

# Load the shared library
lib_path = os.path.join(os.path.dirname(__file__), '..', '..', 'build', 'libinterync-spsc.so')

# Fallback to local build dir if needed
if not os.path.exists(lib_path):
    lib_path = os.path.join(os.path.dirname(__file__), '..', '..', 'build', 'libinterync-spsc.so')

try:
    _lib = ctypes.CDLL(lib_path)
except OSError:
    print(f"Warning: Could not load {lib_path}. Please run 'make build-spsc'.", file=sys.stderr)
    _lib = None

if _lib:
    # spsc_ring_buffer_t* spsc_create(const char* name, uint64_t capacity, uint32_t slot_size, uint32_t flags);
    _lib.spsc_create.argtypes = [ctypes.c_char_p, ctypes.c_uint64, ctypes.c_uint32, ctypes.c_uint32]
    _lib.spsc_create.restype = ctypes.c_void_p

    # void spsc_destroy(spsc_ring_buffer_t* rb);
    _lib.spsc_destroy.argtypes = [ctypes.c_void_p]
    _lib.spsc_destroy.restype = None

    # int spsc_push(spsc_ring_buffer_t* rb, const void* data, size_t size);
    _lib.spsc_push.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
    _lib.spsc_push.restype = ctypes.c_int

    # int spsc_pop(spsc_ring_buffer_t* rb, void* buffer, size_t* out_size);
    _lib.spsc_pop.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
    _lib.spsc_pop.restype = ctypes.c_int

    # mpmc_queue_t* mpmc_create(const char* name, uint64_t capacity, uint32_t slot_size, uint32_t flags);
    _lib.mpmc_create.argtypes = [ctypes.c_char_p, ctypes.c_uint64, ctypes.c_uint32, ctypes.c_uint32]
    _lib.mpmc_create.restype = ctypes.c_void_p

    # void mpmc_destroy(mpmc_queue_t* q);
    _lib.mpmc_destroy.argtypes = [ctypes.c_void_p]
    _lib.mpmc_destroy.restype = None

    # int mpmc_enqueue(mpmc_queue_t* q, const void* data, size_t size);
    _lib.mpmc_enqueue.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
    _lib.mpmc_enqueue.restype = ctypes.c_int

    # int mpmc_dequeue(mpmc_queue_t* q, void* buffer, size_t* out_size);
    _lib.mpmc_dequeue.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
    _lib.mpmc_dequeue.restype = ctypes.c_int


class SPSCRingBuffer:
    def __init__(self, name: str, capacity: int = 1024, slot_size: int = 128, flags: int = 0):
        if not _lib:
            raise RuntimeError("libinterync-spsc.so not loaded")
        self._ptr = _lib.spsc_create(name.encode('utf-8'), capacity, slot_size, flags)
        if not self._ptr:
            raise MemoryError("Failed to create SPSCRingBuffer")
        self.capacity = capacity
        self.slot_size = slot_size

    def __del__(self):
        if hasattr(self, '_ptr') and self._ptr and _lib:
            _lib.spsc_destroy(self._ptr)
            self._ptr = None

    def push(self, data: bytes) -> bool:
        if len(data) > self.slot_size:
            raise ValueError(f"Data size {len(data)} exceeds slot_size {self.slot_size}")
        rc = _lib.spsc_push(self._ptr, data, len(data))
        return rc == 0

    def pop(self) -> bytes:
        buf = ctypes.create_string_buffer(self.slot_size)
        out_size = ctypes.c_size_t(0)
        rc = _lib.spsc_pop(self._ptr, buf, ctypes.byref(out_size))
        if rc < 0:
            return None
        return buf.raw[:out_size.value]


class MPMCQueue:
    def __init__(self, name: str, capacity: int = 1024, slot_size: int = 128, flags: int = 0):
        if not _lib:
            raise RuntimeError("libinterync-spsc.so not loaded")
        self._ptr = _lib.mpmc_create(name.encode('utf-8'), capacity, slot_size, flags)
        if not self._ptr:
            raise MemoryError("Failed to create MPMCQueue")
        self.capacity = capacity
        self.slot_size = slot_size

    def __del__(self):
        if hasattr(self, '_ptr') and self._ptr and _lib:
            _lib.mpmc_destroy(self._ptr)
            self._ptr = None

    def enqueue(self, data: bytes) -> bool:
        if len(data) > self.slot_size:
            raise ValueError(f"Data size {len(data)} exceeds slot_size {self.slot_size}")
        rc = _lib.mpmc_enqueue(self._ptr, data, len(data))
        return rc == 0

    def dequeue(self) -> bytes:
        buf = ctypes.create_string_buffer(self.slot_size)
        out_size = ctypes.c_size_t(0)
        rc = _lib.mpmc_dequeue(self._ptr, buf, ctypes.byref(out_size))
        if rc < 0:
            return None
        return buf.raw[:out_size.value]
