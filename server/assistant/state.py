"""Estados do assistente e notificação de mudanças."""
from __future__ import annotations

import asyncio
import time
from enum import StrEnum
from typing import Awaitable, Callable


class AssistantState(StrEnum):
    BOOTING = "booting"
    IDLE = "idle"
    WAKE = "wake"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    REPLYING = "replying"  # resposta da IA chegando (texto); a fala em voz fica em SPEAKING
    SYNTHESIZING = "synthesizing"
    SPEAKING = "speaking"
    MEDIA = "media"
    OFFLINE = "offline"
    ERROR = "error"


Listener = Callable[[AssistantState, str], Awaitable[None]]


class StateMachine:
    """Guarda o estado atual e avisa ouvintes. Um único assistente por servidor."""

    def __init__(self, initial: AssistantState = AssistantState.BOOTING) -> None:
        self.state = initial
        self.detail = ""
        self.changed_at = time.time()
        self._listeners: list[Listener] = []

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    async def set(self, state: AssistantState, detail: str = "") -> None:
        if state == self.state and detail == self.detail:
            return
        self.state = state
        self.detail = detail
        self.changed_at = time.time()
        if self._listeners:
            await asyncio.gather(*(l(state, detail) for l in self._listeners))

    def snapshot(self) -> dict:
        return {"state": self.state.value, "detail": self.detail, "ts": self.changed_at}
