"""Conversa com a IA local: transcrição -> Ollama -> resposta na interface.

Cada pergunta é independente (sem histórico de conversa). A memória persistente (Fase 6) entra de
duas formas: comandos como "lembre que..." são resolvidos aqui mesmo, sem a IA, por
`MemoryCommands`; perguntas comuns recebem as memórias relevantes como contexto. A Fase 7 acrescenta,
pelo mesmo caminho, os comandos de ingredientes disponíveis ("tenho três ovos", "usei dois ovos") e,
nas perguntas sobre receitas, a orientação de receitas, o estoque e a última receita como contexto
(`RecipeAssistant`), além de falar só um resumo quando a resposta é longa. A chamada HTTP roda em uma
thread; o loop asyncio continua livre para o áudio e o WebSocket. Trechos da resposta são repassados
conforme chegam (`on_reply(texto, final=False)`) e o texto completo no fim.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Awaitable, Callable

from server.assistant.memory import MemoryCommands, MemoryOutcome
from server.assistant.recipes import RecipeAssistant
from server.assistant.speech import Speech
from server.assistant.state import AssistantState, StateMachine
from server.services.llm import LanguageModel, LLMError

log = logging.getLogger("jarvis.conversation")

ReplyHandler = Callable[[str, bool], Awaitable[None]]  # (texto, final)
ReplyErrorHandler = Callable[[str, str], Awaitable[None]]  # (codigo, mensagem amigavel)
MemoryEventHandler = Callable[[dict], Awaitable[None]]  # evento `memory` para a interface

DETAIL_THINKING = "Pensando…"
DETAIL_REPLYING = "Respondendo…"
DETAIL_READY = "Pronta"
DETAIL_CANCELLED = "Cancelado"
DETAIL_FAILED = "Não consegui responder"
PARTIAL_INTERVAL_S = 0.12  # intervalo mínimo entre envios parciais à interface
ACTIVE_STATES = (AssistantState.THINKING, AssistantState.REPLYING)


class Conversation:
    def __init__(
        self,
        machine: StateMachine,
        llm: LanguageModel | None,
        *,
        on_reply: ReplyHandler | None = None,
        on_error: ReplyErrorHandler | None = None,
        done_hold_s: float = 4.0,
        speaker: Speech | None = None,
        memory: MemoryCommands | None = None,
        on_memory: MemoryEventHandler | None = None,
        recipes: RecipeAssistant | None = None,
    ) -> None:
        self.machine = machine
        self.llm = llm
        self.on_reply = on_reply
        self.on_error = on_error
        self.done_hold_s = done_hold_s
        self.speaker = speaker  # voz: fala a resposta completa (synthesizing -> speaking -> idle)
        self.memory = memory  # memoria persistente: comandos por voz e contexto para a IA
        self.on_memory = on_memory
        self.recipes = recipes  # receitas e ingredientes: comandos de estoque, contexto e fala resumida
        self.memory_replies = 0
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="llm") if llm else None
        self._task: asyncio.Task | None = None
        self._cancel: threading.Event | None = None
        self._hold_task: asyncio.Task | None = None
        self._last_prompt_id: int | None = None
        self._retry_text: str | None = None
        self.replies = 0
        self.failures = 0
        self.last_reply_s = 0.0
        self.last_error: tuple[str, str] | None = None  # (codigo, mensagem) da ultima falha

    @property
    def available(self) -> bool:
        return self.llm is not None or self.memory is not None or self.recipes is not None

    @property
    def busy(self) -> bool:
        return self._task is not None

    @property
    def can_retry(self) -> bool:
        return self._retry_text is not None and not self.busy

    async def ask(self, text: str, prompt_id: int | None = None) -> bool:
        """Envia o texto ao modelo. Ignora texto vazio, a mesma transcrição repetida (`prompt_id`)
        e pedidos enquanto outra resposta está em andamento. Retorna True se iniciou."""
        text = " ".join(text.split())
        if not self.available or not text:
            return False
        if prompt_id is not None and prompt_id == self._last_prompt_id:
            log.info("transcricao %d ja processada; ignorada", prompt_id)
            return False
        if self._task is not None:
            log.warning("resposta em andamento; nova pergunta ignorada")
            return False
        if prompt_id is not None:
            self._last_prompt_id = prompt_id
        # Comandos locais, sem a IA: a confirmacao pendente de "apague tudo" tem prioridade; depois os
        # comandos de ingredientes ("tenho tres ovos", "usei dois ovos") e os de memoria ("lembre que...").
        outcome = self._local_outcome(text)
        if outcome is not None:
            self._retry_text = None
            self._cancel_hold()
            self._cancel = None
            self._task = asyncio.create_task(self._run_local(outcome))
            await asyncio.sleep(0)
            return True
        if self.llm is None:
            return False
        self._retry_text = None
        self._cancel_hold()
        # A tarefa e registrada antes de qualquer await: um `cancel` que chegue logo em seguida
        # ja a encontra. Ela propria muda o estado para "Pensando…" no primeiro passo.
        self._cancel = threading.Event()
        self._task = asyncio.create_task(self._run(text, self._cancel))
        await asyncio.sleep(0)  # deixa a tarefa dar o primeiro passo (estado THINKING)
        return True

    async def retry(self) -> bool:
        """Reenvia a última pergunta que falhou. Só em espera e só uma vez por falha."""
        if not self.can_retry or self.machine.state != AssistantState.IDLE:
            return False
        text = self._retry_text or ""
        log.info("nova tentativa de resposta")
        return await self.ask(text)

    def _local_outcome(self, text: str) -> MemoryOutcome | None:
        outcome: MemoryOutcome | None = None
        if self.memory is not None and self.memory.awaiting_confirmation:
            outcome = self.memory.handle(text)  # "sim"/"nao"; qualquer outra frase cancela o pedido
        if outcome is None and self.recipes is not None:
            outcome = self.recipes.handle(text)
        if outcome is None and self.memory is not None:
            outcome = self.memory.handle(text)
        return outcome

    async def cancel(self) -> bool:
        if self._task is None:
            return False
        if self._cancel is not None:
            self._cancel.set()  # a thread fecha a conexao e o Ollama interrompe a geracao
        self._task.cancel()
        self._task = None
        if self.memory is not None:
            self.memory.cancel_confirmation()  # "Cancelar" tambem desiste de apagar tudo
        log.info("resposta cancelada")
        await self._idle(DETAIL_CANCELLED)
        return True

    async def _run_local(self, outcome: MemoryOutcome) -> None:
        """Resposta de um comando de memória: mesmo ciclo visual de uma pergunta, sem chamar a IA."""
        try:
            await self.machine.set(AssistantState.THINKING, DETAIL_THINKING)
            self.memory_replies += 1
            log.info("comando de memoria (%s) respondido", outcome.intent)  # o texto nao vai ao log
            await self._emit(outcome.reply, final=True)
            if outcome.event is not None and self.on_memory is not None:
                await self.on_memory(outcome.event)
            if self.speaker is not None and await self.speaker.speak(outcome.reply):
                return
            await self._idle(DETAIL_READY)
        finally:
            if self._task is asyncio.current_task():
                self._task = None

    async def _run(self, text: str, cancel: threading.Event) -> None:
        """Ciclo completo de uma pergunta; `busy` fica verdadeiro até o estado final ser notificado."""
        try:
            await self._work(text, cancel)
        finally:
            if self._task is asyncio.current_task():
                self._task = None

    async def _work(self, text: str, cancel: threading.Event) -> None:
        assert self.llm is not None
        await self.machine.set(AssistantState.THINKING, DETAIL_THINKING)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        def on_delta(piece: str) -> None:  # thread do cliente HTTP
            loop.call_soon_threadsafe(queue.put_nowait, piece)

        # So as memorias relevantes para esta pergunta (e as instrucoes de resposta) vao como contexto;
        # em perguntas sobre receitas, tambem a orientacao de receitas, o estoque e a ultima receita.
        parts = [self.memory.context_for(text) if self.memory is not None else ""]
        limit: int | None = None
        if self.recipes is not None:
            parts.append(self.recipes.context_for(text))
            limit = self.recipes.max_tokens_for(text)
        context = "\n\n".join(p for p in parts if p)

        def worker() -> str:
            try:
                return self.llm.reply(text, on_delta, cancel, context=context, max_tokens=limit)  # type: ignore[union-attr]
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        t0 = time.perf_counter()
        fut = loop.run_in_executor(self._pool, worker)
        fut.add_done_callback(_consume_exception)
        pieces: list[str] = []
        last_flush = 0.0
        try:
            while True:
                piece = await queue.get()
                if piece is None:
                    break
                if not pieces:
                    await self.machine.set(AssistantState.REPLYING, DETAIL_REPLYING)
                pieces.append(piece)
                now = loop.time()
                if now - last_flush >= PARTIAL_INTERVAL_S:
                    last_flush = now
                    await self._emit("".join(pieces), final=False)
            reply = await fut
        except asyncio.CancelledError:
            raise
        except LLMError as exc:
            await self._fail(text, exc.code, exc.message, exc.detail, cancel)
            return
        except Exception as exc:  # erro de programacao ou biblioteca: nao derruba o servidor
            log.exception("falha inesperada ao gerar a resposta")
            await self._fail(text, "internal", "Algo deu errado ao gerar a resposta.", type(exc).__name__, cancel)
            return
        self.last_reply_s = time.perf_counter() - t0
        if cancel.is_set():
            return
        self.replies += 1
        self.last_error = None
        log.info("resposta em %.2f s: %d caracteres", self.last_reply_s, len(reply))  # o texto nao vai ao log
        await self._emit(reply, final=True)
        # Com voz, a resposta e falada e a propria fala volta a espera ("Pronta") ao terminar. Receitas
        # longas sao faladas em resumo (abertura + aviso); o texto completo ja esta na tela.
        spoken = reply
        if self.recipes is not None:
            spoken = self.recipes.spoken_text(reply, text)
            self.recipes.remember_reply(text, reply)
        if self.speaker is not None and await self.speaker.speak(spoken):
            return
        await self._idle(DETAIL_READY)

    async def _fail(self, text: str, code: str, message: str, detail: str, cancel: threading.Event) -> None:
        if cancel.is_set():
            return
        self.failures += 1
        self.last_error = (code, message)
        self._retry_text = text
        log.warning("falha na resposta (%s): %s", code, detail or message)
        if self.on_error is not None:
            await self.on_error(code, message)
        await self._idle(DETAIL_FAILED)

    async def _emit(self, text: str, final: bool) -> None:
        if self.on_reply is not None:
            await self.on_reply(text, final)

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
