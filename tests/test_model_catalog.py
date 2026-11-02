import copy
import json

import pytest

from scripts import bootstrap as boot
from server.services.performance import GIB, Hardware


def test_catalog_offline_and_quality_first():
    catalog = boot.load_catalog(boot.ROOT)
    h = Hardware(ram_available=24 * GIB, available_threads=12)
    entry, installed = boot.select_recommendation(catalog, [], h)
    assert entry["name"] == "qwen2.5:14b" and not installed
    assert all(m["source"] and m["license"] and m["verified_at"] for m in catalog["models"])


def test_installed_alias_avoids_redundant_pull():
    catalog = boot.load_catalog(boot.ROOT)
    name = "qwen2.5:1.5b-instruct-q4_K_M"
    selected, installed = boot.select_recommendation(catalog, [{"name": name}],
                                                    Hardware(ram_available=24 * GIB, available_threads=12))
    assert installed and selected["name"] == name


@pytest.mark.parametrize("name", ["granite4:350m", "llama3.2:1b", "qwen3:0.6b", "gemma3:1b"])
def test_new_families_are_reused_when_installed(name):
    entry, installed = boot.select_recommendation(boot.load_catalog(boot.ROOT), [{"name": name}],
                                                 Hardware(ram_available=8 * GIB, available_threads=4))
    assert installed and entry["name"] == name


def test_low_thread_count_is_not_a_hard_model_size_requirement():
    catalog = boot.load_catalog(boot.ROOT)
    catalog = {**catalog, "models": [e for e in catalog["models"] if e["name"] == "qwen3:1.7b"]}
    entry, _ = boot.select_recommendation(catalog, [], Hardware(ram_available=8 * GIB, available_threads=1))
    assert entry["name"] == "qwen3:1.7b"


def test_unknown_or_restricted_installed_model_is_not_automatic():
    catalog = boot.load_catalog(boot.ROOT)
    selected, installed = boot.select_recommendation(catalog, [{"name": "random-instruct:1b"}, {"name": "qwen2.5:3b"}],
                                                    Hardware(ram_available=6 * GIB, available_threads=8))
    assert not installed and selected["name"] == "qwen2.5:1.5b"


def test_gpu_needs_free_vram_driver_and_system_ram():
    entry = boot.load_catalog(boot.ROOT)["models"][2]
    h = Hardware(ram_available=3 * GIB, available_threads=8, vram=8 * GIB,
                 vram_available=7 * GIB, acceleration_verified=True)
    assert boot.suitable(entry, h, provisional=True)
    h.vram_available = None
    assert not boot.suitable(entry, h)
    h.vram_available = GIB
    assert not boot.suitable(entry, h, provisional=True)
    h.ollama_vram = 6 * GIB
    assert boot.suitable(entry, h, provisional=True)
    h.acceleration_verified = False
    assert not boot.suitable(entry, h, provisional=True)
    h.acceleration_verified = True
    h.ram_available = GIB
    assert not boot.suitable(entry, h, provisional=True)


def test_loaded_cpu_weights_are_counted_without_inventing_unknown_ram():
    entry = boot.load_catalog(boot.ROOT)["models"][2]
    h = Hardware(ram_total=16 * GIB, ram_available=5 * GIB, ollama_ram=5 * GIB, available_threads=8)
    assert boot.suitable(entry, h)
    h.ram_total = None
    assert not boot.suitable(entry, h)


@pytest.mark.parametrize("key,value", [("download_bytes", True), ("min_memory_gib", float("nan")),
    ("context", 999999), ("cpu_supported", "yes"), ("source", "http://untrusted.test/model"),
    ("license", ""), ("source", []), ("aliases", ["../escape"]),
    ("aliases", ["qwen2.5:1.5b"]), ("recommended_vram_gib", 0.1),
    ("inference_memory_gib", -1), ("inference_memory_gib", float("nan")), ("inference_memory_gib", True)])
def test_external_catalog_data_is_validated(tmp_path, key, value):
    catalog = copy.deepcopy(boot.load_catalog(boot.ROOT))
    catalog["models"][0][key] = value
    path = tmp_path / "config/model-catalog.json"
    path.parent.mkdir()
    path.write_text(json.dumps(catalog), encoding="utf-8")
    with pytest.raises(boot.BootstrapError, match="Catálogo"):
        boot.load_catalog(tmp_path)
