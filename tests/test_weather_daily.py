import asyncio
from datetime import timedelta

import pytest

from tests.test_weather import make_assistant, payload, TODAY, NOW, OFFLINE


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_startup_cache_midnight_failure_recovery(tmp_path):
    wa, source, clock = make_assistant(tmp_path)
    first = await wa.refresh_today()
    assert first["available"] and source.calls == 1
    await asyncio.gather(wa.refresh_today(), wa.refresh_today())
    assert source.calls == 1
    restarted, restarted_source, _ = make_assistant(tmp_path, clock=clock)
    assert (await restarted.refresh_today())["available"]
    assert restarted_source.calls == 0
    restarted.cache.close()
    # Dez minutos antes e depois da meia-noite local: TTL ainda valido,
    # mas a mudanca de dia exige nova consulta.
    clock.now = NOW + (9 * 60 + 18) * 60
    source.data = payload()
    await wa.refresh_today()
    clock.advance(20 * 60)
    source.error = OFFLINE
    failed = await wa.refresh_today()
    calls = source.calls
    assert failed["date"] == "2026-10-13" and failed["stale"]
    assert "23 °C" not in failed["summary"]  # observacao de ontem
    await wa.refresh_today()
    assert source.calls == calls
    clock.advance(61)
    source.error = None
    source.data = payload(TODAY + timedelta(days=1))
    result = await wa.refresh_today()
    assert result["available"] and not result["stale"]
    assert source.calls == calls + 1
    wa.cache.close()


@pytest.mark.anyio
async def test_forecast_popup_keeps_today_and_out_of_range(tmp_path):
    wa, source, clock = make_assistant(tmp_path)
    today = await wa.refresh_today()
    for question in ("como estará o tempo amanhã?", "vai chover terça-feira?"):
        answer = await wa.answer(wa.match(question))
        assert answer.event["summary"] == today["summary"]
        assert "13 de outubro de 2026" in answer.event["forecast"]["text"]
        assert "mínima" in answer.event["forecast"]["text"]
    assert wa.match("qual a previsão para segunda-feira?").offsets == [0]
    answer = await wa.answer(wa.match("qual a previsão para sábado?"))
    assert "não tenho" in answer.reply
    assert "forecast" in answer.event
    wa.cache.close()


@pytest.mark.anyio
async def test_worker_cancel_and_no_cache_retry(tmp_path):
    wa, source, clock = make_assistant(tmp_path, cache=False)
    source.error = OFFLINE
    await wa.refresh_today()
    await wa.refresh_today()
    assert source.calls == 1
    source.error = None
    clock.advance(61)
    await wa.refresh_today()
    await wa.refresh_today()
    assert source.calls == 2
    events = []
    async def publish(event):
        events.append(event)
    task = asyncio.create_task(wa.run_daily(publish))
    await asyncio.sleep(0.02)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled() and len(events) == 2
