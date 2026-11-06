"""Ponto de entrada: `python -m server.main`."""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys

import uvicorn

from server.api.app import create_app
from server.storage.config import ConfigError, load_settings


def setup_logging(level: str, log_dir) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    rotating = logging.handlers.RotatingFileHandler(
        log_dir / "server.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    rotating.setFormatter(fmt)
    root.handlers[:] = [console, rotating]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--without-llm", action="store_true", help="iniciar os demais serviços sem a IA nesta execução")
    parser.add_argument("--validated-llm", action="store_true", help="exigir aprovação salva pelo inicializador, sem repetir testes")
    parser.add_argument("--llm-probed", action="store_true", help="usar o modelo manual testado pelo inicializador")
    parser.add_argument("--llm-unavailable", default="", help="motivo da indisponibilidade nesta execução")
    parser.add_argument("--llm-hardware-notice", default="", help="aviso de hardware do inicializador")
    parser.add_argument("--llm-model", help="modelo aprovado pelo inicializador")
    parser.add_argument("--llm-context", type=int, help="contexto aprovado pelo inicializador")
    parser.add_argument("--llm-threads", type=int, help="threads aprovadas pelo inicializador")
    parser.add_argument("--llm-profile", choices=("economic", "balanced", "performance"),
                        help="perfil usado na validação pelo inicializador")
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"erro de configuracao: {exc}", file=sys.stderr)
        return 2
    setup_logging(settings.log_level, settings.log_dir)
    if args.without_llm:
        settings.llm_enabled = False
    if args.validated_llm or args.llm_probed:
        if args.llm_model:
            settings.llm_model = args.llm_model
        if args.llm_context is not None:
            settings.llm_num_ctx = args.llm_context
        if args.llm_threads is not None:
            settings.llm_runtime_threads = args.llm_threads
        if args.llm_profile:
            settings.performance_runtime_profile = args.llm_profile
        settings.validate()
        logging.getLogger("jarvis.main").info("Modelo testado pelo inicializador: modelo=%s; contexto=%s; threads=%s",
            settings.llm_model, settings.llm_num_ctx, settings.llm_runtime_threads)
    settings.llm_quality_cached_only = args.validated_llm
    settings.llm_manual_probed = args.llm_probed
    settings.llm_unavailable_reason = args.llm_unavailable
    settings.llm_hardware_notice = args.llm_hardware_notice
    app = create_app(settings)
    tls: dict[str, str] = {}
    if settings.tls_enabled:
        tls = {"ssl_certfile": str(settings.tls_certfile), "ssl_keyfile": str(settings.tls_keyfile)}
    else:
        logging.getLogger("jarvis.main").warning(
            "HTTPS desativado: o microfone so funciona em http://localhost ou com "
            "server.tls_certfile/tls_keyfile (scripts/make-cert.ps1)"
        )
    uvicorn.run(
        app,
        host=settings.bind_host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        access_log=False,  # evita registrar URLs/IPs a cada requisicao estatica
        ws_ping_interval=20,
        ws_ping_timeout=20,
        **tls,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
