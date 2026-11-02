"""Benchmark sequencial de candidatos instalados e compatíveis, sem downloads/configuração."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import bootstrap as boot
from server.services.model_quality import QualityRegistry, configured_client
from server.storage.config import load_settings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="refazer resultados salvos")
    parser.add_argument("--limit", type=int, default=3, choices=range(1, 4))
    parser.add_argument("--output", type=Path, default=ROOT / "data/catalog-benchmark.json")
    args = parser.parse_args(argv)
    settings = load_settings()
    if not boot.is_local_inference(settings.llm_url):
        parser.error("Este benchmark mede processos locais; configure Ollama local.")
    h = boot.detect_server()
    catalog = boot.load_catalog(ROOT)
    models = boot.installed_models(settings.llm_url)
    reserve = sum(boot.memory_reserve(settings).values())
    entries = [{**e, "min_memory_gib": e.get("inference_memory_gib", e["min_memory_gib"]) + reserve}
               for e in catalog["models"]]
    feasible = [e for e in entries if e["automatic_allowed"] and boot.suitable(e, h)]
    installed = {m["name"] for m in models}
    candidates = [e for e in feasible if any(n in installed for n in (e["name"], *e.get("aliases", [])))]
    # Separate registry: comparative runs must not replace the active/last approved model.
    registry = QualityRegistry(args.output.with_name(args.output.stem + "-quality.json"))
    reports = []
    client = configured_client(settings, h)
    for entry in sorted(candidates, key=lambda e: e["quality_rank"], reverse=True)[:args.limit]:
        if any(m.get("name") not in (entry["name"], *entry.get("aliases", [])) for m in boot.api(settings.llm_url, "/api/ps").get("models", [])):
            reports.append({"model": entry["name"], "status": "adiado: outro modelo carregado"})
            continue
        name = next(n for n in (entry["name"], *entry.get("aliases", [])) if n in installed)
        probe = client.clone(name=name)
        probe.num_ctx = settings.llm_num_ctx or min(2048, entry["context"])
        fresh = boot.detect_server()
        if not boot.suitable(entry, fresh):
            reports.append({"model": name, "status": "adiado: memória disponível mudou"})
            continue
        try:
            report = registry.ensure(probe, force=args.force)
            reports.append({**report,
                            "options": probe.options(), "conditions": "primeira resposta pode incluir carga; respostas seguintes podem estar quentes; keep_alive da aplicação"})
        except (RuntimeError, ValueError, OSError) as exc:
            reports.append({"model": name, "status": type(exc).__name__, "approved": False})
        finally:
            try:
                boot.api(settings.llm_url, "/api/generate", {"model": name, "keep_alive": 0}, timeout=30)
            except (RuntimeError, ValueError, OSError):
                break  # cannot safely load another candidate while unloading is unconfirmed
    result = {"hardware": asdict(h), "catalog_revision": catalog["revision"],
              "compatible_candidates": [e["name"] for e in feasible], "installed": sorted(installed),
              "reports": reports, "selected_model": None,
              "note": "Somente comparação; não modifica preferências nem aprovação ativa. Nenhum download. Ausência de teste não é reprovação."}
    boot.write_json(args.output, result)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
