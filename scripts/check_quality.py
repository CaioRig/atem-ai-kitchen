"""Run the eight real Portuguese checks against the configured local/LAN Ollama."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.services.model_quality import QualityRegistry, configured_client
from server.storage.config import load_settings


def check_flow(settings, client) -> dict:
    """Synthetic spoken greeting -> real Whisper/Ollama/Kokoro -> actual WS events.

    Activation and playback acknowledgement are simulated, not the physical mic/speaker.
    """
    import asyncio
    import tempfile
    import threading
    import time
    from dataclasses import replace
    import numpy as np
    from fastapi.testclient import TestClient
    from server.api.app import create_app, load_synthesizer, load_transcriber
    from server.assistant.state import AssistantState
    from server.services.llm import clean_reply
    from server.services.model_quality import normalized
    from server.services.tts import prepare_text
    engine, reason = load_synthesizer(settings)
    transcriber, stt_reason = load_transcriber(settings)
    if engine is None or transcriber is None:
        raise RuntimeError(reason or stt_reason)
    input_audio = np.frombuffer(b"".join(engine.synthesize("Bom dia!")), dtype=np.int16).astype(np.float32)
    positions = np.arange(int(len(input_audio) * 16000 / engine.sample_rate)) * engine.sample_rate / 16000
    pcm = np.interp(positions, np.arange(len(input_audio)), input_audio).astype(np.int16).tobytes()
    spoken, raw, returned = [], [], []
    class TracingVoice:
        name, voice, sample_rate = engine.name, engine.voice, engine.sample_rate
        def warmup(self):
            engine.warmup()
        def synthesize(self, text, cancel=None):
            spoken.append(text)
            yield from engine.synthesize(text, cancel)
    class SyntheticActivation:
        name = "synthetic-test-entry"
        def predict(self, frame):
            return 0
        def reset(self):
            pass
    original = client.reply
    def traced(text, on_delta=None, cancel=None, context="", max_tokens=None):
        def delta(piece):
            raw.append(piece)
            if on_delta:
                on_delta(piece)
        answer = original(text, delta, cancel, context, max_tokens)
        returned.append(answer)
        return answer
    client.reply = traced
    with tempfile.TemporaryDirectory(prefix="luna-quality-") as directory:
        current = replace(settings, llm_model=client.name, llm_auto_model=False, llm_num_ctx=client.num_ctx,
            performance_path=Path(directory) / "performance.json", performance_profile="balanced" if client.num_thread == 8 else "economic" if client.num_thread <= 4 else "performance",
            memory_enabled=False, weather_enabled=False, weather_auto_refresh=False)
        app = create_app(current, detector=SyntheticActivation(), transcriber=transcriber, llm=client, synthesizer=TracingVoice())
        with TestClient(app) as http, http.websocket_connect("/ws") as ws:
            ws.send_json({"type": "hello", "token": current.auth_token, "client": "ui", "version": 1})
            ws.receive_json()
            async def captured_utterance():
                await app.state.machine.set(AssistantState.TRANSCRIBING)
                app.state.pipeline._last_speech_wall = app.state.pipeline._finish_wall = time.perf_counter()
                await app.state.pipeline._transcribe(pcm, threading.Event())
            started = time.perf_counter()
            http.portal.call(captured_utterance)
            events, audio_bytes = [], 0
            async def next_message():
                return await asyncio.wait_for(ws._send_rx.receive(), timeout=60)
            while True:
                event = ws.portal.call(next_message)
                if event.get("bytes") is not None:
                    audio_bytes += len(event["bytes"])
                    continue
                message = json.loads(event["text"])
                events.append(message)
                if message["type"] in ("reply_error", "speak_error"):
                    raise RuntimeError(message["type"])
                if message["type"] == "speak_end":
                    ws.send_json({"type": "speak_done", "id": message["id"], "played": True})
                    break
            transcript = next(e["text"] for e in events if e["type"] == "transcript")
            displayed = next(e["text"] for e in events if e["type"] == "reply" and e["final"])
            assert normalized(transcript).strip(".! ") == "bom dia"
            assert clean_reply("".join(raw)) == returned[-1] == displayed
            assert spoken[-1] == prepare_text(displayed)
            assert audio_bytes > 0
            result = {"model": client.name, "transcript": transcript, "ollama_stream": "".join(raw),
                "ui_websocket_text": displayed, "tts_input": spoken[-1], "pcm_bytes": audio_bytes,
                "llm_metrics": client.last_metrics, "first_audio_s": app.state.speech.last_synthesis_s,
                "asr_s": app.state.pipeline.last_stt_s, "through_synthesis_s": time.perf_counter() - started,
                "physical_activation_and_playback_verified": False}
    client.reply = original
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="refazer mesmo com aprovação salva")
    parser.add_argument("--model", help="avaliar um modelo instalado específico; não baixa modelos")
    parser.add_argument("--show-answers", action="store_true", help="mostrar somente as respostas sintéticas dos testes")
    parser.add_argument("--flow", action="store_true", help="testar também ASR, WebSocket e TTS reais com fala sintética")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    settings = load_settings()
    if args.model:
        settings.llm_model = args.model
    client = configured_client(settings)
    try:
        report = QualityRegistry(settings.llm_quality_path).ensure(client, force=args.force)
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"IA não aprovada: {type(exc).__name__}")
        return 1
    print(f"Modelo: {client.name}; aprovado: {report['approved']}; opções: {json.dumps(client.options())}")
    for test in report["tests"]:
        print(f"{test['case']}: {'OK' if test['passed'] else test['reason']} ({test['total_s']:.3f} s)")
        if args.show_answers:
            for answer in test["answers"]:
                print(answer)
    if args.flow and report["approved"]:
        result = check_flow(settings, client)
        path = settings.llm_quality_path.with_name("quality-flow.json")
        path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if report["approved"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
