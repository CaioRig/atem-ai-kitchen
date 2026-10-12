r"""Verifica a IA local (Ollama) configurada em config/settings.toml, sem microfone nem interface.

Uso:
  .venv\Scripts\python scripts\check_llm.py                      # so verifica servidor e modelo
  .venv\Scripts\python scripts\check_llm.py "Como faço arroz soltinho?"   # envia uma pergunta
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.services.llm import LLMError, OllamaClient  # noqa: E402
from server.storage.config import ConfigError, load_settings  # noqa: E402


def main(argv: list[str]) -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"erro de configuracao: {exc}")
        return 2
    if not settings.llm_enabled:
        print("IA local desativada em config/settings.toml ([llm] enabled = false)")
        return 1
    llm = OllamaClient(
        settings.llm_url,
        settings.llm_model,
        system_prompt=settings.llm_system_prompt,
        connect_timeout_s=settings.llm_connect_timeout_s,
        timeout_s=settings.llm_timeout_s,
        keep_alive=settings.llm_keep_alive,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
    )
    print(f"ollama={llm.address} modelo={llm.name}")
    reason = llm.check()
    if reason:
        print(f"indisponivel: {reason}")
        return 1
    print("servidor e modelo prontos")
    if not argv:
        return 0
    question = " ".join(argv)
    print(f"> {question}")
    t0 = time.perf_counter()
    first: list[float] = []

    def on_delta(piece: str) -> None:
        if not first:
            first.append(time.perf_counter() - t0)
        print(piece, end="", flush=True)

    try:
        text = llm.reply(question, on_delta)
    except LLMError as exc:
        print(f"\nfalha ({exc.code}): {exc.message}")
        if exc.detail:
            print(f"detalhe: {exc.detail}")
        return 1
    total = time.perf_counter() - t0
    print(f"\n\nprimeiro trecho em {first[0]:.2f} s, resposta completa em {total:.2f} s, {len(text)} caracteres")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
