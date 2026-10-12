"""Pipeline de voz com um detector falso (sem modelo) e, se os modelos existirem, com o real."""
import asyncio
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from server.api.app import create_app
from server.assistant.state import AssistantState, StateMachine
from server.assistant.voice import (
    DETAIL_CANCELLED,
    DETAIL_DONE,
    DETAIL_FAILED,
    DETAIL_LISTENING,
    DETAIL_NOTHING,
    DETAIL_TRANSCRIBING,
    VoicePipeline,
)
from server.services.transcriber import WhisperCppTranscriber, audio_ctx_for, clean_text
from server.services.wakeword import FRAME_SAMPLES, SAMPLE_RATE, OpenWakeWordDetector
from server.storage.config import PROJECT_ROOT, Settings

TOKEN = "token-de-teste-com-tamanho-ok"
MARK = 31000  # primeira amostra = marcador que o detector falso reconhece
VOICE_MARK = 29000  # primeira amostra = marcador que o VAD falso reconhece como voz
FRAME_BYTES = FRAME_SAMPLES * 2


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeDetector:
    name = "fake"

    def __init__(self) -> None:
        self.resets = 0

    def predict(self, frame: np.ndarray) -> float:
        return 1.0 if frame[0] == MARK else 0.0

    def reset(self) -> None:
        self.resets += 1


def silence(n=1) -> bytes:
    return np.zeros(FRAME_SAMPLES * n, dtype=np.int16).tobytes()


def wake_frame() -> bytes:
    f = np.zeros(FRAME_SAMPLES, dtype=np.int16)
    f[0] = MARK
    return f.tobytes()


def speech(n=1, amplitude=2500) -> bytes:
    f = np.full(FRAME_SAMPLES * n, amplitude, dtype=np.int16)
    f[1::2] *= -1
    return f.tobytes()


def voiced(n=1, amplitude=2500) -> bytes:
    """Quadros que o VAD falso considera voz (marcador na primeira amostra), com a energia pedida."""
    f = np.frombuffer(speech(n, amplitude), dtype=np.int16).copy()
    f[::FRAME_SAMPLES] = VOICE_MARK
    return f.tobytes()


class FakeVad:
    name = "fake-vad"

    def __init__(self) -> None:
        self.resets = 0
        self.calls = 0

    def predict(self, frame: np.ndarray) -> float:
        self.calls += 1
        return 1.0 if frame[0] == VOICE_MARK else 0.0

    def reset(self) -> None:
        self.resets += 1


def make_pipeline(**kw):
    machine = StateMachine(AssistantState.IDLE)
    seen: list[tuple[str, str]] = []

    async def spy(state, detail):
        seen.append((state.value, detail))

    machine.subscribe(spy)
    heard: list[bytes] = []

    async def on_utt(pcm: bytes):
        heard.append(pcm)

    opts = dict(silence_s=0.4, no_speech_s=1.0, listen_max_s=3.0, wake_hold_s=0.16, preroll_s=0.0, on_utterance=on_utt)
    opts.update(kw)
    return machine, VoicePipeline(machine, FakeDetector(), **opts), seen, heard


class FakeTranscriber:
    """Devolve um texto fixo (teste de fluxo; nao e uma transcricao real). `delay_s` simula o custo."""

    name = "fake-stt"

    def __init__(self, text="texto de teste", delay_s=0.0, fail=False) -> None:
        self.text, self.delay_s, self.fail = text, delay_s, fail
        self.calls: list[int] = []
        self.aborted = False

    def transcribe(self, pcm: bytes, cancel: threading.Event | None = None) -> str:
        self.calls.append(len(pcm))
        if self.fail:
            raise RuntimeError("boom")
        deadline = time.monotonic() + self.delay_s
        while time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                self.aborted = True
                return ""
            time.sleep(0.005)
        return self.text


async def settle(p: VoicePipeline, timeout=2.0):
    """Espera a transcricao em andamento terminar (tarefa interna)."""
    end = time.monotonic() + timeout
    while p._stt_task is not None and time.monotonic() < end:
        await asyncio.sleep(0.01)


def make_stt_pipeline(stt=None, **kw):
    texts: list[str] = []

    async def on_text(t: str):
        texts.append(t)

    stt = stt or FakeTranscriber()
    machine, p, seen, heard = make_pipeline(transcriber=stt, on_transcript=on_text, done_hold_s=0.05, **kw)
    return machine, p, seen, heard, stt, texts


@pytest.mark.anyio
async def test_utterance_is_transcribed_and_shown_then_idle_detail_clears():
    machine, p, seen, heard, stt, texts = make_stt_pipeline()
    await p.feed(wake_frame() + speech(5) + silence(7))
    assert machine.state == AssistantState.TRANSCRIBING and seen[-1] == ("transcribing", DETAIL_TRANSCRIBING)
    await settle(p)
    assert texts == ["texto de teste"] and stt.calls == [len(heard[0])]
    assert machine.state == AssistantState.IDLE and machine.detail == DETAIL_DONE
    assert p.transcripts == 1
    await asyncio.sleep(0.15)  # done_hold_s
    assert (machine.state, machine.detail) == (AssistantState.IDLE, "")
    # nova interacao apos o cooldown funciona normalmente
    await p.feed(silence(25) + wake_frame())
    assert machine.state == AssistantState.WAKE and p.wakes == 2


@pytest.mark.anyio
async def test_empty_transcription_reports_nothing_recognized():
    machine, p, seen, heard, stt, texts = make_stt_pipeline(FakeTranscriber(text=""))
    await p.feed(wake_frame() + speech(5) + silence(7))
    await settle(p)
    assert texts == [] and machine.detail == DETAIL_NOTHING and p.transcripts == 0


@pytest.mark.anyio
async def test_transcriber_failure_returns_to_idle_with_detail():
    machine, p, seen, heard, stt, texts = make_stt_pipeline(FakeTranscriber(fail=True))
    await p.feed(wake_frame() + speech(5) + silence(7))
    await settle(p)
    assert (machine.state, machine.detail) == (AssistantState.IDLE, DETAIL_FAILED) and texts == []


@pytest.mark.anyio
async def test_audio_during_transcription_is_ignored_until_idle():
    machine, p, seen, heard, stt, texts = make_stt_pipeline(FakeTranscriber(delay_s=0.3))
    await p.feed(wake_frame() + speech(5) + silence(7))
    await p.feed(silence(25) + wake_frame())  # chega enquanto transcreve: nao reativa
    assert machine.state == AssistantState.TRANSCRIBING and p.wakes == 1
    await settle(p)
    assert machine.detail == DETAIL_DONE


@pytest.mark.anyio
async def test_cancel_while_listening_discards_audio():
    machine, p, seen, heard, stt, texts = make_stt_pipeline()
    await p.feed(wake_frame() + speech(3))
    assert machine.state == AssistantState.LISTENING
    assert await p.cancel() is True
    assert (machine.state, machine.detail) == (AssistantState.IDLE, DETAIL_CANCELLED)
    assert heard == [] and stt.calls == []
    assert await p.cancel() is False  # nada em andamento


@pytest.mark.anyio
async def test_cancel_while_transcribing_aborts_and_allows_new_interaction():
    stt = FakeTranscriber(delay_s=2.0)
    machine, p, seen, heard, _, texts = make_stt_pipeline(stt)
    await p.feed(wake_frame() + speech(5) + silence(7))
    assert machine.state == AssistantState.TRANSCRIBING
    for _ in range(100):  # a transcricao roda em outra thread; espera ela comecar antes de cancelar
        if stt.calls:
            break
        await asyncio.sleep(0.01)
    assert stt.calls
    assert await p.cancel() is True
    assert (machine.state, machine.detail) == (AssistantState.IDLE, DETAIL_CANCELLED)
    for _ in range(100):
        if stt.aborted:
            break
        await asyncio.sleep(0.01)
    assert stt.aborted and texts == []
    await p.feed(silence(25) + wake_frame() + speech(5) + silence(7))
    stt.delay_s = 0.0
    await settle(p)
    assert texts == ["texto de teste"] and machine.detail == DETAIL_DONE


@pytest.mark.anyio
async def test_without_transcriber_capture_returns_to_idle():
    machine, p, seen, heard = make_pipeline()
    assert p.transcription_available is False
    await p.feed(wake_frame() + speech(5) + silence(7))
    assert machine.state == AssistantState.IDLE and len(heard) == 1 and p._stt_task is None


def test_clean_text_removes_non_speech_markers():
    assert clean_text(" [BLANK_AUDIO]  Que horas  são? (música) ") == "Que horas são?"
    assert clean_text("[Risos]") == ""


@pytest.mark.anyio
async def test_wake_listen_and_return_to_idle():
    machine, p, seen, heard = make_pipeline()
    await p.feed(silence(5))
    assert machine.state == AssistantState.IDLE and seen == []
    await p.feed(wake_frame())  # quadro 6, t=0,48 s
    assert seen == [("wake", "Palavra detectada")]
    assert p.detector.resets == 1
    await p.feed(speech(5))  # quadros 7-11: passa a listening no quadro 8 (0,16 s depois)
    assert seen[-1] == ("listening", DETAIL_LISTENING)
    await p.feed(silence(7))  # silencio >= 0,4 s encerra no quadro 16
    assert machine.state == AssistantState.IDLE
    assert len(heard) == 1
    assert len(heard[0]) == FRAME_BYTES * 10  # quadros 7..16 coletados
    # cooldown: nova ativacao imediata e ignorada
    await p.feed(wake_frame())
    assert machine.state == AssistantState.IDLE and p.wakes == 1
    await p.feed(silence(25))  # 2,0 s
    await p.feed(wake_frame())
    assert p.wakes == 2 and machine.state == AssistantState.WAKE


@pytest.mark.anyio
async def test_listening_without_speech_times_out_without_utterance():
    machine, p, seen, heard = make_pipeline()
    await machine.set(AssistantState.LISTENING, "teste")  # acionado pelo modo debug
    await p.feed(silence(14))  # 1,04 s sem fala >= no_speech_s
    assert machine.state == AssistantState.IDLE
    assert heard == []


@pytest.mark.anyio
async def test_listen_max_caps_the_buffer():
    machine, p, seen, heard = make_pipeline(listen_max_s=0.8)
    await machine.set(AssistantState.LISTENING)
    await p.feed(speech(20))
    assert machine.state == AssistantState.IDLE
    assert len(heard[0]) == FRAME_BYTES * 10


@pytest.mark.anyio
async def test_without_detector_audio_is_dropped():
    machine = StateMachine(AssistantState.IDLE)
    p = VoicePipeline(machine, None)
    assert p.available is False
    await p.feed(wake_frame())
    assert machine.state == AssistantState.IDLE and p.frames == 1


@pytest.mark.anyio
async def test_partial_frames_are_accumulated():
    machine, p, seen, heard = make_pipeline()
    data = wake_frame()
    await p.feed(data[:100])
    assert p.frames == 0
    await p.feed(data[100:])
    assert p.frames == 1 and machine.state == AssistantState.WAKE


@pytest.mark.anyio
async def test_preroll_keeps_audio_heard_just_before_the_wake_word():
    """Os quadros anteriores a deteccao (incluindo o da deteccao) entram na captura: a palavra de ativacao
    e reconhecida 80-160 ms depois de terminar e quem fala emendado perderia a primeira palavra."""
    machine, p, seen, heard = make_pipeline(preroll_s=0.24)  # 3 quadros
    await p.feed(silence(5) + wake_frame() + speech(5) + silence(7))
    assert machine.state == AssistantState.IDLE and len(heard) == 1
    assert len(heard[0]) == FRAME_BYTES * (3 + 10)  # quadros 4-6 (pre-buffer) + 7-16 (captura)
    assert np.frombuffer(heard[0], dtype=np.int16)[2 * FRAME_SAMPLES] == MARK  # o 3o quadro do pre-buffer e o da deteccao
    # o pre-buffer e consumido: a proxima captura nao repete os mesmos quadros
    await p.feed(silence(25) + wake_frame() + speech(5) + silence(7))
    assert len(heard) == 2 and len(heard[1]) == FRAME_BYTES * (3 + 10)


@pytest.mark.anyio
async def test_noise_floor_follows_steady_noise_but_not_speech_heard_while_idle():
    """Falar alto em espera (ou a propria "hey Luna") nao pode triplicar o limiar de energia: antes, uma
    media simples subia a ~1800 RMS em 2 s de fala e o limiar (3x) passava de qualquer fala normal.
    Ruido continuo (ventilador), por outro lado, vira o piso e nao e confundido com fala."""
    machine, p, seen, heard = make_pipeline()
    await p.feed((speech(4) + silence(1)) * 5)  # 2 s de fala alta (RMS 2500) com pausas curtas entre palavras
    assert p.noise_rms < 100, p.noise_rms
    await p.feed(wake_frame())
    assert p._speech_thr == 600.0  # limiar minimo, nao 3x um ruido inflado pela fala
    await p.feed(speech(5, amplitude=800) + silence(7))  # fala mais baixa que a anterior
    assert len(heard) == 1, "fala baixa deveria ter sido capturada"
    await p.feed(speech(40, amplitude=1000))  # 3,2 s de ruido continuo em espera
    assert p.noise_rms == pytest.approx(1000, rel=0.01)
    await p.feed(wake_frame() + speech(14, amplitude=1000))  # so o ruido: 1,12 s >= no_speech_s
    assert p._speech_thr == pytest.approx(3000, rel=0.01)
    assert machine.state == AssistantState.IDLE and len(heard) == 1


@pytest.mark.anyio
async def test_vad_decides_speech_instead_of_energy():
    vad = FakeVad()
    machine, p, seen, heard = make_pipeline(vad=vad)
    assert p.vad_available
    # quadros altos sem voz (ruido) nao sao fala: a captura termina por "nenhuma fala"
    await p.feed(wake_frame() + speech(14))  # 1,12 s >= no_speech_s
    assert machine.state == AssistantState.IDLE and heard == [] and vad.resets == 1
    # quadros baixos com voz (fala baixa) sao fala e terminam por silencio (quadros altos sem voz)
    await p.feed(silence(25) + wake_frame() + voiced(5, amplitude=300) + speech(7))
    assert len(heard) == 1
    assert FRAME_BYTES * 10 <= len(heard[0]) <= FRAME_BYTES * 11  # 5 com voz + 0,4 s (5-6 quadros) ate decidir o fim
    assert p.last_capture_s == pytest.approx(len(heard[0]) / (2 * SAMPLE_RATE))


def test_audio_ctx_scales_with_audio_but_never_below_minimum():
    assert audio_ctx_for(2.0, 768) == 768
    assert audio_ctx_for(8.0, 768) == 768  # listen_max_s cabe com folga
    assert audio_ctx_for(20.0, 768) == 20 * 50 + 64
    assert audio_ctx_for(40.0, 768) == 1500
    assert audio_ctx_for(3.0, 0) == 0  # 0 = contexto completo


def ws_client(tmp_path, detector=None, transcriber=None):
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
        llm_enabled=False,  # a IA e testada em tests/test_conversation.py
        memory_path=tmp_path / "memory.sqlite3",
    )
    settings.validate()
    return TestClient(create_app(settings, detector=detector, transcriber=transcriber))


def hello(ws):
    ws.send_json({"type": "hello", "token": TOKEN, "client": "ui", "version": 1})
    return ws.receive_json()


def test_transcript_is_broadcast_and_cancel_works_over_websocket(tmp_path):
    with ws_client(tmp_path, FakeDetector(), FakeTranscriber("que horas são")) as c, c.websocket_connect("/ws") as ws:
        w = hello(ws)
        assert w["transcription"] == {"available": True, "reason": "", "model": "fake-stt", "language": "pt"}
        ws.send_bytes(wake_frame() + speech(6))  # cada mensagem fica abaixo de MAX_AUDIO_FRAME_BYTES (64 KB)
        ws.send_bytes(silence(20))
        states = [ws.receive_json() for _ in range(3)]
        assert [s["state"] for s in states] == ["wake", "listening", "transcribing"]
        t = ws.receive_json()
        assert t == {"type": "transcript", "text": "que horas são", "final": True}
        done = ws.receive_json()
        assert (done["state"], done["detail"]) == ("idle", DETAIL_DONE)
        assert ws.receive_json()["detail"] == ""  # detalhe transitorio some apos done_hold_s
        h = c.get("/health").json()
        assert h["transcripts"] == 1 and h["transcription"]["available"] is True
        assert h["endpointing"] == "rms" and h["last_capture_s"] == pytest.approx(1.68)  # 6 quadros de fala + 1,2 s
        assert h["last_speech_to_text_s"] >= 0.0 and "noise_rms" in h  # audio injetado de uma vez: tempo de relogio ~0
        # cancelar durante a escuta (acionada pelo modo debug)
        ws.send_json({"type": "set_state", "state": "listening"})
        assert ws.receive_json()["state"] == "listening"
        ws.send_json({"type": "cancel"})
        msg = ws.receive_json()
        assert (msg["state"], msg["detail"]) == ("idle", DETAIL_CANCELLED)
        assert ws.receive_json()["detail"] == ""  # "Cancelado" some apos done_hold_s
        ws.send_json({"type": "cancel"})  # sem interacao: ignorado, sem erro
        ws.send_json({"type": "ping", "t": 2})
        assert ws.receive_json()["type"] == "pong"


def test_missing_stt_model_reports_unavailable(tmp_path):
    with ws_client(tmp_path, FakeDetector()) as c, c.websocket_connect("/ws") as ws:
        w = hello(ws)
        assert w["transcription"]["available"] is False
        assert "prepare_models" in w["transcription"]["reason"]
        ws.send_bytes(wake_frame() + speech(6))  # cada mensagem fica abaixo de MAX_AUDIO_FRAME_BYTES (64 KB)
        ws.send_bytes(silence(20))
        assert [ws.receive_json()["state"] for _ in range(3)] == ["wake", "listening", "idle"]


def test_binary_audio_drives_states_over_websocket(tmp_path):
    with ws_client(tmp_path, FakeDetector()) as c, c.websocket_connect("/ws") as ws:
        w = hello(ws)
        assert w["wake"]["available"] is True and w["wake"]["model"] == "fake"
        ws.send_bytes(wake_frame())
        msg = ws.receive_json()
        assert (msg["type"], msg["state"], msg["detail"]) == ("state", "wake", "Palavra detectada")
        ws.send_bytes(speech(6))
        assert ws.receive_json()["state"] == "listening"
        ws.send_bytes(silence(20))
        assert ws.receive_json()["state"] == "idle"
        ws.send_json({"type": "ping", "t": 1})  # o pong so chega depois de todo o audio ser processado
        assert ws.receive_json()["type"] == "pong"
        h = c.get("/health").json()
        assert h["wakes"] == 1 and h["audio_frames"] == 27


def test_missing_model_reports_unavailable_and_ignores_audio(tmp_path):
    with ws_client(tmp_path) as c, c.websocket_connect("/ws") as ws:
        w = hello(ws)
        assert w["wake"]["available"] is False
        assert "prepare_models" in w["wake"]["reason"]
        ws.send_bytes(wake_frame())
        ws.send_json({"type": "ping", "t": 1})
        assert ws.receive_json()["type"] == "pong"  # nenhum estado mudou
        assert c.get("/health").json()["state"] == "idle"


def test_only_one_audio_source_at_a_time(tmp_path):
    with ws_client(tmp_path, FakeDetector()) as c, c.websocket_connect("/ws") as a, c.websocket_connect("/ws") as b:
        hello(a)
        hello(b)
        a.send_bytes(silence())
        b.send_bytes(silence())
        assert b.receive_json()["code"] == "busy"
        a.send_bytes(b"\x00")  # tamanho impar
        assert a.receive_json()["code"] == "bad_audio"


MODELS = PROJECT_ROOT / "models" / "openwakeword"
HAS_MODEL = (MODELS / "hey_jarvis_v0.1.onnx").is_file()


@pytest.mark.skipif(not HAS_MODEL, reason="modelos nao baixados")
def test_real_model_scores_low_on_silence_and_noise():
    det = OpenWakeWordDetector(MODELS / "hey_jarvis_v0.1.onnx", MODELS)
    assert det.name == "hey_jarvis_v0.1"
    rng = np.random.default_rng(0)
    scores = [det.predict(np.zeros(FRAME_SAMPLES, dtype=np.int16)) for _ in range(30)]
    scores += [det.predict((rng.standard_normal(FRAME_SAMPLES) * 2000).astype(np.int16)) for _ in range(30)]
    assert max(scores) < 0.1


FIXTURE = Path(__file__).parent / "fixtures" / "hey_jarvis_16k.wav"


@pytest.mark.skipif(not FIXTURE.is_file() or not HAS_MODEL, reason="amostra ou modelo ausente")
def test_real_model_detects_hey_jarvis_sample():
    import wave

    with wave.open(str(FIXTURE), "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (SAMPLE_RATE, 1, 2)
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    pcm = np.concatenate([pcm, np.zeros(FRAME_SAMPLES * 10, dtype=np.int16)])
    det = OpenWakeWordDetector(MODELS / "hey_jarvis_v0.1.onnx", MODELS)
    best = 0.0
    for i in range(0, len(pcm) - FRAME_SAMPLES + 1, FRAME_SAMPLES):
        best = max(best, det.predict(pcm[i : i + FRAME_SAMPLES]))
    assert best >= 0.5, f"score maximo {best:.2f}"


LUNA_MODEL = MODELS / "hey_luna.onnx"
LUNA_FIXTURE = Path(__file__).parent / "fixtures" / "hey_luna_16k.wav"


def _max_score(det, wav_path: Path) -> float:
    import wave

    with wave.open(str(wav_path), "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (SAMPLE_RATE, 1, 2)
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    pcm = np.concatenate([pcm, np.zeros(FRAME_SAMPLES * 10, dtype=np.int16)])
    best = 0.0
    for i in range(0, len(pcm) - FRAME_SAMPLES + 1, FRAME_SAMPLES):
        best = max(best, det.predict(pcm[i : i + FRAME_SAMPLES]))
    return best


@pytest.mark.skipif(not LUNA_MODEL.is_file(), reason="modelo hey_luna nao baixado (scripts/prepare_models.py hey_luna)")
def test_real_luna_model_scores_low_on_silence_noise_and_other_phrases():
    det = OpenWakeWordDetector(LUNA_MODEL, MODELS)
    assert det.name == "hey_luna"
    rng = np.random.default_rng(0)
    scores = [det.predict(np.zeros(FRAME_SAMPLES, dtype=np.int16)) for _ in range(30)]
    scores += [det.predict((rng.standard_normal(FRAME_SAMPLES) * 2000).astype(np.int16)) for _ in range(30)]
    assert max(scores) < 0.1
    if FIXTURE.is_file():  # "hey Jarvis" nao deve ativar o modelo "hey Luna"
        det.reset()
        assert _max_score(det, FIXTURE) < 0.3


@pytest.mark.skipif(not LUNA_FIXTURE.is_file() or not LUNA_MODEL.is_file(), reason="amostra ou modelo ausente")
def test_real_model_detects_hey_luna_sample():
    det = OpenWakeWordDetector(LUNA_MODEL, MODELS)
    best = _max_score(det, LUNA_FIXTURE)
    assert best >= 0.5, f"score maximo {best:.2f}"


ATEM_MODEL = MODELS / "hey_atem.onnx"
ATEM_FIXTURE = Path(__file__).parent / "fixtures" / "hey_atem_16k.wav"


@pytest.mark.skipif(not ATEM_MODEL.is_file(), reason="modelo hey_atem.onnx nao treinado (docs/palavra-de-ativacao.md)")
def test_real_atem_model_loads_and_scores_low_on_silence():
    det = OpenWakeWordDetector(ATEM_MODEL, MODELS)
    assert det.name == "hey_atem"
    scores = [det.predict(np.zeros(FRAME_SAMPLES, dtype=np.int16)) for _ in range(30)]
    assert max(scores) < 0.1


@pytest.mark.skipif(not ATEM_FIXTURE.is_file() or not ATEM_MODEL.is_file(), reason="amostra ou modelo ausente")
def test_real_model_detects_hey_atem_sample():
    import wave

    with wave.open(str(ATEM_FIXTURE), "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (SAMPLE_RATE, 1, 2)
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    pcm = np.concatenate([pcm, np.zeros(FRAME_SAMPLES * 10, dtype=np.int16)])
    det = OpenWakeWordDetector(ATEM_MODEL, MODELS)
    best = 0.0
    for i in range(0, len(pcm) - FRAME_SAMPLES + 1, FRAME_SAMPLES):
        best = max(best, det.predict(pcm[i : i + FRAME_SAMPLES]))
    assert best >= 0.5, f"score maximo {best:.2f}"


WHISPER = PROJECT_ROOT / "models" / "whisper"
STT_FIXTURE = Path(__file__).parent / "fixtures" / "pt_frase_16k.wav"


@pytest.mark.skipif(not (WHISPER / "ggml-small.bin").is_file() or not STT_FIXTURE.is_file(), reason="modelo whisper ou amostra ausente")
def test_real_whisper_transcribes_portuguese_sample_and_ignores_noise():
    """Amostra gerada pela voz pt-BR do Windows: 'Qual e a previsao do tempo para amanha em Ouro Fino?'"""
    import wave

    stt = WhisperCppTranscriber(WHISPER / "ggml-small.bin", vad_model=WHISPER / "ggml-silero-v5.1.2.bin")
    with wave.open(str(STT_FIXTURE), "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (SAMPLE_RATE, 1, 2)
        pcm = w.readframes(w.getnframes())
    text = stt.transcribe(pcm).lower()
    assert "previs" in text and "amanh" in text and "ouro fino" in text, text
    rng = np.random.default_rng(1)
    noise = (rng.standard_normal(SAMPLE_RATE * 2) * 1500).astype(np.int16).tobytes()
    if stt.vad:
        assert stt.transcribe(noise) == ""
    cancel = threading.Event()
    cancel.set()
    assert stt.transcribe(pcm, cancel) == ""


@pytest.mark.skipif(
    not (LUNA_MODEL.is_file() and LUNA_FIXTURE.is_file() and (WHISPER / "ggml-small.bin").is_file() and STT_FIXTURE.is_file()),
    reason="modelos (hey_luna, whisper-small) ou amostras ausentes",
)
@pytest.mark.anyio
async def test_real_pipeline_transcribes_phrase_after_hey_luna_without_the_wake_word():
    """Fluxo completo com modelos reais: 'hey Luna' + frase em pt-BR -> texto sem a palavra de ativacao."""
    import wave

    def pcm_of(path: Path) -> bytes:
        with wave.open(str(path), "rb") as w:
            return w.readframes(w.getnframes())

    det = OpenWakeWordDetector(LUNA_MODEL, MODELS)
    stt = WhisperCppTranscriber(WHISPER / "ggml-small.bin", vad_model=WHISPER / "ggml-silero-v5.1.2.bin")
    machine = StateMachine(AssistantState.IDLE)
    texts: list[str] = []

    async def on_text(t: str):
        texts.append(t)

    p = VoicePipeline(machine, det, transcriber=stt, on_transcript=on_text, done_hold_s=0.05)
    gap = silence(4)  # 0,32 s entre a palavra de ativacao e a frase
    await p.feed(pcm_of(LUNA_FIXTURE) + gap + pcm_of(STT_FIXTURE) + silence(25))
    assert p.wakes == 1, "hey Luna nao foi detectado"
    assert machine.state == AssistantState.TRANSCRIBING
    await settle(p, timeout=60.0)
    assert texts, machine.detail
    text = texts[0].lower()
    assert "luna" not in text, text
    assert "previs" in text and "ouro fino" in text, text
    assert (machine.state, machine.detail) == (AssistantState.IDLE, DETAIL_DONE)
