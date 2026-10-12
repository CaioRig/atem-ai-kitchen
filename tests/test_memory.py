"""Memória persistente local (Fase 6): loja SQLite, comandos por voz, contexto para a IA, interface e falhas."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from server.api.app import create_app
from server.assistant.conversation import DETAIL_READY, DETAIL_THINKING, Conversation
from server.assistant.memory import (
    REPLY_STORAGE_FAILED,
    REPLY_UNAVAILABLE,
    MemoryCommands,
    classify,
    parse,
    second_person,
    strip_wake_prefix,
)
from server.assistant.state import AssistantState, StateMachine
from server.storage.config import ConfigError, Settings
from server.storage.memory import MemoryStorageError, MemoryStore, normalize, similarity
from tests.test_conversation import FakeLLM, settle
from tests.test_voice import FakeDetector, FakeTranscriber, silence, speech, wake_frame

TOKEN = "token-de-teste-com-tamanho-ok"


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---- Loja SQLite -------------------------------------------------------------------------------------------


def test_store_crud_and_persistence_across_restart(tmp_path):
    path = tmp_path / "mem.sqlite3"
    store = MemoryStore(path)
    assert store.count() == 0 and store.all() == []
    pref, status, previous = store.add("preference", "eu não gosto de cebola.")
    assert status == "created" and previous is None and pref.text == "eu não gosto de cebola"
    kit, status, _ = store.add("kitchen", "tenho uma air fryer")
    assert status == "created" and store.count() == 2 and store.count("kitchen") == 1
    assert store.get(pref.id) == pref and store.get(999) is None
    store.close()
    # "Reinicio": outra instancia sobre o mesmo arquivo ve as mesmas memorias.
    again = MemoryStore(path)
    assert [m.text for m in again.all()] == ["tenho uma air fryer", "eu não gosto de cebola"]  # ordem por tipo
    assert again.remove(kit.id) is True and again.remove(kit.id) is False
    assert again.update(pref.id, "eu não gosto de cebola crua").text == "eu não gosto de cebola crua"
    assert again.update(999, "x") is None
    assert again.clear() == 1 and again.count() == 0
    again.close()


def test_store_deduplicates_and_corrects_contradictions(tmp_path):
    store = MemoryStore(tmp_path / "mem.sqlite3")
    first, status, _ = store.add("preference", "eu não gosto de cebola")
    same, status2, _ = store.add("preference", "Eu não gosto de cebola!")
    assert status == "created" and status2 == "exists" and same.id == first.id and store.count() == 1
    fixed, status3, previous = store.add("preference", "eu gosto de cebola")
    assert status3 == "updated" and previous is not None and previous.text == "eu não gosto de cebola"
    assert fixed.id == first.id and fixed.text == "eu gosto de cebola" and store.count() == 1
    with pytest.raises(ValueError):
        store.add("outro", "x")
    with pytest.raises(ValueError):
        store.add("note", "   ")


def test_store_forget_matches_loosely(tmp_path):
    store = MemoryStore(tmp_path / "mem.sqlite3")
    store.add("kitchen", "tenho uma air fryer")
    store.add("preference", "eu não gosto de cebola")
    assert store.forget("abacaxi") is None
    gone = store.forget("a air fryer")
    assert gone is not None and gone.text == "tenho uma air fryer" and store.count() == 1
    gone = store.forget("eu nao gosto de cebolas")
    assert gone is not None and store.count() == 0


def test_store_search_returns_only_relevant_memories(tmp_path):
    store = MemoryStore(tmp_path / "mem.sqlite3")
    store.add("preference", "eu não gosto de cebola")
    store.add("kitchen", "tenho uma air fryer")
    store.add("note", "o aniversário da Ana é dia 20")
    store.add("instruction", "responda sempre em frases curtas")
    texts = lambda ms: [m.text for m in ms]  # noqa: E731
    found = texts(store.search("como faço frango na air fryer?"))
    assert found[0] == "tenho uma air fryer" and "eu não gosto de cebola" in found  # pergunta de comida
    assert "o aniversário da Ana é dia 20" not in found
    found = texts(store.search("quando é o aniversário da Ana?"))
    assert found == ["o aniversário da Ana é dia 20"]  # sem comida: preferencias ficam de fora
    assert store.search("qual a capital da França?") == []
    assert len(store.search("me sugere um jantar", limit=1)) == 1
    assert store.search("jantar", limit=0) == []
    assert texts(store.search("cebola", kinds=("kitchen",))) == []


def test_store_text_helpers():
    assert normalize("  Eu NÃO gosto de Cebola!  ") == "eu nao gosto de cebola"
    assert similarity("eu não gosto de cebola", "não gosto de cebolas") == 1.0
    assert similarity("abacaxi", "tenho uma air fryer") == 0.0
    assert strip_wake_prefix("Luna, lembre que eu gosto de pimenta") == "lembre que eu gosto de pimenta"
    assert strip_wake_prefix("Hey Luna! o que você lembra?") == "o que você lembra?"
    assert strip_wake_prefix("como faço arroz") == "como faço arroz"
    assert second_person("eu não gosto de cebola") == "você não gosta de cebola"
    assert second_person("Tenho uma air fryer") == "Tem uma air fryer"
    assert second_person("meu filho é alérgico a amendoim") == "seu filho é alérgico a amendoim"


def test_store_failures_become_memory_storage_error(tmp_path):
    with pytest.raises(MemoryStorageError):
        MemoryStore(tmp_path)  # um diretorio nao e um banco
    store = MemoryStore(tmp_path / "mem.sqlite3")
    store.add("note", "x")
    store._conn.close()  # simula o banco indisponivel no meio do uso
    with pytest.raises(MemoryStorageError):
        store.count()
    with pytest.raises(MemoryStorageError):
        store.add("note", "y")
    with pytest.raises(MemoryStorageError):
        store.search("y")


# ---- Interpretação das frases ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, body, kind",
    [
        ("Luna, lembre que eu não gosto de cebola.", "eu não gosto de cebola", "preference"),
        ("lembre que tenho uma air fryer", "tenho uma air fryer", "kitchen"),
        ("Lembra que eu sou vegetariano", "eu sou vegetariano", "preference"),
        ("não esqueça que meu filho é alérgico a amendoim", "meu filho é alérgico a amendoim", "preference"),
        ("anote que responda sempre em frases curtas", "responda sempre em frases curtas", "instruction"),
        ("guarde que o aniversário da Ana é dia 20", "o aniversário da Ana é dia 20", "note"),
        ("pode guardar que eu prefiro café sem açúcar", "eu prefiro café sem açúcar", "preference"),
        ("me lembra que eu gosto de pimenta", "eu gosto de pimenta", "preference"),
        ("lembre-se de que tenho panela de pressão", "tenho panela de pressão", "kitchen"),
    ],
)
def test_parse_remember(text, body, kind):
    intent = parse(text)
    assert intent is not None and intent.name == "remember"
    assert intent.body == body and intent.kind == kind
    assert classify(body) == kind


@pytest.mark.parametrize(
    "text, body, kind",
    [
        ("Luna, o que você lembra sobre minha cozinha?", "", "kitchen"),
        ("o que você lembra sobre as minhas preferências", "", "preference"),
        ("o que você lembra sobre cebola?", "cebola", None),
        ("o que você lembra?", "", None),
        ("o que você sabe sobre mim?", "", None),
        ("quais são minhas memórias?", "", None),
        ("você lembra o que eu gosto?", "", None),
        ("liste suas memórias", "", None),
    ],
)
def test_parse_recall(text, body, kind):
    intent = parse(text)
    assert intent is not None and intent.name == "recall", intent
    assert intent.body == body and intent.kind == kind


def test_parse_forget_and_forget_all():
    assert parse("esqueça que eu não gosto de cebola").name == "forget"
    assert parse("Luna, esqueça que eu não gosto de cebola.").body == "eu não gosto de cebola"
    assert parse("esqueça a air fryer").body == "a air fryer"
    assert parse("apague a memória de que tenho uma air fryer").body == "tenho uma air fryer"
    for text in ("apague todas as minhas memórias", "Luna, apaga todas as memórias.", "esqueça tudo", "limpe sua memória", "apague tudo que você lembra"):
        assert parse(text).name == "forget_all", text


@pytest.mark.parametrize(
    "text",
    [
        "como faço arroz soltinho?",
        "Lembra que horas são?",
        "me lembre de comprar leite",
        "que horas são",
        "lembre quem ganhou a copa",
        "sim",
        "não",
    ],
)
def test_parse_leaves_other_sentences_to_the_llm(text):
    assert parse(text) is None


def test_parse_confirmation_only_while_pending():
    assert parse("sim", pending_confirmation=False) is None
    for yes in ("sim", "Sim.", "sim, pode apagar", "pode apagar", "confirmo", "isso mesmo", "pode apagar tudo, Luna"):
        assert parse(yes, pending_confirmation=True).name == "yes", yes
    for no in ("não", "Não!", "acho que não", "cancela", "melhor não", "deixa", "não, espera"):
        assert parse(no, pending_confirmation=True).name == "no", no
    # Frases incertas ou longas nao confirmam nada.
    assert parse("que horas são", pending_confirmation=True) is None
    assert parse("sim mas antes me conta uma receita de bolo de cenoura bem detalhada por favor", pending_confirmation=True) is None
    assert parse("talvez", pending_confirmation=True) is None


# ---- Comandos ---------------------------------------------------------------------------------------------


def test_commands_remember_recall_forget(tmp_path):
    store = MemoryStore(tmp_path / "mem.sqlite3")
    cmd = MemoryCommands(store)
    assert cmd.handle("como faço arroz?") is None
    out = cmd.handle("Luna, lembre que eu não gosto de cebola.")
    assert out.reply == "Anotado como preferência: você não gosta de cebola."
    assert out.event["type"] == "memory" and out.event["action"] == "saved" and out.event["count"] == 1
    assert out.event["item"]["kind"] == "preference" and out.event["item"]["text"] == "eu não gosto de cebola"
    assert cmd.handle("lembre que eu não gosto de cebola").event["action"] == "exists"
    out = cmd.handle("lembre que eu gosto de cebola")
    assert out.event["action"] == "updated" and "Atualizei" in out.reply and "você não gosta de cebola" in out.reply
    cmd.handle("lembre que tenho uma air fryer")
    out = cmd.handle("o que você lembra sobre minha cozinha?")
    assert out.reply == "Sobre a sua cozinha, eu lembro: tem uma air fryer." and out.event["spoken"] is True
    assert [i["kind"] for i in out.event["items"]] == ["kitchen"]
    out = cmd.handle("o que você lembra?")
    assert out.reply.startswith("Preferências: você gosta de cebola. Cozinha: tem uma air fryer")
    assert cmd.handle("o que você lembra sobre abacaxi?").reply == "Ainda não tenho nada guardado sobre abacaxi."
    out = cmd.handle("esqueça que eu gosto de cebola")
    assert out.reply == "Esqueci: você gosta de cebola." and out.event["action"] == "deleted" and store.count() == 1
    assert cmd.handle("esqueça que eu gosto de cebola").event["action"] == "not_found"
    assert cmd.commands == 9 and cmd.failures == 0  # "como faço arroz?" nao conta: nao e comando


def test_commands_clear_all_requires_explicit_confirmation(tmp_path):
    store = MemoryStore(tmp_path / "mem.sqlite3")
    cmd = MemoryCommands(store)
    assert cmd.handle("apague todas as memórias").reply == "Minha memória já está vazia; não há nada para apagar."
    cmd.handle("lembre que tenho uma air fryer")
    cmd.handle("lembre que eu não gosto de cebola")
    out = cmd.handle("Luna, apague todas as minhas memórias.")
    assert out.reply.startswith("Tem certeza? Vou apagar todas as 2 memórias.") and out.event["action"] == "confirm_clear"
    assert cmd.awaiting_confirmation and store.count() == 2
    # "sim" sozinho, fora de uma confirmacao pendente, nao e comando; aqui confirma.
    assert cmd.handle("que horas são") is None and not cmd.awaiting_confirmation  # outra frase cancela o pedido
    assert cmd.handle("sim") is None and store.count() == 2  # ... e "sim" depois disso nao apaga nada
    cmd.handle("apague todas as memórias")
    out = cmd.handle("não")
    assert out.reply == "Tudo bem, não apaguei nada." and out.event["action"] == "kept" and store.count() == 2
    cmd.handle("esqueça tudo")
    out = cmd.handle("sim, pode apagar")
    assert out.reply == "Pronto, apaguei 2 memórias. Minha memória está vazia."
    assert out.event["action"] == "cleared" and out.event["count"] == 2 and store.count() == 0


def test_commands_confirmation_expires(tmp_path):
    store = MemoryStore(tmp_path / "mem.sqlite3")
    store.add("note", "x")
    cmd = MemoryCommands(store, confirm_timeout_s=0.05)
    cmd.handle("apague todas as memórias")
    time.sleep(0.1)
    assert cmd.handle("sim") is None and store.count() == 1
    cmd.handle("apague todas as memórias")
    cmd.cancel_confirmation()
    assert cmd.handle("sim") is None and store.count() == 1


def test_commands_without_store_or_with_broken_store(tmp_path):
    none = MemoryCommands(None)
    assert none.available is False and none.handle("como faço arroz") is None
    out = none.handle("lembre que tenho uma air fryer")
    assert out.reply == REPLY_UNAVAILABLE and out.event["action"] == "error"
    assert none.context_for("me sugere um jantar") == ""
    store = MemoryStore(tmp_path / "mem.sqlite3")
    store.add("preference", "eu não gosto de cebola")
    broken = MemoryCommands(store)
    store._conn.close()
    out = broken.handle("lembre que tenho uma air fryer")
    assert out.reply == REPLY_STORAGE_FAILED and out.event["action"] == "error" and broken.failures == 1
    assert broken.context_for("me sugere um jantar") == "" and broken.failures == 2


def test_context_for_includes_only_relevant_memories_and_instructions(tmp_path):
    store = MemoryStore(tmp_path / "mem.sqlite3")
    cmd = MemoryCommands(store, max_context=8)
    assert cmd.context_for("me sugere um jantar") .startswith("Memória local:")  # vazia, mas com a regra de nao inventar
    assert "Nenhuma informação guardada" in cmd.context_for("me sugere um jantar")
    store.add("preference", "eu não gosto de cebola")
    store.add("note", "o aniversário da Ana é dia 20")
    store.add("instruction", "responda sempre em frases curtas")
    ctx = cmd.context_for("me sugere um jantar")
    assert "- [preferência] eu não gosto de cebola" in ctx and "- responda sempre em frases curtas" in ctx
    assert "aniversário" not in ctx and "nunca invente lembranças" in ctx
    ctx = cmd.context_for("qual a capital da França?")
    assert "cebola" not in ctx and "Nenhuma informação guardada" in ctx and "frases curtas" in ctx


# ---- Conversa (sem Ollama) ---------------------------------------------------------------------------------


def make_conversation(store, llm=None, **kw):
    machine = StateMachine(AssistantState.IDLE)
    seen: list[tuple[str, str]] = []
    replies: list[tuple[str, bool]] = []
    events: list[dict] = []

    async def spy(state, detail):
        seen.append((state.value, detail))

    async def on_reply(text, final):
        replies.append((text, final))

    async def on_memory(event):
        events.append(event)

    machine.subscribe(spy)
    llm = llm if llm is not None else FakeLLM()
    conv = Conversation(
        machine, llm, on_reply=on_reply, on_memory=on_memory, done_hold_s=0.05,
        memory=MemoryCommands(store, **kw) if store is not None or kw.get("force") else None,
    )
    return machine, conv, llm, seen, replies, events


@pytest.mark.anyio
async def test_conversation_memory_commands_bypass_llm_and_questions_get_context(tmp_path):
    store = MemoryStore(tmp_path / "mem.sqlite3")
    machine, conv, llm, seen, replies, events = make_conversation(store)
    assert await conv.ask("Luna, lembre que eu não gosto de cebola.", prompt_id=1) is True
    await settle(conv)
    assert llm.calls == [] and conv.memory_replies == 1
    assert replies == [("Anotado como preferência: você não gosta de cebola.", True)]
    assert events[0]["action"] == "saved" and events[0]["count"] == 1
    assert seen[0] == ("thinking", DETAIL_THINKING) and (machine.state, machine.detail) == (AssistantState.IDLE, DETAIL_READY)
    assert await conv.ask("Luna, lembre que eu não gosto de cebola.", prompt_id=1) is False  # mesma transcricao
    # Pergunta comum: vai a IA com as memorias relevantes como contexto.
    assert await conv.ask("me sugere um jantar", prompt_id=2) is True
    await settle(conv)
    assert llm.calls == ["me sugere um jantar"] and "eu não gosto de cebola" in llm.contexts[0]
    assert conv.replies == 1
    # Pergunta sem relacao: contexto diz que nao ha nada relevante (a IA nao deve inventar).
    await conv.ask("qual a capital da França?", prompt_id=3)
    await settle(conv)
    assert "cebola" not in llm.contexts[1] and "nunca invente" in llm.contexts[1]
    await conv.ask("o que você lembra sobre minhas preferências?", prompt_id=4)
    await settle(conv)
    assert replies[-1] == ("Sobre as suas preferências, eu lembro: você não gosta de cebola.", True)
    await conv.ask("esqueça que eu não gosto de cebola", prompt_id=5)
    await settle(conv)
    assert replies[-1][0] == "Esqueci: você não gosta de cebola." and events[-1]["action"] == "deleted"
    assert store.count() == 0 and len(llm.calls) == 2


@pytest.mark.anyio
async def test_conversation_memory_survives_restart(tmp_path):
    path = tmp_path / "mem.sqlite3"
    store = MemoryStore(path)
    _, conv, _, _, replies, _ = make_conversation(store)
    await conv.ask("lembre que tenho uma air fryer", prompt_id=1)
    await settle(conv)
    store.close()
    # Nova instancia (reinicio da aplicacao) sobre o mesmo arquivo.
    store2 = MemoryStore(path)
    _, conv2, llm2, _, replies2, _ = make_conversation(store2)
    await conv2.ask("o que você lembra sobre minha cozinha?", prompt_id=1)
    await settle(conv2)
    assert replies2 == [("Sobre a sua cozinha, eu lembro: tem uma air fryer.", True)] and llm2.calls == []


@pytest.mark.anyio
async def test_conversation_clear_all_asks_and_only_explicit_yes_clears(tmp_path):
    store = MemoryStore(tmp_path / "mem.sqlite3")
    store.add("kitchen", "tenho uma air fryer")
    machine, conv, llm, seen, replies, events = make_conversation(store)
    await conv.ask("Luna, apague todas as minhas memórias.", prompt_id=1)
    await settle(conv)
    assert replies[-1][0].startswith("Tem certeza? Vou apagar a 1 memória.") and events[-1]["action"] == "confirm_clear"
    await conv.ask("talvez", prompt_id=2)  # incerto: nao apaga, cancela o pedido e vai para a IA
    await settle(conv)
    assert llm.calls == ["talvez"] and store.count() == 1
    await conv.ask("sim", prompt_id=3)  # sem pedido pendente, "sim" e uma frase comum
    await settle(conv)
    assert llm.calls == ["talvez", "sim"] and store.count() == 1
    await conv.ask("apague todas as memórias", prompt_id=4)
    await settle(conv)
    await conv.ask("sim, pode apagar", prompt_id=5)
    await settle(conv)
    assert replies[-1][0] == "Pronto, apaguei 1 memória. Minha memória está vazia."
    assert events[-1]["action"] == "cleared" and store.count() == 0 and len(llm.calls) == 2


@pytest.mark.anyio
async def test_conversation_storage_failure_does_not_crash(tmp_path):
    store = MemoryStore(tmp_path / "mem.sqlite3")
    machine, conv, llm, seen, replies, events = make_conversation(store)
    store._conn.close()  # falha de armazenamento simulada
    await conv.ask("lembre que tenho uma air fryer", prompt_id=1)
    await settle(conv)
    assert replies == [(REPLY_STORAGE_FAILED, True)] and events[-1]["action"] == "error"
    assert (machine.state, machine.detail) == (AssistantState.IDLE, DETAIL_READY)
    await conv.ask("como faço arroz?", prompt_id=2)  # perguntas comuns seguem funcionando, sem contexto
    await settle(conv)
    assert llm.calls == ["como faço arroz?"] and llm.contexts == [""] and conv.replies == 1


@pytest.mark.anyio
async def test_conversation_without_llm_still_handles_memory(tmp_path):
    store = MemoryStore(tmp_path / "mem.sqlite3")
    machine = StateMachine(AssistantState.IDLE)
    replies: list[tuple[str, bool]] = []

    async def on_reply(text, final):
        replies.append((text, final))

    conv = Conversation(machine, None, on_reply=on_reply, done_hold_s=0.05, memory=MemoryCommands(store))
    assert conv.available is True
    assert await conv.ask("lembre que tenho uma air fryer", prompt_id=1) is True
    await settle(conv)
    assert replies[-1][0].startswith("Anotado como cozinha") and store.count() == 1
    assert await conv.ask("como faço arroz?", prompt_id=2) is False  # sem IA, perguntas comuns nao sao respondidas


# ---- WebSocket e configuração ------------------------------------------------------------------------------


def settings_for(tmp_path, **overrides) -> Settings:
    opts = dict(
        auth_token=TOKEN,
        debug_allow_state_override=True,
        client_dir=tmp_path,
        wake_model=tmp_path / "nao-existe.onnx",
        wake_features_dir=tmp_path,
        wake_vad_model=tmp_path / "nao-existe.onnx",
        listen_preroll_s=0.0,
        stt_model=tmp_path / "nao-existe.bin",
        stt_done_hold_s=0.05,
        tts_enabled=False,
        memory_path=tmp_path / "memory.sqlite3",
    )
    opts.update(overrides)
    settings = Settings(**opts)
    settings.validate()
    return settings


def hello(ws):
    ws.send_json({"type": "hello", "token": TOKEN, "client": "ui", "version": 1})
    return ws.receive_json()


def collect_until(ws, pred, limit=50):
    msgs = []
    for _ in range(limit):
        m = ws.receive_json()
        msgs.append(m)
        if pred(m):
            return msgs
    raise AssertionError(f"condicao nao atingida: {msgs}")


def test_memory_managed_over_websocket(tmp_path):
    settings = settings_for(tmp_path)
    app = create_app(settings, detector=FakeDetector(), transcriber=FakeTranscriber("x"), llm=FakeLLM())
    with TestClient(app) as c, c.websocket_connect("/ws") as a, c.websocket_connect("/ws") as b:
        w = hello(a)
        hello(b)
        assert w["memory"] == {"available": True, "reason": "", "count": 0}
        a.send_json({"type": "memory_list"})
        assert a.receive_json() == {"type": "memory", "action": "list", "items": [], "count": 0, "topic": ""}
        store: MemoryStore = app.state.memory
        item, _, _ = store.add("kitchen", "tenho uma air fryer")
        store.add("preference", "eu não gosto de cebola")
        a.send_json({"type": "memory_list"})
        listed = a.receive_json()
        assert listed["count"] == 2 and [i["text"] for i in listed["items"]] == ["tenho uma air fryer", "eu não gosto de cebola"]
        a.send_json({"type": "memory_delete", "id": item.id})
        for ws in (a, b):  # exclusao e anunciada a todos
            m = ws.receive_json()
            assert m["action"] == "deleted" and m["item"]["id"] == item.id and m["count"] == 1
        a.send_json({"type": "memory_delete", "id": item.id})
        assert a.receive_json()["action"] == "not_found"
        a.send_json({"type": "memory_clear"})
        assert a.receive_json()["code"] == "confirm_required" and store.count() == 1
        a.send_json({"type": "memory_clear", "confirm": True})
        for ws in (a, b):
            m = ws.receive_json()
            assert m["action"] == "cleared" and m["count"] == 0 and m["removed"] == 1
        a.send_json({"type": "memory_delete", "id": 0})
        assert a.receive_json()["code"] == "bad_message"
        h = c.get("/health").json()
        assert h["memory"] == {"available": True, "reason": "", "count": 0, "path": str(settings.memory_path)}
        assert settings.memory_path.is_file()


def test_memory_voice_command_over_websocket(tmp_path):
    llm = FakeLLM()
    app = create_app(
        settings_for(tmp_path), detector=FakeDetector(), transcriber=FakeTranscriber("Luna, lembre que tenho uma air fryer."), llm=llm
    )
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        hello(ws)
        ws.send_bytes(wake_frame() + speech(6))
        ws.send_bytes(silence(20))
        msgs = collect_until(ws, lambda m: m["type"] == "state" and m["state"] == "idle" and m["detail"] == DETAIL_READY)
        kinds = [m["type"] for m in msgs]
        assert "transcript" in kinds and ("state", "thinking") in [(m["type"], m.get("state")) for m in msgs]
        assert [m for m in msgs if m["type"] == "reply"] == [{"type": "reply", "text": "Anotado como cozinha: tem uma air fryer.", "final": True}]
        saved = [m for m in msgs if m["type"] == "memory"]
        assert len(saved) == 1 and saved[0]["action"] == "saved" and saved[0]["item"]["kind"] == "kitchen"
        assert llm.calls == []
        h = c.get("/health").json()
        assert h["memory"]["count"] == 1 and h["memory_commands"] == 1 and h["replies"] == 0


def test_memory_disabled_sends_everything_to_llm(tmp_path):
    llm = FakeLLM()
    app = create_app(
        settings_for(tmp_path, memory_enabled=False), detector=FakeDetector(), transcriber=FakeTranscriber("lembre que tenho uma air fryer"), llm=llm
    )
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        w = hello(ws)
        assert w["memory"]["available"] is False and "desativada" in w["memory"]["reason"]
        ws.send_json({"type": "memory_list"})
        assert ws.receive_json()["code"] == "unavailable"
        ws.send_bytes(wake_frame() + speech(6))
        ws.send_bytes(silence(20))
        collect_until(ws, lambda m: m["type"] == "state" and m["state"] == "idle" and m["detail"] == DETAIL_READY)
        assert llm.calls == ["lembre que tenho uma air fryer"] and llm.contexts == [""]
        assert not (tmp_path / "memory.sqlite3").exists()


def test_memory_open_failure_keeps_server_running(tmp_path):
    llm = FakeLLM()
    (tmp_path / "bloqueado").mkdir()
    settings = settings_for(tmp_path, memory_path=tmp_path / "bloqueado")  # diretorio no lugar do arquivo
    app = create_app(settings, detector=FakeDetector(), transcriber=FakeTranscriber("lembre que tenho uma air fryer"), llm=llm)
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        w = hello(ws)
        assert w["memory"]["available"] is False and "bloqueado" in w["memory"]["reason"]
        ws.send_bytes(wake_frame() + speech(6))
        ws.send_bytes(silence(20))
        msgs = collect_until(ws, lambda m: m["type"] == "state" and m["state"] == "idle" and m["detail"] == DETAIL_READY)
        assert [m["text"] for m in msgs if m["type"] == "reply"] == [REPLY_UNAVAILABLE]
        assert llm.calls == []
        assert c.get("/health").json()["memory"]["available"] is False


def test_settings_memory_from_toml_and_validation(tmp_path):
    from server.storage.config import PROJECT_ROOT

    p = tmp_path / "s.toml"
    p.write_text(f'[auth]\ntoken="{TOKEN}"\n[memory]\nenabled=false\npath="data/outra.sqlite3"\nmax_context=3\n', encoding="utf-8")
    s = Settings.from_toml(p)
    assert (s.memory_enabled, s.memory_path, s.memory_max_context) == (False, PROJECT_ROOT / "data" / "outra.sqlite3", 3)
    s.validate()  # desativada: max_context nao importa
    d = Settings(auth_token=TOKEN)
    assert d.memory_enabled is True and d.memory_path == PROJECT_ROOT / "data" / "memory.sqlite3" and d.memory_max_context == 8
    with pytest.raises(ConfigError, match="max_context"):
        Settings(auth_token=TOKEN, memory_max_context=0).validate()
