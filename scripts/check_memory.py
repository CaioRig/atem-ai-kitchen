r"""Verifica a memória persistente da Luna com a IA local (Ollama), sem microfone nem interface.

Uso:
  .venv\Scripts\python scripts\check_memory.py          # cenário completo em um banco TEMPORÁRIO (não toca no real)
  .venv\Scripts\python scripts\check_memory.py --real   # só lista o que está guardado no banco configurado ([memory] path)

O cenário cobre: guardar preferência e equipamento, consultar, pergunta à IA com o contexto da memória,
pergunta sobre algo que não está guardado (a IA deve dizer que não sabe), "reinício" (reabre o banco),
esquecer uma memória, apagar tudo com confirmação e falha de armazenamento simulada.
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.assistant.memory import MemoryCommands  # noqa: E402
from server.services.llm import LLMError, OllamaClient  # noqa: E402
from server.storage.config import ConfigError, load_settings  # noqa: E402
from server.storage.memory import KIND_LABELS, MemoryStore  # noqa: E402


def say(step: str, text: str) -> None:
    print(f"\n[{step}] {text}")


def voice(cmd: MemoryCommands, phrase: str) -> None:
    print(f"> {phrase}")
    out = cmd.handle(phrase)
    print(f"  Luna: {out.reply if out else '(não é comando de memória; iria para a IA)'}")


def ask(llm: OllamaClient | None, cmd: MemoryCommands, question: str) -> None:
    print(f"> {question}")
    context = cmd.context_for(question)
    print("  contexto enviado à IA:")
    for line in context.splitlines():
        print(f"    | {line}")
    if llm is None:
        print("  (IA indisponível: pergunta não enviada)")
        return
    t0 = time.perf_counter()
    try:
        reply = llm.reply(question, context=context)
    except LLMError as exc:
        print(f"  falha ({exc.code}): {exc.message}")
        return
    print(f"  Luna ({time.perf_counter() - t0:.1f} s): {reply}")


def main(argv: list[str]) -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"erro de configuracao: {exc}")
        return 2
    if "--real" in argv:
        store = MemoryStore(settings.memory_path)
        items = store.all()
        print(f"{len(items)} memória(s) em {store.path}")
        for m in items:
            print(f"  #{m.id} [{KIND_LABELS[m.kind]}] {m.text}")
        return 0

    llm: OllamaClient | None = None
    if settings.llm_enabled:
        llm = OllamaClient(settings.llm_url, settings.llm_model, system_prompt=settings.llm_system_prompt, timeout_s=settings.llm_timeout_s)
        reason = llm.check()
        if reason:
            print(f"IA indisponível ({reason}); as perguntas à IA serão puladas")
            llm = None
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "memory.sqlite3"
        store = MemoryStore(path)
        cmd = MemoryCommands(store)
        say("1", "guardar uma preferência e um equipamento")
        voice(cmd, "Luna, lembre que eu não gosto de cebola.")
        voice(cmd, "lembre que tenho uma air fryer")
        say("2", "consultar")
        voice(cmd, "Luna, o que você lembra sobre minha cozinha?")
        voice(cmd, "o que você lembra?")
        say("3", "pergunta à IA com memória relevante")
        ask(llm, cmd, "me sugere um jantar rápido para hoje?")
        say("4", "pergunta sobre algo que não está guardado (não deve inventar)")
        ask(llm, cmd, "qual é o meu prato favorito?")
        say("5", "reinício: fechar e reabrir o banco")
        store.close()
        store = MemoryStore(path)
        cmd = MemoryCommands(store)
        voice(cmd, "o que você lembra sobre minhas preferências?")
        say("6", "esquecer uma memória específica")
        voice(cmd, "Luna, esqueça que tenho uma air fryer.")
        voice(cmd, "o que você lembra sobre minha cozinha?")
        say("7", "apagar tudo exige confirmação")
        voice(cmd, "Luna, apague todas as minhas memórias.")
        voice(cmd, "que horas são?")  # cancela o pedido
        voice(cmd, "sim")  # sem pedido pendente: nao apaga
        print(f"  memórias ainda guardadas: {store.count()}")
        voice(cmd, "apague todas as minhas memórias")
        voice(cmd, "sim, pode apagar")
        print(f"  memórias guardadas: {store.count()}")
        say("8", "falha de armazenamento simulada (banco fechado)")
        store._conn.close()
        voice(cmd, "lembre que eu gosto de pimenta")
        ask(llm, cmd, "como faço arroz soltinho?")
    print("\nconcluído")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
