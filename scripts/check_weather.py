r"""Verifica a previsão do tempo da Luna (Open-Meteo + cache local), sem microfone nem interface.

Uso:
  .venv\Scripts\python scripts\check_weather.py          # cenário completo em um cache TEMPORÁRIO (uma consulta real à fonte)
  .venv\Scripts\python scripts\check_weather.py --real   # só mostra o que está no cache configurado, sem consultar a fonte
  .venv\Scripts\python scripts\check_weather.py --offline # cenário sem consultar a fonte (simula falta de internet)

O cenário cobre: consulta online (uma única chamada), perguntas de hoje/amanhã/próximos dias respondidas do cache,
"reinício" (reabre o banco), falha de internet simulada com cache (idade dos dados), cache antigo (aviso) e falha
sem cache. Nada além das coordenadas configuradas sai desta máquina.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.assistant.weather import WeatherAssistant  # noqa: E402
from server.services.weather import OpenMeteoClient, WeatherError, local_timezone  # noqa: E402
from server.storage.config import ConfigError, load_settings  # noqa: E402
from server.storage.weather import WeatherCache  # noqa: E402


class FailingSource:
    name = "sem-internet (simulado)"

    def fetch(self):
        raise WeatherError("offline", "Não consegui acessar a internet.", "simulado")


def say(step: str, text: str) -> None:
    print(f"\n[{step}] {text}")


async def ask(wa: WeatherAssistant, phrase: str) -> None:
    print(f"> {phrase}")
    intent = wa.match(phrase)
    if intent is None:
        print("  (não é pergunta sobre o tempo; iria para a IA)")
        return
    t0 = time.perf_counter()
    out = await wa.answer(intent)
    print(f"  [{out.status}{', fonte consultada' if out.fetched else ''}, {time.perf_counter() - t0:.2f} s]")
    for line in out.reply.splitlines():
        print(f"  Luna (tela): {line}")
    if out.spoken != out.reply:
        print(f"  Luna (voz):  {out.spoken}")


def build(settings, source, cache_path, clock=time.time) -> WeatherAssistant:
    return WeatherAssistant(
        source,
        WeatherCache(cache_path),
        latitude=settings.weather_latitude,
        longitude=settings.weather_longitude,
        location_name=settings.weather_location_name,
        tz_name=settings.weather_timezone,
        current_ttl_s=settings.weather_current_ttl_s,
        forecast_ttl_s=settings.weather_forecast_ttl_s,
        stale_after_s=settings.weather_stale_after_s,
        retry_cooldown_s=settings.weather_retry_cooldown_s,
        clock=clock,
    )


async def scenario(settings, offline: bool) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="luna-clima-")) / "memory.sqlite3"
    tz = local_timezone(settings.weather_timezone)
    print(f"Local: {settings.weather_location_name} ({settings.weather_latitude}, {settings.weather_longitude}), fuso {settings.weather_timezone}")
    print(f"Cache temporário: {tmp}")
    source = FailingSource() if offline else OpenMeteoClient(
        settings.weather_latitude,
        settings.weather_longitude,
        location_name=settings.weather_location_name,
        tz_name=settings.weather_timezone,
        forecast_days=settings.weather_forecast_days,
        connect_timeout_s=settings.weather_connect_timeout_s,
        timeout_s=settings.weather_timeout_s,
    )
    wa = build(settings, source, tmp)
    say("1", "Consulta online (uma chamada à fonte)" if not offline else "Sem internet e sem cache")
    await ask(wa, "Hey Luna, como está o tempo em Ouro Fino?")
    if offline:
        await ask(wa, "Luna, você tem uma previsão salva?")
        return 0
    say("2", "Perguntas respondidas do cache (sem nova chamada)")
    for phrase in ("Vai chover hoje?", "Como estará o tempo amanhã?", "Qual é a previsão para os próximos três dias?", "Luna, você tem uma previsão salva?", "Luna, quando o tempo foi atualizado pela última vez?"):
        await ask(wa, phrase)
    say("3", "Reinício: outra instância sobre o mesmo arquivo, agora sem internet (simulado)")
    wa.cache.close()
    wa2 = build(settings, FailingSource(), tmp)
    await ask(wa2, "qual a temperatura agora?")
    say("4", "Cache vencido e sem internet: usa os dados salvos e diz a idade")
    fetched = wa2.cache.get(settings.weather_latitude, settings.weather_longitude).fetched_at
    later = fetched + settings.weather_forecast_ttl_s + 60
    wa3 = build(settings, FailingSource(), tmp, clock=lambda: later)
    print(f"  (relógio simulado: {datetime.fromtimestamp(later, tz):%d/%m %H:%M})")
    await ask(wa3, "vai chover hoje?")
    say("5", "Cache antigo e sem internet: aviso explícito de dados desatualizados")
    much_later = fetched + settings.weather_stale_after_s + 3600
    wa4 = build(settings, FailingSource(), tmp, clock=lambda: much_later)
    print(f"  (relógio simulado: {datetime.fromtimestamp(much_later, tz):%d/%m %H:%M})")
    await ask(wa4, "como estará o tempo amanhã?")
    await ask(wa4, "quando o tempo foi atualizado pela última vez?")
    say("6", "Sem cache e sem internet")
    wa5 = build(settings, FailingSource(), tmp.parent / "vazio.sqlite3")
    await ask(wa5, "vai chover hoje?")
    print(f"\nChamadas reais à fonte: {getattr(source, 'requests', 0)} (falhas: {getattr(source, 'failures', 0)})")
    return 0


def main(argv: list[str]) -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"erro de configuracao: {exc}")
        return 2
    if not settings.weather_enabled:
        print("previsão do tempo desativada ([weather] enabled = false)")
        return 1
    if "--real" in argv:
        cache = WeatherCache(settings.weather_cache_file)
        entry = cache.get(settings.weather_latitude, settings.weather_longitude)
        print(f"Cache configurado: {settings.weather_cache_file}")
        if entry is None:
            print("Nenhum registro: a Luna ainda não consultou o tempo.")
            return 0
        tz = local_timezone(settings.weather_timezone)
        print(f"Local: {entry.location_name}  última atualização: {datetime.fromtimestamp(entry.fetched_at, tz) if entry.fetched_at else 'nunca'}")
        print(f"Período: {entry.period_start} .. {entry.period_end}  última tentativa: {entry.last_status} {entry.last_error}")
        if entry.data is not None and entry.data.current is not None:
            c = entry.data.current
            print(f"Condições (hora do modelo {c.time}): {c.temperature_c} °C, código {c.weather_code}, vento {c.wind_kmh} km/h")
        return 0
    return asyncio.run(scenario(settings, "--offline" in argv))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
