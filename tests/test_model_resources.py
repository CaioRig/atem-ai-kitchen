from server.services import model_resources as resources


def test_sampler_tracks_rss_peak_and_cpu_without_claiming_gpu(monkeypatch):
    snapshots = iter([{1: (100, 1.)}, {1: (250, 2.), 2: (50, .1)}, {1: (120, 3.)}])
    monkeypatch.setattr(resources, "ollama_processes", lambda: next(snapshots))
    sampler = resources.ResourceSampler()
    sampler.started = resources.time.perf_counter() - 2
    for _ in range(3):
        sampler.sample()
    report = sampler.report()
    assert report["peak_ram_bytes"] == 300
    assert sampler.cpu_seconds == 2
    assert report["cpu_percent"] > 0 and report["gpu_percent"] is None


def test_unavailable_or_remote_metrics_are_unknown(monkeypatch):
    monkeypatch.setattr(resources, "ollama_processes", lambda: {})
    with resources.ResourceSampler() as sampler:
        assert sampler.peak is None
    assert sampler.report()["cpu_percent"] is None
    monkeypatch.setattr(resources, "ollama_processes", lambda: (_ for _ in ()).throw(AssertionError("remote")))
    with resources.ResourceSampler(enabled=False) as remote:
        assert remote.peak is None


def test_native_process_inventory_returns_accessible_metrics():
    rows = resources.ollama_processes()
    assert isinstance(rows, dict)
    assert all(pid > 0 and ram >= 0 and cpu >= 0 for pid, (ram, cpu) in rows.items())
