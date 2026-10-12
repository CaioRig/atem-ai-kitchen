r"""Verifica o assistente de receitas e os ingredientes disponíveis com a IA local (Ollama), sem microfone.

Uso:
  .venv\Scripts\python scripts\check_recipes.py            # cenário completo em um banco TEMPORÁRIO (não toca no real)
  .venv\Scripts\python scripts\check_recipes.py --no-llm   # só os comandos de ingredientes e o contexto montado

O cenário cobre: registrar, consultar e descontar ingredientes; sugestão de receita a partir de ingredientes
informados; receita completa para duas pessoas; adaptação da mesma receita para quatro; substituição de um
ingrediente "nesta receita"; pergunta com preferência guardada na memória; pergunta de segurança alimentar;
"o que faço com o que tenho" com o estoque vazio e com estoque. Mostra o contexto enviado à IA, a resposta
completa e o que seria falado em voz alta.
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.assistant.ingredients import IngredientCommands  # noqa: E402
from server.assistant.memory import MemoryCommands  # noqa: E402
from server.assistant.recipes import RecipeAssistant  # noqa: E402
from server.services.llm import LLMError, OllamaClient  # noqa: E402
from server.storage.config import ConfigError, load_settings  # noqa: E402
from server.storage.memory import MemoryStore  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def say(step: str, text: str) -> None:
    print(f"\n[{step}] {text}")


def voice(recipes: RecipeAssistant, memory: MemoryCommands, phrase: str) -> None:
    print(f"> {phrase}")
    out = recipes.handle(phrase) or memory.handle(phrase)
    print(f"  Luna: {out.reply if out else '(não é comando local; iria para a IA)'}")


def ask(llm: OllamaClient | None, recipes: RecipeAssistant, memory: MemoryCommands, question: str, *, show_context: bool = True) -> None:
    print(f"> {question}")
    local = recipes.handle(question)
    if local is not None:
        print(f"  Luna (sem IA): {local.reply}")
        return
    context = "\n\n".join(p for p in (memory.context_for(question), recipes.context_for(question)) if p)
    limit = recipes.max_tokens_for(question)
    if show_context:
        print(f"  contexto enviado à IA ({len(context)} caracteres, max_tokens={limit or 'padrão'}):")
        for line in context.splitlines():
            print(f"    | {line[:160]}")
    if llm is None:
        print("  (IA indisponível: pergunta não enviada)")
        return
    t0 = time.perf_counter()
    try:
        reply = llm.reply(question, context=context, max_tokens=limit)
    except LLMError as exc:
        print(f"  falha ({exc.code}): {exc.message}")
        return
    spoken = recipes.spoken_text(reply, question)
    recipes.remember_reply(question, reply)
    print(f"  Luna ({time.perf_counter() - t0:.1f} s, {len(reply)} caracteres):")
    for line in reply.splitlines():
        print(f"    {line}")
    if spoken != reply:
        print(f"  falado em voz alta: {spoken}")


def main(argv: list[str]) -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"erro de configuracao: {exc}")
        return 2
    llm: OllamaClient | None = None
    if settings.llm_enabled and "--no-llm" not in argv:
        llm = OllamaClient(
            settings.llm_url, settings.llm_model, system_prompt=settings.llm_system_prompt, timeout_s=max(settings.llm_timeout_s, 120.0),
            temperature=settings.llm_temperature, max_tokens=settings.llm_max_tokens,
        )
        reason = llm.check()
        if reason:
            print(f"IA indisponível ({reason}); as perguntas à IA serão puladas")
            llm = None
    with tempfile.TemporaryDirectory() as tmp:
        store = MemoryStore(Path(tmp) / "memory.sqlite3")
        memory = MemoryCommands(store)
        recipes = RecipeAssistant(
            IngredientCommands(store),
            max_tokens=settings.recipes_max_tokens,
            spoken_max_chars=settings.recipes_spoken_max_chars,
            followup_ttl_s=settings.recipes_followup_ttl_s,
        )
        say("1", "estoque vazio: 'o que faço com o que tenho' é respondido sem a IA")
        ask(llm, recipes, memory, "Luna, o que posso fazer com o que tenho em casa?")
        say("2", "registrar, consultar e descontar ingredientes (sem IA)")
        voice(recipes, memory, "Luna, tenho três ovos e um pacote de arroz.")
        voice(recipes, memory, "comprei dois tomates e meio quilo de carne moída")
        voice(recipes, memory, "Quais ingredientes tenho disponíveis?")
        voice(recipes, memory, "Luna, usei dois ovos. Atualize o que ainda tenho.")
        voice(recipes, memory, "quantos ovos tenho?")
        voice(recipes, memory, "tenho uma air fryer")  # nao e estoque: segue para a IA
        voice(recipes, memory, "lembre que tenho uma air fryer")  # memoria de cozinha, como antes
        voice(recipes, memory, "lembre que eu não gosto de cebola")
        say("3", "sugestão a partir de ingredientes informados (não do estoque)")
        ask(llm, recipes, memory, "Luna, o que posso fazer com ovos, arroz e tomate?")
        say("4", "receita completa para duas pessoas (resumo falado, detalhes na tela)")
        ask(llm, recipes, memory, "Me ensine a fazer panqueca para duas pessoas.")
        say("5", "adaptar a mesma receita para quatro pessoas (continuação sem histórico de conversa)")
        ask(llm, recipes, memory, "Adapte essa receita para quatro pessoas.", show_context=False)
        say("6", "substituição nesta receita")
        ask(llm, recipes, memory, "Como posso substituir o leite nesta receita?", show_context=False)
        say("7", "preferência guardada é considerada")
        ask(llm, recipes, memory, "me sugere um jantar rápido com o que tenho?", show_context=False)
        say("8", "segurança alimentar")
        ask(llm, recipes, memory, "o frango está na geladeira há cinco dias, ainda posso comer?", show_context=False)
        store.close()
    print("\nconcluído")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
