"""Voz da Luna: resposta -> síntese (falsa) -> áudio para o quiosque -> confirmação -> espera.

Sem Kokoro/Piper: um sintetizador falso produz silêncio por frase. O motor real é verificado em
`test_real_kokoro_voice_speaks_portuguese` quando os arquivos existem (scripts/prepare_models.py kokoro).
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

from server.api.app import create_app
from server.assistant.conversation import DETAIL_THINKING, Conversation
from server.assistant.speech import (
    DETAIL_CANCELLED,
    DETAIL_FAILED,
    DETAIL_READY,
    DETAIL_SPEAKING,
    DETAIL_SYNTHESIZING,
    Speech,
)
from server.assistant.state import AssistantState, StateMachine
from server.assistant.voice import VoicePipeline
from server.services.tts import TtsError, prepare_text, split_sentences
from server.storage.config import PROJECT_ROOT, Settings
from tests.test_conversation import FakeLLM
from tests.test_voice import FakeDetector, FakeTranscriber, silence, speech, wake_frame

TOKEN = "token-de-teste-com-tamanho-ok"
RATE = 16000


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeSynth:
    name = "fake-tts"
    voice = "fake"
    sample_rate = RATE

    def __init__(self, *, error: TtsError | None = None, delay_s: float = 0.0, chunk_s: float = 0.05, fail_after: int = 0) -> None:
        self.error = error
        self.delay_s = delay_s
        self.chunk_s = chunk_s
        self.fail_after = fail_after  # falha depois de N trechos (0 = nunca)
        self.calls: list[str] = []
        self.aborted = False
        self.warmed = False

    def synthesize(self, text, cancel=None):
        self.calls.append(text)
        if self.error:
            raise self.error
        for i, (sentence, _pause) in enumerate(split_sentences(text)):
            if self.fail_after and i >= self.fail_after:
                raise TtsError("engine", "A voz falhou ao gerar o áudio. Tente de novo.", "simulado")
            deadline = time.monotonic() + self.delay_s
            while time.monotonic() < deadline:
                if cancel is not None and cancel.is_set():
                    self.aborted = True
                    return
                time.sleep(0.005)
            if cancel is not None and cancel.is_set():
                self.aborted = True
                return
            yield bytes(2 * int(self.chunk_s * RATE))

    def warmup(self) -> None:
        self.warmed = True


def make_speech(synth=None, **kw):
    machine = StateMachine(AssistantState.IDLE)
    seen: list[tuple[str, str]] = []
    events: list[tuple] = []  # ("start", id, rate) / ("audio", id, n_bytes) / ("end", id, samples) / ("stop", id) / ("error", code, msg)

    async def spy(state, detail):
        seen.append((state.value, detail))

    async def on_start(uid, rate):
        events.append(("start", uid, rate))

    async def on_audio(uid, pcm):
        events.append(("audio", uid, len(pcm)))

    async def on_end(uid, samples):
        events.append(("end", uid, samples))

    async def on_stop(uid):
        events.append(("stop", uid))

    async def on_error(code, message):
        events.append(("error", code, message))

    machine.subscribe(spy)
    synth = synth if synth is not None else FakeSynth()
    sp = Speech(
        machine, synth, on_start=on_start, on_audio=on_audio, on_end=on_end, on_stop=on_stop, on_error=on_error,
        done_hold_s=kw.pop("done_hold_s", 0.05), playback_margin_s=kw.pop("playback_margin_s", 0.3), **kw,
    )
    return machine, sp, synth, seen, events


async def settle(sp: Speech, timeout=3.0):
    end = time.monotonic() + timeout
    while sp.busy and time.monotonic() < end:
        await asyncio.sleep(0.01)


async def until(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while not pred() and time.monotonic() < end:
        await asyncio.sleep(0.01)
    assert pred()


# ---- texto -----------------------------------------------------------------------------------------------


def test_prepare_text_removes_markdown_bullets_emojis_and_urls():
    text = prepare_text("**Claro!** Para o arroz:\n- lave bem 🍚\n* refogue o alho\n1. Cozinhe. Veja https://x.y/z e pronto.")
    assert text == "Claro! Para o arroz:\nlave bem\nrefogue o alho\nCozinhe. Veja e pronto."
    assert prepare_text("   \n ") == ""


def test_prepare_text_truncates_at_sentence_boundary():
    long = "Primeira frase bem comprida para o teste. " * 10
    out = prepare_text(long, max_chars=120)
    assert out.endswith("teste.") and len(out) <= 120


def test_split_sentences_merges_short_and_splits_long():
    pieces = split_sentences("Claro! Para o arroz use duas xícaras de água. Depois tampe. Sim.")
    assert [p for p, _ in pieces] == ["Claro! Para o arroz use duas xícaras de água.", "Depois tampe. Sim."]
    assert split_sentences("Passo 1. Lave o arroz.") == [("Passo 1. Lave o arroz.", 0.35)]  # "1." nao e fim de frase
    long = ", ".join(["uma oração razoavelmente comprida"] * 8) + "."
    parts = split_sentences(long)
    assert len(parts) > 1 and all(len(p) <= 160 for p, _ in parts) and parts[0][1] < parts[-1][1]
    assert " ".join(p for p, _ in parts) == long


# ---- Speech ----------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_speak_streams_audio_then_waits_for_playback_then_ready():
    machine, sp, synth, seen, events = make_speech(FakeSynth(delay_s=0.05))
    assert await sp.speak("Olá, tudo bem com você? Posso ajudar na cozinha.") is True
    assert (machine.state, machine.detail) == (AssistantState.SYNTHESIZING, DETAIL_SYNTHESIZING) and sp.busy
    await until(lambda: any(e[0] == "end" for e in events))
    kinds = [e[0] for e in events]
    assert kinds == ["start", "audio", "audio", "end"]
    assert events[0] == ("start", 1, RATE) and events[-1] == ("end", 1, 2 * int(0.05 * RATE))
    assert ("speaking", DETAIL_SPEAKING) in seen and machine.state == AssistantState.SPEAKING
    assert sp.busy  # ainda falando: aguarda o quiosque confirmar
    assert sp.playback_done(99) is False  # fala desconhecida: ignorada
    assert sp.playback_done(1) is True
    await settle(sp)
    assert (machine.state, machine.detail) == (AssistantState.IDLE, DETAIL_READY)
    assert sp.spoken == 1 and sp.failures == 0 and sp.last_synthesis_s > 0 and sp.last_audio_s == pytest.approx(0.1)
    await asyncio.sleep(0.15)
    assert (machine.state, machine.detail) == (AssistantState.IDLE, "")
    assert [s for s, _ in seen] == ["synthesizing", "speaking", "idle", "idle"]


@pytest.mark.anyio
async def test_without_confirmation_returns_to_idle_after_audio_duration():
    machine, sp, synth, seen, events = make_speech(playback_margin_s=0.1)
    await sp.speak("Oi.")
    await settle(sp, timeout=2.0)
    assert (machine.state, machine.detail) == (AssistantState.IDLE, DETAIL_READY) and sp.spoken == 1


@pytest.mark.anyio
async def test_nothing_to_say_or_no_engine():
    machine, sp, *_ = make_speech()
    assert await sp.speak("   ") is False and await sp.speak("**") is False
    assert machine.state == AssistantState.IDLE and not sp.busy
    off = Speech(StateMachine(AssistantState.IDLE), None)
    assert off.available is False and await off.speak("oi") is False and await off.replay() is False


@pytest.mark.anyio
async def test_engine_failure_reports_friendly_error_and_replay_works():
    err = TtsError("engine", "A voz falhou ao gerar o áudio. Tente de novo.", "simulado")
    synth = FakeSynth(error=err)
    machine, sp, _, seen, events = make_speech(synth)
    assert sp.can_replay is False and await sp.replay() is False
    await sp.speak("Oi, tudo bem?")
    await settle(sp)
    assert events == [("error", "engine", err.message)]  # nenhum speak_start/stop: nada foi enviado
    assert (machine.state, machine.detail) == (AssistantState.IDLE, DETAIL_FAILED)
    assert sp.failures == 1 and sp.last_error == ("engine", err.message) and sp.can_replay
    synth.error = None
    assert await sp.replay() is True
    await until(lambda: any(e[0] == "end" for e in events))
    sp.playback_done(2)
    await settle(sp)
    assert synth.calls == ["Oi, tudo bem?", "Oi, tudo bem?"] and machine.detail == DETAIL_READY and sp.last_error is None


@pytest.mark.anyio
async def test_failure_midway_stops_partial_audio():
    machine, sp, _, seen, events = make_speech(FakeSynth(fail_after=1))
    await sp.speak("Primeira frase inteira. Segunda frase que falha.")
    await settle(sp)
    kinds = [e[0] for e in events]
    assert kinds == ["start", "audio", "stop", "error"]
    assert machine.detail == DETAIL_FAILED and sp.can_replay


@pytest.mark.anyio
async def test_client_playback_failure_is_reported():
    machine, sp, _, seen, events = make_speech()
    await sp.speak("Oi, tudo bem?")
    await until(lambda: any(e[0] == "end" for e in events))
    sp.playback_done(1, played=False, error="toque na tela para liberar o som")
    await settle(sp)
    assert events[-1] == ("error", "playback", "toque na tela para liberar o som")
    assert machine.detail == DETAIL_FAILED and sp.failures == 1 and sp.can_replay


@pytest.mark.anyio
async def test_cancel_during_speaking_stops_and_aborts_thread():
    synth = FakeSynth(delay_s=0.3)
    machine, sp, _, seen, events = make_speech(synth)
    await sp.speak("Primeira frase. Segunda frase. Terceira frase.")
    await until(lambda: synth.calls)
    assert await sp.cancel() is True
    assert (machine.state, machine.detail) == (AssistantState.IDLE, DETAIL_CANCELLED) and not sp.busy
    assert events[-1] == ("stop", 1)
    await until(lambda: synth.aborted)
    n = len(events)
    await asyncio.sleep(0.2)
    assert len(events) == n  # nada chega depois do cancelamento
    assert await sp.cancel() is False
    assert sp.can_replay  # a resposta pode ser ouvida de novo


@pytest.mark.anyio
async def test_new_speech_interrupts_previous_never_two_at_once():
    synth = FakeSynth(delay_s=0.2)
    machine, sp, _, seen, events = make_speech(synth)
    await sp.speak("Primeira resposta longa. Com duas frases.")
    await until(lambda: any(e[0] == "start" for e in events))
    await sp.speak("Segunda resposta.")
    assert sp.utterance_id == 2 and events[-1] == ("stop", 1)
    await until(lambda: any(e[0] == "end" and e[1] == 2 for e in events))
    ids_after_stop = [e[1] for e in events[events.index(("stop", 1)) + 1 :] if e[0] in ("start", "audio", "end")]
    assert ids_after_stop and set(ids_after_stop) == {2}
    sp.playback_done(2)
    await settle(sp)
    assert machine.detail == DETAIL_READY


@pytest.mark.anyio
async def test_warmup_runs_in_background():
    machine, sp, synth, *_ = make_speech()
    await sp.warmup()
    assert synth.warmed and machine.state == AssistantState.IDLE


# ---- integracao com a conversa e o pipeline ---------------------------------------------------------------


def make_pipeline(synth=None, llm=None, barge_in=True):
    machine = StateMachine(AssistantState.IDLE)
    seen: list[tuple[str, str]] = []
    events: list[tuple] = []
    replies: list[tuple[str, bool]] = []

    async def spy(state, detail):
        seen.append((state.value, detail))

    async def on_reply(t, final):
        replies.append((t, final))

    async def on_start(uid, rate):
        events.append(("start", uid))

    async def on_end(uid, samples):
        events.append(("end", uid))

    async def on_stop(uid):
        events.append(("stop", uid))

    machine.subscribe(spy)
    synth = synth if synth is not None else FakeSynth()
    sp = Speech(machine, synth, on_start=on_start, on_end=on_end, on_stop=on_stop, done_hold_s=0.05, playback_margin_s=0.3)
    conv = Conversation(machine, llm or FakeLLM(pieces=("Claro! ", "Use duas xícaras de água.")), on_reply=on_reply, done_hold_s=0.05, speaker=sp)
    p = VoicePipeline(
        machine, FakeDetector(), transcriber=FakeTranscriber("como faço arroz"), responder=conv, speaker=sp, barge_in=barge_in,
        silence_s=0.4, no_speech_s=1.0, listen_max_s=3.0, wake_hold_s=0.16, done_hold_s=0.05,
    )
    return machine, p, conv, sp, synth, seen, events, replies


async def settle_all(p, conv, sp, timeout=3.0):
    end = time.monotonic() + timeout
    while (p._stt_task is not None or conv.busy or sp.busy) and time.monotonic() < end:
        await asyncio.sleep(0.01)


@pytest.mark.anyio
async def test_reply_is_spoken_after_text_and_flow_returns_to_idle():
    machine, p, conv, sp, synth, seen, events, replies = make_pipeline()
    await p.feed(wake_frame() + speech(5) + silence(7))
    await until(lambda: any(e[0] == "end" for e in events))
    assert replies[-1] == ("Claro! Use duas xícaras de água.", True)
    assert synth.calls == ["Claro! Use duas xícaras de água."]
    assert [s for s, _ in seen] == ["wake", "listening", "transcribing", "thinking", "replying", "synthesizing", "speaking"]
    sp.playback_done(sp.utterance_id)
    await settle_all(p, conv, sp)
    assert (machine.state, machine.detail) == (AssistantState.IDLE, DETAIL_READY)
    # audio durante a fala anterior nao reativa fora do barge-in; nova interacao depois funciona
    await asyncio.sleep(0.1)
    await p.feed(silence(25) + wake_frame() + speech(5) + silence(7))
    await until(lambda: len(events) >= 4)
    assert sp.utterance_id == 2


@pytest.mark.anyio
async def test_wake_word_during_speech_interrupts_it(anyio_backend):
    machine, p, conv, sp, synth, seen, events, replies = make_pipeline(FakeSynth(delay_s=0.0, chunk_s=5.0))
    await p.feed(wake_frame() + speech(5) + silence(7))
    await until(lambda: machine.state == AssistantState.SPEAKING)
    p.detector.reset()
    await p.feed(silence(30) + wake_frame())  # cooldown ja passou (2 s de audio)
    assert machine.state == AssistantState.WAKE
    assert ("stop", 1) in events and not sp.busy
    await p.feed(speech(5) + silence(7))
    await until(lambda: sp.utterance_id == 2 and any(e == ("end", 2) for e in events))
    assert conv.replies == 2


@pytest.mark.anyio
async def test_wake_word_during_speech_ignored_without_barge_in():
    machine, p, conv, sp, synth, seen, events, replies = make_pipeline(FakeSynth(chunk_s=5.0), barge_in=False)
    await p.feed(wake_frame() + speech(5) + silence(7))
    await until(lambda: machine.state == AssistantState.SPEAKING)
    await p.feed(silence(30) + wake_frame() + speech(5))
    assert machine.state == AssistantState.SPEAKING and p.wakes == 1 and sp.busy
    assert await p.cancel() is True  # Cancelar/Esc para a fala
    assert (machine.state, machine.detail) == (AssistantState.IDLE, DETAIL_CANCELLED) and events[-1] == ("stop", 1)


@pytest.mark.anyio
async def test_without_speech_conversation_ends_with_ready():
    machine = StateMachine(AssistantState.IDLE)
    conv = Conversation(machine, FakeLLM(), done_hold_s=0.05, speaker=None)
    await conv.ask("oi")
    end = time.monotonic() + 2
    while conv.busy and time.monotonic() < end:
        await asyncio.sleep(0.01)
    assert (machine.state, machine.detail) == (AssistantState.IDLE, DETAIL_READY)
    assert DETAIL_THINKING  # importado para garantir que o modulo continua expondo os detalhes


# ---- WebSocket --------------------------------------------------------------------------------------------


def ws_client(tmp_path, synth, llm=None):
    settings = Settings(
        auth_token=TOKEN,
        client_dir=tmp_path,
        wake_model=tmp_path / "nao-existe.onnx",
        wake_features_dir=tmp_path,
        wake_vad_model=tmp_path / "nao-existe.onnx",
        listen_preroll_s=0.0,
        stt_model=tmp_path / "nao-existe.bin",
        stt_done_hold_s=0.05,
        memory_path=tmp_path / "memory.sqlite3",
    )
    settings.validate()
    app = create_app(
        settings, detector=FakeDetector(), transcriber=FakeTranscriber("que horas são"),
        llm=llm or FakeLLM(pieces=["São ", "dez horas."]), synthesizer=synth,
    )
    return TestClient(app)


def hello(ws):
    ws.send_json({"type": "hello", "token": TOKEN, "client": "ui", "version": 1})
    return ws.receive_json()


def recv(ws):
    """Próxima mensagem: ("json", dict) ou ("bytes", bytes)."""
    import json

    m = ws.receive()
    if m.get("bytes") is not None:
        return "bytes", m["bytes"]
    return "json", json.loads(m["text"])


def collect_until(ws, stop):
    out = []
    while True:
        kind, m = recv(ws)
        out.append((kind, m))
        if kind == "json" and stop(m):
            return out


def test_speech_is_streamed_over_websocket_and_confirmed(tmp_path):
    synth = FakeSynth()
    with ws_client(tmp_path, synth) as c, c.websocket_connect("/ws") as ws:
        w = hello(ws)
        assert w["tts"] == {"available": True, "reason": "", "engine": "fake-tts", "voice": "fake", "sample_rate": RATE}
        ws.send_bytes(wake_frame() + speech(6))
        ws.send_bytes(silence(20))
        msgs = collect_until(ws, lambda m: m["type"] == "speak_end")
        kinds = [(k, m["type"] if k == "json" else "bytes") for k, m in msgs]
        assert ("json", "speak_start") in kinds and kinds[-1] == ("json", "speak_end")
        start = next(m for k, m in msgs if k == "json" and m["type"] == "speak_start")
        assert start == {"type": "speak_start", "id": 1, "sample_rate": RATE, "channels": 1, "format": "pcm_s16le"}
        i = kinds.index(("json", "speak_start"))
        first_audio = next(j for j in range(i + 1, len(kinds)) if kinds[j][0] == "bytes")
        assert len(msgs[first_audio][1]) == 2 * int(0.05 * RATE)
        assert kinds[i + 1 : first_audio] == [("json", "state")]  # "Falando" entre o inicio e o primeiro trecho
        states = [(m["state"], m["detail"]) for k, m in msgs if k == "json" and m["type"] == "state"]
        assert ("synthesizing", DETAIL_SYNTHESIZING) in states and states[-1] == ("speaking", DETAIL_SPEAKING)
        final = [m for k, m in msgs if k == "json" and m["type"] == "reply" and m["final"]]
        assert final == [{"type": "reply", "text": "São dez horas.", "final": True}]
        assert c.get("/health").json()["state"] == "speaking"
        ws.send_json({"type": "speak_done", "id": 1, "played": True})
        m = ws.receive_json()
        assert (m["type"], m["state"], m["detail"]) == ("state", "idle", DETAIL_READY)
        assert ws.receive_json()["detail"] == ""
        h = c.get("/health").json()
        assert h["spoken"] == 1 and h["speech_failures"] == 0 and h["replies"] == 1


def test_speech_error_and_replay_over_websocket(tmp_path):
    synth = FakeSynth(error=TtsError("engine", "A voz falhou ao gerar o áudio. Tente de novo.", "x"))
    with ws_client(tmp_path, synth) as c, c.websocket_connect("/ws") as ws:
        hello(ws)
        ws.send_bytes(wake_frame() + speech(6))
        ws.send_bytes(silence(20))
        msgs = collect_until(ws, lambda m: m["type"] == "state" and m["state"] == "idle")
        assert msgs[-1][1]["detail"] == DETAIL_FAILED
        errors = [m for k, m in msgs if k == "json" and m["type"] == "speak_error"]
        assert errors == [{"type": "speak_error", "code": "engine", "message": synth.error.message, "retry": True}]
        assert [m for k, m in msgs if k == "json" and m["type"] == "reply" and m["final"]]  # o texto chegou e fica na tela
        assert not [k for k, _ in msgs if k == "bytes"]
        assert ws.receive_json()["detail"] == ""
        assert c.get("/health").json()["speech_failures"] == 1
        synth.error = None
        ws.send_json({"type": "replay"})
        msgs = collect_until(ws, lambda m: m["type"] == "speak_end")
        assert msgs[0][1]["state"] == "synthesizing" and msgs[-1][1]["id"] == 2
        assert not [m for k, m in msgs if k == "json" and m["type"] == "reply"]  # sem nova consulta a IA
        ws.send_json({"type": "speak_done", "id": 2})
        assert ws.receive_json()["detail"] == DETAIL_READY
        assert c.get("/health").json()["replies"] == 1
        ws.send_json({"type": "speak_done", "id": 2, "played": False, "error": "x"})  # tardio: ignorado
        ws.send_json({"type": "ping", "t": 1})
        assert ws.receive_json()["type"] in ("pong", "state")


def test_cancel_during_speech_sends_speak_stop(tmp_path):
    with ws_client(tmp_path, FakeSynth(chunk_s=5.0)) as c, c.websocket_connect("/ws") as ws:
        hello(ws)
        ws.send_bytes(wake_frame() + speech(6))
        ws.send_bytes(silence(20))
        collect_until(ws, lambda m: m["type"] == "speak_end")
        ws.send_json({"type": "cancel"})
        msgs = collect_until(ws, lambda m: m["type"] == "state" and m["state"] == "idle")
        assert [m["type"] for k, m in msgs if k == "json"] == ["speak_stop", "state"]
        assert msgs[-1][1]["detail"] == DETAIL_CANCELLED


def test_say_speaks_text_only_in_debug_mode(tmp_path):
    with ws_client(tmp_path, FakeSynth()) as c, c.websocket_connect("/ws") as ws:
        hello(ws)
        ws.send_json({"type": "say", "text": "Teste de voz."})
        assert ws.receive_json()["code"] == "forbidden"  # debug desligado
    settings = Settings(
        auth_token=TOKEN, debug_allow_state_override=True, client_dir=tmp_path, wake_model=tmp_path / "x.onnx",
        wake_features_dir=tmp_path, wake_vad_model=tmp_path / "x.onnx", stt_model=tmp_path / "x.bin", stt_done_hold_s=0.05,
        memory_path=tmp_path / "memory.sqlite3",
    )
    settings.validate()
    app = create_app(settings, detector=FakeDetector(), transcriber=FakeTranscriber("x"), llm=FakeLLM(), synthesizer=FakeSynth())
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        hello(ws)
        ws.send_json({"type": "say", "text": "Teste de voz."})
        msgs = collect_until(ws, lambda m: m["type"] == "speak_end")
        assert msgs[0][1] == {"type": "reply", "text": "Teste de voz.", "final": True}
        assert [m["type"] for k, m in msgs if k == "json"][1:3] == ["state", "speak_start"]
        ws.send_json({"type": "speak_done", "id": 1})
        assert ws.receive_json()["detail"] == DETAIL_READY


def test_tts_settings_validation():
    with pytest.raises(Exception, match="tts.engine"):
        Settings(auth_token=TOKEN, tts_engine="festival").validate()
    with pytest.raises(Exception, match="tts.speed"):
        Settings(auth_token=TOKEN, tts_speed=3.0).validate()
    Settings(auth_token=TOKEN, tts_enabled=False, tts_engine="festival").validate()  # desativada: nao valida
    s = Settings(auth_token=TOKEN)
    assert s.tts_engine == "kokoro" and s.tts_voice == "pf_dora" and s.tts_model.name == "kokoro-v1.0.onnx"


def test_tts_settings_from_toml_piper_default_model(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text(f'[auth]\ntoken="{TOKEN}"\n[tts]\nengine="piper"\nvolume=1.5\n', encoding="utf-8")
    s = Settings.from_toml(p)
    assert s.tts_engine == "piper" and s.tts_model.name == "pt_BR-faber-medium.onnx" and s.tts_volume == 1.5
    p.write_text(f'[auth]\ntoken="{TOKEN}"\n[tts]\nenabled=false\n', encoding="utf-8")
    assert Settings.from_toml(p).tts_enabled is False


# ---- motor real -------------------------------------------------------------------------------------------


@pytest.mark.skipif(
    not ((PROJECT_ROOT / "models" / "tts" / "kokoro-v1.0.onnx").is_file() and (PROJECT_ROOT / "models" / "tts" / "voices-v1.0.bin").is_file()),
    reason="modelo Kokoro ausente (scripts/prepare_models.py kokoro)",
)
def test_real_kokoro_voice_speaks_portuguese():
    import numpy as np

    from server.services.tts import KokoroSynthesizer

    models = PROJECT_ROOT / "models" / "tts"
    tts = KokoroSynthesizer(models / "kokoro-v1.0.onnx", models / "voices-v1.0.bin", voice="pf_dora", language="pt-br")
    assert tts.sample_rate == 24000 and tts.voice == "pf_dora"
    t0 = time.perf_counter()
    chunks = list(tts.synthesize("Olá! Hoje faz vinte e cinco graus em Ouro Fino. Quer uma receita de bolo?"))
    dt = time.perf_counter() - t0
    assert len(chunks) == 2  # "Olá!" junta-se a frase seguinte; a pergunta fica separada
    audio = np.frombuffer(b"".join(chunks), dtype=np.int16)
    seconds = len(audio) / tts.sample_rate
    assert 3.0 < seconds < 12.0 and np.abs(audio).max() > 3000  # ha fala de verdade, nao silencio
    assert dt < seconds  # mais rapido que o tempo real (medido ~0,3x no Ryzen)
    cancel = threading.Event()
    cancel.set()
    assert list(tts.synthesize("Nada deve sair.", cancel)) == []
