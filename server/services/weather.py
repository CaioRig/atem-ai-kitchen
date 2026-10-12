"""Fonte meteorológica: Open-Meteo (https://open-meteo.com), gratuita para uso não comercial, sem chave.

Só a biblioteca padrão (http.client), como o cliente do Ollama. Uma única requisição traz as condições
atuais, a previsão por hora (probabilidade e volume de chuva, temperatura, código do tempo) e a previsão
diária para os próximos dias, já no fuso `America/Sao_Paulo`, em °C, mm e km/h. Nada além das coordenadas
configuradas sai desta máquina: nenhuma conversa, memória ou áudio vai ao provedor.

Os dados são da licença CC BY 4.0 da Open-Meteo (atribuição: "Dados meteorológicos da Open-Meteo.com").
Limites do plano gratuito (2026-10): 600 chamadas/minuto, 5 000/hora, 10 000/dia; a Luna consulta só
quando alguém pergunta e o cache ainda não vale (`server/assistant/weather.py`).
"""
from __future__ import annotations

import http.client
import json
import logging
import socket
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Protocol
from urllib.parse import urlencode

log = logging.getLogger("jarvis.weather")

API_HOST = "api.open-meteo.com"
API_PATH = "/v1/forecast"
SOURCE_NAME = "Open-Meteo"
ATTRIBUTION = "Dados meteorológicos da Open-Meteo.com (CC BY 4.0)"

# Ouro Fino, MG (geocodificação da Open-Meteo, 2026-10-12: município, 32 094 habitantes, 924 m).
DEFAULT_LATITUDE = -22.28306
DEFAULT_LONGITUDE = -46.36889
DEFAULT_TIMEZONE = "America/Sao_Paulo"
DEFAULT_LOCATION_NAME = "Ouro Fino, MG"
DEFAULT_UTC_OFFSET_S = -3 * 3600  # Brasil sem horário de verão desde 2019; usado se o fuso não estiver instalado
MAX_FORECAST_DAYS = 16
MAX_BODY_BYTES = 2_000_000

CURRENT_VARS = ("temperature_2m", "relative_humidity_2m", "apparent_temperature", "is_day", "precipitation", "weather_code", "wind_speed_10m")
HOURLY_VARS = ("temperature_2m", "precipitation_probability", "precipitation", "weather_code")
DAILY_VARS = (
    "weather_code", "temperature_2m_max", "temperature_2m_min", "precipitation_sum", "precipitation_probability_max",
    "wind_speed_10m_max", "sunrise", "sunset",
)

# Códigos WMO 4677 usados pela Open-Meteo. (descrição, categoria) — a categoria agrupa para as frases curtas.
WEATHER_CODES: dict[int, tuple[str, str]] = {
    0: ("céu limpo", "clear"),
    1: ("predominantemente limpo", "clear"),
    2: ("parcialmente nublado", "clouds"),
    3: ("nublado", "clouds"),
    45: ("nevoeiro", "fog"),
    48: ("nevoeiro com geada", "fog"),
    51: ("garoa fraca", "drizzle"),
    53: ("garoa moderada", "drizzle"),
    55: ("garoa forte", "drizzle"),
    56: ("garoa congelante fraca", "drizzle"),
    57: ("garoa congelante forte", "drizzle"),
    61: ("chuva fraca", "rain"),
    63: ("chuva moderada", "rain"),
    65: ("chuva forte", "rain"),
    66: ("chuva congelante fraca", "rain"),
    67: ("chuva congelante forte", "rain"),
    71: ("neve fraca", "snow"),
    73: ("neve moderada", "snow"),
    75: ("neve forte", "snow"),
    77: ("grãos de neve", "snow"),
    80: ("pancadas de chuva fracas", "showers"),
    81: ("pancadas de chuva moderadas", "showers"),
    82: ("pancadas de chuva fortes", "showers"),
    85: ("pancadas de neve fracas", "snow"),
    86: ("pancadas de neve fortes", "snow"),
    95: ("tempestade", "storm"),
    96: ("tempestade com granizo fraco", "storm"),
    97: ("tempestade forte", "storm"),
    99: ("tempestade com granizo forte", "storm"),
}
RAIN_CATEGORIES = frozenset({"drizzle", "rain", "showers", "storm"})


class WeatherError(RuntimeError):
    """Falha ao obter a previsão. `code` identifica o tipo; `message` é amigável, em pt-BR."""

    def __init__(self, code: str, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.code = code  # offline | unreachable | timeout | rate_limited | http | bad_response
        self.message = message
        self.detail = detail  # informação técnica só para o log


def describe_code(code: int | None, is_day: bool | None = None) -> str:
    """Descrição em pt-BR de um código WMO. À noite, "céu limpo" vira "céu limpo, sem nuvens" — não há sol."""
    if code is None:
        return "condição desconhecida"
    entry = WEATHER_CODES.get(int(code))
    if entry is None:
        return f"condição desconhecida (código {code})"
    text = entry[0]
    if is_day is True and entry[1] == "clear":
        return "sol" if int(code) == 0 else "sol entre poucas nuvens"
    return text


def code_category(code: int | None) -> str:
    entry = WEATHER_CODES.get(int(code)) if code is not None else None
    return entry[1] if entry else "unknown"


def is_rainy_code(code: int | None) -> bool:
    return code_category(code) in RAIN_CATEGORIES


@dataclass
class CurrentConditions:
    time: str  # hora local (ISO, sem deslocamento) a que a observação/modelo se refere
    temperature_c: float | None
    apparent_c: float | None
    humidity_pct: float | None
    precipitation_mm: float | None
    weather_code: int | None
    wind_kmh: float | None
    is_day: bool | None


@dataclass
class HourlyPoint:
    time: str  # "AAAA-MM-DDTHH:MM" local
    temperature_c: float | None
    precipitation_probability: float | None  # % de chance de > 0,1 mm na hora anterior
    precipitation_mm: float | None
    weather_code: int | None


@dataclass
class DailyForecast:
    date: str  # "AAAA-MM-DD" local
    weather_code: int | None  # condição mais severa do dia
    temperature_max_c: float | None
    temperature_min_c: float | None
    precipitation_sum_mm: float | None
    precipitation_probability_max: float | None
    wind_max_kmh: float | None
    sunrise: str | None
    sunset: str | None


@dataclass
class WeatherData:
    """Resposta normalizada da fonte. Horários locais (fuso configurado), °C, mm e km/h."""

    location_name: str
    latitude: float
    longitude: float
    elevation_m: float | None
    timezone: str
    utc_offset_s: int
    fetched_at: float  # epoch UTC do momento da consulta à fonte (não é a hora da previsão)
    current: CurrentConditions | None
    hourly: list[HourlyPoint] = field(default_factory=list)
    daily: list[DailyForecast] = field(default_factory=list)
    source: str = SOURCE_NAME

    @property
    def period_start(self) -> str:
        return self.daily[0].date if self.daily else ""

    @property
    def period_end(self) -> str:
        return self.daily[-1].date if self.daily else ""

    def day(self, date: str) -> DailyForecast | None:
        return next((d for d in self.daily if d.date == date), None)

    def hours_of(self, date: str) -> list[HourlyPoint]:
        return [h for h in self.hourly if h.time.startswith(date)]

    def tz(self) -> tzinfo:
        return local_timezone(self.timezone, self.utc_offset_s)

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "WeatherData":
        current = data.get("current")
        return cls(
            location_name=str(data["location_name"]),
            latitude=float(data["latitude"]),
            longitude=float(data["longitude"]),
            elevation_m=_opt_float(data.get("elevation_m")),
            timezone=str(data.get("timezone") or DEFAULT_TIMEZONE),
            utc_offset_s=int(data.get("utc_offset_s", DEFAULT_UTC_OFFSET_S)),
            fetched_at=float(data["fetched_at"]),
            current=CurrentConditions(**current) if isinstance(current, dict) else None,
            hourly=[HourlyPoint(**h) for h in data.get("hourly", [])],
            daily=[DailyForecast(**d) for d in data.get("daily", [])],
            source=str(data.get("source") or SOURCE_NAME),
        )


def local_timezone(name: str, utc_offset_s: int = DEFAULT_UTC_OFFSET_S) -> tzinfo:
    """Fuso para converter a hora da consulta. Usa o banco de fusos do sistema quando existe (no Windows exige o
    pacote `tzdata`); senão, o deslocamento informado pela própria fonte (fixo: o Brasil não tem horário de verão)."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # ZoneInfoNotFoundError, fuso invalido, banco ausente
        return timezone(timedelta(seconds=utc_offset_s), name)


class WeatherSource(Protocol):
    name: str

    def fetch(self) -> WeatherData:
        """Consulta a fonte. Levanta WeatherError em qualquer falha (rede, HTTP, resposta inválida)."""
        ...


class OpenMeteoClient:
    """Cliente mínimo da API /v1/forecast, sem estado. `host`/`port`/`secure` são injetáveis para testes."""

    name = SOURCE_NAME

    def __init__(
        self,
        latitude: float = DEFAULT_LATITUDE,
        longitude: float = DEFAULT_LONGITUDE,
        *,
        location_name: str = DEFAULT_LOCATION_NAME,
        tz_name: str = DEFAULT_TIMEZONE,
        forecast_days: int = 5,
        connect_timeout_s: float = 5.0,
        timeout_s: float = 10.0,
        host: str = API_HOST,
        port: int | None = None,
        secure: bool = True,
        clock=time.time,
    ) -> None:
        if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
            raise ValueError("coordenadas fora do intervalo válido")
        self.latitude = latitude
        self.longitude = longitude
        self.location_name = location_name
        self.tz_name = tz_name
        self.forecast_days = max(1, min(MAX_FORECAST_DAYS, int(forecast_days)))
        self.connect_timeout_s = connect_timeout_s
        self.timeout_s = timeout_s
        self.host = host
        self.secure = secure
        self.port = port or (443 if secure else 80)
        self._clock = clock
        self.requests = 0
        self.failures = 0

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    def query(self) -> str:
        params = {
            "latitude": f"{self.latitude:.5f}",
            "longitude": f"{self.longitude:.5f}",
            "timezone": self.tz_name,
            "forecast_days": self.forecast_days,
            "current": ",".join(CURRENT_VARS),
            "hourly": ",".join(HOURLY_VARS),
            "daily": ",".join(DAILY_VARS),
            "temperature_unit": "celsius",
            "wind_speed_unit": "kmh",
            "precipitation_unit": "mm",
            "timeformat": "iso8601",
        }
        return f"{API_PATH}?{urlencode(params)}"

    def fetch(self) -> WeatherData:
        self.requests += 1
        try:
            return self._fetch()
        except WeatherError:
            self.failures += 1
            raise

    def _fetch(self) -> WeatherData:
        cls = http.client.HTTPSConnection if self.secure else http.client.HTTPConnection
        conn = cls(self.host, self.port, timeout=self.connect_timeout_s)
        try:
            try:
                conn.connect()
            except socket.gaierror as exc:  # DNS: quase sempre significa "sem internet"
                raise WeatherError("offline", "Não consegui acessar a internet.", f"DNS: {exc}") from exc
            except socket.timeout as exc:
                raise WeatherError("unreachable", "O serviço de previsão do tempo não respondeu.", f"timeout ao conectar: {exc}") from exc
            except OSError as exc:
                raise WeatherError("offline", "Não consegui acessar a internet.", f"{type(exc).__name__}: {exc}") from exc
            assert conn.sock is not None
            conn.sock.settimeout(self.timeout_s)
            try:
                conn.request("GET", self.query(), headers={"Accept": "application/json", "User-Agent": "luna-kitchen/weather"})
                resp = conn.getresponse()
                body = resp.read(MAX_BODY_BYTES)
            except socket.timeout as exc:
                raise WeatherError("timeout", "O serviço de previsão do tempo demorou demais para responder.", str(exc)) from exc
            except (OSError, http.client.HTTPException) as exc:
                raise WeatherError("unreachable", "A conexão com o serviço de previsão do tempo caiu.", f"{type(exc).__name__}: {exc}") from exc
        finally:
            conn.close()
        if resp.status == 429:
            raise WeatherError("rate_limited", "O serviço de previsão do tempo limitou as consultas por agora.", body[:200].decode("utf-8", "replace"))
        if resp.status != 200:
            detail = body[:300].decode("utf-8", "replace")
            reason = ""
            try:
                reason = str(json.loads(detail).get("reason", ""))
            except ValueError:
                pass
            raise WeatherError("http", f"O serviço de previsão do tempo respondeu com erro ({resp.status}).", reason or detail)
        try:
            obj = json.loads(body)
        except ValueError as exc:
            raise WeatherError("bad_response", "O serviço de previsão do tempo devolveu uma resposta inválida.", body[:200].decode("utf-8", "replace")) from exc
        return parse_response(obj, location_name=self.location_name, fetched_at=self._clock(), tz_name=self.tz_name)


def parse_response(obj: object, *, location_name: str, fetched_at: float, tz_name: str = DEFAULT_TIMEZONE) -> WeatherData:
    """Converte o JSON da Open-Meteo em `WeatherData`. Qualquer campo essencial ausente ou com tipo errado vira
    WeatherError("bad_response"): nada é inventado para preencher lacunas."""
    if not isinstance(obj, dict):
        raise WeatherError("bad_response", "O serviço de previsão do tempo devolveu uma resposta inválida.", f"tipo {type(obj).__name__}")
    if obj.get("error"):
        raise WeatherError("http", "O serviço de previsão do tempo recusou a consulta.", str(obj.get("reason", ""))[:300])
    try:
        latitude = float(obj["latitude"])
        longitude = float(obj["longitude"])
    except (KeyError, TypeError, ValueError) as exc:
        raise WeatherError("bad_response", "O serviço de previsão do tempo devolveu uma resposta inválida.", f"coordenadas: {exc!r}") from exc
    utc_offset = obj.get("utc_offset_seconds", DEFAULT_UTC_OFFSET_S)
    utc_offset_s = int(utc_offset) if isinstance(utc_offset, (int, float)) else DEFAULT_UTC_OFFSET_S
    current = _parse_current(obj.get("current"))
    hourly = _parse_series(obj.get("hourly"), HOURLY_VARS, "hourly")
    daily = _parse_series(obj.get("daily"), DAILY_VARS, "daily")
    if current is None and not daily:
        raise WeatherError("bad_response", "O serviço de previsão do tempo devolveu uma resposta sem dados.", "sem current nem daily")
    return WeatherData(
        location_name=location_name,
        latitude=latitude,
        longitude=longitude,
        elevation_m=_opt_float(obj.get("elevation")),
        timezone=str(obj.get("timezone") or tz_name),
        utc_offset_s=utc_offset_s,
        fetched_at=float(fetched_at),
        current=current,
        hourly=[
            HourlyPoint(
                time=row["time"],
                temperature_c=_opt_float(row.get("temperature_2m")),
                precipitation_probability=_opt_float(row.get("precipitation_probability")),
                precipitation_mm=_opt_float(row.get("precipitation")),
                weather_code=_opt_int(row.get("weather_code")),
            )
            for row in hourly
        ],
        daily=[
            DailyForecast(
                date=row["time"][:10],
                weather_code=_opt_int(row.get("weather_code")),
                temperature_max_c=_opt_float(row.get("temperature_2m_max")),
                temperature_min_c=_opt_float(row.get("temperature_2m_min")),
                precipitation_sum_mm=_opt_float(row.get("precipitation_sum")),
                precipitation_probability_max=_opt_float(row.get("precipitation_probability_max")),
                wind_max_kmh=_opt_float(row.get("wind_speed_10m_max")),
                sunrise=_opt_str(row.get("sunrise")),
                sunset=_opt_str(row.get("sunset")),
            )
            for row in daily
        ],
    )


def _parse_current(block: object) -> CurrentConditions | None:
    if not isinstance(block, dict):
        return None
    when = block.get("time")
    if not isinstance(when, str) or len(when) < 13:
        raise WeatherError("bad_response", "O serviço de previsão do tempo devolveu uma resposta inválida.", "current.time ausente")
    is_day = block.get("is_day")
    return CurrentConditions(
        time=when,
        temperature_c=_opt_float(block.get("temperature_2m")),
        apparent_c=_opt_float(block.get("apparent_temperature")),
        humidity_pct=_opt_float(block.get("relative_humidity_2m")),
        precipitation_mm=_opt_float(block.get("precipitation")),
        weather_code=_opt_int(block.get("weather_code")),
        wind_kmh=_opt_float(block.get("wind_speed_10m")),
        is_day=bool(is_day) if isinstance(is_day, (int, float, bool)) else None,
    )


def _parse_series(block: object, keys: tuple[str, ...], label: str) -> list[dict]:
    """Transpõe as listas paralelas da Open-Meteo ({"time": [...], "x": [...]}) em uma lista de linhas."""
    if block is None:
        return []
    if not isinstance(block, dict) or not isinstance(block.get("time"), list):
        raise WeatherError("bad_response", "O serviço de previsão do tempo devolveu uma resposta inválida.", f"{label}.time ausente")
    times = block["time"]
    rows: list[dict] = []
    for i, when in enumerate(times):
        if not isinstance(when, str) or len(when) < 10:
            raise WeatherError("bad_response", "O serviço de previsão do tempo devolveu uma resposta inválida.", f"{label}.time[{i}] inválido")
        row: dict = {"time": when}
        for key in keys:
            values = block.get(key)
            if isinstance(values, list) and i < len(values):
                row[key] = values[i]
        rows.append(row)
    return rows


def _opt_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _opt_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def local_now(data: WeatherData | None, now: float, tz_name: str = DEFAULT_TIMEZONE) -> datetime:
    """`now` (epoch) no fuso dos dados (ou no fuso configurado, sem dados)."""
    tz = data.tz() if data is not None else local_timezone(tz_name)
    return datetime.fromtimestamp(now, tz)


__all__ = [
    "ATTRIBUTION",
    "DEFAULT_LATITUDE",
    "DEFAULT_LOCATION_NAME",
    "DEFAULT_LONGITUDE",
    "DEFAULT_TIMEZONE",
    "DEFAULT_UTC_OFFSET_S",
    "SOURCE_NAME",
    "WEATHER_CODES",
    "CurrentConditions",
    "DailyForecast",
    "HourlyPoint",
    "OpenMeteoClient",
    "WeatherData",
    "WeatherError",
    "WeatherSource",
    "code_category",
    "describe_code",
    "is_rainy_code",
    "local_now",
    "local_timezone",
    "parse_response",
]
