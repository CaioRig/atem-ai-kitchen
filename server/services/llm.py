"""Modelo de linguagem local via Ollama (HTTP, rede local). Nenhuma inferência sai da LAN.

Usa só a biblioteca padrão (http.client), para não adicionar dependências. A geração é lida
em fluxo (NDJSON) para permitir cancelamento no meio e exibição progressiva da resposta.
Sem chaves nem credenciais: o Ollama não tem autenticação e deve escutar apenas na rede local.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import logging
import re
import socket
import threading
from typing import Callable, Protocol
from urllib.parse import urlsplit

log = logging.getLogger("jarvis.llm")

DeltaHandler = Callable[[str], None]
DEFAULT_PORT = 11434
MAX_PROMPT_CHARS = 2000
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)
_LOCAL_SUFFIXES = (".local", ".lan", ".home", ".internal", ".localdomain")

# Instrução de sistema da Luna. Pode ser sobrescrita em config/settings.toml ([llm] system_prompt).
LUNA_SYSTEM_PROMPT = """Você é a Luna, assistente virtual da cozinha de uma casa em Ouro Fino, Minas Gerais.

Personalidade: acolhedora, prática e objetiva. Fala sempre em português do Brasil, em tom natural e simpático, como alguém da família que entende de cozinha.

Suas respostas são curtas por padrão, de uma a três frases, porque serão lidas em uma tela na cozinha e, mais tarde, faladas em voz alta. Só se alongue quando a pessoa pedir detalhes, como o passo a passo de uma receita; nesse caso use frases simples e numere os passos.

Foco: receitas e técnicas de cozinha, substituições de ingredientes, medidas e conversões, conservação de alimentos, organização e limpeza da cozinha, planejamento de refeições, conversa leve e informações úteis do dia a dia.

Escreva texto corrido, sem Markdown, sem emojis, sem listas com marcadores nem símbolos especiais. Se não souber algo ou não tiver acesso à informação, como a hora atual, a previsão do tempo ou notícias, diga isso com naturalidade e sugira uma alternativa. Nunca invente dados de segurança alimentar; na dúvida, recomende cautela.

O texto que você recebe vem de reconhecimento de voz e pode conter pequenos erros: interprete a intenção com bom senso e, se estiver ambíguo, faça uma pergunta curta."""


class LLMError(RuntimeError):
    """Falha ao obter resposta. `code` identifica o tipo; `message` é amigável, em pt-BR."""

    def __init__(self, code: str, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail  # informação técnica só para o log


class LanguageModel(Protocol):
    name: str

    def reply(
        self, text: str, on_delta: DeltaHandler | None = None, cancel: threading.Event | None = None, context: str = ""
    ) -> str:
        """Texto do usuário -> resposta completa. `on_delta` recebe trechos conforme chegam
        (chamado na thread do cliente HTTP). `context` é informação adicional (p.ex. memórias
        relevantes) anexada à instrução de sistema, nunca à fala da pessoa. Retorna "" se cancelado;
        levanta LLMError em falhas."""
        ...

    def check(self) -> str:
        """Verifica servidor e modelo. Retorna "" se tudo está pronto ou o motivo do problema."""
        ...


def local_url_problem(url: str) -> str:
    """Explica por que `url` não serve para o Ollama (vazio = aceitável). Só aceita endereços da
    rede local: loopback, IPs privados ou nomes de máquina sem domínio público."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return f"use http:// ou https:// (recebido: {url!r})"
    host = parts.hostname
    if not host:
        return f"endereço sem host: {url!r}"
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return "informe apenas esquema, host e porta (ex.: http://192.168.1.6:11434)"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        name = host.lower()
        if name == "localhost" or "." not in name or name.endswith(_LOCAL_SUFFIXES):
            return ""
        return f"{host} parece um endereço fora da rede local; a IA deve rodar na LAN"
    if ip.is_loopback or ip.is_private or ip.is_link_local:
        return ""
    return f"{host} não é um IP da rede local; a IA deve rodar na LAN"


class OllamaClient:
    """Cliente mínimo da API /api/chat do Ollama, sem estado entre perguntas."""

    def __init__(
        self,
        url: str,
        model: str,
        *,
        system_prompt: str = LUNA_SYSTEM_PROMPT,
        connect_timeout_s: float = 5.0,
        timeout_s: float = 60.0,
        keep_alive: str = "10m",
        temperature: float = 0.7,
        max_tokens: int = 300,
    ) -> None:
        problem = local_url_problem(url)
        if problem:
            raise ValueError(problem)
        parts = urlsplit(url)
        self.secure = parts.scheme == "https"
        self.host = parts.hostname or "127.0.0.1"
        self.port = parts.port or (443 if self.secure else DEFAULT_PORT)
        self.name = model
        self.system_prompt = system_prompt or LUNA_SYSTEM_PROMPT
        self.connect_timeout_s = connect_timeout_s
        self.timeout_s = timeout_s
        self.keep_alive = keep_alive
        self.temperature = temperature
        self.max_tokens = max_tokens

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    def _connect(self) -> http.client.HTTPConnection:
        cls = http.client.HTTPSConnection if self.secure else http.client.HTTPConnection
        conn = cls(self.host, self.port, timeout=self.connect_timeout_s)
        try:
            conn.connect()
        except socket.timeout as exc:
            raise LLMError("unreachable", self._unreachable_msg(), f"timeout ao conectar: {exc}") from exc
        except OSError as exc:
            raise LLMError("unreachable", self._unreachable_msg(), f"{type(exc).__name__}: {exc}") from exc
        assert conn.sock is not None
        conn.sock.settimeout(self.timeout_s)  # geração pode demorar mais que a conexão
        return conn

    def _unreachable_msg(self) -> str:
        return f"Não consegui falar com a IA em {self.address}. O Ollama está ligado?"

    def check(self) -> str:
        try:
            conn = self._connect()
        except LLMError as exc:
            return exc.message
        try:
            conn.request("GET", "/api/tags")
            resp = conn.getresponse()
            body = resp.read(1_000_000)
            if resp.status != 200:
                return f"O Ollama em {self.address} respondeu {resp.status} ao listar modelos."
            names = {m.get("name", "") for m in json.loads(body).get("models", [])}
        except (OSError, ValueError, http.client.HTTPException) as exc:
            return f"Falha ao consultar o Ollama em {self.address}: {type(exc).__name__}"
        finally:
            conn.close()
        if self.name in names or f"{self.name}:latest" in names:
            return ""
        return self._missing_model_msg()

    def _missing_model_msg(self) -> str:
        return f"O modelo '{self.name}' não está instalado no Ollama. No servidor, execute: ollama pull {self.name}"

    def reply(
        self, text: str, on_delta: DeltaHandler | None = None, cancel: threading.Event | None = None, context: str = ""
    ) -> str:
        text = text.strip()[:MAX_PROMPT_CHARS]
        if not text:
            raise LLMError("empty_prompt", "Não entendi nada para responder.")
        system = self.system_prompt
        if context.strip():
            system = f"{system}\n\n{context.strip()}"  # memórias entram como contexto, separadas das regras
        payload = {
            "model": self.name,
            "stream": True,
            "keep_alive": self.keep_alive,
            "options": {"temperature": self.temperature, "num_predict": self.max_tokens},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
        }
        if cancel is not None and cancel.is_set():
            return ""
        conn = self._connect()
        pieces: list[str] = []
        try:
            try:
                conn.request(
                    "POST", "/api/chat", body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                resp = conn.getresponse()
            except socket.timeout as exc:
                raise LLMError("timeout", "A IA demorou demais para responder. Tente de novo.", str(exc)) from exc
            except (OSError, http.client.HTTPException) as exc:
                raise LLMError("unreachable", self._unreachable_msg(), f"{type(exc).__name__}: {exc}") from exc
            if resp.status != 200:
                raise self._http_error(resp.status, resp.read(65536))
            while True:
                if cancel is not None and cancel.is_set():
                    return ""  # fechar a conexão faz o Ollama interromper a geração
                try:
                    line = resp.readline()
                except socket.timeout as exc:
                    raise LLMError("timeout", "A IA demorou demais para responder. Tente de novo.", str(exc)) from exc
                except (OSError, http.client.HTTPException) as exc:
                    raise LLMError("unreachable", "A conexão com a IA caiu no meio da resposta.", str(exc)) from exc
                if not line:
                    break
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except ValueError as exc:
                    raise LLMError("bad_response", "Resposta inesperada do Ollama.", line[:200].decode("utf-8", "replace")) from exc
                if obj.get("error"):
                    raise LLMError("server", "O Ollama devolveu um erro ao gerar a resposta.", str(obj["error"])[:300])
                piece = (obj.get("message") or {}).get("content") or ""
                if piece:
                    pieces.append(piece)
                    if on_delta is not None:
                        on_delta(piece)
                if obj.get("done"):
                    break
        finally:
            conn.close()
        if cancel is not None and cancel.is_set():
            return ""
        reply = clean_reply("".join(pieces))
        if not reply:
            raise LLMError("empty", "A IA não devolveu nenhuma resposta. Tente perguntar de novo.")
        return reply

    def _http_error(self, status: int, body: bytes) -> LLMError:
        detail = body[:300].decode("utf-8", "replace")
        if status == 404:
            return LLMError("model_missing", self._missing_model_msg(), detail)
        return LLMError("server", f"O Ollama respondeu com erro ({status}). Tente de novo.", detail)


def clean_reply(text: str) -> str:
    """Remove blocos de raciocínio e espaços sobrando; mantém quebras de parágrafo simples."""
    text = _THINK_BLOCK.sub("", text)
    lines = [" ".join(line.split()) for line in text.strip().splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()
