r"""Mede o score do detector em um arquivo WAV (16 kHz, mono, 16 bits), sem microfone.

Uso: .venv\Scripts\python scripts\check_wakeword.py caminho\amostra.wav
Usa o modelo de [wake] model em config/settings.toml (padrao: hey_luna.onnx; para "hey Atem" ver
docs/palavra-de-ativacao.md). Gere uma amostra com a voz do Windows (PowerShell):
  Add-Type -AssemblyName System.Speech
  $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
  $f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, 'Sixteen', 'Mono')
  $s.SetOutputToWaveFile("$env:TEMP\hey_luna.wav", $f); $s.Speak("Hey Luna"); $s.Dispose()
"""
from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.services.wakeword import FRAME_SAMPLES, OpenWakeWordDetector  # noqa: E402
from server.storage.config import load_settings  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    settings = load_settings()
    det = OpenWakeWordDetector(settings.wake_model, settings.wake_features_dir)
    with wave.open(argv[0], "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
            print("o WAV precisa ser 16 kHz, mono, 16 bits")
            return 2
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    pcm = np.concatenate([pcm, np.zeros(FRAME_SAMPLES * 10, dtype=np.int16)])  # cauda para o modelo "fechar" a frase
    best, best_t = 0.0, 0.0
    for i in range(0, len(pcm) - FRAME_SAMPLES + 1, FRAME_SAMPLES):
        s = det.predict(pcm[i : i + FRAME_SAMPLES])
        if s > best:
            best, best_t = s, i / 16000
    verdict = "DETECTADO" if best >= settings.wake_threshold else "nao detectado"
    print(f"modelo={det.name} score_max={best:.3f} em t={best_t:.2f}s limiar={settings.wake_threshold} -> {verdict}")
    return 0 if best >= settings.wake_threshold else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
