import json

from scripts import benchmark_catalog as benchmark
from server.services.performance import GIB, Hardware
from server.storage.config import Settings


def test_no_installed_model_never_starts_generation_or_download(tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark, "load_settings", lambda: Settings())
    monkeypatch.setattr(benchmark.boot, "detect_server", lambda: Hardware(ram_available=8 * GIB, available_threads=4))
    monkeypatch.setattr(benchmark.boot, "installed_models", lambda _: [])
    monkeypatch.setattr(benchmark.boot, "api", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("generation")))
    monkeypatch.setattr(benchmark.boot, "pull_model", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("download")))
    output = tmp_path / "report.json"
    assert benchmark.main(["--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["compatible_candidates"] and report["reports"] == []
    assert report["selected_model"] is None


def test_benchmark_uses_separate_registry_and_unloads_between_candidates(tmp_path, monkeypatch):
    settings = Settings(llm_quality_path=tmp_path / "active.json")
    monkeypatch.setattr(benchmark, "load_settings", lambda: settings)
    monkeypatch.setattr(benchmark.boot, "detect_server", lambda: Hardware(ram_available=8 * GIB, available_threads=4))
    monkeypatch.setattr(benchmark.boot, "installed_models", lambda _: [{"name": "qwen3:0.6b"}, {"name": "granite4:350m"}])
    unloaded, paths = [], []
    def api(url, path, body=None, **kwargs):
        if body:
            unloaded.append(body["model"])
        return {"models": []}
    monkeypatch.setattr(benchmark.boot, "api", api)
    def ensure(self, client, **kwargs):
        paths.append(self.path)
        return {"model": client.name, "approved": True}
    monkeypatch.setattr(benchmark.QualityRegistry, "ensure", ensure)
    output = tmp_path / "comparison.json"
    assert benchmark.main(["--output", str(output)]) == 0
    assert unloaded == ["qwen3:0.6b", "granite4:350m"]
    assert all(path != settings.llm_quality_path for path in paths)
    assert not settings.llm_quality_path.exists()
