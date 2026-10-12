"""Fluxo de áudio: PCM 16 kHz mono -> palavra de ativação -> captura da fala -> transcrição local.

A fala capturada fica só em memória: é transcrita (whisper.cpp) e descartada. Nenhum
áudio é gravado em disco nem sai da rede local. O tempo da captura é contado em quadros
de áudio (80 ms), não em relógio, para ser determinístico e independente de atrasos de rede.

Fim da fala: com o Silero VAD presente, a decisão "este quadro tem voz?" vem dele (robusto a
ruído de fundo e a fala baixa). Sem VAD, usa energia (RMS) com um piso de ruído que sobe devagar
e desce rápido, para a própria "hey Luna" não inflar o limiar. Um pequeno pré-buffer dos quadros
anteriores à detecção preserva o início da frase quando a pessoa fala sem pausa após a ativação.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Awaitable, Callable

import numpy as np

from server.assistant.conversation import Conversation
from server.assistant.speech import Speech
from server.assistant.state import AssistantState, StateMachine
from server.services.transcriber import Transcriber
from server.services.vad import SpeechDetector
from server.services.wakeword import FRAME_BYTES, FRAME_SAMPLES, SAMPLE_RATE, Detector

log = logging.getLogger("jarvis.voice")

FRAME_S = FRAME_SAMPLES / SAMPLE_RATE
UtteranceHandler = Callable[[bytes], Awaitable[None]]
TranscriptHandler = Callable[[str], Awaitable[None]]

DETAIL_LISTENING = "Ouvindo · pode falar"
DETAIL_TRANSCRIBING = "Transcrevendo…"
DETAIL_DONE = "Transcrição concluída"
DETAIL_NOTHING = "Nada reconhecido"
DETAIL_CANCELLED = "Cancelado"
DETAIL_FAILED = "Falha na transcrição"

# Fim da fala por VAD: probabilidade minima para a fala comecar e, com histerese, para continuar
# (medido com o Silero: fala 0,9-1,0; ruido rosa 0,17; ruido branco estacionario chega a 0,7).
VAD_START = 0.6
VAD_CONTINUE = 0.4
# Fim da fala por energia (sem VAD): limiar = max(speech_rms, NOISE_FACTOR x piso de ruido), no maximo
# RMS_THR_MAX; depois que a fala comeca, metade do limiar ainda conta como fala (histerese). O piso e o
# percentil 10 do RMS dos ultimos NOISE_WINDOW_S: a fala tem pausas entre palavras, o ruido continuo nao.
NOISE_FACTOR = 3.0
RMS_THR_MAX = 4000.0
RMS_CONTINUE = 0.5
NOISE_WINDOW_S = 3.0
NOISE_PERCENTILE = 10
NOISE_MIN_FRAMES = 13  # ~1 s de audio antes de confiar na estimativa; antes disso vale so speech_rms


class VoicePipeline:
    def __init__(
        self,
        machine: StateMachine,
        detector: Detector | None,
        *,
        transcriber: Transcriber | None = None,
        vad: SpeechDetector | None = None,
        threshold: float = 0.5,
        cooldown_s: float = 2.0,
        wake_hold_s: float = 0.4,
        preroll_s: float = 0.32,
        listen_max_s: float = 8.0,
        silence_s: float = 1.2,
        no_speech_s: float = 4.0,
        speech_rms: float = 600.0,
        done_hold_s: float = 4.0,
        on_utterance: UtteranceHandler | None = None,
        on_transcript: TranscriptHandler | None = None,
        responder: Conversation | None = None,
        speaker: Speech | None = None,
        barge_in: bool = True,
    ) -> None:
        self.machine = machine
        self.detector = detector
        self.transcriber = transcriber
        self.vad = vad  # fim da fala por voz (Silero); None = limiar de energia
        self.responder = responder  # IA local: recebe a transcricao e conduz thinking -> replying -> idle
        self.speaker = speaker  # voz da Luna: "hey Luna" durante a fala a interrompe (barge_in)
        self.barge_in = barge_in
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self.wake_hold_s = wake_hold_s
        self.preroll_s = preroll_s
        self.listen_max_s = listen_max_s
        self.silence_s = silence_s
        self.no_speech_s = no_speech_s
        self.speech_rms = speech_rms
        self.done_hold_s = done_hold_s
        self.on_utterance = on_utterance
        self.on_transcript = on_transcript
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wake") if detector else None
        self._stt_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt") if transcriber else None
        self._pending = bytearray()
        self._utterance = bytearray()
        self._max_bytes = round(listen_max_s / FRAME_S) * FRAME_BYTES
        # Ultimos quadros ouvidos em espera: entram na captura para nao perder o inicio da frase
        # (a deteccao dispara 80-160 ms depois do fim da palavra; comecar 240 ms dentro da fala perde a 1a palavra).
        self._preroll: deque[bytes] = deque(maxlen=max(0, round(preroll_s / FRAME_S)))
        self.frames = 0  # quadros processados; base de tempo = frames * FRAME_S
        self.last_score = 0.0
        self.last_vad = 0.0
        self.wakes = 0
        self.transcripts = 0
        # Medicoes da ultima interacao (segundos de relogio), tambem expostas em /health
        self.last_stt_s = 0.0  # duracao da transcricao
        self.last_capture_s = 0.0  # audio capturado (inclui pre-buffer e silencio final)
        self.last_speech_to_text_s = 0.0  # do ultimo quadro com fala ate o texto pronto
        self.detect_ms = 0.0  # custo medio do detector por quadro de 80 ms
        self._last_wake_t = -1e9
        self._listen_t: float | None = None
        self._last_speech_t = 0.0
        self._speech_start_t: float | None = None
        self._last_speech_wall = 0.0
        self._finish_wall = 0.0
        self._speech_seen = False
        self._rms_window: deque[float] = deque(maxlen=max(1, round(NOISE_WINDOW_S / FRAME_S)))
        self._noise = 0.0  # piso de ruido (RMS int16) estimado em espera
        self._speech_thr = speech_rms
        self._stt_task: asyncio.Task | None = None
        self._stt_cancel: threading.Event | None = None
        self._hold_task: asyncio.Task | None = None

    @property
    def available(self) -> bool:
        return self.detector is not None

    @property
    def transcription_available(self) -> bool:
        return self.transcriber is not None

    @property
    def vad_available(self) -> bool:
        return self.vad is not None

    @property
    def noise_rms(self) -> float:
        return self._noise

    @property
    def audio_t(self) -> float:
        return self.frames * FRAME_S

    async def feed(self, pcm: bytes) -> None:
        """Recebe bytes PCM int16 LE mono 16 kHz em qualquer tamanho."""
        self._pending += pcm
        while len(self._pending) >= FRAME_BYTES:
            frame = bytes(self._pending[:FRAME_BYTES])
            del self._pending[:FRAME_BYTES]
            await self._frame(np.frombuffer(frame, dtype=np.int16))

    async def _frame(self, samples: np.ndarray) -> None:
        self.frames += 1
        now = self.audio_t
        rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
        state = self.machine.state
        # Em espera o detector procura a palavra de ativacao; enquanto a Luna fala tambem (barge-in):
        # "hey Luna" no meio da resposta interrompe o audio e abre a escuta.
        speaking = state == AssistantState.SPEAKING and self.barge_in and self.speaker is not None
        if state == AssistantState.IDLE or speaking:
            if self._listen_t is not None:
                self._reset_listen()
            self._track_noise(rms)
            self._preroll.append(samples.tobytes())
            if self.detector is None:
                return
            loop = asyncio.get_running_loop()
            t0 = time.perf_counter()
            self.last_score = await loop.run_in_executor(self._pool, self.detector.predict, samples)
            self.detect_ms = 0.9 * self.detect_ms + 0.1 * (time.perf_counter() - t0) * 1000.0
            if self.last_score >= self.threshold and now - self._last_wake_t >= self.cooldown_s:
                if self.machine.state != state:
                    return  # o estado mudou enquanto o detector rodava (fala terminou/cancelada)
                self._last_wake_t = now
                self.wakes += 1
                self.detector.reset()
                if speaking and self.speaker is not None:
                    await self.speaker.stop()
                    log.info("fala interrompida pela palavra de ativacao")
                self._begin_listen(now)
                log.info(
                    "palavra de ativacao detectada (score=%.2f, detector %.1f ms/quadro, ruido RMS %.0f, fim da fala por %s)",
                    self.last_score, self.detect_ms, self._noise, "VAD" if self.vad else f"energia >= {self._speech_thr:.0f}",
                )
                await self.machine.set(AssistantState.WAKE, "Palavra detectada")
        elif state == AssistantState.WAKE:
            if self._listen_t is None:  # acionado externamente (debug)
                self._begin_listen(now)
            self._collect(samples, rms, now)
            if now - self._listen_t >= self.wake_hold_s:
                await self.machine.set(AssistantState.LISTENING, DETAIL_LISTENING)
        elif state == AssistantState.LISTENING:
            if self._listen_t is None:
                self._begin_listen(now)
            self._collect(samples, rms, now)
            if self._should_finish(now):
                await self._finish()
        elif self._listen_t is not None:
            self._reset_listen()

    async def cancel(self) -> bool:
        """Interrompe a captura, a transcrição, a resposta da IA ou a fala em andamento e volta à espera."""
        state = self.machine.state
        if state in (AssistantState.WAKE, AssistantState.LISTENING):
            self._reset_listen()
        elif state == AssistantState.TRANSCRIBING and self._stt_task is not None:
            if self._stt_cancel is not None:
                self._stt_cancel.set()  # o whisper.cpp aborta no proximo passo e a thread termina sozinha
            self._stt_task.cancel()
            self._stt_task = None
        elif self.speaker is not None and self.speaker.busy:
            return await self.speaker.cancel()
        elif self.responder is not None and self.responder.busy:
            return await self.responder.cancel()
        else:
            return False
        log.info("interacao cancelada durante %s", state.value)
        await self._idle(DETAIL_CANCELLED)
        return True

    def _track_noise(self, rms: float) -> None:
        """Piso de ruído em espera: percentil baixo do RMS dos últimos segundos. Uma média simples subia
        junto com a fala (e com a própria palavra de ativação), triplicava o limiar e fala mais baixa
        virava "silêncio"; o percentil ignora a fala, que sempre tem pausas, e segue o ruído contínuo."""
        self._rms_window.append(rms)
        if len(self._rms_window) >= NOISE_MIN_FRAMES:
            self._noise = float(np.percentile(self._rms_window, NOISE_PERCENTILE))

    def _begin_listen(self, now: float) -> None:
        self._cancel_hold()
        self._utterance.clear()
        if self.vad is not None:
            # Estado zerado = "sem fala". Nao aquecer com o pre-buffer: ele termina em "Luna" e deixaria o
            # modelo inclinado a ver fala nos quadros seguintes (ruido logo apos a ativacao virava fala).
            self.vad.reset()
        for frame in self._preroll:
            self._utterance += frame
        self._preroll.clear()
        self._listen_t = now
        self._last_speech_t = now
        self._speech_start_t = None
        self._speech_seen = False
        self._speech_thr = min(RMS_THR_MAX, max(self.speech_rms, NOISE_FACTOR * self._noise))

    def _reset_listen(self) -> None:
        self._utterance.clear()
        self._listen_t = None
        self._speech_seen = False
        self._speech_start_t = None

    def _collect(self, samples: np.ndarray, rms: float, now: float) -> None:
        if len(self._utterance) < self._max_bytes:
            self._utterance += samples.tobytes()
        if self._is_speech(samples, rms):
            if not self._speech_seen:
                self._speech_seen = True
                self._speech_start_t = now
            self._last_speech_t = now
            self._last_speech_wall = time.perf_counter()

    def _is_speech(self, samples: np.ndarray, rms: float) -> bool:
        if self.vad is not None:
            self.last_vad = self.vad.predict(samples)
            return self.last_vad >= (VAD_CONTINUE if self._speech_seen else VAD_START)
        return rms >= self._speech_thr * (RMS_CONTINUE if self._speech_seen else 1.0)

    def _should_finish(self, now: float) -> bool:
        assert self._listen_t is not None
        elapsed = now - self._listen_t
        if elapsed >= self.listen_max_s:
            return True
        if self._speech_seen:
            return now - self._last_speech_t >= self.silence_s
        return elapsed >= self.no_speech_s

    async def _finish(self) -> None:
        pcm = bytes(self._utterance)
        heard = self._speech_seen
        listen_t = self._listen_t or 0.0
        start_t = self._speech_start_t if self._speech_start_t is not None else listen_t
        last_t = self._last_speech_t
        now = self.audio_t
        self._reset_listen()
        if not heard:
            log.info(
                "nenhuma fala apos a ativacao em %.1f s (%s)",
                now - listen_t, "VAD" if self.vad else f"energia abaixo de RMS {self._speech_thr:.0f}",
            )
            await self._idle()
            return
        self.last_capture_s = len(pcm) / (2 * SAMPLE_RATE)
        self._finish_wall = time.perf_counter()
        log.info(
            "fala capturada: %.1f s (fala de %.1f s comecou %.2f s apos a ativacao; fim por %s de %.1f s)",
            self.last_capture_s,
            last_t - start_t + FRAME_S,
            start_t - listen_t,
            "limite" if now - listen_t >= self.listen_max_s else "silencio",
            now - last_t,
        )
        if self.on_utterance is not None:
            await self.on_utterance(pcm)
        if self.transcriber is None:
            await self._idle()
            return
        await self.machine.set(AssistantState.TRANSCRIBING, DETAIL_TRANSCRIBING)
        self._stt_cancel = threading.Event()
        self._stt_task = asyncio.create_task(self._transcribe(pcm, self._stt_cancel))

    async def _transcribe(self, pcm: bytes, cancel: threading.Event) -> None:
        assert self.transcriber is not None
        loop = asyncio.get_running_loop()
        t0 = time.perf_counter()
        try:
            text = await loop.run_in_executor(self._stt_pool, self.transcriber.transcribe, pcm, cancel)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("falha na transcricao")
            self._stt_task = None
            await self._idle(DETAIL_FAILED)
            return
        t1 = time.perf_counter()
        self.last_stt_s = t1 - t0
        self._stt_task = None
        if cancel.is_set():
            return
        # Tempo total percebido: do ultimo quadro com fala ate o texto pronto = espera pelo silencio
        # (relogio; maior que silence_s indica atraso de rede) + fila ate a thread comecar + transcricao.
        self.last_speech_to_text_s = t1 - self._last_speech_wall
        wait_s = self._finish_wall - self._last_speech_wall
        queue_s = t0 - self._finish_wall
        if text:
            self.transcripts += 1
            log.info(  # o texto nao vai ao log
                "transcricao em %.2f s: %d caracteres (audio %.1f s); fim da fala -> texto em %.2f s (silencio %.2f + fila %.2f + transcricao %.2f)",
                self.last_stt_s, len(text), len(pcm) / (2 * SAMPLE_RATE), self.last_speech_to_text_s, wait_s, queue_s, self.last_stt_s,
            )
            if self.on_transcript is not None:
                await self.on_transcript(text)
            # Com IA configurada, a transcricao segue para o modelo (uma unica vez por fala:
            # o numero da transcricao identifica a pergunta). Sem IA, volta a espera como antes.
            if self.responder is not None and await self.responder.ask(text, prompt_id=self.transcripts):
                return
            await self._idle(DETAIL_DONE)
        else:
            log.info("transcricao em %.2f s: nada reconhecido (audio %.1f s)", self.last_stt_s, len(pcm) / (2 * SAMPLE_RATE))
            await self._idle(DETAIL_NOTHING)

    async def _idle(self, detail: str = "") -> None:
        """Volta à espera. Um detalhe transitório ("Transcrição concluída") some após done_hold_s."""
        if self.machine.state not in (
            AssistantState.WAKE,
            AssistantState.LISTENING,
            AssistantState.TRANSCRIBING,
            AssistantState.IDLE,
        ):
            return
        self._cancel_hold()
        await self.machine.set(AssistantState.IDLE, detail)
        if detail:
            self._hold_task = asyncio.create_task(self._clear_detail(detail))

    async def _clear_detail(self, detail: str) -> None:
        await asyncio.sleep(self.done_hold_s)
        if self.machine.state == AssistantState.IDLE and self.machine.detail == detail:
            await self.machine.set(AssistantState.IDLE, "")

    def _cancel_hold(self) -> None:
        if self._hold_task is not None:
            self._hold_task.cancel()
            self._hold_task = None
