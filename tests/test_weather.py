"""Fase 8: previsão do tempo (Open-Meteo) com cache local persistente e funcionamento offline.

Cobre: interpretação da resposta da fonte e códigos WMO, cliente HTTP contra um servidor local falso (sucesso,
429, 500, resposta inválida, conexão recusada, DNS), cache SQLite (uma linha por local, falhas não apagam dados,
reinício), política de validade, frases reconhecidas (e as que não são), respostas online, do cache, offline com e
sem cache, cache antigo, pergunta sobre o próprio cache, integração com a `Conversation` (sem IA, voz curta,
outros comandos seguem funcionando offline), WebSocket (`welcome`, evento `weather`, `/health`) e configuração.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from server.api.app import create_app
from server.assistant.conversation import DETAIL_READY, Conversation
from server.assistant.memory import MemoryCommands
from server.assistant.state import AssistantState, StateMachine
from server.assistant.weather import (
    NOT_CERTAIN,
    REPLY_NO_DATA_OFFLINE,
    STALE_WARNING,
    WeatherAssistant,
    parse,
)
from server.services.weather import (
    DEFAULT_LATITUDE,
    DEFAULT_LONGITUDE,
    OpenMeteoClient,
    WeatherData,
    WeatherError,
    describe_code,
    is_rainy_code,
    local_timezone,
    parse_response,
)
from server.storage.config import ConfigError, Settings
from server.storage.memory import MemoryStore
from server.storage.weather import WeatherCache, WeatherCacheError
from tests.test_conversation import FakeLLM, settle
from tests.test_memory import TOKEN, collect_until, hello, settings_for
from tests.test_voice import FakeDetector, FakeTranscriber, silence, speech, wake_frame

TZ = timezone(timedelta(hours=-3))
TODAY = date(2026, 10, 12)  # segunda-feira
NOW = datetime(2026, 10, 12, 14, 32, tzinfo=TZ).timestamp()
LAT, LON, NAME, TZ_NAME = DEFAULT_LATITUDE, DEFAULT_LONGITUDE, "Ouro Fino, MG", "America/Sao_Paulo"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def payload(start: date = TODAY, days: int = 3, *, rain_pct=(70, 60, 5), current=True) -> dict:
    """JSON no formato da Open-Meteo para `days` dias a partir de `start` (horário local já convertido)."""
    dates = [(start + timedelta(days=i)).isoformat() for i in range(days)]
    times = [f"{d}T{h:02d}:00" for d in dates for h in range(24)]
    # Dia 1: 10% de madrugada/manhã, 70% à tarde, 40% à noite. Demais dias: valor fixo do dia.
    probs = [10] * 12 + [70] * 6 + [40] * 6
    for pct in rain_pct[1:days]:
        probs += [pct] * 24
    probs += [0] * (len(times) - len(probs))
    mm = [0] * 12 + [1.2] * 6 + [0.3] * 6 + [0] * (len(times) - 24)
    codes = [80, 61, 1, 3, 95, 0, 2][:days]
    data = {
        "latitude": -22.25, "longitude": -46.375, "elevation": 924.0, "generationtime_ms": 0.5,
        "utc_offset_seconds": -10800, "timezone": TZ_NAME, "timezone_abbreviation": "GMT-3",
        "hourly": {"time": times, "temperature_2m": [20.0] * len(times), "precipitation_probability": probs, "precipitation": mm, "weather_code": [2] * len(times)},
        "daily": {
            "time": dates, "weather_code": codes + [1] * (days - len(codes)),
            "temperature_2m_max": [27.1, 24.0, 26.5, 25.0, 22.0, 24.0, 25.0][:days],
            "temperature_2m_min": [15.2, 14.1, 13.0, 12.0, 14.0, 13.0, 12.0][:days],
            "precipitation_sum": [5.1, 4.2, 0.0, 0.0, 12.0, 0.0, 0.0][:days],
            "precipitation_probability_max": list(rain_pct[:days]) + [0] * max(0, days - len(rain_pct)),
            "wind_speed_10m_max": [20.5, 18.0, 15.0, 10.0, 30.0, 12.0, 11.0][:days],
            "sunrise": [f"{d}T05:50" for d in dates], "sunset": [f"{d}T18:10" for d in dates],
        },
    }
    if current:
        data["current"] = {
            "time": f"{dates[0]}T14:30", "interval": 900, "temperature_2m": 23.4, "relative_humidity_2m": 61,
            "apparent_temperature": 22.1, "is_day": 1, "precipitation": 0.0, "weather_code": 2, "wind_speed_10m": 12.3,
        }
    return data


class FakeSource:
    name = "fonte-falsa"

    def __init__(self, data: dict | None = None, *, error: WeatherError | None = None, clock=None) -> None:
        self.data = data if data is not None else payload()
        self.error = error
        self.clock = clock or (lambda: NOW)
        self.calls = 0

    def fetch(self) -> WeatherData:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return parse_response(self.data, location_name=NAME, fetched_at=self.clock())


class Clock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_assistant(tmp_path, source=None, clock=None, cache=True, **kw) -> tuple[WeatherAssistant, FakeSource, Clock]:
    clock = clock or Clock()
    source = source if source is not None else FakeSource(clock=clock)
    store = WeatherCache(tmp_path / "memory.sqlite3") if cache else None
    wa = WeatherAssistant(source, store, latitude=LAT, longitude=LON, location_name=NAME, tz_name=TZ_NAME, clock=clock, **kw)
    return wa, source, clock


OFFLINE = WeatherError("offline", "Não consegui acessar a internet.", "gaierror")


def dates_in(text: str) -> list[str]:
    """Datas completas ("13 de outubro de 2026") na ordem em que aparecem: a fala e a tela devem trazer as mesmas."""
    import re

    return re.findall(r"\d{1,2} de [a-zç]+ de \d{4}", text)


# ---- Fonte: interpretação da resposta e códigos ------------------------------------------------------------


def test_parse_response_normalizes_units_times_and_codes():
    data = parse_response(payload(), location_name=NAME, fetched_at=NOW)
    assert (data.location_name, data.timezone, data.utc_offset_s, data.fetched_at) == (NAME, TZ_NAME, -10800, NOW)
    assert data.current is not None and data.current.time == "2026-10-12T14:30" and data.current.temperature_c == 23.4
    assert data.current.wind_kmh == 12.3 and data.current.is_day is True and data.current.weather_code == 2
    assert [d.date for d in data.daily] == ["2026-10-12", "2026-10-13", "2026-10-14"]
    assert data.period_start == "2026-10-12" and data.period_end == "2026-10-14"
    assert data.day("2026-10-13").precipitation_probability_max == 60 and data.day("2026-10-15") is None
    assert len(data.hours_of("2026-10-12")) == 24 and data.hours_of("2026-10-12")[13].precipitation_probability == 70
    # Hora da consulta separada da hora da previsão: fetched_at é epoch; current.time é a hora local do modelo.
    assert datetime.fromtimestamp(data.fetched_at, data.tz()).strftime("%H:%M") == "14:32"
    # Ida e volta pelo JSON do cache.
    again = WeatherData.from_dict(json.loads(json.dumps(data.as_dict())))
    assert again == data


def test_parse_response_rejects_invalid_payloads():
    for bad in ("texto", [], {"error": True, "reason": "x"}, {"latitude": "a"}, {"latitude": 1, "longitude": 2}, {"latitude": 1, "longitude": 2, "daily": {"x": 1}}):
        with pytest.raises(WeatherError) as exc:
            parse_response(bad, location_name=NAME, fetched_at=NOW)
        assert exc.value.code in ("bad_response", "http")
    # Sem o bloco "current" a previsão diária ainda vale; valores nulos não viram números.
    p = payload(current=False)
    p["daily"]["temperature_2m_max"][0] = None
    data = parse_response(p, location_name=NAME, fetched_at=NOW)
    assert data.current is None and data.day("2026-10-12").temperature_max_c is None


def test_weather_codes_cover_sun_clouds_rain_storms_and_unknown():
    assert describe_code(0, True) == "sol" and describe_code(0, False) == "céu limpo"
    assert describe_code(2) == "parcialmente nublado" and describe_code(3) == "nublado" and describe_code(45) == "nevoeiro"
    assert describe_code(61) == "chuva fraca" and describe_code(65) == "chuva forte" and describe_code(80) == "pancadas de chuva fracas"
    assert describe_code(95) == "tempestade" and describe_code(99) == "tempestade com granizo forte" and describe_code(71) == "neve fraca"
    assert describe_code(None) == "condição desconhecida" and "código 42" in describe_code(42)
    assert is_rainy_code(61) and is_rainy_code(95) and is_rainy_code(51) and not is_rainy_code(2) and not is_rainy_code(None)


def test_local_timezone_falls_back_to_source_offset_when_tz_database_is_missing():
    tz = local_timezone("Fuso/Inexistente", -10800)
    assert datetime.fromtimestamp(NOW, tz).strftime("%H:%M") == "14:32"
    assert datetime.fromtimestamp(NOW, local_timezone(TZ_NAME, -10800)).strftime("%H:%M") == "14:32"


# ---- Cliente HTTP contra um servidor local -----------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    mode = "ok"
    seen: list[str] = []

    def do_GET(self):  # noqa: N802
        _Handler.seen.append(self.path)
        if self.mode == "ok":
            body = json.dumps(payload()).encode()
            self.send_response(200)
        elif self.mode == "rate":
            body = b'{"error":true,"reason":"Too many requests"}'
            self.send_response(429)
        elif self.mode == "bad_json":
            body = b"<html>isso nao e json</html>"
            self.send_response(200)
        elif self.mode == "error400":
            body = b'{"error":true,"reason":"Cannot initialize WeatherVariable"}'
            self.send_response(400)
        else:
            body = b"erro"
            self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silencioso
        pass


@pytest.fixture
def fake_api():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _Handler.mode = "ok"
    _Handler.seen = []
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def test_client_fetches_and_parses_from_local_server(fake_api):
    client = OpenMeteoClient(LAT, LON, location_name=NAME, forecast_days=3, host="127.0.0.1", port=fake_api, secure=False, clock=lambda: NOW)
    data = client.fetch()
    assert data.location_name == NAME and data.fetched_at == NOW and len(data.daily) == 3 and client.requests == 1 and client.failures == 0
    q = _Handler.seen[0]
    assert q.startswith("/v1/forecast?") and "latitude=-22.28306" in q and "timezone=America%2FSao_Paulo" in q
    assert "forecast_days=3" in q and "wind_speed_unit=kmh" in q and "precipitation_unit=mm" in q and "temperature_unit=celsius" in q
    assert "current=temperature_2m" in q and "daily=weather_code" in q and "hourly=temperature_2m" in q


@pytest.mark.parametrize("mode, code", [("rate", "rate_limited"), ("bad_json", "bad_response"), ("error400", "http"), ("error500", "http")])
def test_client_reports_http_and_payload_failures(fake_api, mode, code):
    _Handler.mode = mode
    client = OpenMeteoClient(LAT, LON, host="127.0.0.1", port=fake_api, secure=False)
    with pytest.raises(WeatherError) as exc:
        client.fetch()
    assert exc.value.code == code and exc.value.message and client.failures == 1


def test_client_reports_connection_refused_and_dns_failures(fake_api):
    # Porta fechada: o servidor falso é desligado antes da chamada.
    client = OpenMeteoClient(LAT, LON, host="127.0.0.1", port=1, secure=False, connect_timeout_s=1.0)
    with pytest.raises(WeatherError) as exc:
        client.fetch()
    assert exc.value.code in ("offline", "unreachable")
    client = OpenMeteoClient(LAT, LON, host="servidor-que-nao-existe.invalid", connect_timeout_s=2.0)
    with pytest.raises(WeatherError) as exc:
        client.fetch()
    assert exc.value.code in ("offline", "unreachable") and "internet" in exc.value.message or "não respondeu" in exc.value.message
    with pytest.raises(ValueError):
        OpenMeteoClient(91.0, 0.0)


@pytest.mark.skipif(not os.environ.get("LUNA_WEATHER_ONLINE"), reason="consulta real à Open-Meteo só com LUNA_WEATHER_ONLINE=1")
def test_client_real_open_meteo_call():
    data = OpenMeteoClient(LAT, LON, location_name=NAME, forecast_days=2).fetch()
    assert data.current is not None and data.current.temperature_c is not None and len(data.daily) == 2
    assert data.timezone == TZ_NAME and abs(data.latitude - LAT) < 0.2 and abs(data.longitude - LON) < 0.2


# ---- Cache SQLite ---------------------------------------------------------------------------------------------


def test_cache_single_row_per_location_failures_keep_data_and_survives_restart(tmp_path):
    path = tmp_path / "memory.sqlite3"
    cache = WeatherCache(path)
    assert cache.get(LAT, LON) is None
    data = parse_response(payload(), location_name=NAME, fetched_at=NOW)
    entry = cache.store(data, latitude=LAT, longitude=LON)
    assert entry.has_data and entry.fetched_at == NOW and entry.period_start == "2026-10-12" and entry.period_end == "2026-10-14"
    assert entry.last_status == "ok" and entry.last_error == "" and entry.location_name == NAME
    # Falha posterior: só as colunas da tentativa mudam; os dados continuam lá.
    failed = cache.record_failure(LAT, LON, location_name=NAME, timezone=TZ_NAME, error="Não consegui acessar a internet.", at=NOW + 60)
    assert failed.has_data and failed.data == data and failed.fetched_at == NOW
    assert failed.last_status == "failed" and failed.last_attempt_at == NOW + 60 and "internet" in failed.last_error
    # Nova consulta boa substitui a anterior, sem duplicar linhas.
    newer = parse_response(payload(TODAY + timedelta(days=1)), location_name=NAME, fetched_at=NOW + 3600)
    cache.store(newer, latitude=LAT, longitude=LON)
    with cache._conn:
        assert cache._conn.execute("SELECT COUNT(*) FROM weather_cache").fetchone()[0] == 1
    cache.close()
    # "Reinício": outra instância sobre o mesmo arquivo vê os mesmos dados.
    again = WeatherCache(path)
    entry = again.get(LAT, LON)
    assert entry is not None and entry.fetched_at == NOW + 3600 and entry.period_start == "2026-10-13" and entry.last_status == "ok"
    assert again.clear() == 1 and again.get(LAT, LON) is None
    again.close()


def test_cache_shares_the_memory_database_and_rejects_empty_data(tmp_path):
    path = tmp_path / "memory.sqlite3"
    memory = MemoryStore(path)
    memory.add("preference", "eu não gosto de cebola")
    cache = WeatherCache(path)
    cache.store(parse_response(payload(), location_name=NAME, fetched_at=NOW), latitude=LAT, longitude=LON)
    assert memory.count() == 1 and cache.get(LAT, LON) is not None  # mesmas tabelas lado a lado
    tables = {r[0] for r in memory._conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"memories", "weather_cache"} <= tables
    empty = WeatherData(NAME, LAT, LON, None, TZ_NAME, -10800, NOW, None, [], [])
    with pytest.raises(ValueError):
        cache.store(empty, latitude=LAT, longitude=LON)
    cache.close()
    memory.close()


def test_cache_failures_become_weather_cache_error_and_corrupt_payload_is_ignored(tmp_path):
    with pytest.raises(WeatherCacheError):
        WeatherCache(tmp_path)  # um diretorio nao e um banco
    cache = WeatherCache(tmp_path / "memory.sqlite3")
    cache.store(parse_response(payload(), location_name=NAME, fetched_at=NOW), latitude=LAT, longitude=LON)
    with cache._conn:
        cache._conn.execute("UPDATE weather_cache SET payload = '{nao e json'")
    entry = cache.get(LAT, LON)
    assert entry is not None and not entry.has_data and entry.data is None
    cache._conn.close()
    with pytest.raises(WeatherCacheError):
        cache.get(LAT, LON)


# ---- Frases reconhecidas ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, name, offsets",
    [
        ("Hey Luna, como está o tempo em Ouro Fino?", "current", [0]),
        ("como está o tempo?", "current", [0]),
        ("está frio lá fora?", "current", [0]),
        ("qual a temperatura agora?", "current", [0]),
        ("quantos graus faz hoje?", "current", [0]),
        ("Vai chover hoje?", "rain", [0]),
        ("vai chover?", "rain", [0]),
        ("tá chovendo?", "rain", [0]),
        ("preciso de guarda-chuva?", "rain", [0]),
        ("vai chover amanhã?", "rain", [1]),
        ("tem chance de chuva no fim de semana?", "rain", [5, 6]),
        ("Como estará o tempo amanhã?", "forecast", [1]),
        ("como vai ficar o tempo depois de amanhã?", "forecast", [2]),
        ("Qual é a previsão para os próximos três dias?", "forecast", [0, 1, 2]),
        ("previsão para os próximos dias", "forecast", [0, 1, 2]),
        ("qual a previsão da semana?", "forecast", [0, 1, 2, 3, 4, 5, 6]),
        ("previsão do tempo", "forecast", [0]),
        ("como vai ficar o tempo na quinta?", "forecast", [3]),
        ("vai fazer sol amanhã?", "forecast", [1]),
        ("Luna, você tem uma previsão salva?", "cached", [0]),
        ("tem previsão guardada?", "cached", [0]),
        ("Luna, quando o tempo foi atualizado pela última vez?", "last_update", [0]),
        ("quando você atualizou a previsão?", "last_update", [0]),
        ("de quando é essa previsão?", "last_update", [0]),
    ],
)
def test_parse_weather_questions(text, name, offsets):
    intent = parse(text, today=TODAY)
    assert intent is not None and intent.name == name and intent.offsets == offsets


@pytest.mark.parametrize(
    "text",
    [
        "quanto tempo leva para cozinhar arroz?",
        "o tempo de forno é 40 minutos",
        "qual a temperatura do forno para pão?",
        "me ensine a fazer bolo de cenoura",
        "lembre que eu gosto de pimenta",
        "tenho três ovos e um pacote de arroz",
        "que horas são?",
        "clima de festa na cozinha",
        "como está o tempo de cozimento?",
        "o que você lembra sobre mim?",
        "me sugere um jantar rápido",
    ],
)
def test_parse_leaves_other_sentences_alone(text):
    assert parse(text, today=TODAY) is None


# ---- Respostas: online, cache, offline, antigo ------------------------------------------------------------------


@pytest.mark.anyio
async def test_online_success_updates_cache_and_fresh_cache_avoids_new_calls(tmp_path):
    wa, src, clock = make_assistant(tmp_path)
    out = await wa.answer(wa.match("como está o tempo em Ouro Fino?"))
    assert out.status == "online" and out.fetched and src.calls == 1
    assert out.reply.startswith("Agora em Ouro Fino, MG, segunda-feira, 12 de outubro de 2026, às 14:30: 23 graus, parcialmente nublado, vento de 12 km/h")
    assert "Para hoje, segunda-feira, 12 de outubro de 2026: pancadas de chuva fracas, mínima de 15 e máxima de 27 graus, chance de chuva de 70%" in out.reply
    assert out.reply.endswith("Dados atualizados agora, às 14:32.")
    assert out.spoken != out.reply and "umidade" not in out.spoken and out.spoken.endswith("às 14:32.")  # voz mais curta
    assert out.event["type"] == "weather" and out.event["available"] and out.event["summary"] == "23 °C · parcialmente nublado"
    assert out.event["detail"].startswith("hoje, 12/10 · mín 15 °C · máx 27 °C · chuva 70%") and out.event["updated_at"] == NOW and out.event["stale"] is False
    entry = wa.cache.get(LAT, LON)
    assert entry is not None and entry.has_data and entry.last_status == "ok"
    # Dentro da validade: nenhuma nova chamada à fonte, e a resposta diz de quando são os dados.
    clock.advance(10 * 60)
    out = await wa.answer(wa.match("qual a temperatura agora?"))
    assert out.status == "cache" and not out.fetched and src.calls == 1 and out.reply.endswith("Dados atualizados hoje às 14:32.")
    out = await wa.answer(wa.match("vai chover hoje?"))
    assert out.status == "cache" and src.calls == 1
    # Condições atuais vencem em 30 min; a previsão diária ainda vale por 3 h.
    clock.advance(25 * 60)
    out = await wa.answer(wa.match("como estará o tempo amanhã?"))
    assert out.status == "cache" and src.calls == 1
    out = await wa.answer(wa.match("como está o tempo?"))
    assert out.status == "online" and src.calls == 2
    assert wa.snapshot()["fetches"] == 2 and wa.snapshot()["fetch_failures"] == 0


@pytest.mark.anyio
async def test_forecast_for_today_tomorrow_and_next_days(tmp_path):
    wa, src, clock = make_assistant(tmp_path)
    out = await wa.answer(wa.match("como estará o tempo amanhã?"))
    assert out.reply == (
        "Amanhã, terça-feira, 13 de outubro de 2026: chuva fraca, mínima de 14 e máxima de 24 graus, chance de chuva de 60% (cerca de 4 mm) e vento de até 18 km/h. "
        f"{NOT_CERTAIN} Dados atualizados agora, às 14:32."
    )
    out = await wa.answer(wa.match("qual a previsão para hoje?"))
    assert out.reply.startswith("Hoje, segunda-feira, 12 de outubro de 2026: pancadas de chuva fracas, mínima de 15 e máxima de 27 graus, chance de chuva de 70% (cerca de 5 mm)")
    out = await wa.answer(wa.match("qual é a previsão para os próximos três dias?"))
    lines = out.reply.splitlines()
    assert lines[0] == "Previsão para Ouro Fino, MG:" and lines[1].startswith("Hoje, segunda-feira, 12 de outubro de 2026:")
    assert lines[2].startswith("Amanhã, terça-feira, 13 de outubro de 2026: chuva fraca")
    assert lines[3].startswith("Depois de amanhã, quarta-feira, 14 de outubro de 2026: sol entre poucas nuvens, mínima de 13 e máxima de 26 graus, chance de chuva de 5%")
    assert "\n" not in out.spoken and out.spoken.startswith("Previsão para Ouro Fino, MG: Hoje, segunda-feira, 12 de outubro de 2026:")
    assert dates_in(out.spoken) == dates_in(out.reply) == ["12 de outubro de 2026", "13 de outubro de 2026", "14 de outubro de 2026"]
    # Mais dias do que a fonte deu: a Luna diz até onde vai, sem inventar.
    out = await wa.answer(wa.match("qual a previsão da semana?"))
    assert "Não tenho dados para quinta-feira (15/10), sexta-feira (16/10), sábado (17/10) e domingo (18/10): a previsão salva vai até quarta-feira (14/10)." in out.reply
    assert "Os demais dias estão na tela." not in out.spoken and "Não tenho dados para" in out.spoken
    out = await wa.answer(wa.match("como vai ficar o tempo no sábado?"))
    assert out.reply.startswith("Minha previsão salva vai até quarta-feira (14/10); não tenho dados para sábado (17/10).")


@pytest.mark.anyio
async def test_rain_today_reports_periods_and_probability_without_certainty(tmp_path):
    wa, src, clock = make_assistant(tmp_path)
    out = await wa.answer(wa.match("vai chover hoje?"))
    assert out.reply.startswith("Hoje, segunda-feira, 12 de outubro de 2026, é bem provável que chova em Ouro Fino, MG: a chance chega a 70% (70% à tarde; 40% à noite), com cerca de 5 mm previstos.")
    assert "Vale deixar um guarda-chuva por perto." in out.reply and NOT_CERTAIN in out.reply and NOT_CERTAIN in out.spoken
    out = await wa.answer(wa.match("vai chover amanhã?"))
    assert out.reply.startswith("Amanhã, terça-feira, 13 de outubro de 2026, é bem provável que chova em Ouro Fino, MG: a chance chega a 60%")
    assert dates_in(out.spoken) == dates_in(out.reply) == ["13 de outubro de 2026"]
    out = await wa.answer(wa.match("vai chover depois de amanhã?"))
    assert "pouca ou nenhuma chance de chuva em Ouro Fino, MG, no máximo 5%" in out.reply and "guarda-chuva" not in out.reply
    # À noite só restam as horas da noite: 40%.
    clock.now = datetime(2026, 10, 12, 19, 0, tzinfo=TZ).timestamp()
    out = await wa.answer(wa.match("vai chover hoje?"))
    assert out.reply.startswith("Hoje, segunda-feira, 12 de outubro de 2026, pode chover em Ouro Fino, MG: a chance chega a 40%, 40% à noite")
    # Sem probabilidade na fonte: a Luna diz que a fonte não informou, em vez de inventar.
    p = payload()
    p["hourly"]["precipitation_probability"] = [None] * len(p["hourly"]["time"])
    p["daily"]["precipitation_probability_max"] = [None, None, None]
    wa2, src2, _ = make_assistant(tmp_path / "b", FakeSource(p))
    out = await wa2.answer(wa2.match("vai chover hoje?"))
    assert "a fonte não informou a probabilidade" in out.reply and "cerca de 5 mm" in out.reply


@pytest.mark.anyio
async def test_offline_with_cache_tells_the_age_and_flags_old_data(tmp_path):
    wa, src, clock = make_assistant(tmp_path)
    await wa.answer(wa.match("como está o tempo?"))
    src.error = OFFLINE
    clock.advance(2 * 3600)
    out = await wa.answer(wa.match("como está o tempo?"))
    assert out.status == "offline" and not out.fetched and src.calls == 2
    assert out.reply.startswith("Na última leitura, segunda-feira, 12 de outubro de 2026, às 14:30, Ouro Fino, MG tinha 23 graus")  # não afirma o tempo "agora"
    assert "Não consegui acessar a internet. Estou usando os dados salvos, atualizados hoje às 14:32 (há 2 horas)." in out.reply
    assert STALE_WARNING not in out.reply and out.event["status"] == "offline" and out.event["stale"] is False
    entry = wa.cache.get(LAT, LON)
    assert entry.has_data and entry.last_status == "failed" and entry.fetched_at == NOW  # dados preservados
    # Logo em seguida: período de espera, sem martelar a fonte.
    out = await wa.answer(wa.match("vai chover hoje?"))
    assert out.status == "offline" and src.calls == 2 and "dados salvos" in out.reply
    # Cache antigo (> 6 h): aviso explícito.
    clock.advance(6 * 3600)
    out = await wa.answer(wa.match("como estará o tempo amanhã?"))
    assert src.calls == 3 and out.reply.endswith(f"atualizados hoje às 14:32 (há 8 horas). {STALE_WARNING}") and out.event["stale"] is True
    # Dias depois: o cache não cobre "amanhã"; nada é inventado.
    clock.advance(3 * 86400)
    out = await wa.answer(wa.match("vai chover amanhã?"))
    assert out.reply.startswith("Minha previsão salva vai até quarta-feira (14/10); não tenho dados para amanhã.")
    assert "atualizados em 12/10 às 14:32 (há 3 dias)" in out.reply and STALE_WARNING in out.reply
    # A fonte voltou: a resposta atualiza o cache e volta ao normal.
    src.error = None
    src.data = payload(TODAY + timedelta(days=3))
    clock.advance(120)
    out = await wa.answer(wa.match("vai chover hoje?"))
    assert out.status == "online" and out.reply.endswith("Dados atualizados agora, às 22:34.") and wa.cache.get(LAT, LON).last_status == "ok"


@pytest.mark.anyio
async def test_cached_forecast_keeps_its_own_dates_separate_from_the_update_time(tmp_path):
    """Previsão consultada na segunda, perguntada na terça sem internet: "amanhã" é quarta-feira, 14 de outubro (data
    da fonte, inalterada), e a hora da atualização continua sendo a de segunda; os dados são marcados como antigos."""
    wa, src, clock = make_assistant(tmp_path)
    await wa.answer(wa.match("previsão do tempo"))
    src.error = OFFLINE
    clock.now = datetime(2026, 10, 13, 9, 0, tzinfo=TZ).timestamp()  # terça de manhã, 18,5 h depois
    out = await wa.answer(wa.match("como estará o tempo amanhã?"))
    assert out.status == "offline" and out.event["stale"] is True
    assert out.reply.startswith("Amanhã, quarta-feira, 14 de outubro de 2026: sol entre poucas nuvens, mínima de 13 e máxima de 26 graus, chance de chuva de 5%")
    assert "Estou usando os dados salvos, atualizados ontem às 14:32 (há 18 horas)." in out.reply and out.reply.endswith(STALE_WARNING)
    assert "pode não refletir as condições atuais" in STALE_WARNING and dates_in(out.spoken) == dates_in(out.reply) == ["14 de outubro de 2026"]
    out = await wa.answer(wa.match("qual a previsão para hoje?"))
    assert out.reply.startswith("Hoje, terça-feira, 13 de outubro de 2026: chuva fraca, mínima de 14 e máxima de 24 graus, chance de chuva de 60%")
    assert out.event["detail"].startswith("hoje, 13/10 · mín 14 °C · máx 24 °C · chuva 60%") and out.event["updated_at"] == NOW
    # Condições "atuais" do cache: a leitura é identificada pela data e hora da fonte, não por "agora".
    out = await wa.answer(wa.match("como está o tempo?"))
    assert out.reply.startswith("Na última leitura, segunda-feira, 12 de outubro de 2026, às 14:30, Ouro Fino, MG tinha 23 graus")
    assert "Para hoje, terça-feira, 13 de outubro de 2026: chuva fraca" in out.reply and "atualizados ontem às 14:32" in out.reply
    # Perto da meia-noite no fuso local a data continua sendo a local (sem erro de um dia por UTC).
    clock.now = datetime(2026, 10, 13, 23, 30, tzinfo=TZ).timestamp()
    out = await wa.answer(wa.match("qual a previsão para hoje?"))
    assert out.reply.startswith("Hoje, terça-feira, 13 de outubro de 2026:")


@pytest.mark.anyio
async def test_offline_without_cache_says_so_clearly(tmp_path):
    wa, src, clock = make_assistant(tmp_path, FakeSource(error=OFFLINE))
    out = await wa.answer(wa.match("vai chover hoje?"))
    assert out.status == "none" and out.reply == f"Não consegui acessar a internet. {REPLY_NO_DATA_OFFLINE}"
    assert out.event == {**out.event, "available": False, "summary": "", "updated_at": None, "status": "none"}
    entry = wa.cache.get(LAT, LON)
    assert entry is not None and not entry.has_data and entry.last_status == "failed"
    out = await wa.answer(wa.match("você tem uma previsão salva?"))
    assert out.reply.startswith("Não tenho nenhuma previsão salva. A última tentativa de atualizar, hoje às 14:32, falhou: não consegui acessar a internet.")
    out = await wa.answer(wa.match("quando o tempo foi atualizado pela última vez?"))
    assert out.reply.startswith("Não tenho nenhuma previsão salva.") and src.calls == 1  # perguntas sobre o cache não consultam a fonte
    # Sem cache nenhum (arquivo indisponível): só online.
    wa2, src2, _ = make_assistant(tmp_path, FakeSource(error=WeatherError("timeout", "O serviço de previsão do tempo demorou demais para responder.")), cache=False)
    out = await wa2.answer(wa2.match("como está o tempo?"))
    assert out.reply == f"O serviço de previsão do tempo demorou demais para responder. {REPLY_NO_DATA_OFFLINE}"


@pytest.mark.anyio
async def test_invalid_response_and_rate_limit_fall_back_to_cache(tmp_path):
    wa, src, clock = make_assistant(tmp_path)
    await wa.answer(wa.match("como está o tempo?"))
    for error in (
        WeatherError("bad_response", "O serviço de previsão do tempo devolveu uma resposta inválida."),
        WeatherError("rate_limited", "O serviço de previsão do tempo limitou as consultas por agora."),
        WeatherError("http", "O serviço de previsão do tempo respondeu com erro (500)."),
    ):
        src.error = error
        clock.advance(3 * 3600 + 60)  # previsão vencida (3 h), ainda no mesmo dia
        out = await wa.answer(wa.match("como estará o tempo amanhã?"))
        assert out.status == "offline" and error.message in out.reply and "Amanhã, terça-feira, 13 de outubro de 2026: chuva fraca" in out.reply
        assert wa.cache.get(LAT, LON).has_data  # a resposta ruim não substituiu os dados válidos
    # Erro inesperado na fonte vira falha tratada.
    src.error = None
    src.data = "quebrado"  # type: ignore[assignment]
    clock.advance(3 * 3600 + 60)
    out = await wa.answer(wa.match("como está o tempo?"))
    assert out.status == "offline" and "resposta inválida" in out.reply and wa.fetch_failures == 4


@pytest.mark.anyio
async def test_questions_about_the_cache_never_call_the_source(tmp_path):
    wa, src, clock = make_assistant(tmp_path)
    await wa.answer(wa.match("previsão do tempo"))
    clock.advance(90 * 60)
    out = await wa.answer(wa.match("Luna, você tem uma previsão salva?"))
    assert out.reply == "Sim, tenho uma previsão salva de Ouro Fino, MG, atualizada hoje às 14:32 (há 1 hora), cobrindo de hoje até depois de amanhã (quarta-feira)."
    out = await wa.answer(wa.match("quando o tempo foi atualizado pela última vez?"))
    assert out.reply == "A última atualização do tempo de Ouro Fino, MG foi hoje às 14:32, há 1 hora." and src.calls == 1
    assert out.status == "cached_only" and out.event["available"] and out.event["updated_at"] == NOW
    src.error = OFFLINE
    clock.advance(6 * 3600)
    await wa.answer(wa.match("como está o tempo?"))
    out = await wa.answer(wa.match("quando o tempo foi atualizado?"))
    assert out.reply == f"A última atualização do tempo de Ouro Fino, MG foi hoje às 14:32, há 7 horas. Depois disso, a tentativa hoje às 22:02 falhou: não consegui acessar a internet. {STALE_WARNING}"


@pytest.mark.anyio
async def test_cache_persists_across_restart_of_the_assistant(tmp_path):
    wa, src, clock = make_assistant(tmp_path)
    await wa.answer(wa.match("como está o tempo?"))
    wa.cache.close()
    clock.advance(5 * 60)
    again, src2, _ = make_assistant(tmp_path, FakeSource(error=OFFLINE), clock=clock)
    out = await again.answer(again.match("vai chover hoje?"))
    assert out.status == "cache" and src2.calls == 0 and "70% à tarde" in out.reply and out.reply.endswith("Dados atualizados hoje às 14:32.")
    assert again.snapshot()["available"] and again.snapshot()["updated_at"] == NOW


# ---- Integração com a conversa ---------------------------------------------------------------------------------


class FakeSpeaker:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def speak(self, text: str) -> bool:
        self.texts.append(text)
        return False  # sem voz de verdade: a conversa volta a "Pronta" sozinha


def make_conversation(tmp_path, llm=None, source=None, clock=None):
    machine = StateMachine(AssistantState.IDLE)
    replies: list[tuple[str, bool]] = []
    events: list[dict] = []
    seen: list[tuple[str, str]] = []

    async def on_reply(text, final):
        replies.append((text, final))

    async def on_weather(event):
        events.append(event)

    async def spy(state, detail):
        seen.append((state.value, detail))

    machine.subscribe(spy)
    llm = llm if llm is not None else FakeLLM()
    wa, src, clock = make_assistant(tmp_path, source, clock)
    speaker = FakeSpeaker()
    conv = Conversation(
        machine, llm, on_reply=on_reply, done_hold_s=0.05, speaker=speaker,  # type: ignore[arg-type]
        memory=MemoryCommands(MemoryStore(tmp_path / "memory.sqlite3")), weather=wa, on_weather=on_weather,
    )
    return machine, conv, llm, wa, src, speaker, replies, events, seen


@pytest.mark.anyio
async def test_conversation_answers_weather_without_the_llm_and_speaks_the_short_version(tmp_path):
    machine, conv, llm, wa, src, speaker, replies, events, seen = make_conversation(tmp_path)
    assert await conv.ask("Hey Luna, como está o tempo em Ouro Fino?", prompt_id=1) is True
    await settle(conv)
    assert llm.calls == [] and src.calls == 1 and conv.weather_replies == 1 and conv.replies == 0
    assert seen[0] == ("thinking", "Pensando…") and (machine.state, machine.detail) == (AssistantState.IDLE, DETAIL_READY)
    assert len(replies) == 1 and replies[0][1] is True and replies[0][0].startswith("Agora em Ouro Fino, MG, segunda-feira, 12 de outubro de 2026, às 14:30: 23 graus")
    assert dates_in(speaker.texts[0]) == dates_in(replies[0][0]) == ["12 de outubro de 2026", "12 de outubro de 2026"]  # leitura e previsão de hoje
    assert len(events) == 1 and events[0]["type"] == "weather" and events[0]["summary"] == "23 °C · parcialmente nublado"
    assert len(speaker.texts) == 1 and "umidade" not in speaker.texts[0] and speaker.texts[0].endswith("às 14:32.")  # voz curta
    # Perguntas comuns e comandos de memória seguem como antes.
    await conv.ask("como faço arroz?", prompt_id=2)
    await settle(conv)
    assert llm.calls == ["como faço arroz?"]
    await conv.ask("lembre que eu gosto de pimenta", prompt_id=3)
    await settle(conv)
    assert replies[-1][0].startswith("Anotado como preferência") and llm.calls == ["como faço arroz?"]


@pytest.mark.anyio
async def test_conversation_keeps_working_offline_for_other_commands(tmp_path):
    machine, conv, llm, wa, src, speaker, replies, events, seen = make_conversation(tmp_path, source=FakeSource(error=OFFLINE))
    await conv.ask("vai chover hoje?", prompt_id=1)
    await settle(conv)
    assert replies[-1][0] == f"Não consegui acessar a internet. {REPLY_NO_DATA_OFFLINE}"
    assert events[-1]["available"] is False and conv.failures == 0 and conv.last_error is None  # nao e falha da IA
    assert (machine.state, machine.detail) == (AssistantState.IDLE, DETAIL_READY)
    # A IA local, a memória e os ingredientes não dependem da internet.
    await conv.ask("como faço arroz?", prompt_id=2)
    await settle(conv)
    assert llm.calls == ["como faço arroz?"] and replies[-1] == ("Claro! Posso ajudar.", True)
    await conv.ask("o que você lembra?", prompt_id=3)
    await settle(conv)
    assert replies[-1][0].startswith("Ainda não tenho nenhuma memória guardada")
    assert conv.weather_replies == 1 and wa.fetch_failures == 1


@pytest.mark.anyio
async def test_conversation_weather_can_be_cancelled_and_disabled(tmp_path):
    slow = threading.Event()

    class SlowSource(FakeSource):
        def fetch(self):
            slow.wait(2.0)
            return super().fetch()

    machine, conv, llm, wa, src, speaker, replies, events, seen = make_conversation(tmp_path, source=SlowSource())
    await conv.ask("como está o tempo?", prompt_id=1)
    assert conv.busy and machine.state == AssistantState.THINKING
    assert await conv.cancel() is True
    slow.set()
    await asyncio.sleep(0.05)
    assert not conv.busy and replies == [] and machine.detail == "Cancelado"
    # Sem o assistente do tempo, a pergunta vai para a IA (que diz não ter acesso à previsão).
    conv.weather = None
    await conv.ask("como está o tempo?", prompt_id=2)
    await settle(conv)
    assert llm.calls == ["como está o tempo?"]


# ---- WebSocket e configuração ------------------------------------------------------------------------------------


def test_weather_voice_command_over_websocket_updates_everyone(tmp_path):
    llm = FakeLLM()
    source = FakeSource()
    app = create_app(
        settings_for(tmp_path), detector=FakeDetector(), transcriber=FakeTranscriber("Luna, vai chover hoje?"), llm=llm, weather_source=source
    )
    with TestClient(app) as c, c.websocket_connect("/ws") as ws, c.websocket_connect("/ws") as other:
        w = hello(ws)
        hello(other)
        assert w["weather"]["enabled"] is True and w["weather"]["available"] is False and w["weather"]["location"] == NAME
        assert w["weather"]["source"] == "fonte-falsa" and w["weather"]["updated_at"] is None
        ws.send_bytes(wake_frame() + speech(6))
        ws.send_bytes(silence(20))
        msgs = collect_until(ws, lambda m: m["type"] == "state" and m["state"] == "idle" and m["detail"] == DETAIL_READY)
        replies = [m for m in msgs if m["type"] == "reply"]
        assert len(replies) == 1 and replies[0]["final"] is True and "a chance chega a" in replies[0]["text"]  # o percentual depende da hora real
        weather = [m for m in msgs if m["type"] == "weather"]
        assert len(weather) == 1 and weather[0]["available"] and weather[0]["status"] == "online" and weather[0]["summary"] == "23 °C · parcialmente nublado"
        assert weather[0]["updated_at"] is not None and weather[0]["stale"] is False and weather[0]["source"] == "fonte-falsa"
        seen = collect_until(other, lambda m: m["type"] == "weather")  # o outro quiosque também atualiza o cartão
        assert seen[-1]["summary"] == "23 °C · parcialmente nublado"
        assert llm.calls == [] and source.calls == 1
        h = c.get("/health").json()
        assert h["weather"]["available"] and h["weather"]["questions"] == 1 and h["weather"]["fetches"] == 1 and h["weather_replies"] == 1
        assert h["weather"]["cache_path"] == str(tmp_path / "memory.sqlite3") and h["weather"]["timezone"] == TZ_NAME
        assert h["weather"]["current_ttl_s"] == 1800.0 and h["weather"]["forecast_ttl_s"] == 10800.0 and h["memory"]["available"] is True
    # Reinício do servidor: o welcome já traz o cache, sem consultar a fonte.
    app = create_app(settings_for(tmp_path), detector=FakeDetector(), transcriber=FakeTranscriber("x"), llm=llm, weather_source=FakeSource(error=OFFLINE))
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        w = hello(ws)
        assert w["weather"]["available"] is True and w["weather"]["summary"] == "23 °C · parcialmente nublado" and w["weather"]["status"] == "cache"


def test_weather_disabled_sends_weather_questions_to_llm(tmp_path):
    llm = FakeLLM()
    app = create_app(
        settings_for(tmp_path, weather_enabled=False), detector=FakeDetector(), transcriber=FakeTranscriber("vai chover hoje?"), llm=llm
    )
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        w = hello(ws)
        assert w["weather"] == {"enabled": False, "available": False, "reason": "previsão do tempo desativada (weather.enabled = false)", "location": NAME}
        ws.send_bytes(wake_frame() + speech(6))
        ws.send_bytes(silence(20))
        collect_until(ws, lambda m: m["type"] == "state" and m["state"] == "idle" and m["detail"] == DETAIL_READY)
        assert llm.calls == ["vai chover hoje?"]
        assert c.get("/health").json()["weather"]["enabled"] is False


def test_weather_cache_open_failure_keeps_server_running_online_only(tmp_path):
    (tmp_path / "bloqueado").mkdir()
    settings = settings_for(tmp_path, weather_cache_path=tmp_path / "bloqueado")
    app = create_app(settings, detector=FakeDetector(), transcriber=FakeTranscriber("como está o tempo?"), llm=FakeLLM(), weather_source=FakeSource())
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        w = hello(ws)
        assert w["weather"]["enabled"] and w["weather"]["cache"] is False
        ws.send_bytes(wake_frame() + speech(6))
        ws.send_bytes(silence(20))
        msgs = collect_until(ws, lambda m: m["type"] == "state" and m["state"] == "idle" and m["detail"] == DETAIL_READY)
        assert [m for m in msgs if m["type"] == "reply"][0]["text"].startswith("Agora em Ouro Fino, MG")
        assert c.get("/health").json()["weather"]["cache_path"] == ""


def test_settings_weather_from_toml_and_validation(tmp_path):
    from server.storage.config import PROJECT_ROOT

    p = tmp_path / "s.toml"
    p.write_text(
        f'[auth]\ntoken="{TOKEN}"\n[weather]\nenabled=true\nlatitude=-22.3\nlongitude=-46.4\nlocation_name="Ouro Fino"\n'
        'timezone="America/Sao_Paulo"\nforecast_days=3\ncurrent_ttl_s=600\nforecast_ttl_s=7200\nstale_after_s=3600\nretry_cooldown_s=0\ncache_path="data/clima.sqlite3"\n',
        encoding="utf-8",
    )
    s = Settings.from_toml(p)
    assert (s.weather_enabled, s.weather_latitude, s.weather_longitude, s.weather_location_name) == (True, -22.3, -46.4, "Ouro Fino")
    assert (s.weather_forecast_days, s.weather_current_ttl_s, s.weather_forecast_ttl_s, s.weather_stale_after_s, s.weather_retry_cooldown_s) == (3, 600.0, 7200.0, 3600.0, 0.0)
    assert s.weather_cache_file == PROJECT_ROOT / "data" / "clima.sqlite3"
    s.validate()
    d = Settings(auth_token=TOKEN)
    assert (d.weather_enabled, d.weather_latitude, d.weather_longitude, d.weather_timezone) == (True, -22.28306, -46.36889, "America/Sao_Paulo")
    assert (d.weather_forecast_days, d.weather_current_ttl_s, d.weather_forecast_ttl_s, d.weather_stale_after_s, d.weather_retry_cooldown_s) == (5, 1800.0, 10800.0, 21600.0, 60.0)
    assert d.weather_cache_file == d.memory_path  # mesmo banco da memoria
    assert Settings(auth_token=TOKEN, memory_enabled=False).weather_cache_file == PROJECT_ROOT / "data" / "weather.sqlite3"
    d.validate()
    for field, value, match in (
        ("weather_latitude", 95.0, "latitude"),
        ("weather_timezone", "SaoPaulo", "timezone"),
        ("weather_forecast_days", 0, "forecast_days"),
        ("weather_timeout_s", 0, "timeout_s"),
        ("weather_current_ttl_s", 0, "ttl"),
        ("weather_retry_cooldown_s", -1, "retry_cooldown_s"),
        ("weather_location_name", " ", "location_name"),
    ):
        with pytest.raises(ConfigError, match=match):
            Settings(auth_token=TOKEN, **{field: value}).validate()
    Settings(auth_token=TOKEN, weather_enabled=False, weather_latitude=95.0).validate()  # desativada: nao valida
