"""Ponto de entrada: `python -m server.main`."""
from __future__ import annotations

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


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"erro de configuracao: {exc}", file=sys.stderr)
        return 2
    setup_logging(settings.log_level, settings.log_dir)
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
