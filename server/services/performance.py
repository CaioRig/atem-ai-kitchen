"""Lightweight server hardware policy; no downloads, cloud calls or startup benchmarks."""
from __future__ import annotations

import ctypes
import ipaddress
import json
import logging
import os
import platform
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

GIB = 1024 ** 3
PROFILES = ("automatic", "economic", "balanced", "performance")
log = logging.getLogger("luna.hardware")


@dataclass
class Hardware:
    architecture: str = ""
    os: str = ""
    cpu: str = ""
    cores: int | None = None
    threads: int = 1
    available_threads: int = 1
    ram_total: int | None = None
    ram_available: int | None = None
    gpu: str | None = None
    vram: int | None = None
    vram_available: int | None = None
    disk_free: int | None = None
    acceleration_verified: bool = False
    inference_gpu_used: bool = False
    ollama_vram: int = 0
    ollama_ram: int = 0
    acceleration: str | None = None
    detection_limits: list[str] = field(default_factory=list)


def hardware_key(h: Hardware) -> dict:
    return {k: v for k, v in asdict(h).items() if k not in ("ram_available", "vram_available", "disk_free", "inference_gpu_used", "ollama_vram", "ollama_ram", "detection_limits")}


def _run(args: list[str], timeout: float = 3) -> str:
    return subprocess.check_output(args, timeout=timeout, text=True, stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).strip()


def detect_hardware() -> Hardware:
    h = Hardware(os=platform.platform(), architecture=platform.machine(), cpu=platform.processor(), threads=os.cpu_count() or 1)
    try:
        import shutil
        h.disk_free = shutil.disk_usage(Path(__file__).resolve().parents[2]).free
    except OSError:
        h.detection_limits.append("Espaço livre em disco desconhecido")
    h.available_threads = h.threads
    try:
        if os.name == "nt":
            class Memory(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                    (name, ctypes.c_ulonglong) for name in
                    ("total", "available", "page_total", "page_available", "virtual", "virtual_available", "extended")]
            m = Memory()
            m.length = ctypes.sizeof(m)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                h.ram_total, h.ram_available = m.total, m.available
            process = ctypes.c_void_p(-1)
            mask, system = ctypes.c_size_t(), ctypes.c_size_t()
            if ctypes.windll.kernel32.GetProcessAffinityMask(process, ctypes.byref(mask), ctypes.byref(system)):
                h.available_threads = max(1, mask.value.bit_count())
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                h.cpu = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
            class Info(ctypes.Structure):
                _fields_ = [("mask", ctypes.c_size_t), ("relationship", ctypes.c_ulong),
                            ("data", ctypes.c_ulonglong * 2)]
            length = ctypes.c_ulong()
            ctypes.windll.kernel32.GetLogicalProcessorInformation(None, ctypes.byref(length))
            entries = (Info * (length.value // ctypes.sizeof(Info)))()
            if ctypes.windll.kernel32.GetLogicalProcessorInformation(entries, ctypes.byref(length)):
                h.cores = sum(e.relationship == 0 for e in entries) or None
        else:
            h.available_threads = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else h.threads
            mem = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
            h.ram_total = int(mem["MemTotal"].split()[0]) * 1024
            h.ram_available = int(mem["MemAvailable"].split()[0]) * 1024
            blocks = Path("/proc/cpuinfo").read_text().split("\n\n")
            cores = set()
            for block in blocks:
                fields = dict(line.split(":", 1) for line in block.splitlines() if ":" in line)
                fields = {k.strip(): v.strip() for k, v in fields.items()}
                h.cpu = fields.get("model name", h.cpu)
                if "core id" in fields:
                    cores.add((fields.get("physical id"), fields["core id"]))
            h.cores = len(cores) or None
            # cgroup v2 limits describe the resources available to this server, not the host.
            limit = Path("/sys/fs/cgroup/memory.max")
            if limit.exists() and limit.read_text().strip() != "max":
                total = int(limit.read_text())
                used = int(Path("/sys/fs/cgroup/memory.current").read_text())
                h.ram_total = min(h.ram_total, total)
                h.ram_available = min(h.ram_available, max(0, total - used))
            quota = Path("/sys/fs/cgroup/cpu.max")
            if quota.exists():
                q, period = quota.read_text().split()
                if q != "max":
                    h.available_threads = min(h.available_threads, max(1, int(q) // int(period)))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
        h.detection_limits.append("Detecção de CPU/RAM parcial; recursos desconhecidos não são considerados disponíveis")
    try:
        lines = _run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"]).splitlines()
        devices = [(name.strip(), int(total.strip()) * 1024 ** 2, int(free.strip()) * 1024 ** 2)
                   for name, total, free in (line.rsplit(",", 2) for line in lines)]
        h.gpu, h.vram, h.vram_available = max(devices, key=lambda d: d[1])
        h.acceleration = "Driver NVIDIA detectado; aceleração ainda não confirmada"
        try:
            cuda = ctypes.WinDLL("nvcuda.dll") if os.name == "nt" else ctypes.CDLL("libcuda.so.1")
            count = ctypes.c_int()
            h.acceleration_verified = cuda.cuInit(0) == 0 and cuda.cuDeviceGetCount(ctypes.byref(count)) == 0 and count.value > 0
            if h.acceleration_verified:
                h.acceleration = "CUDA Driver API inicializada; uso pelo Ollama verificado separadamente"
        except (OSError, AttributeError):
            h.detection_limits.append("Driver CUDA não inicializado; aceleração não confirmada")
    except (OSError, ValueError, subprocess.SubprocessError):
        # Inventory alone is not evidence of a usable inference backend or dedicated VRAM.
        try:
            if os.name == "nt":
                names = json.loads(_run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                    "@(Get-CimInstance Win32_VideoController -ErrorAction Stop | Select-Object -ExpandProperty Name) | ConvertTo-Json -Compress"], timeout=15))
                h.gpu = "; ".join(names if isinstance(names, list) else [names]) or None
            else:
                names = []
                for card in Path("/sys/class/drm").glob("card[0-9]*"):
                    if (card / "device/vendor").is_file():
                        vendor = (card / "device/vendor").read_text().strip()
                        names.append({"0x1002": "AMD", "0x8086": "Intel", "0x10de": "NVIDIA"}.get(vendor, vendor))
                h.gpu = "; ".join(names) or None
        except (OSError, ValueError, TypeError, subprocess.SubprocessError):
            pass
        h.detection_limits.append("GPU/VRAM sem confirmação pelo driver NVIDIA; inferência em CPU é a opção conservadora")
    for message in h.detection_limits:
        log.warning(message)
    return h


def is_local_inference(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() == "localhost"


def initial_profile(h: Hardware) -> str:
    ram = h.ram_available or 0
    if ram < 4 * GIB or h.available_threads < 4:
        return "economic"
    if ram >= 12 * GIB and h.available_threads >= 8 and (h.vram or 0) >= 6 * GIB and h.acceleration_verified:
        return "performance"
    return "balanced"


def choose_model(models: list[dict], h: Hardware | None, profile: str, fallback: str) -> str:
    """Disk size is only a lower bound; reserve RAM for weights, context, ASR and TTS.
    Unknown remote hardware: retain a working configured model; only missing-model fallback uses size.
    Family ranking is a language-support heuristic, never a measured quality score.
    """
    usable = [m for m in models if isinstance(m.get("name"), str) and isinstance(m.get("size"), (int, float)) and m["size"] > 0
              and not any(x in m["name"].lower() for x in ("embed", "vision", "coder"))]
    if h is None:
        if any(m["name"] in (fallback, fallback + ":latest") for m in usable):
            return fallback
        return min(usable, key=lambda m: m["size"])["name"] if usable else fallback
    budget = max(0, ((h.ram_available or 0) - 2 * GIB) * 0.65)
    if h.acceleration_verified and h.vram:
        budget = max(budget, max(0, h.vram - 2 * GIB) * 0.8)
    ceiling = {"economic": 2.5, "balanced": 5, "performance": 10}[profile] * GIB
    candidates = [m for m in usable if m["size"] <= min(budget, ceiling)]
    if not candidates:
        return min(usable, key=lambda m: m["size"])["name"] if usable else fallback
    def score(m):
        family = m["name"].lower()
        multilingual = any(x in family for x in ("qwen", "llama3", "gemma"))
        quantized = str(m.get("details", {}).get("quantization_level", "")).startswith(("Q4", "Q5", "Q6", "Q8"))
        return multilingual, quantized, m["size"]
    return max(candidates, key=score)["name"]


class PerformancePolicy:
    def __init__(self, settings, hardware: Hardware | None = None):
        self.settings = settings
        self.notices: list[str] = []
        try:
            self.hardware = hardware or detect_hardware()
        except Exception:
            self.hardware = Hardware()
            self.notices.append("Falha na detecção de hardware: limites conservadores aplicados.")
        self.local = is_local_inference(settings.llm_url)
        self.path = settings.performance_path
        self.preferences = {"profile": settings.performance_profile, "model": "", "voice": ""}
        self.models: list[dict] = []
        self.samples: list[dict] = []
        self._save_lock = threading.Lock()
        self._cpu_at = time.monotonic()
        self._cpu_seconds = sum(os.times()[:2])
        self.last_adjustment = 0.0
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self.preferences = self.validate(saved["preferences"])
        except (OSError, ValueError, KeyError, TypeError):
            saved = {}
        self.profile = self.preferences["profile"]
        if self.profile == "automatic":
            self.profile = initial_profile(self.hardware)
        self.context = settings.llm_num_ctx or {"economic": 2048, "balanced": 8192, "performance": 16384}[self.profile]
        self.threads = max(1, min(self.hardware.available_threads - 1, {"economic": 4, "balanced": 8, "performance": 12}[self.profile]))
        if settings.llm_runtime_threads:
            self.threads = settings.llm_runtime_threads
        if not settings.stt_threads:
            settings.stt_threads = self.threads
        if self.preferences["voice"]:
            settings.tts_voice = self.preferences["voice"]
        self.segment_chars = {"economic": 90, "balanced": 120, "performance": 160}[self.profile]
        settings.tts_threads = self.threads
        settings.tts_segment_chars = self.segment_chars
        # Explicit TOML Piper selection is preserved. Automatic keeps the female pt-BR voice.
        if not self.local:
            self.notices.append("Hardware do Ollama remoto desconhecido: modelo e contexto preservados; ajuste no servidor de inferência.")
        self.pending_restart = False
        selected = saved.get("selected", {}) if isinstance(saved, dict) else {}
        if not isinstance(selected, dict):
            selected = {}
        if (self.local and self.preferences["profile"] == "automatic"
                and selected.get("hardware") == hardware_key(self.hardware)
                and selected.get("url") == settings.llm_url
                and selected.get("model") == settings.llm_model
                and selected.get("configured_context", 0) == settings.llm_num_ctx
                and selected.get("context") in (2048, 4096, 8192, 16384)):
            self.context = min(self.context, selected["context"])

    @staticmethod
    def validate(value: dict) -> dict:
        if not isinstance(value, dict) or set(value) - {"profile", "model", "voice"}:
            raise ValueError("Preferências inválidas")
        out = {key: value.get(key, default) for key, default in (("profile", "automatic"), ("model", ""), ("voice", ""))}
        if out["profile"] not in PROFILES or not isinstance(out["model"], str) or len(out["model"]) > 200:
            raise ValueError("Perfil ou modelo inválido")
        if out["voice"] not in ("", "pf_dora", "pm_alex", "pm_santa"):
            raise ValueError("Voz inválida")
        return out

    def save(self, preferences: dict | None = None) -> None:
        with self._save_lock:
            self._save(preferences)

    def _save(self, preferences: dict | None = None) -> None:
        prefs = self.validate(preferences) if preferences is not None else self.preferences
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps({"preferences": prefs, "selected": {"profile": self.profile,
            "model": self.settings.llm_model, "context": self.context, "configured_context": self.settings.llm_num_ctx,
            "hardware": hardware_key(self.hardware), "url": self.settings.llm_url}, "samples": self.samples[-20:]}, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.path)
        if preferences is not None:
            self.preferences = prefs
            self.pending_restart = True

    def configure_llm(self, client) -> None:
        try:
            self.models = client.list_models()
            manual = self.preferences["model"]
            installed_manual = False
            if manual:
                if not any(m.get("name") in (manual, manual + ":latest") for m in self.models):
                    self.notices.append(f"Modelo solicitado ausente. Instale no servidor: ollama pull {manual}")
                else:
                    client.name = manual
                    installed_manual = True
            if manual and not installed_manual:
                client.name = manual  # preserve the explicit choice; check() reports its absence
            elif (not manual and self.settings.llm_auto_model and not self.settings.bootstrap_managed
                  and not self.settings.llm_quality_cached_only):
                client.name = choose_model(self.models, self.hardware if self.local else None,
                                           self.profile, client.name)
            selected = next((m for m in self.models if m.get("name") == client.name), {})
            if self.local and selected.get("size", 0) > max(
                    max(0, (self.hardware.ram_available or 0) - 2 * GIB) * 0.65,
                    max(0, (self.hardware.vram or 0) - 2 * GIB) * 0.8):
                self.notices.append("Memória livre limitada: o start.bat verifica candidatos ultraleves e a reserva dos serviços antes de habilitar a IA.")
            if self.local:
                client.num_ctx = self.context
                client.num_thread = self.threads
                client.backend_identity = hardware_key(self.hardware)
                if self.profile == "economic":
                    client.configure_economic()
        except (OSError, ValueError, RuntimeError) as exc:
            self.notices.append(f"Otimização do Ollama indisponível: {type(exc).__name__}; configuração preservada.")

    def observe(self, client) -> None:
        metric = getattr(client, "last_metrics", {})
        if not metric:
            return
        self.samples.append(dict(metric))
        self.samples = self.samples[-20:]
        # Three slow completed requests and a five-minute cooldown, never momentary oscillations.
        slow = len(self.samples) >= 3 and all((s.get("ttft_s") or 0) - (s.get("load_s") or 0) > 8 for s in self.samples[-3:])
        if self.local and self.preferences["profile"] == "automatic" and slow and time.monotonic() - self.last_adjustment > 300:
            if getattr(client, "num_ctx", 0) > 4096:
                client.num_ctx = max(4096, client.num_ctx // 2)
                self.context = client.num_ctx
                self.last_adjustment = time.monotonic()
                self.notices.append("Contexto ajustado após três respostas lentas; modelo, memória e limites de saída preservados.")
        try:
            self.save()
        except OSError:
            pass

    def snapshot(self) -> dict:
        now, cpu = time.monotonic(), sum(os.times()[:2])
        usage = {"process_cpu_percent": round(100 * (cpu - self._cpu_seconds) /
                 max(0.001, now - self._cpu_at) / self.hardware.available_threads, 2),
                 "process_ram_bytes": process_ram(), "gpu_utilization_percent": None}
        self._cpu_at, self._cpu_seconds = now, cpu
        return {"hardware": asdict(self.hardware), "inference": "local" if self.local else "remote_unknown",
                "profile": self.profile, "preferences": self.preferences, "model": self.settings.llm_model,
                "context": self.context if self.local else None, "threads": self.threads,
                "tts_engine": self.settings.tts_engine, "tts_voice": self.settings.tts_voice,
                "models": [m.get("name") for m in self.models], "notices": self.notices[-10:],
                "samples": self.samples, "resources": usage, "restart_required": self.pending_restart}


def process_ram() -> int | None:
    try:
        if os.name == "nt":
            class Counters(ctypes.Structure):
                _fields_ = [("size", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
                    (name, ctypes.c_size_t) for name in
                    ("peak", "working", "paged_peak", "paged", "nonpaged_peak", "nonpaged", "page", "page_peak")]
            counters = Counters()
            counters.size = ctypes.sizeof(counters)
            handle = ctypes.c_void_p(-1)
            if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.size):
                return counters.working
        else:
            return int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, AttributeError):
        pass
    return None


def gpu_utilization() -> float | None:
    try:
        values = _run(["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"])
        return max(float(v) for v in values.splitlines())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
