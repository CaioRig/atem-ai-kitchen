"""API HTTP + WebSocket. Serve também a interface estática do cliente."""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import replace
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from server import __version__
from server.api.protocol import (
    Cancel,
    Hello,
    MemoryClear,
    MemoryDelete,
    MemoryList,
    Ping,
    Replay,
    Retry,
    Say,
    SetState,
    SpeakDone,
    error,
    parse_inbound,
)
from server.assistant.conversation import Conversation
from server.assistant.ingredients import IngredientCommands
from server.assistant.memory import REPLY_STORAGE_FAILED, MemoryCommands
from server.assistant.recipes import RecipeAssistant
from server.assistant.speech import Speech
from server.assistant.state import AssistantState, StateMachine
from server.assistant.voice import VoicePipeline
from server.assistant.weather import WeatherAssistant
from server.services.llm import LLMError, LanguageModel, OllamaClient
from server.services.performance import GIB, PerformancePolicy, gpu_utilization
from server.services.model_quality import LIMITED, QualityRegistry
from server.services.transcriber import Transcriber, TranscriberUnavailable, WhisperCppTranscriber
from server.services.tts import KokoroSynthesizer, PiperSynthesizer, Synthesizer, TtsUnavailable
from server.services.vad import SileroVad, SpeechDetector, VadUnavailable
from server.services.wakeword import Detector, OpenWakeWordDetector, WakeWordUnavailable
from server.services.weather import OpenMeteoClient, WeatherSource
from server.storage.config import Settings
from server.services.videos import VideoSearch
from server.storage.memory import MemoryStorageError, MemoryStore
from server.storage.weather import WeatherCache, WeatherCacheError

log = logging.getLogger("jarvis.api")

HELLO_TIMEOUT_S = 5.0
CLOSE_BAD_TOKEN = 4401
CLOSE_BAD_HELLO = 4400
MAX_AUDIO_FRAME_BYTES = 64 * 1024


class Hub:
    """Clientes conectados e autenticados; envia eventos para todos."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self.audio_source: WebSocket | None = None  # um unico microfone por vez

    def add(self, ws: WebSocket) -> None:
        self._clients.add(ws)

    def discard(self, ws: WebSocket) -> None:
        self._clients.discard(ws)
        if self.audio_source is ws:
            self.audio_source = None

    def __len__(self) -> int:
        return len(self._clients)

    async def broadcast(self, message: dict) -> None:
        payload = json.dumps(message, ensure_ascii=False)
        await self._send_all(lambda ws: ws.send_text(payload))

    async def broadcast_bytes(self, data: bytes) -> None:
        """Áudio da voz da Luna (PCM) para todos os clientes; cada um só o usa após um `speak_start`."""
        await self._send_all(lambda ws: ws.send_bytes(data))

    async def _send_all(self, send) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                await send(ws)
            except Exception:  # conexão já caiu; limpeza abaixo
                dead.append(ws)
        for ws in dead:
            self.discard(ws)


def load_detector(settings: Settings) -> tuple[Detector | None, str]:
    """Carrega o detector; se faltar biblioteca ou modelo, devolve (None, motivo)."""
    try:
        return OpenWakeWordDetector(settings.wake_model, settings.wake_features_dir), ""
    except WakeWordUnavailable as exc:
        log.warning("palavra de ativacao indisponivel: %s", exc)
        return None, str(exc)
    except Exception as exc:  # modelo corrompido, onnxruntime incompativel etc.
        log.exception("falha ao carregar o detector")
        return None, f"falha ao carregar o detector: {type(exc).__name__}"


def load_vad(settings: Settings) -> SpeechDetector | None:
    """Carrega o Silero VAD para o fim da fala; sem ele, o fim da fala usa o limiar de energia."""
    try:
        return SileroVad(settings.wake_vad_model)
    except VadUnavailable as exc:
        log.warning("fim da fala por energia (VAD indisponivel: %s)", exc)
        return None
    except Exception as exc:  # modelo corrompido, onnxruntime incompativel etc.
        log.exception("falha ao carregar o VAD")
        return None


def load_transcriber(settings: Settings) -> tuple[Transcriber | None, str]:
    """Carrega o whisper.cpp; se faltar biblioteca ou modelo, devolve (None, motivo)."""
    try:
        t0 = time.perf_counter()
        stt = WhisperCppTranscriber(
            settings.stt_model,
            language=settings.stt_language,
            threads=settings.stt_threads,
            vad_model=settings.stt_vad_model,
            audio_ctx=settings.stt_audio_ctx,
        )
        log.info(
            "transcricao: %s (%s, %d threads, vad=%s, audio_ctx=%s) carregado em %.1f s",
            stt.name, stt.language, stt.threads, stt.vad, stt.audio_ctx or "completo", time.perf_counter() - t0,
        )
        if not stt.vad:
            log.warning("VAD ausente (%s): ruido pode virar texto; execute scripts/prepare_models.py", settings.stt_vad_model.name)
        return stt, ""
    except TranscriberUnavailable as exc:
        log.warning("transcricao indisponivel: %s", exc)
        return None, str(exc)
    except Exception as exc:  # modelo corrompido, binario incompativel etc.
        log.exception("falha ao carregar o transcritor")
        return None, f"falha ao carregar o transcritor: {type(exc).__name__}"


def load_llm(settings: Settings) -> LanguageModel | None:
    """Cliente do Ollama (sem conexão ainda: a disponibilidade é verificada ao iniciar e a cada pergunta)."""
    if not settings.llm_enabled:
        log.info("IA local desativada (llm.enabled = false)")
        return None
    if settings.llm_unavailable_reason:
        log.warning("IA indisponível nesta execução: %s", settings.llm_unavailable_reason)
        return None
    client = OllamaClient(
        settings.llm_url,
        settings.llm_model,
        system_prompt=settings.llm_system_prompt,
        connect_timeout_s=settings.llm_connect_timeout_s,
        timeout_s=settings.llm_timeout_s,
        generation_limit_s=settings.llm_generation_limit_s,
        keep_alive=settings.llm_keep_alive,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        top_p=settings.llm_top_p,
        repeat_penalty=settings.llm_repeat_penalty,
    )
    client.recipe_policy = {"max_tokens": settings.recipes_max_tokens} if settings.recipes_enabled else None
    log.info("IA local: modelo %s em %s", client.name, client.address)
    return client


def load_synthesizer(settings: Settings) -> tuple[Synthesizer | None, str]:
    """Carrega a voz (Kokoro ou Piper); se faltar biblioteca ou modelo, devolve (None, motivo)."""
    if not settings.tts_enabled:
        log.info("voz desativada (tts.enabled = false)")
        return None, "voz desativada (tts.enabled = false)"
    try:
        t0 = time.perf_counter()
        tts: Synthesizer
        if settings.tts_engine == "piper":
            tts = PiperSynthesizer(settings.tts_model, speed=settings.tts_speed, volume=settings.tts_volume)
        else:
            tts = KokoroSynthesizer(
                settings.tts_model,
                settings.tts_voices,
                voice=settings.tts_voice,
                language=settings.tts_language,
                speed=settings.tts_speed,
                volume=settings.tts_volume,
                threads=settings.tts_threads,
                segment_chars=settings.tts_segment_chars,
            )
        log.info("voz: %s (%s, %d Hz) carregada em %.1f s", tts.name, tts.voice, tts.sample_rate, time.perf_counter() - t0)
        return tts, ""
    except TtsUnavailable as exc:
        log.warning("voz indisponivel: %s", exc)
        return None, str(exc)
    except Exception as exc:  # modelo corrompido, onnxruntime incompativel etc.
        log.exception("falha ao carregar a voz")
        return None, f"falha ao carregar a voz: {type(exc).__name__}"


def load_memory(settings: Settings) -> tuple[MemoryStore | None, str]:
    """Abre a memória SQLite local; se o arquivo não puder ser aberto, o servidor segue sem memória."""
    if not settings.memory_enabled:
        log.info("memoria desativada (memory.enabled = false)")
        return None, "memória desativada (memory.enabled = false)"
    try:
        store = MemoryStore(settings.memory_path)
        log.info("memoria: %d registro(s) em %s", store.count(), store.path)
        return store, ""
    except MemoryStorageError as exc:
        log.warning("memoria indisponivel: %s", exc)
        return None, str(exc)
    except Exception as exc:  # nunca derruba o servidor
        log.exception("falha ao abrir a memoria")
        return None, f"falha ao abrir a memória: {type(exc).__name__}"


def load_weather(settings: Settings, source: WeatherSource | None = None) -> tuple[WeatherAssistant | None, str]:
    """Previsão do tempo (Fase 8): fonte Open-Meteo + cache no SQLite da memória. Sem conexão ainda: a fonte só é
    consultada quando alguém pergunta. Se o cache não abrir, a Luna responde só online (e avisa no log)."""
    if not settings.weather_enabled:
        log.info("previsao do tempo desativada (weather.enabled = false)")
        return None, "previsão do tempo desativada (weather.enabled = false)"
    if source is None:
        try:
            source = OpenMeteoClient(
                settings.weather_latitude,
                settings.weather_longitude,
                location_name=settings.weather_location_name,
                tz_name=settings.weather_timezone,
                forecast_days=settings.weather_forecast_days,
                connect_timeout_s=settings.weather_connect_timeout_s,
                timeout_s=settings.weather_timeout_s,
            )
        except ValueError as exc:
            log.warning("previsao do tempo indisponivel: %s", exc)
            return None, f"previsão do tempo indisponível: {exc}"
    cache: WeatherCache | None
    try:
        cache = WeatherCache(settings.weather_cache_file)
    except WeatherCacheError as exc:
        log.warning("cache do tempo indisponivel (so consultas online): %s", exc)
        cache = None
    assistant = WeatherAssistant(
        source,
        cache,
        latitude=settings.weather_latitude,
        longitude=settings.weather_longitude,
        location_name=settings.weather_location_name,
        tz_name=settings.weather_timezone,
        current_ttl_s=settings.weather_current_ttl_s,
        forecast_ttl_s=settings.weather_forecast_ttl_s,
        stale_after_s=settings.weather_stale_after_s,
        retry_cooldown_s=settings.weather_retry_cooldown_s,
    )
    log.info(
        "previsao do tempo: %s (%.4f, %.4f, %s) via %s; cache em %s",
        settings.weather_location_name, settings.weather_latitude, settings.weather_longitude, settings.weather_timezone,
        source.name, cache.path if cache is not None else "(indisponível)",
    )
    return assistant, ""


def create_app(
    settings: Settings,
    detector: Detector | None = None,
    transcriber: Transcriber | None = None,
    llm: LanguageModel | None = None,
    vad: SpeechDetector | None = None,
    synthesizer: Synthesizer | None = None,
    memory: MemoryStore | None = None,
    weather_source: WeatherSource | None = None,
) -> FastAPI:
    hub = Hub()
    machine = StateMachine(AssistantState.BOOTING)
    started_at = time.time()
    performance = PerformancePolicy(settings)
    interaction_started = 0.0
    last_interaction_s = 0.0
    wake_reason = stt_reason = tts_reason = memory_reason = ""
    llm_status = {"reason": ""}  # motivo da indisponibilidade da IA na ultima verificacao
    if detector is None:
        detector, wake_reason = load_detector(settings)
    if transcriber is None:
        transcriber, stt_reason = load_transcriber(settings)
    if llm is None:
        llm = load_llm(settings)
    if vad is None:
        vad = load_vad(settings)
    if synthesizer is None:
        synthesizer, tts_reason = load_synthesizer(settings)
        # Recover only with an already installed supported voice; never download a model.
        piper_path = settings.tts_model.parent / "pt_BR-faber-medium.onnx"
        if synthesizer is None and settings.tts_enabled and settings.tts_engine == "kokoro" and piper_path.is_file():
            alternate, _ = load_synthesizer(replace(settings, tts_engine="piper", tts_model=piper_path))
            if alternate is not None:
                synthesizer = alternate
                settings.tts_engine, settings.tts_model = "piper", piper_path
                performance.notices.append("Kokoro indisponível: usando a voz alternativa Piper já instalada (masculina pt-BR). Restaure os arquivos Kokoro e reinicie para voltar à voz feminina.")
                tts_reason = ""
    if memory is None:
        memory, memory_reason = load_memory(settings)
    weather, weather_reason = load_weather(settings, weather_source)

    async def on_transcript(text: str) -> None:
        await hub.broadcast({"type": "transcript", "text": text, "final": True})

    async def on_reply(text: str, final: bool) -> None:
        if final:
            llm_status["reason"] = ""
            if isinstance(llm, OllamaClient) and llm.recovery_notice:
                await hub.broadcast({"type": "screen", "action": "notice", "message": llm.recovery_notice})
                performance.notices.append(llm.recovery_notice)
                llm.recovery_notice = ""
            await asyncio.to_thread(performance.observe, llm)
        event = {"type": "reply", "text": text, "final": final}
        if conversation.view_kind == "recipe":
            event["view"] = "recipe"
        await hub.broadcast(event)

    async def on_reply_error(code: str, message: str) -> None:
        if (not isinstance(llm, OllamaClient) or llm.approval_error
                or code in ("model_missing", "unreachable", "memory_insufficient", "model_load", "server")):
            llm_status["reason"] = message
        await hub.broadcast({"type": "reply_error", "code": code, "message": message, "retry": True})

    async def on_memory(event: dict) -> None:
        """Confirmação de memória salva/apagada (ou lista pedida por voz) para todos os clientes."""
        await hub.broadcast(event)

    async def on_weather(event: dict) -> None:
        """Cartão "Clima" da interface: resumo, hora da última atualização e aviso de dados antigos."""
        await hub.broadcast(event)

    # Voz: o texto e sintetizado aqui e reproduzido no quiosque. Sequencia por fala: `speak_start`,
    # quadros binarios PCM (um por frase), `speak_end`; `speak_stop` manda parar; `speak_error` explica.
    async def on_speak_start(utterance_id: int, sample_rate: int) -> None:
        await hub.broadcast(
            {"type": "speak_start", "id": utterance_id, "sample_rate": sample_rate, "channels": 1, "format": "pcm_s16le"}
        )

    async def on_speak_audio(utterance_id: int, pcm: bytes) -> None:
        await hub.broadcast_bytes(pcm)

    async def on_speak_end(utterance_id: int, samples: int) -> None:
        await hub.broadcast({"type": "speak_end", "id": utterance_id, "samples": samples})

    async def on_speak_stop(utterance_id: int) -> None:
        await hub.broadcast({"type": "speak_stop", "id": utterance_id})

    async def on_speak_error(code: str, message: str) -> None:
        await hub.broadcast({"type": "speak_error", "code": code, "message": message, "retry": True})

    speech = Speech(
        machine,
        synthesizer,
        on_start=on_speak_start,
        on_audio=on_speak_audio,
        on_end=on_speak_end,
        on_stop=on_speak_stop,
        on_error=on_speak_error,
        done_hold_s=settings.stt_done_hold_s,
        max_chars=settings.tts_max_chars,
    )

    # Memoria: com [memory] enabled, os comandos por voz sao interpretados mesmo se o arquivo nao abriu
    # (a Luna explica que a memoria esta indisponivel). Desativada, nada e interpretado.
    memory_commands = MemoryCommands(memory, max_context=settings.memory_max_context) if settings.memory_enabled else None
    # Receitas e ingredientes (Fase 7): os comandos de estoque usam a mesma memoria; a orientacao de receitas
    # e a fala resumida funcionam mesmo sem memoria. Desativado com [recipes] enabled = false.
    recipes = (
        RecipeAssistant(
            IngredientCommands(memory) if settings.memory_enabled else None,
            max_tokens=settings.recipes_max_tokens,
            spoken_max_chars=settings.recipes_spoken_max_chars,
            followup_ttl_s=settings.recipes_followup_ttl_s,
        )
        if settings.recipes_enabled
        else None
    )

    conversation = Conversation(
        machine,
        llm,
        on_reply=on_reply,
        on_error=on_reply_error,
        done_hold_s=settings.stt_done_hold_s,
        speaker=speech if speech.available else None,
        memory=memory_commands,
        on_memory=on_memory,
        recipes=recipes,
        weather=weather,
        on_weather=on_weather,
        on_screen=hub.broadcast,
        videos=VideoSearch(),
    )

    pipeline = VoicePipeline(
        machine,
        detector,
        transcriber=transcriber,
        vad=vad,
        threshold=settings.wake_threshold,
        cooldown_s=settings.wake_cooldown_s,
        preroll_s=settings.listen_preroll_s,
        listen_max_s=settings.listen_max_s,
        silence_s=settings.listen_silence_s,
        done_hold_s=settings.stt_done_hold_s,
        on_transcript=on_transcript,
        responder=conversation if conversation.available else None,
        speaker=speech if speech.available else None,
        barge_in=settings.tts_barge_in,
    )

    def wake_info() -> dict:
        return {
            "available": pipeline.available,
            "reason": wake_reason,
            "phrase": settings.wake_word,
            "model": detector.name if detector else "",
        }

    def stt_info() -> dict:
        return {
            "available": pipeline.transcription_available,
            "reason": stt_reason,
            "model": transcriber.name if transcriber else "",
            "language": settings.stt_language,
        }

    def llm_info() -> dict:
        info = {"enabled": settings.llm_enabled, "notice": settings.llm_hardware_notice}
        if llm is None:
            return {**info, "available": False,
                    "reason": settings.llm_unavailable_reason if settings.llm_enabled else "IA local desativada (llm.enabled = false)",
                    "model": (performance.manual_model or settings.llm_model) if settings.llm_enabled else ""}
        reason = llm_status["reason"]
        if isinstance(llm, OllamaClient):
            reason = llm.approval_error or reason
        info.update(available=not reason, reason=reason, model=llm.name)
        if isinstance(llm, OllamaClient):
            info["status"] = "failed" if reason else llm.quality_status
        return info

    def tts_info() -> dict:
        return {
            "available": speech.available,
            "reason": tts_reason,
            "engine": synthesizer.name if synthesizer else "",
            "voice": synthesizer.voice if synthesizer else "",
            "sample_rate": synthesizer.sample_rate if synthesizer else 0,
        }

    def memory_info(with_path: bool = False) -> dict:
        info: dict = {"available": memory is not None, "reason": memory_reason, "count": 0}
        if memory is not None:
            try:
                info["count"] = memory.count()
            except MemoryStorageError as exc:
                info["available"] = False
                info["reason"] = str(exc)
            if with_path:
                info["path"] = str(memory.path)
        return info

    def recipes_info() -> dict:
        if recipes is None:
            return {"enabled": False}
        ingredients = 0
        if memory is not None:
            try:
                ingredients = memory.count("ingredient")
            except MemoryStorageError:
                ingredients = 0
        return {
            "enabled": True,
            "ingredients": ingredients,
            "recipe_requests": recipes.recipe_requests,
            "followups": recipes.followups,
            "spoken_summaries": recipes.summaries,
            "recent_recipe": recipes.has_recent_recipe,
            "stock_commands": recipes.ingredients.commands if recipes.ingredients else 0,
            "stock_failures": recipes.ingredients.failures if recipes.ingredients else 0,
        }

    def weather_info(with_path: bool = False) -> dict:
        """Estado do tempo a partir do cache (nunca consulta a fonte): `welcome` e `/health`."""
        if weather is None:
            return {"enabled": False, "available": False, "reason": weather_reason, "location": settings.weather_location_name}
        info = weather.snapshot()
        info["reason"] = ""
        info["timezone"] = settings.weather_timezone
        if with_path:
            info["cache_path"] = str(weather.cache.path) if weather.cache is not None else ""
            info["cache_failures"] = weather.cache_failures
            info["current_ttl_s"] = settings.weather_current_ttl_s
            info["forecast_ttl_s"] = settings.weather_forecast_ttl_s
            info["stale_after_s"] = settings.weather_stale_after_s
        return info

    async def check_llm() -> None:
        """Verificação inicial (servidor e modelo), sem bloquear a inicialização nem o áudio."""
        if llm is None:
            return
        loop = asyncio.get_running_loop()
        try:
            reason = await loop.run_in_executor(None, llm.check)
        except Exception as exc:  # a verificacao nunca derruba o servidor
            reason = f"falha ao verificar a IA: {type(exc).__name__}"
        llm_status["reason"] = reason
        if reason:
            log.warning("IA local indisponivel: %s", reason)
        else:
            log.info("IA local pronta: modelo %s", llm.name)

    async def on_state(state: AssistantState, detail: str) -> None:
        nonlocal interaction_started, last_interaction_s
        if state == AssistantState.WAKE:
            interaction_started = time.perf_counter()
        elif state == AssistantState.IDLE and interaction_started:
            last_interaction_s = time.perf_counter() - interaction_started
            interaction_started = 0.0
        await hub.broadcast({"type": "state", **machine.snapshot()})

    machine.subscribe(on_state)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if isinstance(llm, OllamaClient):
            await asyncio.to_thread(performance.configure_llm, llm)
            if settings.llm_hardware_notice:
                log.warning("%s Modelo=%s; contexto=%s.", settings.llm_hardware_notice, llm.name, performance.context)
        if isinstance(llm, OllamaClient) and (settings.llm_manual_probed or (
                performance.manual_model and not settings.llm_quality_cached_only)):
            reason = ""
            try:
                if not settings.llm_manual_probed:
                    if not any(m.get("name") in (llm.name, llm.name + ":latest") for m in performance.models):
                        reason = await asyncio.to_thread(llm.check)
                    if not reason:
                        await asyncio.to_thread(llm.probe_conversation)
            except LLMError as exc:
                reason = f"{exc.message} ({exc.code})"
            if reason:
                llm.approval_error = f"{reason} Reinicie após corrigir ou selecione outro modelo nas configurações."
                llm_status["reason"] = llm.approval_error
                log.warning("IA indisponível: modelo manual %s; %s", llm.name, llm.approval_error)
            else:
                llm.quality_status = "ready"
            settings.llm_model = llm.name
        elif isinstance(llm, OllamaClient):
            registry = QualityRegistry(settings.llm_quality_path)
            hardware = performance.hardware
            budget = max(max(0, (hardware.ram_available or 0) - 2 * GIB) * .65,
                         max(0, (hardware.vram or 0) - 1.5 * GIB) if hardware.acceleration_verified else 0)
            limit = {"economic": 2.5, "balanced": 5, "performance": 10}[performance.profile] * GIB
            compatible = [m.get("name") for m in performance.models if m.get("name")
                          and isinstance(m.get("size"), (int, float)) and 0 < m["size"] <= min(limit, budget)]
            alternatives = compatible if settings.llm_auto_model else []
            if settings.bootstrap_managed or settings.llm_quality_cached_only:
                alternatives, compatible = [], []  # bootstrap already selected and tested this exact configuration
            passed = await asyncio.to_thread(registry.approve_or_fallback, llm, alternatives,
                                             allow_new=settings.llm_auto_model, compatible_names=compatible,
                                             cached_only=settings.bootstrap_managed or settings.llm_quality_cached_only)
            if not passed:
                llm_status["reason"] = llm.approval_error
            else:
                settings.llm_model = llm.name
                if llm.quality_status == "limited":
                    performance.notices.append(LIMITED)
                if llm.recovery_notice:
                    performance.notices.append(llm.recovery_notice)
                await asyncio.to_thread(performance.save)
        await machine.set(AssistantState.IDLE)
        log.info("servidor pronto em %s://%s:%s", settings.scheme, settings.bind_host, settings.port)
        check_task = asyncio.create_task(check_llm()) if not isinstance(llm, OllamaClient) or not llm.approval_error else None
        warm_task = asyncio.create_task(speech.warmup())
        weather_task = asyncio.create_task(weather.run_daily(on_weather)) if weather and settings.weather_auto_refresh else None
        try:
            yield
        finally:
            if weather_task:
                weather_task.cancel()
                await asyncio.gather(weather_task, return_exceptions=True)
        if check_task:
            check_task.cancel()
        warm_task.cancel()
        await speech.stop()
        await machine.set(AssistantState.OFFLINE, "desligando")
        if memory is not None:
            memory.close()
        if weather is not None and weather.cache is not None:
            weather.cache.close()

    app = FastAPI(title="Jarvis Kitchen", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.hub = hub
    app.state.machine = machine
    app.state.pipeline = pipeline
    app.state.conversation = conversation
    app.state.speech = speech
    app.state.memory = memory
    app.state.weather = weather
    app.state.performance = performance

    def authorize(request: Request) -> None:
        supplied = request.headers.get("authorization", "")
        if not secrets.compare_digest(supplied, f"Bearer {settings.auth_token}"):
            raise HTTPException(401, "Não autorizado")

    @app.get("/api/performance")
    async def performance_info(request: Request) -> JSONResponse:
        authorize(request)
        if llm is None:
            try:
                inventory = OllamaClient(settings.llm_url, settings.llm_model, connect_timeout_s=settings.llm_connect_timeout_s)
                performance.models = await asyncio.to_thread(inventory.list_models)
            except (OSError, ValueError, RuntimeError):
                pass
        snapshot = performance.snapshot()
        snapshot["llm"] = llm_info()
        snapshot["resources"]["gpu_utilization_percent"] = await asyncio.to_thread(gpu_utilization)
        return JSONResponse({**snapshot, "latency": {
            "detection_ms_per_frame": pipeline.detect_ms,
            "capture_s": pipeline.last_capture_s, "transcription_s": pipeline.last_stt_s,
            "llm_first_token_s": conversation.last_first_token_s, "llm_total_s": conversation.last_reply_s,
            "tts_first_audio_s": speech.last_synthesis_s, "tts_total_s": speech.last_total_s,
            "interaction_s": last_interaction_s}})

    @app.put("/api/performance")
    async def performance_update(request: Request) -> JSONResponse:
        authorize(request)
        if len(await request.body()) > 4096:
            raise HTTPException(413, "Preferências muito grandes")
        try:
            preferences = performance.validate(await request.json())
            allowed = {m.get("name") for m in performance.models}
            allowed.update(m["name"] for m in performance.recommended_models())
            allowed.add(performance.manual_model)
            if preferences["model"] and preferences["model"] not in allowed:
                raise ValueError("Selecione um modelo instalado ou recomendado")
            if "llm_enabled" in performance.preferences:
                preferences.setdefault("llm_enabled", performance.preferences["llm_enabled"])
            await asyncio.to_thread(performance.save, preferences)
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(503, "Não foi possível salvar as preferências") from exc
        return JSONResponse({**performance.snapshot(), "llm": llm_info()})

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "version": __version__,
                "uptime_s": round(time.time() - started_at, 1),
                "clients": len(hub),
                "wake": wake_info(),
                "transcription": stt_info(),
                "llm": llm_info(),
                "tts": tts_info(),
                "memory": memory_info(with_path=True),
                "recipes": recipes_info(),
                "weather": weather_info(with_path=True),
                "audio_frames": pipeline.frames,
                "wakes": pipeline.wakes,
                "transcripts": pipeline.transcripts,
                "endpointing": "vad" if pipeline.vad_available else "rms",
                "noise_rms": round(pipeline.noise_rms),
                "detector_ms_per_frame": round(pipeline.detect_ms, 2),
                "last_capture_s": round(pipeline.last_capture_s, 2),
                "last_transcription_s": round(pipeline.last_stt_s, 2),
                "last_speech_to_text_s": round(pipeline.last_speech_to_text_s, 2),
                "replies": conversation.replies,
                "reply_failures": conversation.failures,
                "last_reply_s": round(conversation.last_reply_s, 2),
                "last_first_token_s": round(conversation.last_first_token_s, 3),
                "last_interaction_s": round(last_interaction_s, 3),
                "memory_commands": conversation.memory_replies,
                "memory_failures": memory_commands.failures if memory_commands else 0,
                "weather_replies": conversation.weather_replies,
                "spoken": speech.spoken,
                "speech_failures": speech.failures,
                "last_synthesis_s": round(speech.last_synthesis_s, 2),
                "last_speech_audio_s": round(speech.last_audio_s, 2),
                **machine.snapshot(),
            }
        )

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        client_kind = await _authenticate(ws, settings)
        if client_kind is None:
            return
        hub.add(ws)
        peer = ws.client.host if ws.client else "?"
        log.info("cliente %s conectado (%s); total=%d", client_kind, peer, len(hub))
        try:
            await ws.send_json(
                {
                    "type": "welcome",
                    "server": "jarvis-kitchen",
                    "version": __version__,
                    "wake_word": settings.wake_word,
                    "debug": settings.debug_allow_state_override,
                    "wake": wake_info(),
                    "transcription": stt_info(),
                    "llm": llm_info(),
                    "tts": tts_info(),
                    "memory": memory_info(),
                    "weather": weather_info(),
                    **machine.snapshot(),
                }
            )
            while True:
                event = await ws.receive()
                if event["type"] == "websocket.disconnect":
                    raise WebSocketDisconnect(event.get("code", 1000))
                data = event.get("bytes")
                if data is not None:
                    await _handle_audio(ws, data, hub, pipeline)
                elif event.get("text") is not None:
                    await _handle_message(ws, event["text"], settings, machine, pipeline, conversation, speech, hub, memory)
        except WebSocketDisconnect:
            pass
        finally:
            hub.discard(ws)
            log.info("cliente %s desconectado (%s); total=%d", client_kind, peer, len(hub))

    # Interface estática por último, para não sombrear as rotas acima.
    if settings.client_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(settings.client_dir), html=True), name="client")
    else:
        log.warning("diretorio do cliente nao encontrado: %s", settings.client_dir)

    return app


async def _authenticate(ws: WebSocket, settings: Settings) -> str | None:
    """Exige `hello` com token válido como primeira mensagem. Nunca registra o token."""
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=HELLO_TIMEOUT_S)
        hello = Hello.model_validate(json.loads(raw))
    except WebSocketDisconnect:
        return None
    except (asyncio.TimeoutError, ValueError, ValidationError):
        await ws.close(code=CLOSE_BAD_HELLO, reason="hello esperado")
        return None
    if not secrets.compare_digest(hello.token, settings.auth_token):
        log.warning("token invalido de %s", ws.client.host if ws.client else "?")
        await ws.close(code=CLOSE_BAD_TOKEN, reason="nao autorizado")
        return None
    return hello.client


async def _handle_audio(ws: WebSocket, data: bytes, hub: Hub, pipeline: VoicePipeline) -> None:
    """Quadros PCM int16 LE mono 16 kHz. Apenas um cliente alimenta o detector por vez."""
    if len(data) > MAX_AUDIO_FRAME_BYTES or len(data) % 2:
        await ws.send_json(error("bad_audio", "quadro de audio invalido"))
        return
    if hub.audio_source is None:
        hub.audio_source = ws
        log.info("fonte de audio: %s", ws.client.host if ws.client else "?")
    elif hub.audio_source is not ws:
        await ws.send_json(error("busy", "outro cliente ja envia audio"))
        return
    await pipeline.feed(data)


async def _handle_message(
    ws: WebSocket,
    raw: str,
    settings: Settings,
    machine: StateMachine,
    pipeline: VoicePipeline,
    conversation: Conversation,
    speech: Speech,
    hub: Hub,
    memory: MemoryStore | None = None,
) -> None:
    try:
        msg = parse_inbound(json.loads(raw))
    except (ValueError, ValidationError) as exc:
        await ws.send_json(error("bad_message", f"mensagem invalida: {type(exc).__name__}"))
        return
    if isinstance(msg, Ping):
        await ws.send_json({"type": "pong", "t": msg.t})
    elif isinstance(msg, (MemoryList, MemoryDelete, MemoryClear)):
        await _handle_memory(ws, msg, memory, hub)
        if isinstance(msg, MemoryDelete) or isinstance(msg, MemoryClear) and msg.confirm:
            clear = getattr(conversation.llm, "clear_history", None)
            if clear:
                clear()
    elif isinstance(msg, SetState):
        if not settings.debug_allow_state_override:
            await ws.send_json(error("forbidden", "set_state desabilitado (debug_allow_state_override=false)"))
            return
        await machine.set(msg.state, msg.detail)
    elif isinstance(msg, Cancel):
        await pipeline.cancel()
    elif isinstance(msg, Retry):
        await conversation.retry()
    elif isinstance(msg, SpeakDone):
        if not msg.played:
            log.warning("quiosque nao reproduziu a fala %d: %s", msg.id, msg.error or "sem detalhe")
        speech.playback_done(msg.id, msg.played, msg.error)
    elif isinstance(msg, Replay):
        if not await speech.replay():
            await ws.send_json({"type": "screen", "action": "notice", "message": "Não há fala disponível para repetir agora"})
    elif isinstance(msg, Say):
        if not settings.debug_allow_state_override:
            await ws.send_json(error("forbidden", "say desabilitado (debug_allow_state_override=false)"))
            return
        if not speech.available:
            await ws.send_json(error("unavailable", "voz indisponivel"))
            return
        if machine.state not in (AssistantState.IDLE, AssistantState.SPEAKING, AssistantState.SYNTHESIZING):
            await ws.send_json(error("busy", "interacao em andamento"))
            return
        await hub.broadcast({"type": "reply", "text": msg.text, "final": True})
        await speech.speak(msg.text)


async def _handle_memory(ws: WebSocket, msg: MemoryList | MemoryDelete | MemoryClear, memory: MemoryStore | None, hub: Hub) -> None:
    """Gerência da memória pela tela. A lista vai só a quem pediu; exclusões vão a todos (as telas atualizam)."""
    if memory is None:
        await ws.send_json(error("unavailable", "memória indisponível"))
        return
    try:
        if isinstance(msg, MemoryList):
            items = [m.as_dict() for m in memory.all()]
            await ws.send_json({"type": "memory", "action": "list", "items": items, "count": len(items), "topic": ""})
        elif isinstance(msg, MemoryDelete):
            item = memory.get(msg.id)
            if item is None or not memory.remove(msg.id):
                await ws.send_json({"type": "memory", "action": "not_found", "count": memory.count()})
                return
            await hub.broadcast({"type": "memory", "action": "deleted", "item": item.as_dict(), "count": memory.count()})
        elif isinstance(msg, MemoryClear):
            if not msg.confirm:
                await ws.send_json(error("confirm_required", "memory_clear exige confirm = true"))
                return
            n = memory.clear()
            await hub.broadcast({"type": "memory", "action": "cleared", "count": 0, "removed": n})
    except MemoryStorageError as exc:
        log.warning("operacao de memoria pela interface falhou: %s", exc)
        await ws.send_json({"type": "memory", "action": "error", "message": REPLY_STORAGE_FAILED})
