"""Cliente do Ollama contra um servidor HTTP falso (sem Ollama real) e validação de URLs locais."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from server.services.llm import LLMError, OllamaClient, clean_reply, local_url_problem
from server.storage.config import ConfigError, Settings

TOKEN = "token-de-teste-com-tamanho-ok"


class FakeOllama:
    """Servidor HTTP mínimo que imita /api/tags e /api/chat (NDJSON) do Ollama."""

    def __init__(self) -> None:
        self.models = ["qwen2.5:7b"]
        self.pieces = ["Olá! ", "Posso ", "ajudar?"]
        self.error: str | None = None  # erro no meio do fluxo
        self.status = 200
        self.requests: list[dict] = []
        self.hold = threading.Event()  # setado: o servidor segura a resposta ate `go` (cancelamento/timeout)
        self.go = threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):  # silencio no teste
                pass

            def do_GET(self):
                body = json.dumps({"models": [{"name": m} for m in outer.models]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(payload)
                if payload["model"] not in outer.models:
                    body = json.dumps({"error": f"model '{payload['model']}' not found, try pulling it first"}).encode()
                    self.send_response(404)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if outer.status != 200:
                    self.send_response(outer.status)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                try:
                    for piece in outer.pieces:
                        if outer.hold.is_set():
                            outer.go.wait(5)
                        self._chunk({"message": {"role": "assistant", "content": piece}, "done": False})
                    if outer.error:
                        self._chunk({"error": outer.error})
                    else:
                        self._chunk({"message": {"role": "assistant", "content": ""}, "done": True})
                    self.wfile.write(b"0\r\n\r\n")
                except OSError:
                    return  # cliente fechou a conexao (cancelamento/timeout): esperado

            def _chunk(self, obj):
                data = (json.dumps(obj) + "\n").encode()
                self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.go.set()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def ollama():
    fake = FakeOllama()
    yield fake
    fake.close()


def client(fake: FakeOllama, model="qwen2.5:7b", **kw) -> OllamaClient:
    return OllamaClient(fake.url, model, timeout_s=kw.pop("timeout_s", 5.0), **kw)


def test_reply_streams_pieces_and_sends_system_prompt(ollama):
    c = client(ollama)
    seen: list[str] = []
    assert c.check() == ""
    assert c.reply("oi luna", seen.append) == "Olá! Posso ajudar?"
    assert seen == ["Olá! ", "Posso ", "ajudar?"]
    req = ollama.requests[0]
    assert req["stream"] is True and req["model"] == "qwen2.5:7b"
    assert [m["role"] for m in req["messages"]] == ["system", "user"]
    assert "Luna" in req["messages"][0]["content"] and req["messages"][1]["content"] == "oi luna"
    assert req["options"] == {"temperature": 0.7, "num_predict": 300} and req["keep_alive"] == "10m"


def test_custom_system_prompt_is_used(ollama):
    c = client(ollama, system_prompt="Responda só com sim ou não.")
    c.reply("teste")
    assert ollama.requests[0]["messages"][0]["content"] == "Responda só com sim ou não."


def test_missing_model_is_reported_with_pull_hint(ollama):
    c = client(ollama, model="nao-existe:1b")
    assert "ollama pull nao-existe:1b" in c.check()
    with pytest.raises(LLMError) as exc:
        c.reply("oi")
    assert exc.value.code == "model_missing" and "ollama pull nao-existe:1b" in exc.value.message


def test_unreachable_server_is_friendly():
    c = OllamaClient("http://127.0.0.1:9", "qwen2.5:7b", connect_timeout_s=0.5)  # porta discard: recusa
    assert "O Ollama está ligado?" in c.check()
    with pytest.raises(LLMError) as exc:
        c.reply("oi")
    assert exc.value.code == "unreachable" and "127.0.0.1:9" in exc.value.message


def test_empty_reply_and_server_errors(ollama):
    ollama.pieces = ["  ", "\n"]
    with pytest.raises(LLMError) as exc:
        client(ollama).reply("oi")
    assert exc.value.code == "empty"
    ollama.pieces = ["parcial"]
    ollama.error = "cuda out of memory"
    with pytest.raises(LLMError) as exc:
        client(ollama).reply("oi")
    assert exc.value.code == "server" and "out of memory" in exc.value.detail
    ollama.error = None
    ollama.status = 500
    with pytest.raises(LLMError) as exc:
        client(ollama).reply("oi")
    assert exc.value.code == "server" and "500" in exc.value.message


def test_empty_prompt_is_never_sent(ollama):
    with pytest.raises(LLMError) as exc:
        client(ollama).reply("   ")
    assert exc.value.code == "empty_prompt" and ollama.requests == []


def test_cancel_stops_reading_and_returns_empty(ollama):
    ollama.pieces = ["a"] * 50
    cancel = threading.Event()
    c = client(ollama, timeout_s=2.0)
    seen: list[str] = []

    def first_piece(piece: str) -> None:
        seen.append(piece)
        cancel.set()

    assert c.reply("oi", first_piece, cancel) == ""
    assert seen == ["a"]  # para de ler assim que o cancelamento e pedido
    cancel2 = threading.Event()
    cancel2.set()
    assert c.reply("oi", None, cancel2) == ""  # cancelado antes de conectar: nenhuma requisicao nova
    assert len(ollama.requests) == 1


def test_timeout_is_reported(ollama):
    ollama.hold.set()
    c = client(ollama, timeout_s=0.3)
    with pytest.raises(LLMError) as exc:
        c.reply("oi")
    ollama.go.set()
    assert exc.value.code == "timeout"


def test_clean_reply_strips_thinking_and_blank_lines():
    assert clean_reply("<think>hmm\n</think>\n\nOi!\n\n\n\nTudo  bem? \n") == "Oi!\n\nTudo bem?"
    assert clean_reply("   ") == ""


def test_local_url_validation():
    assert local_url_problem("http://127.0.0.1:11434") == ""
    assert local_url_problem("http://192.168.1.20:11434") == ""
    assert local_url_problem("http://10.0.0.5") == ""
    assert local_url_problem("http://ryzen:11434") == ""
    assert local_url_problem("http://ryzen.local:11434") == ""
    assert local_url_problem("http://8.8.8.8:11434")
    assert local_url_problem("https://api.example.com")
    assert local_url_problem("ftp://127.0.0.1")
    assert local_url_problem("http://127.0.0.1:11434/api")
    with pytest.raises(ValueError):
        OllamaClient("http://example.com", "m")


def test_settings_llm_from_toml_and_validation(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text(
        f'[auth]\ntoken="{TOKEN}"\n[llm]\nurl="http://192.168.1.6:11434/"\nmodel="llama3.1:8b"\n'
        'system_prompt="""Oi\nLuna"""\nmax_tokens=120\n',
        encoding="utf-8",
    )
    s = Settings.from_toml(p)
    assert (s.llm_enabled, s.llm_url, s.llm_model, s.llm_max_tokens) == (True, "http://192.168.1.6:11434", "llama3.1:8b", 120)
    assert s.llm_system_prompt == "Oi\nLuna"
    s.validate()
    with pytest.raises(ConfigError, match="llm.url"):
        Settings(auth_token=TOKEN, llm_url="http://api.example.com").validate()
    with pytest.raises(ConfigError, match="llm.model"):
        Settings(auth_token=TOKEN, llm_model=" ").validate()
    Settings(auth_token=TOKEN, llm_enabled=False, llm_url="http://api.example.com").validate()  # desativada: nao valida
    assert Settings(auth_token=TOKEN).llm_url == "http://127.0.0.1:11434"
