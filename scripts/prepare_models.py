r"""Baixa os modelos para models/ (execucao manual, com internet). Nada e baixado na inicializacao.

Uso: .venv\Scripts\python scripts\prepare_models.py [nome ...]
Padrao: hey_luna whisper-small-q8_0 kokoro
  - openWakeWord: features (melspectrogram + embedding, base de qualquer modelo, e o Silero VAD
    usado para o fim da fala), os pre-treinados hey_jarvis, alexa, hey_mycroft (ingles, oficiais)
    e hey_luna (colecao da comunidade Home Assistant, MIT; em uso no projeto). A palavra "hey Atem"
    usa um modelo customizado que NAO e baixado: treine conforme docs/palavra-de-ativacao.md e
    copie o arquivo para models/openwakeword/hey_atem.onnx.
  - whisper.cpp (ggml, multilingue): whisper-tiny, whisper-base, whisper-small, whisper-medium
    e variantes quantizadas: whisper-small-q8_0 (em uso: mesmo texto que o small completo nos
    testes, ~20 % mais rapido e metade da RAM) e whisper-small-q5_1 (menor ainda).
    Evite os modelos ".en": sao so para ingles. Destino: models/whisper/ggml-<nome>.bin
  - Voz (TTS): kokoro (Kokoro-82M fp32, 326 MB + vozes 28 MB; voz feminina pt-BR pf_dora, em uso),
    kokoro-int8 (114 MB, um pouco pior) e piper-<voz> para o Piper (pt_BR-faber-medium 63 MB,
    pt_BR-cadu-medium, pt_BR-edresson-low, pt_BR-jeff-medium; todas masculinas).
    Destino: models/tts/. Origens: github.com/thewh1teagle/kokoro-onnx (releases) e
    huggingface.co/rhasspy/piper-voices.
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OWW_DEST = ROOT / "models" / "openwakeword"
OWW_BASE = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
FEATURES = ["melspectrogram.onnx", "embedding_model.onnx", "silero_vad.onnx"]  # o VAD decide o fim da fala
PRETRAINED = {"hey_jarvis": "hey_jarvis_v0.1.onnx", "alexa": "alexa_v0.1.onnx", "hey_mycroft": "hey_mycroft_v0.1.onnx"}
# Modelos da comunidade (github.com/fwartner/home-assistant-wakewords-collection, MIT), treinados
# com o notebook oficial. "hey Luna" soa igual em portugues; validado com TTS em docs/microfone.md.
COMMUNITY_BASE = "https://raw.githubusercontent.com/fwartner/home-assistant-wakewords-collection/main/"
COMMUNITY = {"hey_luna": "en/Hey%20Luna/hey_luna.onnx"}
WHISPER_DEST = ROOT / "models" / "whisper"
WHISPER_BASE = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
WHISPER_SIZES = ("tiny", "base", "small", "medium", "large-v3", "large-v3-turbo")
VAD_FILE = "ggml-silero-v5.1.2.bin"
VAD_URL = "https://huggingface.co/ggml-org/whisper-vad/resolve/main/" + VAD_FILE
# Voz: Kokoro-82M exportado para ONNX pelo projeto kokoro-onnx (MIT; pesos Apache-2.0, hexgrad/Kokoro-82M).
# Tamanhos conferidos em 2026-10-11: fp32 325,5 MB, int8 114,1 MB, vozes 28,2 MB.
TTS_DEST = ROOT / "models" / "tts"
KOKORO_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/"
KOKORO = {"kokoro": ["kokoro-v1.0.onnx", "voices-v1.0.bin"], "kokoro-int8": ["kokoro-v1.0.int8.onnx", "voices-v1.0.bin"]}
# Vozes oficiais do Piper em pt-BR (rhasspy/piper-voices, MIT; todas masculinas). .onnx + .onnx.json.
PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/"
PIPER_VOICES = {
    "pt_BR-faber-medium": "faber/medium/",
    "pt_BR-cadu-medium": "cadu/medium/",
    "pt_BR-edresson-low": "edresson/low/",
    "pt_BR-jeff-medium": "jeff/medium/",
}


def fetch(url: str, target: Path) -> Path:
    if target.exists() and target.stat().st_size > 0:
        print(f"ok      {target.relative_to(ROOT)} (ja existe)")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part")
    print(f"baixando {target.name} ...", flush=True)
    with urllib.request.urlopen(url, timeout=120) as r, tmp.open("wb") as f:
        while chunk := r.read(1 << 16):
            f.write(chunk)
    tmp.replace(target)
    print(f"ok      {target.relative_to(ROOT)} ({target.stat().st_size // 1024 // 1024} MB)")
    return target


def main(argv: list[str]) -> int:
    names = argv or ["hey_luna", "whisper-small-q8_0", "kokoro"]
    for n in names:
        if n.startswith("whisper-"):
            size = n[len("whisper-"):]
            if size.endswith(".en") or not size.startswith(WHISPER_SIZES):
                print(f"modelo whisper invalido: {n}. Use whisper-<{'|'.join(WHISPER_SIZES)}>[-q5_1|-q8_0]", file=sys.stderr)
                return 2
            fetch(f"{WHISPER_BASE}ggml-{size}.bin", WHISPER_DEST / f"ggml-{size}.bin")
            fetch(VAD_URL, WHISPER_DEST / VAD_FILE)  # VAD (Silero, 0,9 MB): evita texto inventado em silencio/ruido
        elif n in KOKORO:
            for f in KOKORO[n]:
                fetch(KOKORO_BASE + f, TTS_DEST / f)
        elif n.startswith("piper-"):
            voice = n[len("piper-"):]
            if voice not in PIPER_VOICES:
                print(f"voz piper desconhecida: {voice}. Disponiveis: {', '.join(PIPER_VOICES)}", file=sys.stderr)
                return 2
            for ext in (".onnx", ".onnx.json"):
                fetch(f"{PIPER_BASE}{PIPER_VOICES[voice]}{voice}{ext}", TTS_DEST / f"{voice}{ext}")
        elif n == "features":
            for f in FEATURES:
                fetch(OWW_BASE + f, OWW_DEST / f)
            custom = OWW_DEST / "hey_atem.onnx"
            status = "ok      " if custom.is_file() else "FALTA   "
            print(f"{status}{custom.relative_to(ROOT)} (modelo customizado; ver docs/palavra-de-ativacao.md)")
        elif n in PRETRAINED:
            for f in FEATURES:
                fetch(OWW_BASE + f, OWW_DEST / f)
            fetch(OWW_BASE + PRETRAINED[n], OWW_DEST / PRETRAINED[n])
        elif n in COMMUNITY:
            for f in FEATURES:
                fetch(OWW_BASE + f, OWW_DEST / f)
            fetch(COMMUNITY_BASE + COMMUNITY[n], OWW_DEST / f"{n}.onnx")
        else:
            print(
                f"modelo desconhecido: {n}. Disponiveis: features, {', '.join(PRETRAINED)}, {', '.join(COMMUNITY)}, "
                f"whisper-<tamanho>, {', '.join(KOKORO)}, piper-<{'|'.join(PIPER_VOICES)}>",
                file=sys.stderr,
            )
            return 2
    print("Concluido. Ajuste [wake], [transcription] e [tts] em config/settings.toml se necessario.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
