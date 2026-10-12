r"""Verifica a voz da Luna configurada em config/settings.toml, sem microfone nem interface.

Uso:
  .venv\Scripts\python scripts\check_tts.py                                  # carrega a voz e fala uma frase de teste
  .venv\Scripts\python scripts\check_tts.py "Texto para falar"               # sintetiza o texto e mede os tempos
  .venv\Scripts\python scripts\check_tts.py --out %TEMP%\luna.wav "Texto"    # tambem grava um WAV para ouvir
"""
from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.api.app import load_synthesizer  # noqa: E402
from server.services.tts import TtsError, prepare_text, split_sentences  # noqa: E402
from server.storage.config import ConfigError, load_settings  # noqa: E402

DEFAULT_TEXT = (
    "Olá! Eu sou a Luna. Para o arroz ficar soltinho, refogue o alho no óleo, acrescente duas xícaras de "
    "água para cada xícara de arroz e cozinhe em fogo baixo, com a panela semitampada, por uns quinze minutos."
)


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # acentos no console do Windows
    out: Path | None = None
    if "--out" in argv:
        i = argv.index("--out")
        if i + 1 >= len(argv):
            print("--out exige um caminho de arquivo .wav")
            return 2
        out = Path(argv[i + 1])
        argv = argv[:i] + argv[i + 2 :]
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"erro de configuracao: {exc}")
        return 2
    if not settings.tts_enabled:
        print("voz desativada em config/settings.toml ([tts] enabled = false)")
        return 1
    t0 = time.perf_counter()
    tts, reason = load_synthesizer(settings)
    if tts is None:
        print(f"indisponivel: {reason}")
        return 1
    print(f"motor={tts.name} voz={tts.voice} taxa={tts.sample_rate} Hz carga={time.perf_counter() - t0:.1f} s")
    text = prepare_text(" ".join(argv) if argv else DEFAULT_TEXT, settings.tts_max_chars)
    pieces = split_sentences(text)
    print(f"> {text}")
    print(f"{len(pieces)} trecho(s)")
    t0 = time.perf_counter()
    first = 0.0
    chunks: list[bytes] = []
    try:
        for pcm in tts.synthesize(text):
            if not chunks:
                first = time.perf_counter() - t0
            chunks.append(pcm)
            print(f"  trecho {len(chunks)}: {len(pcm) / 2 / tts.sample_rate:.2f} s de audio apos {time.perf_counter() - t0:.2f} s")
    except TtsError as exc:
        print(f"falha ({exc.code}): {exc.message}")
        if exc.detail:
            print(f"detalhe: {exc.detail}")
        return 1
    total = time.perf_counter() - t0
    audio_s = sum(len(c) for c in chunks) / 2 / tts.sample_rate
    if not chunks:
        print("nenhum audio gerado")
        return 1
    print(f"primeiro trecho em {first:.2f} s; {audio_s:.1f} s de audio em {total:.2f} s (RTF {total / audio_s:.2f})")
    if out is not None:
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(tts.sample_rate)
            for c in chunks:
                w.writeframes(c)
        print(f"gravado em {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
