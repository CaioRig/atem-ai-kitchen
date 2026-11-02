"""Verify bootstrap flags reach the server without opening a port or loading models."""
from types import SimpleNamespace

import pytest

from server.storage.config import Settings


@pytest.mark.parametrize("args,enabled,cached", [
    (["--without-llm"], False, False), (["--validated-llm"], True, True), ([], True, False)])
def test_server_runtime_flags_preserve_disk_configuration(monkeypatch, args, enabled, cached):
    import server.main as module
    original = Settings(auth_token="valid-test-token")
    monkeypatch.setattr(module, "load_settings", lambda: original)
    monkeypatch.setattr(module, "setup_logging", lambda *a: None)
    seen = []
    monkeypatch.setattr(module, "create_app", lambda settings: seen.append(settings) or SimpleNamespace())
    monkeypatch.setattr(module.uvicorn, "run", lambda *a, **kw: None)
    assert module.main(args) == 0
    assert seen[0].llm_enabled is enabled
    assert seen[0].llm_quality_cached_only is cached


def test_policy_does_not_reselect_a_model_validated_by_bootstrap(tmp_path, monkeypatch):
    from server.services.performance import Hardware, PerformancePolicy, GIB
    import server.services.performance as module
    settings = Settings(llm_model="qwen2.5:7b", llm_auto_model=True, llm_quality_cached_only=True,
                        performance_path=tmp_path / "prefs.json")
    policy = PerformancePolicy(settings, Hardware(ram_available=12 * GIB, available_threads=8))
    monkeypatch.setattr(module, "choose_model", lambda *a: pytest.fail("bootstrap selection replaced"))
    client = SimpleNamespace(name=settings.llm_model, list_models=lambda: [{"name": "qwen2.5:7b", "size": 4 * GIB}])
    policy.configure_llm(client)
    assert client.name == "qwen2.5:7b"
