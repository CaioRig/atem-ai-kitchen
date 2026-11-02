"""Installer scenarios use mocks: never install system software or download models."""
from __future__ import annotations

import io
import json
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import bootstrap as boot
from scripts import downloads
from server.services.performance import GIB, Hardware
from server.storage.config import Settings, apply_automatic_settings

CATALOG = boot.read_json(boot.ROOT / "config/model-catalog.json")


def hardware(**values):
    return Hardware(cpu="simulated CPU", ram_total=16 * GIB, ram_available=12 * GIB, available_threads=8, **values)


@pytest.fixture
def installation(tmp_path, monkeypatch):
    import server.storage.config as config
    import server.api.app as app
    (tmp_path / "config").mkdir()
    (tmp_path / "config/settings.example.toml").write_text((boot.ROOT / "config/settings.example.toml").read_text(encoding="utf-8"), encoding="utf-8")
    boot.write_json(tmp_path / "config/model-catalog.json", CATALOG)
    (tmp_path / "requirements.txt").write_text("fastapi>=0.115,<1")
    monkeypatch.delenv("JARVIS_CONFIG", raising=False)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    calls = {"probe": [], "pull": [], "voice": []}
    h = hardware()
    models = [{"name": "qwen2.5:7b", "size": 4_800_000_000, "digest": "unchanged"}]
    monkeypatch.setattr(boot, "ensure_dependencies", lambda *a: {"fake-package": "1"})
    monkeypatch.setattr(boot, "ensure_client", lambda *a: {"fingerprint": "compiled"})
    monkeypatch.setattr(boot, "detect_hardware", lambda: h)
    monkeypatch.setattr(boot, "ensure_ollama", lambda *a: models)
    monkeypatch.setattr(boot, "installed_models", lambda *a: models)
    monkeypatch.setattr(boot, "api", lambda *a: {"version": "test-version"})
    monkeypatch.setattr(boot, "update_acceleration", lambda *a: None)
    def probe(url, name, context):
        calls["probe"].append(name)
        return {"total_s": .2, "tokens_per_s": 30, "model": name}
    monkeypatch.setattr(boot, "probe_llm", probe)
    def pull(url, name, *args):
        calls["pull"].append(name)
        models.append({"name": name, "digest": "downloaded"})
    monkeypatch.setattr(boot, "pull_model", pull)
    monkeypatch.setattr(boot, "model_assets", lambda *a: {"stable-file": {"bytes": 12, "mtime_ns": 1}})
    def tls(settings):
        for file in (settings.tls_certfile, settings.tls_keyfile):
            if file:
                file.parent.mkdir(parents=True, exist_ok=True)
                file.touch()
    monkeypatch.setattr(boot, "ensure_tls", tls)
    monkeypatch.setattr(app, "load_detector", lambda *a: (object(), ""))
    monkeypatch.setattr(app, "load_vad", lambda *a: object())
    monkeypatch.setattr(app, "load_transcriber", lambda *a: (object(), ""))
    class Voice:
        def warmup(self):
            calls["voice"].append("warmup")
        def synthesize(self, text):
            yield b"\0\0"
    monkeypatch.setattr(app, "load_synthesizer", lambda *a: (Voice(), ""))
    options = SimpleNamespace(offline=False, reprobe=False)
    return tmp_path, calls, h, models, options


@pytest.mark.parametrize("system", ["Windows", "Linux"])
def test_first_install_then_fast_restart(installation, monkeypatch, system):
    root, calls, h, models, options = installation
    monkeypatch.setattr(boot.platform, "system", lambda: system)
    first = boot.prepare(root, options, [])
    assert first["settings"].bootstrap_managed
    assert first["settings"].llm_auto_model
    assert calls["probe"] == ["qwen2.5:7b"]
    assert len(calls["voice"]) == 1
    before = (root / "config/settings.toml").read_bytes()
    options.offline = True
    second = boot.prepare(root, options, [])
    assert second["state"]["model"] == "qwen2.5:7b"
    assert calls["pull"] == []
    assert calls["probe"] == ["qwen2.5:7b"]
    assert len(calls["voice"]) == 1
    assert (root / "config/settings.toml").read_bytes() == before


def test_hardware_change_selects_only_needed_smaller_model(installation):
    root, calls, h, models, options = installation
    boot.prepare(root, options, [])
    h.ram_total = h.ram_available = 4 * GIB
    h.available_threads = 2
    result = boot.prepare(root, options, [])
    assert calls["pull"] == ["qwen2.5:1.5b"]
    assert result["settings"].stt_model.name == "ggml-base.bin"
    assert result["settings"].tts_model.name == "kokoro-v1.0.int8.onnx"
    boot.prepare(root, options, [])
    assert calls["pull"] == ["qwen2.5:1.5b"]


def test_existing_manual_configuration_and_secrets_are_untouched(installation):
    root, calls, h, models, options = installation
    path, _ = boot.ensure_config(root)
    text = path.read_text(encoding="utf-8").replace("automatic_model = true", "automatic_model = false").replace("managed = true", "managed = false").replace('model = "qwen2.5:7b"', 'model = "manual:custom"')
    path.write_text(text, encoding="utf-8")
    models.append({"name": "manual:custom"})
    snapshot = path.read_bytes()
    assert boot.prepare(root, options, [])["settings"].llm_model == "manual:custom"
    assert path.read_bytes() == snapshot
    assert calls["pull"] == []
    assert Settings.from_toml(path).auth_token not in json.dumps(boot.read_json(root / "data/bootstrap-state.json"))


def test_toml_model_edit_overrides_generated_overlay(installation):
    root, calls, h, models, options = installation
    boot.prepare(root, options, [])
    path = root / "config/settings.toml"
    path.write_text(path.read_text(encoding="utf-8").replace('model = "qwen2.5:7b"', 'model = "manual:custom"'), encoding="utf-8")
    models.append({"name": "manual:custom"})
    result = boot.prepare(root, options, [])
    assert result["settings"].llm_model == "manual:custom"
    assert not result["settings"].llm_auto_model


def test_gpu_detection_is_not_ollama_acceleration(monkeypatch):
    h = hardware(gpu="NVIDIA", vram=8 * GIB, acceleration_verified=False)
    monkeypatch.setattr(boot, "api", lambda *a: {"models": [{"name": "qwen2.5:7b", "size_vram": 0}]})
    boot.update_acceleration(h, "http://localhost:11434")
    assert not h.inference_gpu_used
    monkeypatch.setattr(boot, "api", lambda *a: {"models": [{"name": "qwen2.5:7b", "size_vram": 5 * GIB}]})
    boot.update_acceleration(h, "http://localhost:11434", "qwen2.5:7b")
    assert h.inference_gpu_used
    assert h.ollama_vram == 5 * GIB


def test_low_ram_and_confirmed_gpu_profiles():
    h = hardware()
    h.ram_available = 4 * GIB
    h.available_threads = 2
    entry, _ = boot.select_recommendation(CATALOG, [], h)
    assert entry["name"] == "qwen2.5:1.5b"
    h.available_threads = 8
    h.vram = h.vram_available = 8 * GIB
    h.acceleration_verified = True
    entry, _ = boot.select_recommendation(CATALOG, [], h)
    assert entry["name"] == "qwen2.5:7b"
    h.acceleration_verified = False
    entry, _ = boot.select_recommendation(CATALOG, [], h)
    assert entry["name"] == "qwen2.5:1.5b"


def test_missing_dependency_is_installed_once(tmp_path, monkeypatch):
    reports = iter([{"missing": ["fastapi"], "versions": {}}, {"missing": [], "versions": {"fastapi": "0.115"}}])
    monkeypatch.setattr(boot, "dependency_report", lambda *a: next(reports))
    commands = []
    monkeypatch.setattr(boot, "command", lambda args, **kw: commands.append(args))
    versions = boot.ensure_dependencies(Path("python"), tmp_path, {}, False)
    assert sum("install" in c for c in commands) == 1
    monkeypatch.setattr(boot, "dependency_report", lambda *a: {"missing": [], "versions": versions})
    commands.clear()
    boot.ensure_dependencies(Path("python"), tmp_path, {"python_versions": versions}, True)
    assert commands == []


def test_missing_dependency_offline_is_actionable(tmp_path, monkeypatch):
    monkeypatch.setattr(boot, "dependency_report", lambda *a: {"missing": ["numpy"], "versions": {}})
    monkeypatch.setattr(boot, "command", lambda *a, **kw: pytest.fail("must not install offline"))
    with pytest.raises(boot.BootstrapError, match="offline"):
        boot.ensure_dependencies(Path("python"), tmp_path, {}, True)


def test_existing_client_skips_node_npm_and_build(tmp_path, monkeypatch):
    client = tmp_path / "client"
    (client / "src").mkdir(parents=True)
    (client / "public/js").mkdir(parents=True)
    (client / "src/main.ts").write_text("export {}")
    (client / "public/js/main.js").write_text("export {}")
    for name in ("tsconfig.json", "package.json", "package-lock.json"):
        (client / name).write_text("{}")
    sources = list((client / "src").rglob("*.ts")) + [client / n for n in ("tsconfig.json", "package.json", "package-lock.json")]
    state = {"client": {"fingerprint": boot.fingerprint(sources, tmp_path)}}
    monkeypatch.setattr(boot, "ensure_node", lambda *a: pytest.fail("Node installation repeated"))
    assert boot.ensure_client(tmp_path, CATALOG, state, True) == state["client"]


def test_invalid_model_stops_before_persisting(installation, monkeypatch):
    root, calls, h, models, options = installation
    monkeypatch.setattr(boot, "probe_llm", lambda *a: (_ for _ in ()).throw(boot.BootstrapError("modelo corrompido")))
    with pytest.raises(boot.BootstrapError, match="corrompido"):
        boot.prepare(root, options, [])
    assert not (root / "data/bootstrap-state.json").exists()


def test_remote_service_failure_never_installs_locally(tmp_path, monkeypatch):
    monkeypatch.setattr(boot, "installed_models", lambda *a: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(boot, "executable", lambda *a: pytest.fail("must not install for remote inference"))
    with pytest.raises(boot.BootstrapError, match="remoto"):
        boot.ensure_ollama(tmp_path, "http://192.168.1.9:11434", False, [])


def test_no_space_prevents_pull(tmp_path, monkeypatch):
    monkeypatch.setattr(boot.shutil, "disk_usage", lambda p: SimpleNamespace(free=1))
    with pytest.raises(boot.BootstrapError, match="Espaço"):
        boot.pull_model("http://localhost:11434", "qwen2.5:3b", 2 * GIB, tmp_path, False)


class Response(io.BytesIO):
    status = 200
    def __init__(self, content, headers):
        super().__init__(content)
        self.headers = headers


def test_interrupted_download_resumes_and_validates(tmp_path, monkeypatch):
    path = tmp_path / "model.bin"
    partial = tmp_path / "model.bin.part"
    partial.write_bytes(b"abc")
    (tmp_path / "model.bin.part.etag").write_text('"same"')
    requests = []
    def fetch(request, **kw):
        requests.append(request)
        response = Response(b"def", {"Content-Length": "3", "Content-Range": "bytes 3-5/6", "ETag": '"same"'})
        response.status = 206
        return response
    monkeypatch.setattr(downloads.urllib.request, "urlopen", fetch)
    downloads.download("https://example.test/model", path, __import__("hashlib").sha256(b"abcdef").hexdigest())
    assert path.read_bytes() == b"abcdef"
    assert requests[0].get_header("Range") == "bytes=3-"
    assert not partial.exists()


def test_incomplete_download_does_not_become_installed_file(tmp_path, monkeypatch):
    monkeypatch.setattr(downloads.urllib.request, "urlopen", lambda *a, **k: Response(b"abc", {"Content-Length": "6"}))
    path = tmp_path / "model.bin"
    with pytest.raises(OSError, match="incompleto"):
        downloads.download("https://example.test/model", path)
    assert not path.exists()
    assert (tmp_path / "model.bin.part").exists()


def test_bad_hash_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(downloads.urllib.request, "urlopen", lambda *a, **k: Response(b"bad", {"Content-Length": "3"}))
    with pytest.raises(ValueError, match="Checksum"):
        downloads.download("https://example.test/model", tmp_path / "model", "0" * 64)
    assert not (tmp_path / "model").exists()


def test_archive_paths_cannot_escape_project(tmp_path):
    archive = tmp_path / "unsafe.tar"
    with tarfile.open(archive, "w") as file:
        item = tarfile.TarInfo("../escape")
        item.size = 3
        file.addfile(item, io.BytesIO(b"bad"))
    with pytest.raises(boot.BootstrapError, match="inseguro"):
        boot.safe_unpack(archive, tmp_path / "tools")
    assert not (tmp_path / "escape").exists()


def test_install_permission_denied_is_bounded(monkeypatch):
    monkeypatch.setattr(boot.sys.stdin, "isatty", lambda: False)
    with pytest.raises(boot.BootstrapError, match="não autorizada"):
        boot.approve_system("Install required component")


def test_launcher_contracts():
    batch = (boot.ROOT / "start.bat").read_text()
    shell = (boot.ROOT / "start.sh").read_text()
    assert "scripts\\bootstrap.py" in batch
    assert "scripts/bootstrap.py" in shell
    assert "ExecutionPolicy" not in batch
    assert "curl" not in shell


def test_tls_generation_preserves_existing_private_key(tmp_path):
    pytest.importorskip("cryptography")
    from cryptography import x509
    settings = Settings(bind_host="192.168.1.4", tls_certfile=tmp_path / "cert.pem", tls_keyfile=tmp_path / "key.pem")
    boot.ensure_tls(settings)
    original = settings.tls_keyfile.read_bytes()
    certificate = x509.load_pem_x509_certificate(settings.tls_certfile.read_bytes())
    assert "192.168.1.4" in str(certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value)
    boot.ensure_tls(settings)
    assert settings.tls_keyfile.read_bytes() == original


def test_measured_slow_model_is_not_retried_every_restart(installation, monkeypatch):
    root, calls, h, models, options = installation
    def probe(url, name, context):
        calls["probe"].append(name)
        return {"tokens_per_s": 2 if name == "qwen2.5:7b" else 30, "model": name}
    monkeypatch.setattr(boot, "probe_llm", probe)
    result = boot.prepare(root, options, [])
    assert result["settings"].llm_model == "qwen2.5:3b"
    initial_probes = list(calls["probe"])
    boot.prepare(root, options, [])
    assert calls["probe"] == initial_probes
    assert calls["pull"] == ["qwen2.5:3b"]


def test_main_reexecutes_venv_by_prefix_not_resolved_symlink(tmp_path, monkeypatch):
    monkeypatch.setattr(boot, "ROOT", tmp_path)
    monkeypatch.setattr(boot.sys, "prefix", str(tmp_path / "system-python"))
    venv = tmp_path / ".venv/bin/python"
    monkeypatch.setattr(boot, "prepare_venv", lambda *a: venv)
    commands = []
    monkeypatch.setattr(boot.subprocess, "call", lambda args, **kw: commands.append(args) or 0)
    assert boot.main(["--offline", "--prepare-only"]) == 0
    assert commands[0][0] == str(venv)


def test_compatible_installed_multilingual_model_avoids_download():
    installed = [{"name": "llama3.1:8b", "size": 4 * GIB, "details": {"quantization_level": "Q4_K_M"}}]
    selected, ready = boot.select_recommendation(CATALOG, installed, hardware())
    assert ready
    assert selected["name"] == "llama3.1:8b"


def test_416_from_full_partial_restarts_once(tmp_path, monkeypatch):
    import urllib.error
    path = tmp_path / "model.bin"
    (tmp_path / "model.bin.part").write_bytes(b"old")
    (tmp_path / "model.bin.part.etag").write_text('"old"')
    calls = []
    def fetch(request, **kwargs):
        calls.append(request)
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 416, "range", {}, None)
        return Response(b"fresh", {"Content-Length": "5"})
    monkeypatch.setattr(downloads.urllib.request, "urlopen", fetch)
    downloads.download("https://example.test/model", path)
    assert path.read_bytes() == b"fresh"
    assert len(calls) == 2
    assert calls[1].get_header("Range") is None


def test_incomplete_venv_is_not_reexecuted_forever(tmp_path):
    python = tmp_path / (".venv/Scripts/python.exe" if boot.os.name == "nt" else ".venv/bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    with pytest.raises(boot.BootstrapError, match="incompleto"):
        boot.prepare_venv(tmp_path, False)


def test_failed_venv_reexec_stops_after_one_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(boot, "ROOT", tmp_path)
    monkeypatch.setattr(boot.sys, "prefix", str(tmp_path / "system-python"))
    monkeypatch.setattr(boot, "prepare_venv", lambda *a: tmp_path / ".venv/bin/python")
    monkeypatch.setenv("LUNA_BOOTSTRAP_VENV", str((tmp_path / ".venv").resolve()))
    monkeypatch.setattr(boot.subprocess, "call", lambda *a, **kw: pytest.fail("recursive installer"))
    assert boot.main(["--offline"]) == 1


def test_invalid_catalog_cannot_control_download_paths(tmp_path):
    boot.write_json(tmp_path / "config/model-catalog.json", {**CATALOG, "node_version": "../../outside"})
    with pytest.raises(boot.BootstrapError, match="Catálogo"):
        boot.load_catalog(tmp_path)


@pytest.mark.parametrize("url", ["http://secret:password@localhost:11434", "http://localhost:99999"])
def test_invalid_service_config_cannot_trigger_installation(tmp_path, monkeypatch, url):
    monkeypatch.setattr(boot, "executable", lambda *a: pytest.fail("invalid configuration tried installation"))
    with pytest.raises(boot.BootstrapError, match="llm.url"):
        boot.ensure_ollama(tmp_path, url, False, [])
