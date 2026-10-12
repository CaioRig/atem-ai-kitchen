"""Mensagens do WebSocket. Validação de entrada com pydantic (já vem com FastAPI)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from server.assistant.state import AssistantState

PROTOCOL_VERSION = 1


class Hello(BaseModel):
    type: Literal["hello"]
    token: str = Field(min_length=1, max_length=256)
    client: Literal["ui", "audio", "debug"] = "ui"
    version: int = PROTOCOL_VERSION


class Ping(BaseModel):
    type: Literal["ping"]
    t: float | None = None


class SetState(BaseModel):
    type: Literal["set_state"]
    state: AssistantState
    detail: str = Field(default="", max_length=200)


class Cancel(BaseModel):
    """Interrompe a captura ou a transcrição em andamento. Sempre permitido."""

    type: Literal["cancel"]


class Retry(BaseModel):
    """Reenvia à IA a última transcrição cuja resposta falhou. Ignorado se não há o que repetir."""

    type: Literal["retry"]


class SpeakDone(BaseModel):
    """O cliente terminou de reproduzir a fala `id` (ou não conseguiu: `played: false` + `error`)."""

    type: Literal["speak_done"]
    id: int
    played: bool = True
    error: str = Field(default="", max_length=300)


class Replay(BaseModel):
    """Fala de novo a última resposta (após uma falha de voz ou a pedido). Só em espera."""

    type: Literal["replay"]


class Say(BaseModel):
    """Mostra e fala um texto sem passar pela IA. **Somente** com debug_allow_state_override (teste da voz)."""

    type: Literal["say"]
    text: str = Field(min_length=1, max_length=2000)


class MemoryList(BaseModel):
    """Pede a lista de memórias guardadas; a resposta é um `memory` com `action: "list"` só para quem pediu."""

    type: Literal["memory_list"]


class MemoryDelete(BaseModel):
    """Apaga a memória `id` (botão "Esquecer" na tela). Todos os clientes recebem `memory` com `action: "deleted"`."""

    type: Literal["memory_delete"]
    id: int = Field(ge=1)


class MemoryClear(BaseModel):
    """Apaga todas as memórias. Só executa com `confirm: true` (a interface pede confirmação antes)."""

    type: Literal["memory_clear"]
    confirm: bool = False


Inbound = Ping | SetState | Cancel | Retry | SpeakDone | Replay | Say | MemoryList | MemoryDelete | MemoryClear

_PARSERS: dict[str, type[BaseModel]] = {
    "ping": Ping,
    "set_state": SetState,
    "cancel": Cancel,
    "retry": Retry,
    "speak_done": SpeakDone,
    "replay": Replay,
    "say": Say,
    "memory_list": MemoryList,
    "memory_delete": MemoryDelete,
    "memory_clear": MemoryClear,
}


def parse_inbound(data: dict) -> Inbound:
    kind = data.get("type")
    model = _PARSERS.get(kind) if isinstance(kind, str) else None
    if model is None:
        expected = ", ".join(f"'{k}'" for k in _PARSERS)
        raise ValidationError.from_exception_data("Inbound", [{"type": "literal_error", "loc": ("type",), "input": kind, "ctx": {"expected": expected}}])
    return model.model_validate(data)  # type: ignore[return-value]


def error(code: str, message: str) -> dict:
    return {"type": "error", "code": code, "message": message}
