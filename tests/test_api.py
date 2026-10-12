import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server.api.app import CLOSE_BAD_HELLO, CLOSE_BAD_TOKEN, create_app
from server.storage.config import ConfigError, Settings

TOKEN = "token-de-teste-com-tamanho-ok"


def make_client(tmp_path, **overrides) -> TestClient:
    opts = {
        "auth_token": TOKEN,
        "debug_allow_state_override": True,
        "client_dir": tmp_path,
        "tts_enabled": False,
        "memory_path": tmp_path / "memory.sqlite3",
    }
    opts.update(overrides)
    settings = Settings(**opts)
    settings.validate()
    return TestClient(create_app(settings))


def hello(ws, token=TOKEN):
    ws.send_json({"type": "hello", "token": token, "client": "ui", "version": 1})
    return ws.receive_json()


def test_health_reports_idle_after_startup(tmp_path):
    with make_client(tmp_path) as c:
        r = c.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["state"] == "idle"
        assert body["clients"] == 0


def test_static_client_is_served(tmp_path):
    (tmp_path / "index.html").write_text("<h1>ok</h1>", encoding="utf-8")
    with make_client(tmp_path) as c:
        r = c.get("/")
        assert r.status_code == 200
        assert "ok" in r.text


def test_hello_returns_welcome_with_state(tmp_path):
    with make_client(tmp_path) as c, c.websocket_connect("/ws") as ws:
        msg = hello(ws)
        assert msg["type"] == "welcome"
        assert msg["state"] == "idle"
        assert msg["wake_word"] == "hey Luna"
        assert msg["debug"] is True


def test_bad_token_is_rejected_with_4401(tmp_path):
    with make_client(tmp_path) as c, c.websocket_connect("/ws") as ws:
        ws.send_json({"type": "hello", "token": "errado", "client": "ui"})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == CLOSE_BAD_TOKEN


def test_first_message_must_be_hello(tmp_path):
    with make_client(tmp_path) as c, c.websocket_connect("/ws") as ws:
        ws.send_json({"type": "ping"})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == CLOSE_BAD_HELLO


def test_ping_pong(tmp_path):
    with make_client(tmp_path) as c, c.websocket_connect("/ws") as ws:
        hello(ws)
        ws.send_json({"type": "ping", "t": 123.0})
        assert ws.receive_json() == {"type": "pong", "t": 123.0}


def test_set_state_is_broadcast_to_all_clients(tmp_path):
    with make_client(tmp_path) as c, c.websocket_connect("/ws") as a, c.websocket_connect("/ws") as b:
        hello(a)
        hello(b)
        a.send_json({"type": "set_state", "state": "listening", "detail": "teste"})
        for ws in (a, b):
            msg = ws.receive_json()
            assert msg["type"] == "state"
            assert msg["state"] == "listening"
            assert msg["detail"] == "teste"
        assert c.get("/health").json()["state"] == "listening"


def test_set_state_denied_when_debug_disabled(tmp_path):
    with make_client(tmp_path, debug_allow_state_override=False) as c, c.websocket_connect("/ws") as ws:
        hello(ws)
        ws.send_json({"type": "set_state", "state": "error"})
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert msg["code"] == "forbidden"
        assert c.get("/health").json()["state"] == "idle"


def test_invalid_message_returns_error_without_closing(tmp_path):
    with make_client(tmp_path) as c, c.websocket_connect("/ws") as ws:
        hello(ws)
        ws.send_text("isto nao e json")
        assert ws.receive_json()["code"] == "bad_message"
        ws.send_json({"type": "set_state", "state": "inexistente"})
        assert ws.receive_json()["code"] == "bad_message"
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"


def test_settings_validation_rejects_weak_token_and_wildcard_bind():
    with pytest.raises(ConfigError):
        Settings(auth_token="curto").validate()
    with pytest.raises(ConfigError):
        Settings(auth_token=TOKEN, bind_host="0.0.0.0").validate()


def test_settings_from_toml(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text(
        '[server]\nbind_host="192.168.1.6"\nport=8801\ndebug_allow_state_override=true\n'
        f'[auth]\ntoken="{TOKEN}"\n[assistant]\nwake_word="computador"\n',
        encoding="utf-8",
    )
    s = Settings.from_toml(p)
    assert (s.bind_host, s.port, s.debug_allow_state_override, s.wake_word) == ("192.168.1.6", 8801, True, "computador")
    with pytest.raises(ConfigError):
        Settings.from_toml(tmp_path / "nao-existe.toml")


def test_settings_tls_requires_both_files_and_existing_paths(tmp_path):
    cert = tmp_path / "server.crt"
    key = tmp_path / "server.key"
    cert.write_text("cert", encoding="utf-8")
    key.write_text("key", encoding="utf-8")
    s = Settings(auth_token=TOKEN, tls_certfile=cert, tls_keyfile=key)
    s.validate()
    assert s.tls_enabled and s.scheme == "https"
    assert not Settings(auth_token=TOKEN).tls_enabled
    assert Settings(auth_token=TOKEN).scheme == "http"
    with pytest.raises(ConfigError, match="juntos"):
        Settings(auth_token=TOKEN, tls_certfile=cert).validate()
    with pytest.raises(ConfigError, match="tls_keyfile nao encontrado"):
        Settings(auth_token=TOKEN, tls_certfile=cert, tls_keyfile=tmp_path / "falta.key").validate()


def test_settings_from_toml_tls_paths_are_relative_to_project_root(tmp_path):
    from server.storage.config import PROJECT_ROOT

    p = tmp_path / "s.toml"
    p.write_text(
        '[server]\ntls_certfile="config/certs/server.crt"\ntls_keyfile="config/certs/server.key"\n'
        f'[auth]\ntoken="{TOKEN}"\n',
        encoding="utf-8",
    )
    s = Settings.from_toml(p)
    assert s.tls_certfile == PROJECT_ROOT / "config" / "certs" / "server.crt"
    assert s.tls_keyfile == PROJECT_ROOT / "config" / "certs" / "server.key"
    p.write_text(f'[server]\ntls_certfile=""\n[auth]\ntoken="{TOKEN}"\n', encoding="utf-8")
    assert Settings.from_toml(p).tls_certfile is None
