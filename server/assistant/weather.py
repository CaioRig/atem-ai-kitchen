"""Previsão do tempo por voz (Fase 8): perguntas em pt-BR, cache local e funcionamento offline.

Nada aqui passa pela IA: a pergunta é reconhecida por regras determinísticas (`parse`) e respondida com
os dados da fonte (`server/services/weather.py`) ou do cache persistente (`server/storage/weather.py`).
A fonte só é consultada quando alguém pergunta e o cache já não vale (validades distintas para condições
atuais e previsão). Se a consulta falha (sem internet, serviço fora, resposta inválida), a Luna usa o que
tem guardado, diz sempre de quando são os dados e avisa quando estão antigos; sem nada guardado, diz que
não consegue informar o tempo sem internet. Probabilidade de chuva nunca vira certeza.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from server.assistant.memory import strip_polite_prefix, strip_wake_prefix
from server.services.weather import (
    ATTRIBUTION,
    SOURCE_NAME,
    DailyForecast,
    HourlyPoint,
    WeatherData,
    WeatherError,
    WeatherSource,
    describe_code,
    is_rainy_code,
    local_timezone,
)
from server.storage.memory import fold
from server.storage.weather import CacheEntry, WeatherCache, WeatherCacheError

log = logging.getLogger("jarvis.weather")

DEFAULT_CURRENT_TTL_S = 1800.0
DEFAULT_FORECAST_TTL_S = 10800.0
DEFAULT_STALE_AFTER_S = 21600.0
DEFAULT_RETRY_COOLDOWN_S = 60.0
MAX_SPOKEN_DAYS = 3  # acima disso, a fala resume e aponta para a tela
DEFAULT_NEXT_DAYS = 3  # "previsão para os próximos dias" sem número

REPLY_NO_DATA_OFFLINE = "Não tenho nenhuma previsão salva, então não consigo informar o tempo sem internet."
REPLY_DISABLED = "A previsão do tempo não está ativada neste servidor."
STALE_WARNING = "Atenção: esses dados estão desatualizados e a previsão pode não refletir as condições atuais."
NOT_CERTAIN = "Previsão é estimativa, não certeza."

WEEKDAYS = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo")
MONTHS = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro")
_WEEKDAY_WORDS = {
    "segunda": 0, "terca": 1, "quarta": 2, "quinta": 3, "sexta": 4, "sabado": 5, "domingo": 6,
}
_NUMBER_WORDS = {"dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7}
PERIODS = (("madrugada", 0, 5, "de madrugada"), ("manhã", 6, 11, "de manhã"), ("tarde", 12, 17, "à tarde"), ("noite", 18, 23, "à noite"))

# Reconhecimento (texto "dobrado": minúsculas, sem acentos). Só frases claramente sobre o tempo/clima;
# "quanto tempo leva o arroz" ou "tempo de forno" nunca casam.
_WEATHER_WORD = r"(?:tempo|clima)"
_PLACE = r"(?:\s+(?:em|de|la em|aqui em|na cidade de)\s+ouro\s*fino(?:\s+(?:mg|minas|minas gerais))?|\s+la fora|\s+aqui|\s+ai fora|\s+na rua)?"
_CACHED = re.compile(
    r"\b(?:tem|tens|voce tem|guardou|salvou|possui|existe|ha|ainda tem)\b.*\bprevisao\b.*\b(?:salva|guardada|armazenada|em cache|no cache|offline|antiga|anterior)\b|"
    r"\b(?:tem|tens|voce tem|guardou|salvou|possui|existe|ha)\b.*\b(?:dados|informacoes|informacao)\b.*\b(?:do tempo|do clima|meteorologic\w*)\b.*\b(?:salv\w*|guardad\w*|armazenad\w*|em cache|no cache|offline)\b|"
    r"\bprevisao (?:salva|guardada|armazenada|em cache|offline)\b|\b(?:tem|voce tem) (?:alguma )?previsao (?:do tempo )?(?:salva|guardada)\b"
)
_LAST_UPDATE = re.compile(
    r"\bquando\b.*\b(?:tempo|previsao|clima|dados do tempo|dados meteorologicos)\b.*\b(?:atualizad\w*|atualizou|atualizaram|consultad\w*|baixad\w*)\b|"
    r"\b(?:ultima|a ultima) (?:atualizacao|consulta)\b.*\b(?:tempo|previsao|clima)\b|"
    r"\b(?:de quando|de que horas|ha quanto tempo|faz quanto tempo)\b.*\b(?:previsao|dados do tempo|dados do clima|dados meteorologicos|essa previsao|a previsao)\b|"
    r"\bquando (?:voce )?(?:atualizou|consultou|baixou|pegou) (?:o tempo|a previsao|o clima|os dados do tempo)\b|"
    r"\b(?:previsao|tempo|clima)\b.*\b(?:atualizad[oa]|consultad[oa])\b.*\b(?:pela ultima vez|por ultimo|quando)\b"
)
_RAIN = re.compile(
    r"\b(?:vai|ira|deve|pode|podera|tem chance de|chance de|chances de|probabilidade de|risco de|previsao de|possibilidade de|sera que vai|sera que)\s*"
    r"(?:chover|chuva|chuvisc\w*|garoar|garoa|tempestade|temporal|trovoada|trovejar|cair (?:chuva|agua|um temporal|uma tempestade))\b|"
    r"\bchove\b|\bvai cair (?:chuva|agua)\b|\bvai ter (?:chuva|temporal|tempestade|trovoada)\b|\b(?:precis[oa]|preciso levar|levo|levar|pegar|pego) (?:de |o |um )?guarda[- ]?chuva\b|"
    r"\b(?:esta|ta|tá) chovendo\b|\bchuva (?:hoje|amanha|para hoje|para amanha|pra hoje|pra amanha|no fim de semana|nos proximos dias)\b|\bquanto vai chover\b|\bvai chuviscar\b"
)
_CURRENT = re.compile(
    rf"\bcomo (?:esta|ta|tá|anda|se encontra|e que esta|e que ta|que esta|que ta) o {_WEATHER_WORD}{_PLACE}\b(?!.*\b(?:amanha|depois de amanha|semana|proximos?|fim de semana|segunda|terca|quarta|quinta|sexta|sabado|domingo)\b)|"
    rf"\b(?:que|qual) {_WEATHER_WORD} (?:faz|esta fazendo|ta fazendo|esta|ta)\b|\bo {_WEATHER_WORD} (?:agora|neste momento|nesse momento|la fora|em ouro fino)\b|"
    r"\b(?:qual|quantos graus|quanto)\b.*\btemperatura\b(?!.*\b(?:amanha|semana|proximos?)\b)|\bquantos graus (?:faz|esta|ta|estao|tem)\b|\bque temperatura (?:faz|esta|ta|tem)\b|"
    r"\b(?:esta|ta|tá|faz|anda) (?:muito |bem |um pouco )?(?:frio|calor|quente|gelado|abafado|ventando|nublado|garoando|ensolarado|sol|chuvoso|umido)\b(?!.*\b(?:amanha|semana|proximos?)\b)|"
    r"\bclima (?:de hoje|agora|em ouro fino|atual|la fora)\b|\btempo (?:de )?agora\b|\bcondicoes (?:do tempo|climaticas|meteorologicas)\b|\bcomo esta o dia\b|\bcomo esta la fora\b"
)
_FORECAST = re.compile(
    r"\bprevisao\b|\bcomo (?:vai (?:estar|ficar|ser|amanhecer)|estara|ficara|sera|fica|vai ta|vai estar|tá|ta) o (?:tempo|clima|dia)\b|"
    r"\bvai (?:fazer|ter|estar|ficar|esfriar|esquentar|ventar|nevar|abrir|limpar)\b.*\b(?:sol|frio|calor|quente|nublado|tempo|vento|neve|hoje|amanha|semana)\b|"
    r"\bvai (?:esfriar|esquentar|ventar|nevar)\b|\btemperatura (?:de |para |pra )?(?:amanha|hoje|da semana|dos proximos dias)\b|\b(?:maxima|minima)\b.*\b(?:hoje|amanha|semana)\b|"
    r"\b(?:tempo|clima)\b.*\b(?:amanha|depois de amanha|proximos? \w+ dias|proximos dias|fim de semana|semana que vem|essa semana|nesta semana|segunda|terca|quarta|quinta|sexta|sabado|domingo)\b|"
    r"\b(?:amanha|depois de amanha)\b.*\b(?:tempo|clima|frio|calor|sol|chuva|nublado)\b|\bque dia (?:vai fazer|faz) (?:hoje|amanha)\b"
)
_NOT_WEATHER = re.compile(
    r"\bquanto tempo\b|\btempo de (?:cozimento|forno|preparo|descanso|fermentacao|cozinhar|assar|fritar|espera)\b|\bpor quanto tempo\b|\btempo (?:leva|demora|dura)\b|"
    r"\b(?:receita|cozinhar|cozinho|assar|fritar|ingrediente|forno|panela|fogao|lembre|lembra|anote|esqueca|apague)\b|\bclima (?:de festa|de natal|agradavel|gostoso|bom entre|na casa|em casa|familiar)\b|"
    r"\btemperatura (?:do forno|do oleo|da agua|do leite|da carne|do frango|ambiente|interna|de cozimento|ideal para)\b|\bsol (?:a sol|nascente)\b|\bcomo esta o tempo de (?:cozimento|forno)\b"
)
_TOMORROW = re.compile(r"\bamanha\b")
_AFTER_TOMORROW = re.compile(r"\bdepois de amanha\b")
_TODAY = re.compile(r"\bhoje\b|\bhoje a noite\b|\bessa noite\b|\besta noite\b|\bhoje a tarde\b|\bagora\b")
_WEEKEND = re.compile(r"\bfi(?:m|nal) de semana\b")
_WEEK = re.compile(r"\b(?:semana|semana que vem|essa semana|nesta semana|proxima semana)\b")
_NEXT_DAYS = re.compile(r"\bproxim[oa]s\s+(?P<n>\d+|dois|duas|tres|quatro|cinco|seis|sete)?\s*dias\b")
_WEEKDAY = re.compile(r"\b(?:na|no|nesta|neste|nessa|nesse|proxima|proximo|para|pra|em)?\s*(?P<day>segunda|terca|quarta|quinta|sexta|sabado|domingo)(?:[- ]feira)?\b")


@dataclass
class WeatherIntent:
    name: str  # current | rain | forecast | cached | last_update
    offsets: list[int] = field(default_factory=lambda: [0])  # dias a partir de hoje (0 = hoje)
    explicit_day: bool = False  # a pessoa nomeou o dia ("amanhã", "quinta", "próximos três dias")

    @property
    def needs_current(self) -> bool:
        return self.name == "current" or (self.name == "rain" and self.offsets == [0])


@dataclass
class WeatherOutcome:
    """O que dizer (tela e voz) e o evento `weather` para a interface."""

    reply: str  # texto completo para o cartão "Resposta"
    spoken: str  # versão curta para a voz
    event: dict  # {"type": "weather", ...}
    intent: str
    status: str  # online | cache | offline | none | cached_only
    fetched: bool = False  # a fonte foi consultada (com sucesso) nesta resposta


def parse(text: str, *, today: date | None = None) -> WeatherIntent | None:
    """Interpreta a transcrição. `None` = não é pergunta sobre o tempo (segue para os outros comandos ou a IA)."""
    text = strip_polite_prefix(strip_wake_prefix(text))
    folded = fold(text).replace("?", " ").replace("!", " ")
    folded = " ".join(folded.split())
    if not folded or _NOT_WEATHER.search(folded):
        return None
    if _CACHED.search(folded):
        return WeatherIntent("cached")
    if _LAST_UPDATE.search(folded):
        return WeatherIntent("last_update")
    offsets, explicit = _day_offsets(folded, today or date.today())
    if _RAIN.search(folded):
        return WeatherIntent("rain", offsets, explicit)
    if _CURRENT.search(folded) and offsets == [0]:
        return WeatherIntent("current", [0], explicit)  # "como está o tempo hoje/agora?" = condições atuais
    if _FORECAST.search(folded) or _CURRENT.search(folded):
        return WeatherIntent("forecast", offsets, explicit)
    return None


def _day_offsets(folded: str, today: date) -> tuple[list[int], bool]:
    m = _NEXT_DAYS.search(folded)
    if m:
        n = _NUMBER_WORDS.get(m.group("n") or "", DEFAULT_NEXT_DAYS)
        return list(range(0, max(1, min(n, 16)))), True
    if _WEEK.search(folded) and not _WEEKEND.search(folded):
        return list(range(0, 7)), True
    if _WEEKEND.search(folded):
        wd = today.weekday()
        sat, sun = (5 - wd) % 7, (6 - wd) % 7
        if wd == 6:  # domingo: "fim de semana" ainda é hoje
            return [0], True
        return sorted({sat, sun}), True
    if _AFTER_TOMORROW.search(folded):
        return [2], True
    if _TOMORROW.search(folded):
        return [1], True
    m = _WEEKDAY.search(folded)
    if m:
        target = _WEEKDAY_WORDS[m.group("day")]
        return [(target - today.weekday()) % 7], True
    if _TODAY.search(folded):
        return [0], True
    return [0], False


class WeatherAssistant:
    """Responde perguntas sobre o tempo com a fonte configurada e o cache persistente."""

    def __init__(
        self,
        source: WeatherSource | None,
        cache: WeatherCache | None,
        *,
        latitude: float,
        longitude: float,
        location_name: str,
        tz_name: str,
        current_ttl_s: float = DEFAULT_CURRENT_TTL_S,
        forecast_ttl_s: float = DEFAULT_FORECAST_TTL_S,
        stale_after_s: float = DEFAULT_STALE_AFTER_S,
        retry_cooldown_s: float = DEFAULT_RETRY_COOLDOWN_S,
        clock=time.time,
    ) -> None:
        self.source = source
        self.cache = cache
        self.latitude = latitude
        self.longitude = longitude
        self.location_name = location_name
        self.tz_name = tz_name
        self.current_ttl_s = current_ttl_s
        self.forecast_ttl_s = forecast_ttl_s
        self.stale_after_s = stale_after_s
        self.retry_cooldown_s = retry_cooldown_s
        self._clock = clock
        self._lock = asyncio.Lock()
        self.questions = 0
        self.fetches = 0
        self.fetch_failures = 0
        self.cache_failures = 0
        self.last_error: tuple[str, str] | None = None

    # -- reconhecimento --------------------------------------------------------------------------

    def match(self, text: str) -> WeatherIntent | None:
        return parse(text, today=self._today())

    # -- resposta -----------------------------------------------------------------------------------

    async def answer(self, intent: WeatherIntent) -> WeatherOutcome:
        """Responde `intent`. Nunca levanta exceção: falhas viram texto explicando o que aconteceu. A consulta
        à fonte roda em uma thread; o loop continua livre para o áudio e o WebSocket."""
        self.questions += 1
        async with self._lock:  # uma consulta à fonte por vez
            try:
                return await self._answer(intent)
            except Exception as exc:  # erro de programacao: nunca derruba o servidor
                log.exception("falha inesperada ao responder sobre o tempo (%s)", intent.name)
                text = "Algo deu errado ao consultar o tempo. Tente de novo daqui a pouco."
                return WeatherOutcome(text, text, self._event(None, "none", message=type(exc).__name__), intent.name, "none")

    async def _answer(self, intent: WeatherIntent) -> WeatherOutcome:
        now = self._clock()
        entry = self._cached_entry()
        if intent.name in ("cached", "last_update"):
            return self._answer_about_cache(intent, entry, now)
        data = entry.data if entry is not None and entry.has_data else None
        error: WeatherError | None = None
        status = "cache"
        fetched = False
        if self._is_fresh(entry, intent, now):
            status = "cache"
        elif self.source is None:
            status = "cache" if data is not None else "none"
        elif self._in_cooldown(entry, now):
            status = "offline"
            error = WeatherError("cooldown", entry.last_error if entry is not None and entry.last_error else "Não consegui acessar o serviço de previsão do tempo.")
        else:
            try:
                data = await self._fetch()
                status, fetched = "online", True
                now = self._clock()
                entry = self._store(data) or entry
            except WeatherError as exc:
                error = exc
                status = "offline"
                self._record_failure(exc, now)
                if data is not None:
                    log.info("tempo: usando o cache de %s (%s)", _hhmm(data.fetched_at, data.tz()), exc.code)
        if data is None:
            text = self._no_data_text(error, entry)
            return WeatherOutcome(text, text, self._event(None, "none", message=error.message if error else ""), intent.name, "none")
        reply, spoken = self._compose(intent, data, now, status, error, entry)
        stale = self._is_stale(data, now)
        return WeatherOutcome(reply, spoken, self._event(data, status, stale=stale, message=error.message if error else ""), intent.name, status, fetched)

    # -- política de atualização ------------------------------------------------------------------------

    def _is_fresh(self, entry: CacheEntry | None, intent: WeatherIntent, now: float) -> bool:
        if entry is None or not entry.has_data or entry.data is None:
            return False
        age = entry.age_s(now) or 0.0
        ttl = self.current_ttl_s if intent.needs_current else self.forecast_ttl_s
        if age > ttl:
            return False
        return self._covers(entry.data, intent, now)

    def _covers(self, data: WeatherData, intent: WeatherIntent, now: float) -> bool:
        today = self._today(data, now)
        return all(data.day((today + timedelta(days=o)).isoformat()) is not None for o in intent.offsets[:1])

    def _in_cooldown(self, entry: CacheEntry | None, now: float) -> bool:
        if entry is None or entry.last_status != "failed" or entry.last_attempt_at is None or self.retry_cooldown_s <= 0:
            return False
        return (now - entry.last_attempt_at) < self.retry_cooldown_s

    def _is_stale(self, data: WeatherData, now: float) -> bool:
        return (now - data.fetched_at) > self.stale_after_s

    async def _fetch(self) -> WeatherData:
        assert self.source is not None
        loop = asyncio.get_running_loop()
        self.fetches += 1
        try:
            data = await loop.run_in_executor(None, self.source.fetch)
        except WeatherError as exc:
            self.fetch_failures += 1
            self.last_error = (exc.code, exc.message)
            log.warning("falha ao consultar o tempo (%s): %s", exc.code, exc.detail or exc.message)
            raise
        except Exception as exc:  # erro inesperado na fonte vira falha tratada
            self.fetch_failures += 1
            self.last_error = ("internal", "Falha inesperada ao consultar o tempo.")
            log.exception("falha inesperada na fonte meteorológica")
            raise WeatherError("internal", "Falha inesperada ao consultar o tempo.", type(exc).__name__) from exc
        self.last_error = None
        log.info("tempo: %s atualizado pela %s (%d dias)", data.location_name, data.source, len(data.daily))
        return data

    # -- cache ------------------------------------------------------------------------------------------

    def _cached_entry(self) -> CacheEntry | None:
        if self.cache is None:
            return None
        try:
            return self.cache.get(self.latitude, self.longitude)
        except WeatherCacheError as exc:
            self.cache_failures += 1
            log.warning("cache do tempo indisponível para leitura: %s", exc)
            return None

    def _store(self, data: WeatherData) -> CacheEntry | None:
        if self.cache is None:
            return None
        try:
            return self.cache.store(data, latitude=self.latitude, longitude=self.longitude)
        except (WeatherCacheError, ValueError) as exc:
            self.cache_failures += 1
            log.warning("não consegui guardar a previsão no cache: %s", exc)
            return None

    def _record_failure(self, exc: WeatherError, now: float) -> None:
        if self.cache is None:
            return
        try:
            self.cache.record_failure(
                self.latitude, self.longitude, location_name=self.location_name, timezone=self.tz_name, error=exc.message, at=now
            )
        except WeatherCacheError as cache_exc:
            self.cache_failures += 1
            log.warning("não consegui anotar a falha no cache do tempo: %s", cache_exc)

    def snapshot(self) -> dict:
        """Estado para `welcome` e `/health`, só a partir do cache (nunca consulta a fonte)."""
        entry = self._cached_entry()
        data = entry.data if entry is not None and entry.has_data else None
        now = self._clock()
        if data is None:
            info = self._event(None, "none", message=entry.last_error if entry else "")
        else:
            info = self._event(data, "cache", stale=self._is_stale(data, now), message=entry.last_error if entry else "")
        info.pop("type", None)
        info["enabled"] = True
        info["source"] = self.source.name if self.source is not None else SOURCE_NAME
        info["cache"] = self.cache is not None
        info["questions"] = self.questions
        info["fetches"] = self.fetches
        info["fetch_failures"] = self.fetch_failures
        return info

    # -- textos -------------------------------------------------------------------------------------------

    def _answer_about_cache(self, intent: WeatherIntent, entry: CacheEntry | None, now: float) -> WeatherOutcome:
        data = entry.data if entry is not None and entry.has_data else None
        if data is None:
            if entry is not None and entry.last_status == "failed" and entry.last_attempt_at is not None:
                tz = local_timezone(self.tz_name)
                text = (
                    f"Não tenho nenhuma previsão salva. A última tentativa de atualizar, {_when(entry.last_attempt_at, now, tz)}, "
                    f"falhou: {_lower_first(entry.last_error or 'sem detalhe')}"
                )
            else:
                text = "Não tenho nenhuma previsão salva. Pergunte como está o tempo quando houver internet e eu guardo o resultado."
            return WeatherOutcome(text, text, self._event(None, "none", message=entry.last_error if entry else ""), intent.name, "cached_only")
        tz = data.tz()
        when = _when(data.fetched_at, now, tz)
        age = _age(now - data.fetched_at)
        stale = self._is_stale(data, now)
        if intent.name == "cached":
            period = _period_label(data, self._today(data, now))
            text = f"Sim, tenho uma previsão salva de {data.location_name}, atualizada {when} ({age}){period}."
        else:
            text = f"A última atualização do tempo de {data.location_name} foi {when}, {age}."
        if entry is not None and entry.last_status == "failed" and entry.last_attempt_at is not None:
            text += f" Depois disso, a tentativa {_when(entry.last_attempt_at, now, tz)} falhou: {_lower_first(entry.last_error or 'sem detalhe')}"
        if stale:
            text += f" {STALE_WARNING}"
        return WeatherOutcome(text, text, self._event(data, "cache", stale=stale, message=entry.last_error if entry else ""), intent.name, "cached_only")

    def _no_data_text(self, error: WeatherError | None, entry: CacheEntry | None) -> str:
        if error is None:
            return "Ainda não tenho dados meteorológicos salvos e a consulta ao serviço de tempo não está disponível."
        return f"{error.message} {REPLY_NO_DATA_OFFLINE}"

    def _compose(
        self, intent: WeatherIntent, data: WeatherData, now: float, status: str, error: WeatherError | None, entry: CacheEntry | None
    ) -> tuple[str, str]:
        tz = data.tz()
        today = self._today(data, now)
        stale = self._is_stale(data, now)
        if intent.name == "current":
            body, spoken_body = self._current_text(data, now, status)
        elif intent.name == "rain":
            body, spoken_body = self._rain_text(data, now, today, intent.offsets)
        else:
            body, spoken_body = self._forecast_text(data, today, intent.offsets)
        # Sempre: de quando são os dados. Em falha, o motivo e o aviso de dados antigos.
        if status == "online":
            footer = f"Dados atualizados agora, às {_hhmm(data.fetched_at, tz)}."
        elif error is not None:
            footer = f"{error.message} Estou usando os dados salvos, atualizados {_when(data.fetched_at, now, tz)} ({_age(now - data.fetched_at)})."
        else:
            footer = f"Dados atualizados {_when(data.fetched_at, now, tz)}."
        if stale:
            footer += f" {STALE_WARNING}"
        reply = f"{body} {footer}".strip()
        spoken = f"{spoken_body} {footer}".strip()
        return reply, spoken

    def _current_text(self, data: WeatherData, now: float, status: str) -> tuple[str, str]:
        cur = data.current
        today = self._today(data, now)
        day = data.day(today.isoformat())
        if cur is None:
            body = f"A fonte não informou as condições atuais de {data.location_name}."
            if day is not None:
                body += " " + _day_sentence(day, today)
            return body, body
        age = now - data.fetched_at
        recent = status == "online" or age <= self.current_ttl_s
        # Data e hora a que a leitura se refere (campo `time` da fonte, no fuso local), não a hora da consulta.
        ref_date, ref_time = _reference(cur.time, data)
        reference = f"{_full_date(ref_date)}, às {ref_time}"
        lead = f"Agora em {data.location_name}, {reference}" if recent else f"Na última leitura, {reference}, {data.location_name} tinha"
        parts: list[str] = []
        if cur.temperature_c is not None:
            temp = f"{_temp(cur.temperature_c)} graus"
            if cur.apparent_c is not None and abs(cur.apparent_c - cur.temperature_c) >= 2:
                temp += f" (sensação de {_temp(cur.apparent_c)})"
            parts.append(temp)
        parts.append(describe_code(cur.weather_code, cur.is_day))
        if cur.wind_kmh is not None:
            parts.append(f"vento de {_num(cur.wind_kmh)} km/h")
        if cur.humidity_pct is not None:
            parts.append(f"umidade de {_num(cur.humidity_pct)}%")
        if cur.precipitation_mm is not None:
            parts.append(f"{_mm(cur.precipitation_mm)} de chuva na última hora" if cur.precipitation_mm > 0 else "sem chuva no momento")
        body = f"{lead}: {_join(parts)}." if recent else f"{lead} {_join(parts)}."
        spoken_parts = parts[:3]
        spoken = f"{lead}: {_join(spoken_parts)}." if recent else f"{lead} {_join(spoken_parts)}."
        if day is not None:
            extra = _day_sentence(day, today, with_name=False)
            body += f" {extra}"
            spoken += f" {extra}"
        return body, spoken

    def _rain_text(self, data: WeatherData, now: float, today: date, offsets: list[int]) -> tuple[str, str]:
        target = today + timedelta(days=offsets[0])
        label = _day_label(target, today)
        day = data.day(target.isoformat())
        if day is None:
            text = f"Minha previsão salva vai {_period_until(data)}; não tenho dados para {label}."
            return text, text
        hours = data.hours_of(target.isoformat())
        if offsets[0] == 0:
            hour_now = datetime.fromtimestamp(now, data.tz()).hour
            hours = [h for h in hours if _hour_of(h) >= hour_now] or hours
        periods = _period_summary(hours)
        probs = [p for _, p, _ in periods if p is not None]
        max_prob = max(probs) if probs else day.precipitation_probability_max
        total_mm = day.precipitation_sum_mm
        cur = data.current
        raining_now = offsets[0] == 0 and cur is not None and ((cur.precipitation_mm or 0) > 0 or is_rainy_code(cur.weather_code))
        sentences: list[str] = []
        if raining_now and cur is not None:
            if now - data.fetched_at <= self.current_ttl_s:
                sentences.append(f"Neste momento a leitura indica {describe_code(cur.weather_code, cur.is_day)} em {data.location_name}.")
            else:
                sentences.append(f"Na última leitura, às {_hhmm(data.fetched_at, data.tz())}, havia {describe_code(cur.weather_code, cur.is_day)} em {data.location_name}.")
        # O dia é identificado pelo nome e pela data completa, a partir do campo `time` da fonte.
        cap = _cap(_heading(_source_date(day.date) or target, today)) + ","
        if max_prob is None:
            cond = describe_code(day.weather_code, True)
            if total_mm is not None and total_mm > 0:
                sentences.append(f"{cap} a previsão para {data.location_name} é de {cond}, com cerca de {_mm(total_mm)} acumulados; a fonte não informou a probabilidade.")
            else:
                sentences.append(f"{cap} a previsão para {data.location_name} é de {cond}, sem chuva prevista, mas a fonte não informou a probabilidade.")
            return " ".join(sentences), " ".join(sentences)
        detail = [f"{_num(p)}% {phrase}" for phrase, p, _ in periods if p is not None]
        if max_prob >= 60:
            verdict = f"{cap} é bem provável que chova em {data.location_name}: a chance chega a {_num(max_prob)}%"
        elif max_prob >= 30:
            verdict = f"{cap} pode chover em {data.location_name}: a chance chega a {_num(max_prob)}%"
        elif max_prob >= 10:
            verdict = f"{cap} a chance de chuva em {data.location_name} é baixa, no máximo {_num(max_prob)}%"
        else:
            verdict = f"{cap} a previsão indica pouca ou nenhuma chance de chuva em {data.location_name}, no máximo {_num(max_prob)}%"
        if detail and len(detail) > 1:
            verdict += f" ({'; '.join(detail)})"
        elif detail:
            verdict += f", {detail[0]}"
        if total_mm is not None and total_mm > 0:
            verdict += f", com cerca de {_mm(total_mm)} previstos"
        verdict += "."
        sentences.append(verdict)
        if max_prob >= 30:
            sentences.append("Vale deixar um guarda-chuva por perto.")
        sentences.append(NOT_CERTAIN)
        full = " ".join(sentences)
        # Falado: sem a lista por período quando há mais de dois; o detalhe fica na tela.
        spoken_verdict = re.sub(r" \([^()]*\)", "", verdict) if len(detail) > 2 else verdict
        spoken_parts = [s if s is not verdict else spoken_verdict for s in sentences]
        return full, " ".join(spoken_parts)

    def _forecast_text(self, data: WeatherData, today: date, offsets: list[int]) -> tuple[str, str]:
        lines: list[str] = []
        missing: list[str] = []
        for o in offsets:
            target = today + timedelta(days=o)
            day = data.day(target.isoformat())
            if day is None:
                missing.append(_day_label(target, today))
                continue
            lines.append(_day_sentence(day, today))
        if not lines:
            text = f"Minha previsão salva vai {_period_until(data)}; não tenho dados para {_join(missing)}."
            return text, text
        head = f"Previsão para {data.location_name}:" if len(lines) > 1 else ""
        body_lines = ([head] if head else []) + lines
        if missing:
            body_lines.append(f"Não tenho dados para {_join(missing)}: a previsão salva vai {_period_until(data)}.")
        body_lines.append(NOT_CERTAIN)
        full = "\n".join(body_lines) if len(lines) > 1 else " ".join(body_lines)
        spoken_lines = lines[:MAX_SPOKEN_DAYS]
        spoken = " ".join(([head] if head else []) + spoken_lines)
        if len(lines) > MAX_SPOKEN_DAYS:
            spoken += " Os demais dias estão na tela."
        if missing:
            spoken += f" Não tenho dados para {_join(missing)}."
        return full, spoken

    def _event(self, data: WeatherData | None, status: str, *, stale: bool = False, message: str = "") -> dict:
        if data is None:
            return {
                "type": "weather", "available": False, "location": self.location_name, "summary": "", "detail": "",
                "updated_at": None, "stale": False, "status": status, "message": message, "source": SOURCE_NAME, "attribution": ATTRIBUTION,
            }
        today = self._today(data, self._clock())
        day = data.day(today.isoformat())
        cur = data.current
        summary = ""
        if cur is not None:
            bits = []
            if cur.temperature_c is not None:
                bits.append(f"{_temp(cur.temperature_c)} °C")
            bits.append(describe_code(cur.weather_code, cur.is_day))
            summary = " · ".join(bits)
        elif day is not None:
            summary = describe_code(day.weather_code, True)
        detail_bits = []
        if day is not None:
            detail_bits.append(f"hoje, {_source_date(day.date) or today:%d/%m}")  # dia a que a previsão se refere
            if day.temperature_min_c is not None and day.temperature_max_c is not None:
                detail_bits.append(f"mín {_temp(day.temperature_min_c)} °C · máx {_temp(day.temperature_max_c)} °C")
            if day.precipitation_probability_max is not None:
                detail_bits.append(f"chuva {_num(day.precipitation_probability_max)}%")
            if day.precipitation_sum_mm is not None and day.precipitation_sum_mm > 0:
                detail_bits.append(_mm(day.precipitation_sum_mm))
        if cur is not None and cur.wind_kmh is not None:
            detail_bits.append(f"vento {_num(cur.wind_kmh)} km/h")
        return {
            "type": "weather",
            "available": True,
            "location": data.location_name,
            "summary": summary,
            "detail": " · ".join(detail_bits),
            "updated_at": round(data.fetched_at, 3),
            "observed_at": cur.time if cur is not None else None,
            "period_start": data.period_start,
            "period_end": data.period_end,
            "stale": stale,
            "status": status,
            "message": message,
            "source": self.source.name if self.source is not None else data.source,
            "attribution": ATTRIBUTION,
        }

    def _today(self, data: WeatherData | None = None, now: float | None = None) -> date:
        now = self._clock() if now is None else now
        tz = data.tz() if data is not None else local_timezone(self.tz_name)
        return datetime.fromtimestamp(now, tz).date()


# -- formatação em pt-BR ------------------------------------------------------------------------------------


def _temp(value: float) -> str:
    return str(int(round(value)))


def _num(value: float) -> str:
    return str(int(round(value)))


def _mm(value: float) -> str:
    if value < 1:
        return f"{value:.1f}".replace(".", ",") + " mm"
    return f"{int(round(value))} mm"


def _hhmm(epoch: float, tz) -> str:
    return datetime.fromtimestamp(epoch, tz).strftime("%H:%M")


def _when(epoch: float, now: float, tz) -> str:
    """"hoje às 14:32" / "ontem às 09:10" / "em 10/10 às 14:32"."""
    then = datetime.fromtimestamp(epoch, tz)
    today = datetime.fromtimestamp(now, tz).date()
    if then.date() == today:
        return f"hoje às {then:%H:%M}"
    if then.date() == today - timedelta(days=1):
        return f"ontem às {then:%H:%M}"
    return f"em {then:%d/%m} às {then:%H:%M}"


def _age(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return "há menos de um minuto"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"há {minutes} minuto{'s' if minutes != 1 else ''}"
    hours = int(seconds // 3600)
    if hours < 48:
        return f"há {hours} hora{'s' if hours != 1 else ''}"
    days = int(seconds // 86400)
    return f"há {days} dias"


def _day_label(target: date, today: date) -> str:
    delta = (target - today).days
    if delta == 0:
        return "hoje"
    if delta == 1:
        return "amanhã"
    if delta == 2:
        return f"depois de amanhã ({WEEKDAYS[target.weekday()]})"
    if delta < 0:
        return f"{WEEKDAYS[target.weekday()]} passada ({target:%d/%m})" if target.weekday() < 5 else f"{WEEKDAYS[target.weekday()]} passado ({target:%d/%m})"
    return f"{WEEKDAYS[target.weekday()]} ({target:%d/%m})"


def _day_sentence(day: DailyForecast, today: date, *, with_name: bool = True) -> str:
    """Uma linha por dia, sempre identificada pelo nome do dia e pela data completa (campo `time` da fonte)."""
    heading = _heading(_source_date(day.date) or today, today)
    parts: list[str] = [describe_code(day.weather_code, True)]
    if day.temperature_min_c is not None and day.temperature_max_c is not None:
        parts.append(f"mínima de {_temp(day.temperature_min_c)} e máxima de {_temp(day.temperature_max_c)} graus")
    elif day.temperature_max_c is not None:
        parts.append(f"máxima de {_temp(day.temperature_max_c)} graus")
    if day.precipitation_probability_max is not None:
        rain = f"chance de chuva de {_num(day.precipitation_probability_max)}%"
        if day.precipitation_sum_mm is not None and day.precipitation_sum_mm > 0:
            rain += f" (cerca de {_mm(day.precipitation_sum_mm)})"
        parts.append(rain)
    elif day.precipitation_sum_mm is not None and day.precipitation_sum_mm > 0:
        parts.append(f"cerca de {_mm(day.precipitation_sum_mm)} de chuva")
    if day.wind_max_kmh is not None:
        parts.append(f"vento de até {_num(day.wind_max_kmh)} km/h")
    text = f"{_cap(heading)}: {_join(parts)}." if with_name else f"Para {heading}: {_join(parts)}."
    return text


def _source_date(text: str) -> date | None:
    """Data "AAAA-MM-DD" (ou "AAAA-MM-DDTHH:MM") vinda da fonte, já no fuso local; None se não der para ler."""
    try:
        return date.fromisoformat(text[:10])
    except (TypeError, ValueError):
        return None


def _reference(iso_time: str, data: WeatherData) -> tuple[date, str]:
    """(data, "HH:MM") a que uma leitura se refere, a partir do campo `time` da fonte; se ele não puder ser lido,
    usa a hora da consulta convertida para o fuso local."""
    ref = _source_date(iso_time)
    if ref is not None and len(iso_time) >= 16 and iso_time[10] == "T":
        return ref, iso_time[11:16]
    then = datetime.fromtimestamp(data.fetched_at, data.tz())
    return then.date(), then.strftime("%H:%M")


def _full_date(d: date) -> str:
    """"segunda-feira, 12 de outubro de 2026"."""
    return f"{WEEKDAYS[d.weekday()]}, {d.day} de {MONTHS[d.month - 1]} de {d.year}"


def _heading(target: date, today: date) -> str:
    """"hoje, segunda-feira, 12 de outubro de 2026" / "amanhã, terça-feira, …" / "quinta-feira, 15 de outubro de 2026"."""
    delta = (target - today).days
    full = _full_date(target)
    if delta == 0:
        return f"hoje, {full}"
    if delta == 1:
        return f"amanhã, {full}"
    if delta == 2:
        return f"depois de amanhã, {full}"
    return full


def _cap(text: str) -> str:
    return f"{text[:1].upper()}{text[1:]}"


def _hour_of(point: HourlyPoint) -> int:
    try:
        return int(point.time[11:13])
    except ValueError:
        return 0


def _period_summary(hours: list[HourlyPoint]) -> list[tuple[str, float | None, float]]:
    """[(frase do período, probabilidade máxima, mm acumulados)] só para períodos com dados."""
    out: list[tuple[str, float | None, float]] = []
    for _, start, end, phrase in PERIODS:
        pts = [h for h in hours if start <= _hour_of(h) <= end]
        if not pts:
            continue
        probs = [h.precipitation_probability for h in pts if h.precipitation_probability is not None]
        mm = sum(h.precipitation_mm or 0.0 for h in pts)
        out.append((phrase, max(probs) if probs else None, mm))
    return out


def _period_label(data: WeatherData, today: date) -> str:
    if not data.daily:
        return ""
    try:
        end = date.fromisoformat(data.period_end)
        start = date.fromisoformat(data.period_start)
    except ValueError:
        return ""
    if end < today:
        return f", mas ela cobre só até {_day_label(end, today)}"
    first = _day_label(max(start, today), today) if start <= today else _day_label(start, today)
    return f", cobrindo de {first} até {_day_label(end, today)}"


def _period_until(data: WeatherData) -> str:
    try:
        end = date.fromisoformat(data.period_end)
    except ValueError:
        return "até uma data desconhecida"
    return f"até {WEEKDAYS[end.weekday()]} ({end:%d/%m})"


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " e " + items[-1]


def _lower_first(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    return text[0].lower() + text[1:] if not text.isupper() else text


__all__ = [
    "NOT_CERTAIN",
    "REPLY_NO_DATA_OFFLINE",
    "STALE_WARNING",
    "WEEKDAYS",
    "WeatherAssistant",
    "WeatherIntent",
    "WeatherOutcome",
    "parse",
]
