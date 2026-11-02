"""Sample local Ollama processes without optional packages (Windows and Linux)."""
from __future__ import annotations

import ctypes
import os
import threading
import time
from pathlib import Path


def ollama_processes() -> dict[int, tuple[int, float]]:
    """PID -> (resident bytes, cumulative CPU seconds); inaccessible PIDs are omitted."""
    result = {}
    if os.name == "nt":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(ctypes.c_ulonglong)] * 4
        class Memory(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (n, ctypes.c_size_t) for n in ("peak", "working", "quota_peak", "quota", "nonpaged_peak", "nonpaged", "page", "page_peak")]
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Memory), wintypes.DWORD]
        pids = (wintypes.DWORD * 8192)()
        needed = wintypes.DWORD()
        if not psapi.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(needed)):
            return result
        for pid in pids[:needed.value // ctypes.sizeof(wintypes.DWORD)]:
            handle = kernel.OpenProcess(0x410, False, pid)
            if not handle:
                continue
            try:
                name, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
                if not kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)):
                    continue
                # Recent Ollama versions launch lib/ollama/llama-server.exe.
                if "ollama" not in name.value.lower():
                    continue
                counters = Memory()
                counters.cb = ctypes.sizeof(counters)
                times = [ctypes.c_ulonglong() for _ in range(4)]
                if (psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
                        and kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times))):
                    result[pid] = counters.working, (times[2].value + times[3].value) / 1e7
            finally:
                kernel.CloseHandle(handle)
    elif Path("/proc").is_dir():
        for process in Path("/proc").iterdir():
            if not process.name.isdigit():
                continue
            try:
                if ("ollama" not in (process / "comm").read_text().lower()
                        and "ollama" not in str((process / "exe").readlink()).lower()):
                    continue
                fields = (process / "stat").read_text().rsplit(")", 1)[1].split()
                rss = int((process / "statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
                cpu = (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
                result[int(process.name)] = rss, cpu
            except (OSError, ValueError, IndexError):
                continue
    return result


class ResourceSampler:
    """Sampled RSS peak, not exact allocation peak; CPU normalized to all logical cores."""
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.stop = threading.Event()
        self.peak = None
        self.cpu_seconds = 0.0
        self.samples = 0
        self.previous = {}

    def sample(self):
        if not self.enabled:
            return
        try:
            processes = ollama_processes()
        except (OSError, AttributeError):
            return
        if processes:
            self.peak = max(self.peak or 0, sum(row[0] for row in processes.values()))
            self.cpu_seconds += sum(max(0, cpu - self.previous[pid][1]) for pid, (_, cpu) in processes.items() if pid in self.previous)
            self.samples += 1
        self.previous = processes

    def __enter__(self):
        self.started = time.perf_counter()
        self.sample()
        def run():
            while not self.stop.wait(0.5):
                self.sample()
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(timeout=2)
        self.sample()

    def report(self):
        return {"peak_ram_bytes": self.peak,
                "cpu_percent": 100 * self.cpu_seconds / max(.001, time.perf_counter() - self.started) / (os.cpu_count() or 1) if self.samples > 1 else None,
                "gpu_percent": None, "sample_interval_s": 0.5, "samples": self.samples,
                "scope": "soma RSS dos processos Ollama locais; pico amostrado; CPU pode subestimar processos encerrados"}
