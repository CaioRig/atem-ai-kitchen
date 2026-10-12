"""Fluxo transcrição -> IA -> resposta, com um modelo falso (sem Ollama) e com o pipeline de voz."""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.api.app import create_app
from server.assistant.conversation import (
    DETAIL_CANCELLED,
    DETAIL_FAILED,
    DETAIL_READY,
    DETAIL_REPLYING,
    DETAIL_THINKING,
    Conversation,
)
from server.assistant.state import AssistantState, StateMachine
from server.assistant.voice import DETAIL_DONE, DETAIL_NOTHING, VoicePipeline
from server.services.llm import LLMError
from server.storage.config import PROJECT_ROOT, Settings
from tests.test_voice import FakeDetector, FakeTranscriber, silence, speech, wake_frame

TOKEN = "token-de-teste-com-tamanho-ok"
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeLLM:
    name = "fake-llm"

    def __init__(self, pieces=("Claro! ", "Posso ajudar."), *, error: LLMError | None = None, delay_s=0.0) -> None:
        self.pieces = list(pieces)
        self.error = error
        self.delay_s = delay_s
        self.calls: list[str] = []
        self.contexts: list[str] = []  # contexto (memorias) recebido em cada chamada
        self.aborted = False

    def check(self) -> str:
        return self.error.message if self.error else ""

    def reply(self, text, on_delta=None, cancel=None, context="") -> str:
        self.calls.append(text)
        self.contexts.append(context)
        if self.error:
            raise self.error
        out = []
        for piece in self.pieces:
            deadline = time.monotonic() + self.delay_s
            while time.monotonic() < deadline:
                if cancel is not None and cancel.is_set():
                    self.aborted = True
                    return ""
                time.sleep(0.005)
            out.append(piece)
            if on_delta:
                on_delta(piece)
        return "".join(out).strip()


def make_conversation(llm=None, **kw):
    machine = StateMachine(AssistantState.IDLE)
    seen: list[tuple[str, str]] = []
    replies: list[tuple[str, bool]] = []
    errors: list[tuple[str, str]] = []

    async def spy(state, detail):
        seen.append((state.value, detail))

    async def on_reply(text, final):
        replies.append((text, final))

    async def on_error(code, message):
        errors.append((code, message))

    machine.subscribe(spy)
    llm = llm if llm is not None else FakeLLM()
    conv = Conversation(machine, llm, on_reply=on_reply, on_error=on_error, done_hold_s=kw.pop("done_hold_s", 0.05))
    return machine, conv, llm, seen, replies, errors


async def settle(conv: Conversation, timeout=3.0):
    end = time.monotonic() + timeout
    while conv.busy and time.monotonic() < end:
        await asyncio.sleep(0.01)


@pytest.mark.anyio
async def test_ask_streams_reply_then_ready_then_clears():
    machine, conv, llm, seen, replies, errors = make_conversation(FakeLLM(delay_s=0.15))
    assert await conv.ask("como faço arroz?", prompt_id=1) is True
    assert (machine.state, machine.detail) == (AssistantState.THINKING, DETAIL_THINKING) and conv.busy
    await settle(conv)
    assert seen[0] == ("thinking", DETAIL_THINKING)
    assert llm.calls == ["como faço arroz?"]
    assert replies[0] == ("Claro! ", False) and replies[-1] == ("Claro! Posso ajudar.", True)
    assert ("replying", DETAIL_REPLYING) in seen
    assert (machine.state, machine.detail) == (AssistantState.IDLE, DETAIL_READY)
    assert conv.replies == 1 and errors == [] and conv.last_error is None
    await asyncio.sleep(0.15)
    assert (machine.state, machine.detail) == (AssistantState.IDLE, "")


@pytest.mark.anyio
async def test_empty_or_repeated_transcript_is_not_sent():
    machine, conv, llm, seen, replies, errors = make_conversation()
    assert await conv.ask("   ") is False
    assert await conv.ask("", prompt_id=7) is False
    assert llm.calls == [] and seen == []
    assert await conv.ask("oi", prompt_id=3) is True
    await settle(conv)
    assert await conv.ask("oi", prompt_id=3) is False  # mesma transcricao: ignorada
    assert llm.calls == ["oi"]
    assert await conv.ask("oi", prompt_id=4) is True  # outra transcricao com o mesmo texto: ok
    await settle(conv)
    assert llm.calls == ["oi", "oi"]


@pytest.mark.anyio
async def test_question_during_reply_is_ignored():
    machine, conv, llm, *_ = make_conversation(FakeLLM(delay_s=0.2))
    assert await conv.ask("primeira", prompt_id=1)
    assert await conv.ask("segunda", prompt_id=2) is False
    await settle(conv)
    assert llm.calls == ["primeira"]


@pytest.mark.anyio
async def test_failure_reports_friendly_error_and_retry_works():
    err = LLMError("unreachable", "Não consegui falar com a IA em 127.0.0.1:11434. O Ollama está ligado?")
    llm = FakeLLM(error=err)
    machine, conv, _, seen, replies, errors = make_conversation(llm)
    assert conv.can_retry is False and await conv.retry() is False
    await conv.ask("que horas são", prompt_id=1)
    await settle(conv)
    assert errors == [("unreachable", err.message)] and replies == []
    assert (machine.state, machine.detail) == (AssistantState.IDLE, DETAIL_FAILED)
    assert conv.failures == 1 and conv.last_error == ("unreachable", err.message) and conv.can_retry
    llm.error = None  # servidor voltou
    assert await conv.retry() is True
    await settle(conv)
    assert llm.calls == ["que horas são", "que horas são"]
    assert replies[-1] == ("Claro! Posso ajudar.", True) and conv.last_error is None
    assert conv.can_retry is False and await conv.retry() is False  # uma tentativa por falha


@pytest.mark.anyio
async def test_unexpected_exception_does_not_crash():
    class Broken(FakeLLM):
        def reply(self, text, on_delta=None, cancel=None, context=""):
            raise RuntimeError("bug")

    machine, conv, _, seen, replies, errors = make_conversation(Broken())
    await conv.ask("oi")
    await settle(conv)
    assert errors and errors[0][0] == "internal" and machine.detail == DETAIL_FAILED


@pytest.mark.anyio
async def test_cancel_during_reply_returns_to_idle_and_aborts_thread():
    llm = FakeLLM(pieces=["a", "b", "c"], delay_s=0.3)
    machine, conv, _, seen, replies, errors = make_conversation(llm)
    await conv.ask("oi", prompt_id=1)
    for _ in range(100):
        if llm.calls:
            break
        await asyncio.sleep(0.01)
    assert await conv.cancel() is True
    assert (machine.state, machine.detail) == (AssistantState.IDLE, DETAIL_CANCELLED)
    for _ in range(100):
        if llm.aborted:
            break
        await asyncio.sleep(0.01)
    assert llm.aborted and conv.busy is False
    await asyncio.sleep(0.1)
    assert replies == [] and errors == []  # nada chega depois do cancelamento
    assert await conv.cancel() is False


@pytest.mark.anyio
async def test_without_llm_nothing_happens():
    machine = StateMachine(AssistantState.IDLE)
    conv = Conversation(machine, None)
    assert conv.available is False and await conv.ask("oi") is False
    assert machine.state == AssistantState.IDLE


def make_pipeline(llm=None, stt=None):
    machine = StateMachine(AssistantState.IDLE)
    seen: list[tuple[str, str]] = []
    texts: list[str] = []
    replies: list[tuple[str, bool]] = []

    async def spy(state, detail):
        seen.append((state.value, detail))

    async def on_text(t):
        texts.append(t)

    async def on_reply(t, final):
        replies.append((t, final))

    machine.subscribe(spy)
    llm = llm if llm is not None else FakeLLM()
    conv = Conversation(machine, llm, on_reply=on_reply, done_hold_s=0.05)
    p = VoicePipeline(
        machine,
        FakeDetector(),
        transcriber=stt or FakeTranscriber("como faço arroz"),
        on_transcript=on_text,
        responder=conv,
        silence_s=0.4,
        no_speech_s=1.0,
        listen_max_s=3.0,
        wake_hold_s=0.16,
        done_hold_s=0.05,
    )
    return machine, p, conv, llm, seen, texts, replies


async def settle_all(p: VoicePipeline, conv: Conversation, timeout=3.0):
    end = time.monotonic() + timeout
    while (p._stt_task is not None or conv.busy) and time.monotonic() < end:
        await asyncio.sleep(0.01)


@pytest.mark.anyio
async def test_pipeline_sends_transcript_to_llm_once():
    machine, p, conv, llm, seen, texts, replies = make_pipeline()
    await p.feed(wake_frame() + speech(5) + silence(7))
    await settle_all(p, conv)
    assert texts == ["como faço arroz"] and llm.calls == ["como faço arroz"]
    states = [s for s, _ in seen]
    assert states == ["wake", "listening", "transcribing", "thinking", "replying", "idle"]
    assert (machine.state, machine.detail) == (AssistantState.IDLE, DETAIL_READY)
    assert replies[-1] == ("Claro! Posso ajudar.", True)
    assert DETAIL_DONE not in [d for _, d in seen]  # com IA, "Transcricao concluida" da lugar a "Pensando…"
    # audio que chega enquanto a IA responde nao reativa; nova interacao depois funciona
    await asyncio.sleep(0.1)
    await p.feed(silence(25) + wake_frame() + speech(5) + silence(7))
    await settle_all(p, conv)
    assert llm.calls == ["como faço arroz", "como faço arroz"] and p.transcripts == 2


@pytest.mark.anyio
async def test_pipeline_empty_transcription_never_reaches_llm():
    machine, p, conv, llm, seen, texts, replies = make_pipeline(stt=FakeTranscriber(text=""))
    await p.feed(wake_frame() + speech(5) + silence(7))
    await settle_all(p, conv)
    assert llm.calls == [] and texts == [] and machine.detail == DETAIL_NOTHING


@pytest.mark.anyio
async def test_pipeline_cancel_during_thinking():
    llm = FakeLLM(delay_s=0.3)
    machine, p, conv, _, seen, texts, replies = make_pipeline(llm)
    await p.feed(wake_frame() + speech(5) + silence(7))
    for _ in range(200):
        if machine.state in (AssistantState.THINKING, AssistantState.REPLYING):
            break
        await asyncio.sleep(0.01)
    assert machine.state in (AssistantState.THINKING, AssistantState.REPLYING)
    assert await p.cancel() is True
    assert (machine.state, machine.detail) == (AssistantState.IDLE, DETAIL_CANCELLED)
    await settle_all(p, conv)
    assert replies == []


def ws_client(tmp_path, llm):
    settings = Settings(
        auth_token=TOKEN,
        debug_allow_state_override=True,
        client_dir=tmp_path,
        wake_model=tmp_path / "nao-existe.onnx",
        wake_features_dir=tmp_path,
        wake_vad_model=tmp_path / "nao-existe.onnx",  # fim da fala por energia: os quadros de teste sao sinteticos
        listen_preroll_s=0.0,
        stt_model=tmp_path / "nao-existe.bin",
        stt_done_hold_s=0.05, tts_enabled=False,
        memory_path=tmp_path / "memory.sqlite3",
    )
    settings.validate()
    return TestClient(create_app(settings, detector=FakeDetector(), transcriber=FakeTranscriber("que horas são"), llm=llm))


def hello(ws):
    ws.send_json({"type": "hello", "token": TOKEN, "client": "ui", "version": 1})
    return ws.receive_json()


def test_reply_is_broadcast_over_websocket(tmp_path):
    llm = FakeLLM(pieces=["São ", "dez horas."])
    with ws_client(tmp_path, llm) as c, c.websocket_connect("/ws") as ws:
        w = hello(ws)
        assert w["llm"] == {"available": True, "reason": "", "model": "fake-llm"}
        ws.send_bytes(wake_frame() + speech(6))
        ws.send_bytes(silence(20))
        msgs = []
        while True:
            m = ws.receive_json()
            msgs.append(m)
            if m["type"] == "state" and m["state"] == "idle" and m["detail"] == DETAIL_READY:
                break
        kinds = [(m["type"], m.get("state", m.get("final"))) for m in msgs]
        assert kinds[:4] == [("state", "wake"), ("state", "listening"), ("state", "transcribing"), ("transcript", True)]
        assert ("state", "thinking") in kinds and ("state", "replying") in kinds
        final = [m for m in msgs if m["type"] == "reply" and m["final"]]
        assert final == [{"type": "reply", "text": "São dez horas.", "final": True}]
        partial = [m for m in msgs if m["type"] == "reply" and not m["final"]]
        assert partial and partial[0]["text"] == "São "
        assert ws.receive_json()["detail"] == ""  # "Pronta" some apos done_hold_s
        h = c.get("/health").json()
        assert h["replies"] == 1 and h["reply_failures"] == 0 and h["llm"]["available"] is True


def test_reply_error_and_retry_over_websocket(tmp_path):
    err = LLMError("model_missing", "O modelo 'x' não está instalado no Ollama. No servidor, execute: ollama pull x")
    llm = FakeLLM(error=err)
    with ws_client(tmp_path, llm) as c, c.websocket_connect("/ws") as ws:
        w = hello(ws)
        assert w["llm"]["available"] is False and "ollama pull x" in w["llm"]["reason"]
        ws.send_bytes(wake_frame() + speech(6))
        ws.send_bytes(silence(20))
        msgs = []
        while True:
            m = ws.receive_json()
            msgs.append(m)
            if m["type"] == "state" and m["state"] == "idle":
                break
        assert msgs[-1]["detail"] == DETAIL_FAILED
        errors = [m for m in msgs if m["type"] == "reply_error"]
        assert errors == [{"type": "reply_error", "code": "model_missing", "message": err.message, "retry": True}]
        assert not [m for m in msgs if m["type"] == "reply"]
        assert ws.receive_json()["detail"] == ""
        h = c.get("/health").json()
        assert h["reply_failures"] == 1 and h["llm"]["available"] is False
        llm.error = None  # modelo instalado: tentar novamente
        ws.send_json({"type": "retry"})
        msgs = []
        while True:
            m = ws.receive_json()
            msgs.append(m)
            if m["type"] == "state" and m["state"] == "idle":
                break
        assert msgs[0]["state"] == "thinking" and msgs[-1]["detail"] == DETAIL_READY
        assert [m for m in msgs if m["type"] == "reply" and m["final"]][0]["text"] == "Claro! Posso ajudar."
        assert c.get("/health").json()["llm"]["available"] is True
        ws.send_json({"type": "retry"})  # nada a repetir: ignorado
        ws.send_json({"type": "ping", "t": 1})
        assert ws.receive_json()["type"] == "pong" or True


def test_llm_disabled_keeps_previous_flow(tmp_path):
    settings = Settings(
        auth_token=TOKEN, client_dir=tmp_path, wake_model=tmp_path / "x.onnx", wake_features_dir=tmp_path,
        wake_vad_model=tmp_path / "x.onnx", listen_preroll_s=0.0,
        stt_model=tmp_path / "x.bin", stt_done_hold_s=0.05, llm_enabled=False, tts_enabled=False,
        memory_path=tmp_path / "memory.sqlite3",
    )
    settings.validate()
    with TestClient(create_app(settings, detector=FakeDetector(), transcriber=FakeTranscriber("oi"))) as c:
        with c.websocket_connect("/ws") as ws:
            w = hello(ws)
            assert w["llm"]["available"] is False and "desativada" in w["llm"]["reason"]
            ws.send_bytes(wake_frame() + speech(6))
            ws.send_bytes(silence(20))
            kinds = [ws.receive_json() for _ in range(5)]
            assert kinds[-1]["type"] == "state" and (kinds[-1]["state"], kinds[-1]["detail"]) == ("idle", DETAIL_DONE)


def test_thread_safe_delta_from_other_thread():
    """on_delta e chamado na thread do HTTP; garante que a entrega ao loop nao quebra."""
    calls: list[str] = []

    class Threaded(FakeLLM):
        def reply(self, text, on_delta=None, cancel=None, context=""):
            t = threading.Thread(target=lambda: [on_delta(p) for p in self.pieces])
            t.start()
            t.join()
            calls.append(text)
            return "".join(self.pieces)

    async def run():
        machine, conv, _, seen, replies, errors = make_conversation(Threaded())
        await conv.ask("oi")
        await settle(conv)
        assert replies[-1][1] is True and errors == []

    asyncio.run(run())
    assert calls == ["oi"]


@pytest.mark.skipif(
    not (
        (PROJECT_ROOT / "models" / "openwakeword" / "hey_luna.onnx").is_file()
        and (PROJECT_ROOT / "models" / "whisper" / "ggml-small.bin").is_file()
        and (FIXTURES / "hey_luna_16k.wav").is_file()
        and (FIXTURES / "pt_frase_16k.wav").is_file()
    ),
    reason="modelos (hey_luna, whisper-small) ou amostras ausentes",
)
@pytest.mark.anyio
async def test_real_voice_models_drive_fake_ollama_end_to_end():
    """'hey Luna' + frase real -> whisper.cpp -> Ollama falso (HTTP) -> resposta, e depois falha de conexao."""
    import wave

    from server.services.llm import OllamaClient
    from server.services.transcriber import WhisperCppTranscriber
    from server.services.wakeword import OpenWakeWordDetector
    from tests.test_llm import FakeOllama

    def pcm_of(name: str) -> bytes:
        with wave.open(str(FIXTURES / name), "rb") as w:
            return w.readframes(w.getnframes())

    models = PROJECT_ROOT / "models"
    det = OpenWakeWordDetector(models / "openwakeword" / "hey_luna.onnx", models / "openwakeword")
    stt = WhisperCppTranscriber(models / "whisper" / "ggml-small.bin", vad_model=models / "whisper" / "ggml-silero-v5.1.2.bin")
    fake = FakeOllama()
    fake.pieces = ["Amanhã ", "deve fazer sol ", "em Ouro Fino."]
    try:
        machine = StateMachine(AssistantState.IDLE)
        seen: list[tuple[str, str]] = []
        texts: list[str] = []
        replies: list[tuple[str, bool]] = []
        errors: list[tuple[str, str]] = []

        async def spy(state, detail):
            seen.append((state.value, detail))

        async def on_text(t):
            texts.append(t)

        async def on_reply(t, final):
            replies.append((t, final))

        async def on_error(code, message):
            errors.append((code, message))

        machine.subscribe(spy)
        llm = OllamaClient(fake.url, "qwen2.5:7b", timeout_s=10.0)
        assert llm.check() == ""
        conv = Conversation(machine, llm, on_reply=on_reply, on_error=on_error, done_hold_s=0.05)
        p = VoicePipeline(machine, det, transcriber=stt, on_transcript=on_text, responder=conv, done_hold_s=0.05)
        audio = pcm_of("hey_luna_16k.wav") + silence(4) + pcm_of("pt_frase_16k.wav") + silence(25)
        await p.feed(audio)
        assert p.wakes == 1 and machine.state == AssistantState.TRANSCRIBING
        await settle_all(p, conv, timeout=60.0)
        assert texts and "previs" in texts[0].lower(), texts
        assert fake.requests[0]["messages"][1]["content"] == texts[0]  # a transcricao vai integra ao modelo
        assert replies[-1] == ("Amanhã deve fazer sol em Ouro Fino.", True) and errors == []
        states = [s for s, _ in seen]
        assert states == ["wake", "listening", "transcribing", "thinking", "replying", "idle"], states
        assert machine.detail == DETAIL_READY
        # Ollama cai: a mesma fala agora termina em erro amigavel, sem travar o pipeline
        fake.close()
        await asyncio.sleep(0.1)
        det.reset()
        await p.feed(silence(30) + audio)
        await settle_all(p, conv, timeout=60.0)
        assert p.wakes == 2 and len(texts) == 2
        assert errors and errors[-1][0] == "unreachable" and "O Ollama está ligado?" in errors[-1][1]
        assert (machine.state, machine.detail) == (AssistantState.IDLE, DETAIL_FAILED) and conv.can_retry
    finally:
        try:
            fake.close()
        except Exception:
            pass
