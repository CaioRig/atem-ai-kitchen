r"""Transcreve um arquivo WAV (16 kHz, mono, 16 bits) com o whisper.cpp local, sem microfone.

Uso: .venv\Scripts\python scripts\check_transcription.py caminho\frase.wav
Gere uma amostra em portugues com a voz do Windows (PowerShell):
  Add-Type -AssemblyName System.Speech
  $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
  $s.SelectVoice('Microsoft Maria Desktop')
  $f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, 'Sixteen', 'Mono')
  $s.SetOutputToWaveFile("$env:TEMP\frase.wav", $f); $s.Speak('Que horas são?'); $s.Dispose()
"""
from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.services.transcriber import TranscriberUnavailable, WhisperCppTranscriber  # noqa: E402
from server.storage.config import load_settings  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    settings = load_settings()
    t0 = time.perf_counter()
    try:
        stt = WhisperCppTranscriber(
            settings.stt_model,
            language=settings.stt_language,
            threads=settings.stt_threads,
            vad_model=settings.stt_vad_model,
            audio_ctx=settings.stt_audio_ctx,
        )
    except TranscriberUnavailable as exc:
        print(f"transcricao indisponivel: {exc}")
        return 1
    print(
        f"modelo={stt.name} idioma={stt.language} threads={stt.threads} vad={stt.vad} "
        f"audio_ctx={stt.audio_ctx or 'completo'} carga={time.perf_counter() - t0:.2f}s"
    )
    with wave.open(argv[0], "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
            print("o WAV precisa ser 16 kHz, mono, 16 bits")
            return 2
        pcm = w.readframes(w.getnframes())
    t0 = time.perf_counter()
    text = stt.transcribe(pcm)
    print(f"audio={len(pcm) / 32000:.1f}s transcricao={time.perf_counter() - t0:.2f}s")
    print(f"texto: {text!r}")
    return 0 if text else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
