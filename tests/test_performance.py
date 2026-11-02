from types import SimpleNamespace

import pytest

from server.services.performance import GIB, Hardware, PerformancePolicy, choose_model, initial_profile, is_local_inference
from server.services.tts import split_sentences
from server.storage.config import Settings


@pytest.mark.parametrize("ram,threads,vram,expected", [
    (2, 2, 0, "economic"), (8, 6, 0, "balanced"), (24, 16, 8, "performance"),
    (2, 16, 8, "economic"), (32, 2, 0, "economic"), (None, 8, 0, "economic")])
def test_profiles(ram, threads, vram, expected):
    h = Hardware(ram_available=ram * GIB if ram else None, available_threads=threads, vram=vram * GIB, acceleration_verified=bool(vram))
    assert initial_profile(h) == expected


MODELS = [{"name": "qwen2.5:3b", "size": 2 * GIB, "details": {"quantization_level": "Q4_K_M"}},
          {"name": "qwen2.5:7b", "size": 4.5 * GIB, "details": {"quantization_level": "Q4_K_M"}}, {"name": "giant:70b", "size": 40 * GIB}]


def test_model_selection_and_remote_boundary():
    assert choose_model(MODELS, Hardware(ram_available=6 * GIB), "economic", "absent") == "qwen2.5:3b"
    assert choose_model(MODELS, None, "economic", "qwen2.5:7b") == "qwen2.5:7b"
    assert choose_model([], Hardware(), "economic", "configured") == "configured"
    assert choose_model(MODELS, None, "balanced", "missing") == "qwen2.5:3b"
    assert is_local_inference("http://localhost:11434")
    assert is_local_inference("http://[::1]:11434")
    assert not is_local_inference("http://192.168.1.3:11434")


def policy(tmp_path, **kwargs):
    settings = Settings(performance_path=tmp_path / "performance.json", **kwargs)
    return PerformancePolicy(settings, Hardware(ram_available=16 * GIB, available_threads=12, vram=8 * GIB, acceleration_verified=True))


def test_preferences_roundtrip_reset_and_bad_file(tmp_path):
    p = policy(tmp_path)
    p.save({"profile": "economic", "model": "qwen2.5:3b", "voice": "pf_dora"})
    loaded = policy(tmp_path)
    assert loaded.profile == "economic"
    assert loaded.preferences["model"] == "qwen2.5:3b"
    loaded.save({"profile": "automatic", "model": "", "voice": ""})
    assert policy(tmp_path).profile == "performance"
    p.path.write_text("broken")
    assert policy(tmp_path).preferences["profile"] == "automatic"


def test_dynamic_context_has_hysteresis_and_respects_manual_remote(tmp_path):
    p = policy(tmp_path)
    client = SimpleNamespace(num_ctx=16384, last_metrics={"ttft_s": 10, "total_s": 20})
    p.observe(client)
    p.observe(client)
    assert client.num_ctx == 16384
    p.observe(client)
    assert client.num_ctx == 8192
    p.observe(client)
    assert client.num_ctx == 8192
    remote = policy(tmp_path / "remote", llm_url="http://192.168.1.3:11434")
    for _ in range(5):
        remote.observe(client)
    assert client.num_ctx == 8192


def test_measured_context_survives_bootstrap_overlay_but_new_manual_value_wins(tmp_path):
    p = policy(tmp_path, llm_num_ctx=16384)
    client = SimpleNamespace(num_ctx=16384, last_metrics={"ttft_s": 10})
    for _ in range(3):
        p.observe(client)
    assert policy(tmp_path, llm_num_ctx=16384).context == 8192
    assert policy(tmp_path, llm_num_ctx=32768).context == 32768


def test_no_download_and_missing_manual_is_preserved(tmp_path):
    p = policy(tmp_path)
    p.preferences["model"] = "missing"
    client = SimpleNamespace(name="missing", list_models=lambda: MODELS, keep_alive="10m")
    p.configure_llm(client)
    assert client.name == "missing"
    assert any("ollama pull missing" in n for n in p.notices)


@pytest.mark.parametrize("value", [{"profile": "fast"}, {"voice": "invalid"}, {"model": 1}, {"extra": True}, []])
def test_invalid_preferences(value):
    with pytest.raises((ValueError, TypeError)):
        PerformancePolicy.validate(value)


def test_unpunctuated_speech_is_bounded_without_losing_words():
    text = "Coloque os ingredientes na panela e mexa devagar até obter uma mistura uniforme " * 4
    pieces = split_sentences(text.strip(), 90)
    assert all(len(piece) <= 90 for piece, _ in pieces)
    assert " ".join(piece for piece, _ in pieces) == text.strip()


def test_failed_detection_is_conservative(tmp_path, monkeypatch):
    import server.services.performance as module
    def fail():
        raise OSError("unavailable")
    monkeypatch.setattr(module, "detect_hardware", fail)
    p = PerformancePolicy(Settings(performance_path=tmp_path / "prefs.json"))
    assert p.profile == "economic"
    assert p.hardware.ram_total is None
    assert p.threads == 1


def test_settings_api_requires_auth_and_saves_for_restart(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import server.api.app as module
    monkeypatch.setattr(module, "load_detector", lambda s: (None, "unavailable"))
    monkeypatch.setattr(module, "load_transcriber", lambda s: (None, "unavailable"))
    monkeypatch.setattr(module, "load_vad", lambda s: None)
    monkeypatch.setattr(module, "gpu_utilization", lambda: None)
    s = Settings(auth_token="a-long-test-token", performance_path=tmp_path / "prefs.json",
                 llm_enabled=False, tts_enabled=False, memory_enabled=False, weather_enabled=False)
    with TestClient(module.create_app(s)) as client:
        assert client.get("/api/performance").status_code == 401
        headers = {"Authorization": "Bearer a-long-test-token"}
        assert client.get("/api/performance", headers=headers).status_code == 200
        assert client.put("/api/performance", headers=headers, json={"profile": "invalid"}).status_code == 400
        response = client.put("/api/performance", headers=headers, json={"profile": "economic"})
        assert response.json()["restart_required"]
        assert response.json()["preferences"]["profile"] == "economic"
    assert policy(tmp_path).profile == "performance"  # unrelated preferences file stays independent
    assert PerformancePolicy(s).profile == "economic"
