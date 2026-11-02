"""Hardware probes use simulated Linux resources and never require a GPU."""
from types import SimpleNamespace

import pytest

from server.services import performance as module


@pytest.fixture
def linux(monkeypatch):
    files = {
        "/proc/meminfo": "MemTotal: 16777216 kB\nMemAvailable: 12582912 kB\n",
        "/proc/cpuinfo": "model name: Test CPU\nphysical id: 0\ncore id: 0\n\n"
                         "model name: Test CPU\nphysical id: 0\ncore id: 1\n",
        "/sys/fs/cgroup/memory.max": str(8 * module.GIB),
        "/sys/fs/cgroup/memory.current": str(3 * module.GIB),
        "/sys/fs/cgroup/cpu.max": "200000 100000",
    }
    monkeypatch.setattr(module, "os", SimpleNamespace(name="posix", cpu_count=lambda: 8,
                                                     sched_getaffinity=lambda _: {0, 1, 2, 3}))
    original_read = module.Path.read_text
    original_exists = module.Path.exists
    monkeypatch.setattr(module.Path, "read_text", lambda p, *a, **k: files[str(p).replace("\\", "/")] if str(p).replace("\\", "/") in files else original_read(p, *a, **k))
    monkeypatch.setattr(module.Path, "exists", lambda p: str(p).replace("\\", "/") in files or original_exists(p))
    def unavailable(*args):
        raise OSError("driver unavailable")
    monkeypatch.setattr(module, "_run", unavailable)
    return files


def test_linux_cgroup_and_cpu_only(linux):
    h = module.detect_hardware()
    assert (h.cpu, h.cores, h.threads, h.available_threads) == ("Test CPU", 2, 8, 2)
    assert (h.ram_total, h.ram_available) == (8 * module.GIB, 5 * module.GIB)
    assert h.disk_free > 0
    assert not h.acceleration_verified and not h.inference_gpu_used
    assert h.vram is None and h.detection_limits


@pytest.mark.parametrize("driver_ok", [True, False])
def test_nvidia_inventory_requires_cuda_confirmation(linux, monkeypatch, driver_ok):
    monkeypatch.setattr(module, "_run", lambda _: "GPU small, 2048, 1024\nGPU large, 8192, 6000")
    def count(pointer):
        pointer._obj.value = 2
        return 0
    monkeypatch.setattr(module.ctypes, "CDLL", lambda _: SimpleNamespace(cuInit=lambda _: 0 if driver_ok else 1,
                                                                       cuDeviceGetCount=count))
    h = module.detect_hardware()
    assert h.gpu == "GPU large" and h.vram == 8 * module.GIB
    assert h.vram_available == 6000 * 1024 ** 2
    assert h.acceleration_verified is driver_ok
    assert not h.inference_gpu_used  # Only Ollama can confirm actual use.


def test_unknown_memory_is_conservative_and_logged(linux, caplog):
    linux["/proc/meminfo"] = "invalid"
    h = module.detect_hardware()
    assert h.ram_available is None
    assert module.initial_profile(h) == "economic"
    assert "CPU/RAM parcial" in caplog.text


def test_transient_resources_do_not_invalidate_identity():
    h = module.Hardware(ram_total=8 * module.GIB)
    key = module.hardware_key(h)
    h.ram_available = module.GIB
    h.disk_free = module.GIB
    h.detection_limits.append("temporary error")
    assert module.hardware_key(h) == key
