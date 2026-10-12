"""Fase 7: receitas e ingredientes disponíveis (estoque na memória, comandos por voz, contexto para a IA,
continuação da última receita, fala resumida, interface e configuração)."""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from server.api.app import create_app
from server.assistant.conversation import DETAIL_READY, Conversation
from server.assistant.ingredients import IngredientCommands, describe, parse, parse_items, stock_summary
from server.assistant.memory import REPLY_UNAVAILABLE, MemoryCommands
from server.assistant.recipes import (
    RECIPE_GUIDE,
    SAFETY_GUIDE,
    SPOKEN_DETAILS_HINT,
    SUGGESTION_GUIDE,
    RecipeAssistant,
    is_followup,
    is_recipe_request,
    looks_like_recipe,
    mentions_safety,
    refers_to_stock,
    spoken_summary,
)
from server.assistant.state import AssistantState, StateMachine
from server.storage.config import ConfigError, Settings
from server.storage.memory import (
    MemoryStore,
    format_ingredient,
    ingredient_key,
    parse_ingredient,
    plural_unit,
    singular,
)
from tests.test_conversation import FakeLLM, settle
from tests.test_memory import TOKEN, collect_until, hello, settings_for
from tests.test_voice import FakeDetector, FakeTranscriber, silence, speech, wake_frame

PANCAKE = """Panqueca simples para duas pessoas, pronta em cerca de 20 minutos, com farinha, ovo e leite.

Ingredientes:
1 xícara de farinha de trigo
1 ovo
1 xícara de leite
1 colher de sopa de óleo
1 pitada de sal

Preparo:
1. Bata todos os ingredientes no liquidificador até ficar liso.
2. Aqueça uma frigideira antiaderente untada em fogo médio.
3. Despeje uma concha de massa, espalhe e doure dos dois lados.
4. Repita até acabar a massa e recheie a gosto.
Dica: deixe a massa descansar dez minutos para panquecas mais macias."""


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---- Armazenamento (mesma tabela da memória) ---------------------------------------------------------------


def test_store_migrates_schema_created_before_phase_7(tmp_path):
    path = tmp_path / "mem.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, profile TEXT NOT NULL DEFAULT 'default',
        kind TEXT NOT NULL CHECK (kind IN ('preference', 'kitchen', 'instruction', 'note')), text TEXT NOT NULL,
        normalized TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL, UNIQUE (profile, normalized))"""
    )
    conn.execute("CREATE INDEX memories_profile_kind ON memories (profile, kind)")
    conn.execute("INSERT INTO memories VALUES (7, 'default', 'preference', 'eu não gosto de cebola', 'eu nao gosto de cebola', 1.0, 1.0)")
    conn.commit()
    conn.close()
    store = MemoryStore(path)  # recria a tabela com o tipo `ingredient`, preservando ids e dados
    assert [(m.id, m.kind, m.text) for m in store.all()] == [(7, "preference", "eu não gosto de cebola")]
    mem, status, _ = store.set_ingredient("ovos", 3, None)
    assert status == "created" and mem.kind == "ingredient" and mem.text == "ovos: 3 unidades" and mem.id == 8
    store.close()
    again = MemoryStore(path)  # segunda abertura: nada a migrar
    assert again.count() == 2 and again.count("ingredient") == 1
    again.close()


def test_store_ingredient_text_helpers():
    assert [singular(w) for w in ("ovos", "limões", "pães", "nozes", "cereais", "atuns", "tomates", "arroz", "pires")] == [
        "ovo", "limão", "pão", "noz", "cereal", "atum", "tomate", "arroz", "pires",
    ]
    assert ingredient_key("Ovos de codorna") == "ovo de codorna" and ingredient_key("ovo") == ingredient_key("OVOS")
    assert format_ingredient("ovos", 3, None) == "ovos: 3 unidades"
    assert format_ingredient("carne moída", 500, "grama") == "carne moída: 500 gramas"
    assert format_ingredient("leite", 1.5, "litro") == "leite: 1,5 litros"
    assert format_ingredient("tomate", None, None) == "tomate: quantidade não informada"
    assert parse_ingredient("ovos: 3 unidades") == ("ovos", 3.0, None)
    assert parse_ingredient("açúcar: 2 colheres de sopa") == ("açúcar", 2.0, "colher de sopa")
    assert parse_ingredient("tomate: quantidade não informada") == ("tomate", None, None)
    assert plural_unit("colher de sopa", 2) == "colheres de sopa" and plural_unit("pacote", 1) == "pacote"
    assert describe("ovos", 1, None) == "1 ovo" and describe("ovo", 3, None) == "3 ovos"
    assert describe("arroz", 0.5, "pacote") == "meio pacote de arroz" and describe("leite", 0.5, "xícara") == "meia xícara de leite"
    assert describe("tomate", None, None) == "tomate, sem quantidade informada"


def test_store_set_find_remove_ingredients(tmp_path):
    store = MemoryStore(tmp_path / "mem.sqlite3")
    store.add("kitchen", "tenho uma air fryer")
    mem, status, previous = store.set_ingredient("ovos", 3, None)
    assert status == "created" and previous is None and store.count("ingredient") == 1
    assert store.find_ingredient("ovo").id == mem.id and store.find_ingredient("OVOS").id == mem.id
    same, status, _ = store.set_ingredient("ovos", 3, None)
    assert status == "exists" and same.id == mem.id
    updated, status, previous = store.set_ingredient("ovo", 1, None)
    assert status == "updated" and updated.id == mem.id and updated.text == "ovo: 1 unidade" and previous.text == "ovos: 3 unidades"
    assert [m.text for m in store.ingredients()] == ["ovo: 1 unidade"]
    assert store.find_ingredient("arroz") is None and store.remove_ingredient("arroz") is None
    assert store.remove_ingredient("ovos").id == mem.id and store.ingredients() == []
    store.set_ingredient("arroz", 1, "pacote")
    store.set_ingredient("tomate", None, None)
    assert store.clear_ingredients() == 2 and store.count() == 1  # a memoria de cozinha fica
    # O estoque nao polui o contexto de memorias comuns: `search` com tipos explicitos o ignora.
    store.set_ingredient("cebola", 2, None)
    assert [m.kind for m in store.search("me sugere um jantar", kinds=("preference", "kitchen", "note"))] == ["kitchen"]


# ---- Interpretação das frases ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, name, items",
    [
        ("Luna, tenho três ovos e um pacote de arroz.", "set", [("ovos", 3.0, None), ("arroz", 1.0, "pacote")]),
        ("tenho 500g de carne moída e duas cebolas", "set", [("carne moída", 500.0, "grama"), ("cebolas", 2.0, None)]),
        ("tenho vinte e cinco ovos", "set", [("ovos", 25.0, None)]),
        ("tenho arroz e feijão", "set", [("arroz", None, None), ("feijão", None, None)]),
        ("tenho em casa macarrão, molho de tomate e queijo", "set", [("macarrão", None, None), ("molho de tomate", None, None), ("queijo", None, None)]),
        ("na geladeira tem um pote de iogurte e três tomates", "set", [("iogurte", 1.0, "pote"), ("tomates", 3.0, None)]),
        ("lembre que tenho duas colheres de sopa de manteiga", "set", [("manteiga", 2.0, "colher de sopa")]),
        ("registre os ingredientes: farinha de trigo, açúcar e fermento", "set", [("farinha de trigo", None, None), ("açúcar", None, None), ("fermento", None, None)]),
        ("comprei dois litros de leite, meia dúzia de ovos e um quilo e meio de batata", "add", [("leite", 2.0, "litro"), ("ovos", 6.0, None), ("batata", 1.5, "quilo")]),
        ("adicione duas latas de milho na despensa", "add", [("milho", 2.0, "lata")]),
        ("Luna, usei dois ovos. Atualize o que ainda tenho.", "use", [("ovos", 2.0, None)]),
        ("gastei 200 gramas de carne moída", "use", [("carne moída", 200.0, "grama")]),
        ("comi duas bananas", "use", [("bananas", 2.0, None)]),
        ("usei ovos", "use", [("ovos", None, None)]),
        ("acabou o arroz", "remove", [("arroz", None, None)]),
        ("os ovos acabaram", "remove", [("ovos", None, None)]),
        ("não tenho mais leite", "remove", [("leite", None, None)]),
        ("tire o queijo da lista", "remove", [("queijo", None, None)]),
        ("quantos ovos tenho?", "check", [("ovos", None, None)]),
        ("tenho ovos?", "check", [("ovos", None, None)]),
        ("tem leite na geladeira?", "check", [("leite", None, None)]),
    ],
)
def test_parse_stock_commands(text, name, items):
    intent = parse(text)
    assert intent is not None and intent.name == name, intent
    assert [(i.name, i.quantity, i.unit) for i in intent.items] == items


@pytest.mark.parametrize(
    "text",
    [
        "Quais ingredientes tenho disponíveis?",
        "quais são os meus ingredientes?",
        "o que tenho na geladeira?",
        "o que tem na despensa",
        "mostra minha lista de ingredientes",
    ],
)
def test_parse_stock_list(text):
    assert parse(text).name == "list"


@pytest.mark.parametrize(
    "text",
    [
        "tenho uma air fryer",
        "lembre que tenho uma air fryer",
        "tenho que comprar ovos",
        "tenho fome",
        "tenho um filho alérgico a amendoim",
        "tenho uma dúvida sobre o bolo",
        "tem como fazer bolo sem ovo?",
        "tem como fazer bolo sem ovo",
        "o que posso fazer com ovos, arroz e tomate?",
        "quais ingredientes vão no bolo de cenoura?",
        "comprei um presente",
        "tenho 30 anos",
        "usei a air fryer",
        "a reunião acabou",
        "me ensine a fazer panqueca para duas pessoas",
        "usei dois ovos. Como faço omelete?",
        "como faço arroz soltinho?",
        "sim",
    ],
)
def test_parse_leaves_other_sentences_alone(text):
    assert parse(text) is None, parse(text)


def test_parse_fractions_and_items():
    assert [(i.name, i.fraction) for i in parse("usei o resto do leite").items] == [("leite", 1.0)]
    assert [(i.name, i.fraction) for i in parse("usei metade do arroz").items] == [("arroz", 0.5)]
    assert [(i.name, i.quantity) for i in parse_items("três ovos, dois tomates e mais um limão")] == [("ovos", 3.0), ("tomates", 2.0), ("limão", 1.0)]
    assert parse_items("um pouco de sal")[0].quantity is None  # quantificador vago: sem numero inventado


# ---- Comandos ---------------------------------------------------------------------------------------------


def test_commands_register_query_use_and_remove(tmp_path):
    store = MemoryStore(tmp_path / "mem.sqlite3")
    cmd = IngredientCommands(store)
    assert cmd.handle("como faço arroz?") is None
    out = cmd.handle("Luna, tenho três ovos e um pacote de arroz.")
    assert out.reply == "Anotei: 3 ovos e 1 pacote de arroz." and out.event["action"] == "stock_saved"
    assert [i["text"] for i in out.event["items"]] == ["ovos: 3 unidades", "arroz: 1 pacote"] and out.event["count"] == 2
    out = cmd.handle("Quais ingredientes tenho disponíveis?")
    assert out.reply == "Você tem: 3 ovos e 1 pacote de arroz." and out.event["action"] == "list"
    assert out.event["topic"] == "ingredientes disponíveis" and out.event["spoken"] is True and len(out.event["items"]) == 2
    out = cmd.handle("Luna, usei dois ovos. Atualize o que ainda tenho.")
    assert out.reply == "Sobrou 1 ovo." and out.event["action"] == "stock_updated" and store.find_ingredient("ovos").text == "ovo: 1 unidade"
    assert cmd.handle("quantos ovos tenho?").reply == "Você tem 1 ovo."
    assert cmd.handle("comprei meia dúzia de ovos").reply == "Atualizei: 7 ovos (antes 1 unidade)."
    assert cmd.handle("usei três ovos").reply == "Sobraram 4 ovos."
    assert cmd.handle("usei quatro ovos").reply == "Ovos acabaram; tirei da lista."
    assert cmd.handle("tenho ovos?").reply.startswith("Não encontrei ovos na lista")
    assert cmd.handle("agora tenho cinco ovos").reply == "Anotei: 5 ovos."
    assert cmd.handle("tenho cinco ovos").reply == "Já estava anotado: 5 ovos."
    assert cmd.handle("tenho três ovos").reply == "Atualizei: 3 ovos (antes 5 unidades)."  # "tenho" e o estado atual
    out = cmd.handle("acabou o arroz")
    assert out.reply == "Tirei da lista: arroz." and out.event["action"] == "stock_removed" and store.count("ingredient") == 1
    assert cmd.handle("tire o leite da lista").reply == "Leite não estava na lista."
    out = cmd.handle("apague todos os ingredientes")
    assert out.reply == "Tirei 1 ingrediente da lista." and store.count("ingredient") == 0
    assert cmd.handle("quais ingredientes tenho?").reply.startswith("Não tenho nenhum ingrediente registrado")
    assert cmd.commands == 15 and cmd.failures == 0


def test_commands_never_invent_quantities(tmp_path):
    store = MemoryStore(tmp_path / "mem.sqlite3")
    cmd = IngredientCommands(store)
    assert cmd.handle("tenho tomate").reply == "Anotei: tomate, sem quantidade informada."
    assert cmd.handle("usei um tomate").reply == "Você usou 1 tomate, mas eu não sabia quanto tinha; tomate continua sem quantidade."
    cmd.handle("tenho um pacote de arroz")
    out = cmd.handle("usei 200 gramas de arroz")  # unidades diferentes: nao da para descontar
    assert out.reply == "Não consigo descontar 200 gramas de arroz de 1 pacote de arroz; deixei arroz sem quantidade."
    assert store.find_ingredient("arroz").text == "arroz: quantidade não informada"
    assert cmd.handle("comprei dois pacotes de arroz").reply == "Não sei o total de arroz agora; me diga quanto tem."
    assert cmd.handle("usei o resto do arroz").reply == "Arroz acabou; tirei da lista."
    cmd.handle("tenho 500 gramas de carne moída")
    assert cmd.handle("usei metade da carne moída").reply == "Sobrou 250 gramas de carne moída."
    assert cmd.handle("usei ovos").reply == "Ovos não estava na lista, então não descontei nada."
    cmd.handle("tenho seis ovos")
    assert cmd.handle("usei ovos").reply == "Anotei que você usou ovos; não sei quanto sobrou, me diga depois."
    assert cmd.handle("quantos ovos tenho?").reply == "Tem ovos registrado, mas sem quantidade. Me diga quanto tem e eu anoto."
    assert stock_summary(store.ingredients()) == "Você tem: 250 gramas de carne moída. Sem quantidade informada: tomate e ovos."


def test_commands_without_store_or_with_broken_store(tmp_path):
    none = IngredientCommands(None)
    assert none.handle("como faço arroz") is None and none.stock() == []
    out = none.handle("tenho três ovos")
    assert out.reply == REPLY_UNAVAILABLE and out.event["action"] == "error"
    store = MemoryStore(tmp_path / "mem.sqlite3")
    broken = IngredientCommands(store)
    store._conn.close()
    assert broken.handle("tenho três ovos").event["action"] == "error" and broken.failures == 1


def test_memory_recall_and_panel_see_ingredients(tmp_path):
    store = MemoryStore(tmp_path / "mem.sqlite3")
    IngredientCommands(store).handle("tenho três ovos")
    memory = MemoryCommands(store)
    out = memory.handle("o que você lembra sobre meus ingredientes?")
    assert out.reply == "Sobre os seus ingredientes, eu lembro: ovos: 3 unidades." and out.event["items"][0]["kind"] == "ingredient"
    assert memory.handle("esqueça os ovos").event["action"] == "deleted" and store.count() == 0  # "Esquecer" tambem vale


# ---- Receitas: detectores, resumo falado e contexto -------------------------------------------------------


def test_recipe_detectors():
    for q in ("Luna, o que posso fazer com ovos, arroz e tomate?", "Me ensine a fazer panqueca para duas pessoas.", "como faço arroz soltinho?",
              "me sugere um jantar rápido", "Como posso substituir o leite nesta receita?", "Adapte essa receita para quatro pessoas."):
        assert is_recipe_request(q), q
    for q in ("que horas são?", "qual a capital da França?", "lembre que eu não gosto de cebola"):
        assert not is_recipe_request(q), q
    for q in ("Adapte essa receita para quatro pessoas.", "Como posso substituir o leite nesta receita?", "e para seis pessoas?", "me ensina a primeira", "quero a segunda opção"):
        assert is_followup(q), q
    for q in ("Me ensine a fazer panqueca para duas pessoas.", "como faço arroz soltinho?", "que horas são?"):
        assert not is_followup(q), q
    assert mentions_safety("o frango está na geladeira há cinco dias, ainda posso comer?") and not mentions_safety("como faço arroz?")
    assert refers_to_stock("o que posso fazer com o que tenho em casa?") and not refers_to_stock("o que posso fazer com ovos e arroz?")
    assert looks_like_recipe(PANCAKE) and not looks_like_recipe("Pode trocar o leite por água na mesma medida.")


def test_spoken_summary_keeps_details_on_screen():
    spoken = spoken_summary(PANCAKE, 420)
    assert spoken == f"Panqueca simples para duas pessoas, pronta em cerca de 20 minutos, com farinha, ovo e leite. {SPOKEN_DETAILS_HINT}"
    assert spoken_summary(PANCAKE, 0) == PANCAKE  # 0 = falar tudo
    assert spoken_summary(PANCAKE, 2000) == PANCAKE  # curta o bastante
    long_plain = "Frase comum. " * 60
    assert spoken_summary(long_plain, 420) == long_plain  # sem estrutura de receita: comportamento anterior
    headless = "1. Bata os ovos.\n2. Frite.\n3. Sirva.\n" * 12
    assert spoken_summary(headless, 100).endswith(SPOKEN_DETAILS_HINT)


def test_recipe_assistant_context_stock_preferences_followup_and_safety(tmp_path):
    store = MemoryStore(tmp_path / "mem.sqlite3")
    now = [1000.0]
    recipes = RecipeAssistant(IngredientCommands(store), max_tokens=555, followup_ttl_s=60.0, clock=lambda: now[0])
    # Pergunta comum: nada de receita, nada de estoque.
    assert recipes.context_for("qual a capital da França?") == "" and recipes.max_tokens_for("qual a capital da França?") is None
    # Receita com estoque vazio: orientacao + aviso de que nao ha ingredientes registrados.
    ctx = recipes.context_for("me ensine a fazer panqueca")
    assert ctx.startswith(RECIPE_GUIDE) and "registrados pela pessoa: nenhum" in ctx and "Segurança alimentar" in ctx
    assert recipes.max_tokens_for("me ensine a fazer panqueca") == 555
    # Estoque registrado entra separado do que a pessoa cita, com a regra de nao inventar quantidades.
    recipes.handle("tenho três ovos e um pacote de arroz")
    recipes.handle("tenho tomate")
    ctx = recipes.context_for("o que posso fazer com ovos e queijo?")
    assert "- 3 ovos" in ctx and "- 1 pacote de arroz" in ctx and "- tomate, sem quantidade informada" in ctx
    assert "não a invente" in ctx and "Ingredientes citados na pergunta são o que a pessoa quer usar" in ctx
    # Pergunta de comida sem ser receita tambem ve o estoque (sem a orientacao de receita).
    ctx = recipes.context_for("o que tenho dá para um lanche?")
    assert "- 3 ovos" in ctx
    # Continuacao: so depois de uma receita respondida, e so por followup_ttl_s.
    assert recipes.followup_for("adapte essa receita para quatro pessoas") is None
    recipes.remember_reply("me ensine a fazer panqueca para duas pessoas", PANCAKE)
    ctx = recipes.context_for("Adapte essa receita para quatro pessoas.")
    assert "Sua resposta anterior:" in ctx and "Panqueca simples" in ctx and recipes.max_tokens_for("e para seis pessoas?") == 555
    assert "Sua resposta anterior" not in recipes.context_for("como faço arroz soltinho?")  # pergunta nova nao e continuacao
    now[0] += 61
    assert recipes.followup_for("Adapte essa receita para quatro pessoas.") is None and not recipes.has_recent_recipe
    # Seguranca alimentar: orientacao completa quando a pergunta fala de conservacao/deterioracao.
    assert SAFETY_GUIDE in recipes.context_for("o frango está na geladeira há cinco dias, ainda posso comer?")
    assert recipes.recipe_requests >= 2 and recipes.followups == 1


def test_recipe_assistant_answers_stock_questions_locally_when_empty(tmp_path):
    store = MemoryStore(tmp_path / "mem.sqlite3")
    recipes = RecipeAssistant(IngredientCommands(store))
    out = recipes.handle("Luna, o que posso fazer com o que tenho em casa?")
    assert out is not None and out.reply.startswith("Ainda não tenho nenhum ingrediente registrado") and out.event["action"] == "list"
    recipes.handle("tenho três ovos")
    assert recipes.handle("o que posso fazer com o que tenho em casa?") is None  # com estoque, a IA responde
    assert recipes.handle("o que posso fazer com ovos e arroz?") is None  # ingredientes citados nao sao estoque
    assert RecipeAssistant(None).handle("o que posso fazer com o que tenho?") is None  # sem memoria: segue para a IA
    assert "não há registro de estoque" in RecipeAssistant(None).context_for("me ensine a fazer panqueca")
    assert RecipeAssistant(None).context_for("o que é bom para o jantar?") == ""  # sem memoria e sem receita: como antes


def test_suggestions_are_spoken_in_full_and_followups_skip_the_stock(tmp_path):
    store = MemoryStore(tmp_path / "mem.sqlite3")
    recipes = RecipeAssistant(IngredientCommands(store), spoken_max_chars=100)
    recipes.handle("tenho três ovos")
    question = "Luna, o que posso fazer com ovos, arroz e tomate?"
    ctx = recipes.context_for(question)
    assert ctx.startswith(SUGGESTION_GUIDE) and "- 3 ovos" in ctx
    options = "Com esses ingredientes dá para fazer três coisas:\n1. Omelete de arroz.\n2. Arroz de forno com tomate.\n3. Tomate recheado com arroz e ovo.\nQual você quer em detalhe?"
    assert recipes.spoken_text(options, question) == options  # as opcoes sao o que a pessoa quer ouvir
    assert recipes.spoken_text(options, "me ensine a fazer omelete") != options  # receita longa: so a abertura
    recipes.remember_reply(question, options)
    ctx = recipes.context_for("me ensina a primeira")
    assert "Sua resposta anterior:" in ctx and "- 3 ovos" not in ctx and SUGGESTION_GUIDE not in ctx
    assert "- 3 ovos" in recipes.context_for("adapte essa receita para o que tenho em casa")  # estoque so se citado


# ---- Conversa (sem Ollama) ---------------------------------------------------------------------------------


class FakeSpeaker:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    async def speak(self, text: str) -> bool:
        self.spoken.append(text)
        return False  # sem voz real: a conversa volta a "Pronta" sozinha


def make_conversation(store, llm=None, **kw):
    machine = StateMachine(AssistantState.IDLE)
    replies: list[tuple[str, bool]] = []
    events: list[dict] = []

    async def on_reply(text, final):
        replies.append((text, final))

    async def on_memory(event):
        events.append(event)

    llm = llm if llm is not None else FakeLLM()
    speaker = FakeSpeaker()
    recipes = RecipeAssistant(IngredientCommands(store), **kw)
    conv = Conversation(
        machine, llm, on_reply=on_reply, on_memory=on_memory, done_hold_s=0.05, speaker=speaker,
        memory=MemoryCommands(store), recipes=recipes,
    )
    return machine, conv, llm, replies, events, speaker, recipes


@pytest.mark.anyio
async def test_conversation_stock_commands_bypass_llm_and_recipes_get_context(tmp_path):
    store = MemoryStore(tmp_path / "mem.sqlite3")
    machine, conv, llm, replies, events, speaker, _ = make_conversation(store)
    assert await conv.ask("Luna, tenho três ovos e um pacote de arroz.", prompt_id=1) is True
    await settle(conv)
    assert llm.calls == [] and conv.memory_replies == 1 and (machine.state, machine.detail) == (AssistantState.IDLE, DETAIL_READY)
    assert replies[-1] == ("Anotei: 3 ovos e 1 pacote de arroz.", True) and events[-1]["action"] == "stock_saved" and events[-1]["type"] == "memory"
    assert speaker.spoken == ["Anotei: 3 ovos e 1 pacote de arroz."]
    await conv.ask("lembre que eu não gosto de cebola", prompt_id=2)  # memoria comum continua igual
    await settle(conv)
    assert replies[-1][0] == "Anotado como preferência: você não gosta de cebola." and llm.calls == []
    # Receita: orientacao + estoque + preferencia no contexto, limite de tokens maior.
    await conv.ask("Luna, o que posso fazer com ovos, arroz e tomate?", prompt_id=3)
    await settle(conv)
    assert llm.calls == ["Luna, o que posso fazer com ovos, arroz e tomate?"] and llm.limits == [600]
    ctx = llm.contexts[0]
    assert "Pedido sobre receita" in ctx and "- 3 ovos" in ctx and "eu não gosto de cebola" in ctx and "Memória local:" in ctx
    # Pergunta comum: sem orientacao de receita, sem estoque, limite padrao.
    await conv.ask("qual a capital da França?", prompt_id=4)
    await settle(conv)
    assert llm.limits[-1] is None and "Pedido sobre receita" not in llm.contexts[-1] and "3 ovos" not in llm.contexts[-1]
    # Estoque vazio + "com o que tenho": respondido sem a IA.
    await conv.ask("apague todos os ingredientes", prompt_id=5)
    await settle(conv)
    await conv.ask("o que posso fazer com o que tenho?", prompt_id=6)
    await settle(conv)
    assert len(llm.calls) == 2 and replies[-1][0].startswith("Ainda não tenho nenhum ingrediente registrado")


@pytest.mark.anyio
async def test_conversation_followup_uses_last_recipe_and_speaks_a_summary(tmp_path):
    store = MemoryStore(tmp_path / "mem.sqlite3")
    machine, conv, llm, replies, events, speaker, recipes = make_conversation(store, FakeLLM(pieces=(PANCAKE,)), spoken_max_chars=420)
    await conv.ask("Me ensine a fazer panqueca para duas pessoas.", prompt_id=1)
    await settle(conv)
    assert replies[-1] == (PANCAKE, True)  # texto completo na tela
    assert speaker.spoken == [spoken_summary(PANCAKE, 420)] and speaker.spoken[0].endswith(SPOKEN_DETAILS_HINT)
    assert recipes.has_recent_recipe and recipes.summaries == 1
    await conv.ask("Adapte essa receita para quatro pessoas.", prompt_id=2)
    await settle(conv)
    assert "Sua resposta anterior:" in llm.contexts[-1] and "Panqueca simples" in llm.contexts[-1] and llm.limits[-1] == 600
    await conv.ask("Como posso substituir o leite nesta receita?", prompt_id=3)
    await settle(conv)
    assert "Sua resposta anterior:" in llm.contexts[-1] and "Substituições" in llm.contexts[-1]


@pytest.mark.anyio
async def test_conversation_pending_clear_confirmation_keeps_priority(tmp_path):
    store = MemoryStore(tmp_path / "mem.sqlite3")
    store.add("kitchen", "tenho uma air fryer")
    machine, conv, llm, replies, events, _, _ = make_conversation(store)
    await conv.ask("apague todas as memórias", prompt_id=1)
    await settle(conv)
    assert conv.memory.awaiting_confirmation
    await conv.ask("tenho três ovos", prompt_id=2)  # outra frase cancela o pedido e e tratada normalmente
    await settle(conv)
    assert not conv.memory.awaiting_confirmation and replies[-1][0] == "Anotei: 3 ovos." and store.count() == 2
    await conv.ask("apague todas as memórias", prompt_id=3)
    await settle(conv)
    await conv.ask("sim", prompt_id=4)
    await settle(conv)
    assert events[-1]["action"] == "cleared" and store.count() == 0 and llm.calls == []


# ---- WebSocket e configuração ------------------------------------------------------------------------------


def test_stock_command_over_websocket_and_health(tmp_path):
    llm = FakeLLM()
    app = create_app(settings_for(tmp_path), detector=FakeDetector(), transcriber=FakeTranscriber("Luna, tenho três ovos."), llm=llm)
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        w = hello(ws)
        assert w["memory"] == {"available": True, "reason": "", "count": 0}  # forma do welcome inalterada
        ws.send_bytes(wake_frame() + speech(6))
        ws.send_bytes(silence(20))
        msgs = collect_until(ws, lambda m: m["type"] == "state" and m["state"] == "idle" and m["detail"] == DETAIL_READY)
        assert [m["text"] for m in msgs if m["type"] == "reply"] == ["Anotei: 3 ovos."]
        stock = [m for m in msgs if m["type"] == "memory"]
        assert len(stock) == 1 and stock[0]["action"] == "stock_saved" and stock[0]["items"][0]["kind"] == "ingredient" and stock[0]["count"] == 1
        assert llm.calls == []
        ws.send_json({"type": "memory_list"})  # o painel "Memoria" lista (e pode apagar) o estoque
        listed = ws.receive_json()
        assert listed["items"][0] == {**listed["items"][0], "kind": "ingredient", "text": "ovos: 3 unidades"}
        h = c.get("/health").json()
        assert h["recipes"]["enabled"] is True and h["recipes"]["ingredients"] == 1 and h["recipes"]["stock_commands"] == 1
        assert h["memory"]["count"] == 1 and h["memory_commands"] == 1 and h["replies"] == 0


def test_recipes_disabled_keeps_previous_behaviour(tmp_path):
    llm = FakeLLM()
    app = create_app(settings_for(tmp_path, recipes_enabled=False), detector=FakeDetector(), transcriber=FakeTranscriber("tenho três ovos"), llm=llm)
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        hello(ws)
        ws.send_bytes(wake_frame() + speech(6))
        ws.send_bytes(silence(20))
        collect_until(ws, lambda m: m["type"] == "state" and m["state"] == "idle" and m["detail"] == DETAIL_READY)
        assert llm.calls == ["tenho três ovos"] and llm.limits == [None]
        assert c.get("/health").json()["recipes"] == {"enabled": False}


def test_settings_recipes_from_toml_and_validation(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text(f'[auth]\ntoken="{TOKEN}"\n[recipes]\nenabled=false\nmax_tokens=900\nspoken_max_chars=0\nfollowup_ttl_s=60\n', encoding="utf-8")
    s = Settings.from_toml(p)
    assert (s.recipes_enabled, s.recipes_max_tokens, s.recipes_spoken_max_chars, s.recipes_followup_ttl_s) == (False, 900, 0, 60.0)
    d = Settings(auth_token=TOKEN)
    assert (d.recipes_enabled, d.recipes_max_tokens, d.recipes_spoken_max_chars, d.recipes_followup_ttl_s) == (True, 600, 420, 1800.0)
    d.validate()
    with pytest.raises(ConfigError, match="recipes.max_tokens"):
        Settings(auth_token=TOKEN, recipes_max_tokens=0).validate()
    with pytest.raises(ConfigError, match="spoken_max_chars"):
        Settings(auth_token=TOKEN, recipes_spoken_max_chars=-1).validate()
    with pytest.raises(ConfigError, match="followup_ttl_s"):
        Settings(auth_token=TOKEN, recipes_followup_ttl_s=0).validate()
