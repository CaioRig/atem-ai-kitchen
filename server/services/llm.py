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
import time
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

Responda diretamente ao que a pessoa perguntou, inclusive perguntas gerais e contas. Não diga que a pergunta é desnecessária nem desvie toda resposta para a cozinha. Evite convites repetidos para ajudar; quando houver uma instrução de formato exato, siga-a.

Não tenho acesso aos pensamentos, senhas ou informações privadas não fornecidas pela pessoa. Quando perguntarem por algo assim, diga claramente que não sabe e não pode adivinhar. Não peça senhas nem pistas para descobri-las.

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
        self,
        text: str,
        on_delta: DeltaHandler | None = None,
        cancel: threading.Event | None = None,
        context: str = "",
        max_tokens: int | None = None,
    ) -> str:
        """Texto do usuário -> resposta completa. `on_delta` recebe trechos conforme chegam
        (chamado na thread do cliente HTTP). `context` é informação adicional (p.ex. memórias
        relevantes) anexada à instrução de sistema, nunca à fala da pessoa. `max_tokens` substitui o
        limite padrão da resposta (p.ex. receitas completas). Retorna "" se cancelado; levanta LLMError
        em falhas."""
        ...

    def check(self) -> str:
        """Verifica servidor e modelo. Retorna "" se tudo está pronto ou o motivo do problema."""
        ...


def local_url_problem(url: str) -> str:
    """Explica por que `url` não serve para o Ollama (vazio = aceitável). Só aceita endereços da
    rede local: loopback, IPs privados ou nomes de máquina sem domínio público."""
    parts = urlsplit(url)
    if parts.username is not None or parts.password is not None:
        return "Não inclua credenciais na URL do Ollama; informe somente o endereço local"
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
        top_p: float | None = None,
        repeat_penalty: float | None = None,
    ) -> None:
        problem = local_url_problem(url)
        if problem:
            raise ValueError(problem)
        parts = urlsplit(url)
        self.secure = parts.scheme == "https"
        self.host = parts.hostname or "127.0.0.1"
        self.port = parts.port or (443 if self.secure else DEFAULT_PORT)
        self.name = model
        self.url = url
        self.system_prompt = system_prompt or LUNA_SYSTEM_PROMPT
        self.connect_timeout_s = connect_timeout_s
        self.timeout_s = timeout_s
        self.keep_alive = keep_alive
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.num_ctx = 0
        self.num_thread = 0
        self.last_metrics: dict = {}
        self.top_p = top_p
        self.repeat_penalty = repeat_penalty
        self._history: list[dict] = []  # volatile, bounded; never written to persistent memory
        self._history_at = 0.0
        self.quality_registry = None
        self.quality_signature = ""
        self.quality_key = ""
        self.approval_error = ""
        self.recovery_notice = ""
        self.backend_identity = {}
        self.recipe_policy = None
        self.quality_profile = "standard"

    def configure_economic(self) -> None:
        self.quality_profile = "economic"
        self.max_tokens = min(self.max_tokens, 128)
        self.timeout_s = max(self.timeout_s, 180)
        self.keep_alive = "0"  # release LLM memory before speech synthesis

    def options(self, max_tokens: int | None = None) -> dict:
        options = {"temperature": self.temperature, "num_predict": max_tokens or self.max_tokens}
        qwen = bool(re.match(r"^qwen2\.5:(?:7b|14b|1\.5b|0\.5b)(?:-|$)", self.name))
        top_p = self.top_p if self.top_p is not None else .8 if qwen else None
        penalty = self.repeat_penalty if self.repeat_penalty is not None else (1.1 if ":1.5b" in self.name else 1.05) if qwen else None
        if top_p is not None:
            options["top_p"] = top_p
        if penalty is not None:
            options["repeat_penalty"] = penalty
        if self.num_ctx:
            options["num_ctx"] = self.num_ctx
        if self.num_thread:
            options["num_thread"] = self.num_thread
        return options

    def clone(self, name: str | None = None):
        other = OllamaClient(self.url, name or self.name, system_prompt=self.system_prompt,
            connect_timeout_s=self.connect_timeout_s, timeout_s=self.timeout_s, keep_alive=self.keep_alive,
            temperature=self.temperature, max_tokens=self.max_tokens, top_p=self.top_p, repeat_penalty=self.repeat_penalty)
        other.num_ctx, other.num_thread = self.num_ctx, self.num_thread
        other.backend_identity = self.backend_identity
        other.recipe_policy = self.recipe_policy
        other.quality_profile = self.quality_profile
        return other

    def clear_history(self) -> None:
        self._history = []
        self._history_at = 0.0

    def adopt(self, other) -> None:
        for field in ("name", "num_ctx", "num_thread", "quality_registry", "quality_signature", "quality_key", "approval_error"):
            setattr(self, field, getattr(other, field))

    def request_json(self, path: str, body: dict | None = None) -> dict:
        conn = self._connect()
        try:
            if conn.sock:
                conn.sock.settimeout(min(5.0, self.timeout_s))
            conn.request("POST" if body is not None else "GET", path,
                         body=json.dumps(body).encode() if body is not None else None,
                         headers={"Content-Type": "application/json"})
            response = conn.getresponse()
            data = response.read(2_000_000)
            if response.status != 200:
                raise self._http_error(response.status, data)
            result = json.loads(data)
            if not isinstance(result, dict) or result.get("error"):
                raise LLMError("bad_response", "Resposta inesperada ao inspecionar a IA local.")
            return result
        except (OSError, ValueError, http.client.HTTPException) as exc:
            raise LLMError("inspection_failed", "Não foi possível verificar o modelo local.", type(exc).__name__) from exc
        finally:
            conn.close()

    def list_models(self) -> list[dict]:
        conn = self._connect()
        try:
            if conn.sock is not None:
                conn.sock.settimeout(min(5.0, self.timeout_s))
            conn.request("GET", "/api/tags")
            response = conn.getresponse()
            body = response.read(1_000_000)
            if response.status != 200:
                raise LLMError("server", "Falha ao listar modelos locais")
            models = json.loads(body).get("models", [])
            if not isinstance(models, list):
                raise ValueError("Catálogo de modelos inválido")
            return [m for m in models if isinstance(m, dict)]
        except (OSError, http.client.HTTPException) as exc:
            raise LLMError("unreachable", self._unreachable_msg()) from exc
        finally:
            conn.close()

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
        self,
        text: str,
        on_delta: DeltaHandler | None = None,
        cancel: threading.Event | None = None,
        context: str = "",
        max_tokens: int | None = None,
    ) -> str:
        from server.services.model_quality import QualityRegistry, local_signature, UNAVAILABLE
        if self.approval_error:
            raise LLMError("quality_unavailable", self.approval_error)
        if self.quality_registry and self.quality_signature != local_signature(self):
            if not QualityRegistry(self.quality_registry).approve_or_fallback(self):
                raise LLMError("quality_unavailable", UNAVAILABLE)
        try:
            return self._reply_once(text, on_delta, cancel, context, max_tokens)
        except LLMError as exc:
            if exc.code != "corrupt_response" or not self.quality_registry or (cancel and cancel.is_set()):
                raise
            registry = QualityRegistry(self.quality_registry)
            registry.invalidate(self, exc.detail)
            alternate = registry.runtime_alternative(self)
            if alternate is None:
                self.approval_error = UNAVAILABLE
                raise
            previous = self.name
            self.adopt(alternate)
            self.recovery_notice = f"Falha na geração com {previous}; usando {self.name}, previamente aprovado."
            try:
                return self._reply_once(text, on_delta, cancel, context, max_tokens)  # exactly one recovery attempt
            except LLMError as recovery_error:
                registry.invalidate(self, recovery_error.code)
                self.approval_error = UNAVAILABLE
                raise

    def _reply_once(self, text, on_delta=None, cancel=None, context="", max_tokens=None) -> str:
        from server.services.model_quality import response_problem, CORRUPT
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
            "options": self.options(max_tokens),
            "messages": [
                {"role": "system", "content": system},
            ],
        }
        if self.name.split(":", 1)[0] == "qwen3":
            payload["think"] = False  # interactive voice responses have a small output budget
        if time.monotonic() - self._history_at > 1800:
            self._history = []
        history_budget = max(0, min(8000, (self.num_ctx or 4096) * 2 - len(system) - len(text)))
        while sum(len(m["content"]) for m in self._history) > history_budget and self._history:
            self._history = self._history[2:]
        payload["messages"].extend(self._history)
        payload["messages"].append({"role": "user", "content": text})
        if cancel is not None and cancel.is_set():
            return ""
        conn = self._connect()
        if self.num_ctx:
            payload["options"]["num_ctx"] = self.num_ctx
        if self.num_thread:
            payload["options"]["num_thread"] = self.num_thread
        started = time.perf_counter()
        first_token = None
        self.last_metrics = {}
        pieces: list[str] = []
        completed = False
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
                    raise self._http_error(500, str(obj["error"]).encode())
                piece = (obj.get("message") or {}).get("content") or ""
                if piece:
                    if not isinstance(piece, str):
                        raise LLMError("bad_response", "A IA enviou texto inválido.")
                    problem = response_problem(text, "".join(pieces) + piece, partial=True)
                    if problem:
                        raise LLMError("corrupt_response", CORRUPT, problem)
                    if first_token is None:
                        first_token = time.perf_counter() - started
                    pieces.append(piece)
                    if on_delta is not None:
                        on_delta(piece)
                if obj.get("done"):
                    completed = True
                    duration = obj.get("eval_duration", 0) / 1e9
                    self.last_metrics = {"ttft_s": first_token, "total_s": time.perf_counter() - started,
                        "tokens_per_s": obj.get("eval_count", 0) / duration if duration else None,
                        "load_s": obj.get("load_duration", 0) / 1e9, "model": self.name}
                    break
        finally:
            conn.close()
        if cancel is not None and cancel.is_set():
            return ""
        if not completed:
            raise LLMError("incomplete_stream", "A resposta da IA foi interrompida. Tente novamente.")
        reply = clean_reply("".join(pieces))
        if not reply:
            raise LLMError("empty", "A IA não devolveu nenhuma resposta. Tente perguntar de novo.")
        problem = response_problem(text, reply)
        if problem:
            raise LLMError("corrupt_response", CORRUPT, problem)
        self._history.extend([{"role": "user", "content": text}, {"role": "assistant", "content": reply}])
        self._history = self._history[-6:]
        while sum(len(m["content"]) for m in self._history) > 8000 and len(self._history) >= 2:
            self._history = self._history[2:]
        self._history_at = time.monotonic()
        return reply

    def _http_error(self, status: int, body: bytes) -> LLMError:
        detail = body[:300].decode("utf-8", "replace")
        if re.search(r"out of memory|not enough (?:system )?memory|requires more (?:system )?memory|insufficient memory|cannot allocate|failed to allocate", detail, re.I):
            return LLMError("memory_insufficient", "Memória insuficiente para executar o modelo.", detail)
        if status == 400:
            return LLMError("configuration", "O Ollama recusou a configuração do modelo.", detail)
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
