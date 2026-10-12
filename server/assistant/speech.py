"""Fala da Luna: texto da resposta -> síntese local (Kokoro/Piper) -> áudio PCM para o quiosque.

A síntese roda em uma thread, frase a frase; cada trecho pronto é enviado na hora, então a fala
começa antes de toda a resposta estar sintetizada. O áudio fica só em memória. A reprodução acontece
no navegador da cozinha, que avisa (`speak_done`) quando terminou; se o aviso não vier, um prazo
baseado na duração do áudio devolve o assistente à espera. Nunca há duas falas ao mesmo tempo: uma
nova fala (ou "hey Luna", ou Cancelar) interrompe a anterior e manda o cliente parar o áudio.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Awaitable, Callable

from server.assistant.state import AssistantState, StateMachine
from server.services.tts import Synthesizer, TtsError, prepare_text

log = logging.getLogger("jarvis.speech")

StartHandler = Callable[[int, int], Awaitable[None]]  # (id da fala, taxa de amostragem)
AudioHandler = Callable[[int, bytes], Awaitable[None]]  # (id, PCM int16 LE mono)
EndHandler = Callable[[int, int], Awaitable[None]]  # (id, total de amostras)
StopHandler = Callable[[int], Awaitable[None]]  # (id): parar a reproducao
SpeechErrorHandler = Callable[[str, str], Awaitable[None]]  # (codigo, mensagem amigavel)

DETAIL_SYNTHESIZING = "Preparando a voz…"
DETAIL_SPEAKING = "Falando"
DETAIL_READY = "Pronta"
DETAIL_CANCELLED = "Cancelado"
DETAIL_FAILED = "Não consegui falar"
ACTIVE_STATES = (AssistantState.SYNTHESIZING, AssistantState.SPEAKING)
PLAYBACK_MARGIN_S = 3.0  # tolerancia alem da duracao do audio para o cliente confirmar o fim


class Speech:
    def __init__(
        self,
        machine: StateMachine,
        synthesizer: Synthesizer | None,
        *,
        on_start: StartHandler | None = None,
        on_audio: AudioHandler | None = None,
        on_end: EndHandler | None = None,
        on_stop: StopHandler | None = None,
        on_error: SpeechErrorHandler | None = None,
        done_hold_s: float = 4.0,
        max_chars: int = 1500,
        playback_margin_s: float = PLAYBACK_MARGIN_S,
    ) -> None:
        self.machine = machine
        self.synthesizer = synthesizer
        self.on_start = on_start
        self.on_audio = on_audio
        self.on_end = on_end
        self.on_stop = on_stop
        self.on_error = on_error
        self.done_hold_s = done_hold_s
        self.max_chars = max_chars
        self.playback_margin_s = playback_margin_s
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts") if synthesizer else None
        self._task: asyncio.Task | None = None
        self._cancel: threading.Event | None = None
        self._hold_task: asyncio.Task | None = None
        self._done: asyncio.Future | None = None  # confirmacao do cliente: (reproduziu?, erro)
        self._utterance_id = 0
        self._last_text: str | None = None
        self.spoken = 0
        self.failures = 0
        self.last_synthesis_s = 0.0  # do texto pronto ate o primeiro trecho de audio
        self.last_audio_s = 0.0  # duracao do audio da ultima fala
        self.last_error: tuple[str, str] | None = None

    @property
    def available(self) -> bool:
        return self.synthesizer is not None

    @property
    def busy(self) -> bool:
        return self._task is not None

    @property
    def can_replay(self) -> bool:
        return self._last_text is not None and not self.busy

    @property
    def utterance_id(self) -> int:
        return self._utterance_id

    async def warmup(self) -> None:
        """Primeira inferência em segundo plano, para a primeira resposta não pagar por ela."""
        if self.synthesizer is None:
            return
        loop = asyncio.get_running_loop()
        t0 = time.perf_counter()
        try:
            await loop.run_in_executor(self._pool, self.synthesizer.warmup)
            log.info("voz aquecida em %.1f s", time.perf_counter() - t0)
        except Exception as exc:  # nunca derruba o servidor
            log.warning("falha ao aquecer a voz: %s", exc)

    async def speak(self, text: str) -> bool:
        """Fala `text`. Interrompe uma fala anterior. Retorna False se não há voz ou nada a falar."""
        if self.synthesizer is None:
            return False
        text = prepare_text(text, self.max_chars)
        if not text:
            return False
        if self._task is not None:
            await self.stop()  # nunca duas respostas ao mesmo tempo
        self._last_text = text
        self._cancel_hold()
        self._utterance_id += 1
        self._cancel = threading.Event()
        self._task = asyncio.create_task(self._run(self._utterance_id, text, self._cancel))
        await asyncio.sleep(0)  # a tarefa muda o estado para "Preparando a voz…" no primeiro passo
        return True

    async def replay(self) -> bool:
        """Fala de novo a última resposta (após uma falha, ou a pedido). Só em espera."""
        if not self.can_replay or self.machine.state != AssistantState.IDLE:
            return False
        log.info("nova tentativa de fala")
        return await self.speak(self._last_text or "")

    async def stop(self) -> bool:
        """Interrompe síntese e reprodução sem mudar o estado (quem chama decide o próximo)."""
        if self._task is None:
            return False
        uid = self._utterance_id
        if self._cancel is not None:
            self._cancel.set()  # a thread para na proxima frase
        self._task.cancel()
        self._task = None
        self._settle_done()
        if self.on_stop is not None:
            await self.on_stop(uid)
        return True

    async def cancel(self) -> bool:
        if not await self.stop():
            return False
        log.info("fala cancelada")
        await self._idle(DETAIL_CANCELLED)
        return True

    def playback_done(self, utterance_id: int, played: bool = True, error: str = "") -> bool:
        """O cliente terminou (ou não conseguiu) reproduzir a fala `utterance_id`."""
        if utterance_id != self._utterance_id or self._done is None or self._done.done():
            return False
        self._done.set_result((played, error))
        return True

    async def _run(self, uid: int, text: str, cancel: threading.Event) -> None:
        try:
            await self._work(uid, text, cancel)
        finally:
            if self._task is asyncio.current_task():
                self._task = None

    async def _work(self, uid: int, text: str, cancel: threading.Event) -> None:
        assert self.synthesizer is not None
        await self.machine.set(AssistantState.SYNTHESIZING, DETAIL_SYNTHESIZING)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        synthesizer = self.synthesizer

        def worker() -> None:  # thread "tts"
            try:
                for pcm in synthesizer.synthesize(text, cancel):
                    if cancel.is_set():
                        return
                    loop.call_soon_threadsafe(queue.put_nowait, pcm)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        t0 = time.perf_counter()
        self._done = loop.create_future()
        fut = loop.run_in_executor(self._pool, worker)
        fut.add_done_callback(_consume_exception)
        rate = synthesizer.sample_rate
        samples = 0
        started = False
        try:
            while True:
                pcm = await queue.get()
                if pcm is None:
                    break
                if not started:
                    started = True
                    self.last_synthesis_s = time.perf_counter() - t0
                    if self.on_start is not None:
                        await self.on_start(uid, rate)
                    await self.machine.set(AssistantState.SPEAKING, DETAIL_SPEAKING)
                samples += len(pcm) // 2
                if self.on_audio is not None:
                    await self.on_audio(uid, pcm)
            await fut  # propaga a excecao da sintese, se houver
        except asyncio.CancelledError:
            raise
        except TtsError as exc:
            await self._fail(uid, started, exc.code, exc.message, exc.detail, cancel)
            return
        except Exception as exc:  # erro de programacao ou biblioteca: nao derruba o servidor
            log.exception("falha inesperada ao gerar a voz")
            await self._fail(uid, started, "internal", "Algo deu errado ao gerar a voz.", type(exc).__name__, cancel)
            return
        if cancel.is_set():
            return
        if not samples:
            await self._fail(uid, started, "empty", "Não consegui gerar o áudio desta resposta.", "", cancel)
            return
        self.last_audio_s = samples / rate
        log.info(
            "voz: primeiro trecho em %.2f s, %.1f s de audio sintetizados em %.2f s (%d caracteres)",
            self.last_synthesis_s, self.last_audio_s, time.perf_counter() - t0, len(text),
        )
        if self.on_end is not None:
            await self.on_end(uid, samples)
        # Aguarda o quiosque terminar de reproduzir. Sem confirmacao (cliente antigo ou desconectado),
        # volta a espera quando o audio ja teria acabado.
        try:
            played, error = await asyncio.wait_for(self._done, timeout=self.last_audio_s + self.playback_margin_s)
        except asyncio.TimeoutError:
            log.warning("sem confirmacao da reproducao apos %.1f s; voltando a espera", self.last_audio_s + self.playback_margin_s)
            played, error = True, ""
        if cancel.is_set():
            return
        if not played:
            await self._fail(uid, False, "playback", error or "O computador da cozinha não conseguiu reproduzir o áudio.", "", cancel)
            return
        self.spoken += 1
        self.last_error = None
        await self._idle(DETAIL_READY)

    async def _fail(self, uid: int, started: bool, code: str, message: str, detail: str, cancel: threading.Event) -> None:
        if cancel.is_set():
            return
        cancel.set()  # a thread de sintese, se ainda estiver rodando, para na proxima frase
        self.failures += 1
        self.last_error = (code, message)
        log.warning("falha na voz (%s): %s", code, detail or message)
        if started and self.on_stop is not None:
            await self.on_stop(uid)  # o cliente descarta o audio parcial
        if self.on_error is not None:
            await self.on_error(code, message)
        await self._idle(DETAIL_FAILED)

    def _settle_done(self) -> None:
        if self._done is not None and not self._done.done():
            self._done.cancel()
        self._done = None

    async def _idle(self, detail: str = "") -> None:
        """Volta à espera com um detalhe transitório ("Pronta"), que some após done_hold_s."""
        if self.machine.state not in (*ACTIVE_STATES, AssistantState.IDLE):
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


def _consume_exception(fut: Future) -> None:
    """Marca a exceção como vista quando a tarefa foi cancelada antes de aguardar o resultado."""
    if not fut.cancelled():
        fut.exception()
