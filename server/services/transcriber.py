"""Transcrição de fala local com whisper.cpp (pywhispercpp, CPU). Nada sai desta máquina.

O áudio chega como PCM int16 mono 16 kHz em memória, é transcrito e descartado.
Nenhum modelo é baixado aqui: o arquivo ggml vem de scripts/prepare_models.py.
"""
from __future__ import annotations

import math
import os
import re
import threading
from pathlib import Path
from typing import Protocol

import numpy as np

SAMPLE_RATE = 16000
MIN_SECONDS = 1.1  # whisper.cpp rejeita entradas com menos de 1 s; completamos com silêncio
_NON_SPEECH = re.compile(r"[\[(][^\])]*[\])]")  # "[BLANK_AUDIO]", "(música)", "[Risos]"...
# Contexto do encoder do Whisper: 1500 posições cobrem a janela fixa de 30 s (50 por segundo de áudio).
# O encoder custa o mesmo para 2 s ou 30 s de fala; um contexto menor corta esse custo fixo.
AUDIO_CTX_MAX = 1500
AUDIO_CTX_PER_S = 50
AUDIO_CTX_MARGIN = 64
DEFAULT_AUDIO_CTX = 768  # medido no Ryzen 5 5600GT: 2,4 s -> 1,0 s por frase, mesmo texto em 18 amostras (limpas e com ruído)


class Transcriber(Protocol):
    name: str

    def transcribe(self, pcm: bytes, cancel: threading.Event | None = None) -> str:
        """PCM int16 LE mono 16 kHz -> texto. Retorna "" se nada for reconhecido ou se cancelado."""
        ...


class TranscriberUnavailable(RuntimeError):
    """Biblioteca ou modelo ausente. O servidor continua funcionando sem transcrição."""


class WhisperCppTranscriber:
    """whisper.cpp em CPU. Com o modelo de VAD (Silero) presente, trechos sem fala são
    descartados antes do decodificador, o que evita as "alucinações" do Whisper em
    silêncio ou ruído (medido: ruído puro passou de frases inventadas para texto vazio)."""

    def __init__(
        self,
        model_path: Path,
        *,
        language: str = "pt",
        threads: int = 0,
        vad_model: Path | None = None,
        audio_ctx: int = DEFAULT_AUDIO_CTX,
    ) -> None:
        try:
            from pywhispercpp.model import Model
        except ImportError as exc:  # pragma: no cover - depende do ambiente
            raise TranscriberUnavailable("pywhispercpp nao instalado (pip install -r requirements.txt)") from exc
        if not model_path.is_file():
            variant = model_path.stem.removeprefix("ggml-") or "small-q8_0"
            raise TranscriberUnavailable(
                f"modelo nao encontrado: {model_path.name}; execute scripts/prepare_models.py whisper-{variant}"
            )
        if model_path.name.endswith(".en.bin"):
            raise TranscriberUnavailable(f"{model_path.name} e um modelo so para ingles; use um multilingue")
        self.name = model_path.stem
        self.language = language
        # Todos os nucleos logicos: medido 15-20 % mais rapido que metade deles (12 vs 6 threads no 5600GT).
        self.threads = threads if threads > 0 else max(1, os.cpu_count() or 2)
        self.audio_ctx = max(0, audio_ctx)  # 0 = contexto completo (1500), o mais robusto e o mais lento
        self.vad = bool(vad_model and vad_model.is_file())
        self._lock = threading.Lock()  # o contexto do whisper.cpp nao e reentrante
        extra = {"vad": True, "vad_model_path": str(vad_model)} if self.vad else {}
        self._model = Model(
            str(model_path),
            redirect_whispercpp_logs_to=None,
            print_progress=False,
            print_realtime=False,
            language=language,
            translate=False,
            n_threads=self.threads,
            no_context=True,
            suppress_blank=True,
            suppress_nst=True,
            temperature_inc=0.0,  # sem novas tentativas em temperatura maior: latencia previsivel
            **extra,
        )

    def transcribe(self, pcm: bytes, cancel: threading.Event | None = None) -> str:
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        missing = int(MIN_SECONDS * SAMPLE_RATE) - len(audio)
        if missing > 0:
            audio = np.concatenate([audio, np.zeros(missing, dtype=np.float32)])
        abort = (lambda: cancel.is_set()) if cancel is not None else None
        ctx = audio_ctx_for(len(audio) / SAMPLE_RATE, self.audio_ctx)
        with self._lock:
            if cancel is not None and cancel.is_set():
                return ""
            segments = self._model.transcribe(audio, abort_callback=abort, audio_ctx=ctx)
        if cancel is not None and cancel.is_set():
            return ""
        return clean_text(" ".join(s.text for s in segments))


def audio_ctx_for(seconds: float, minimum: int) -> int:
    """Contexto do encoder para `seconds` de áudio: nunca menor que `minimum` nem que o necessário para
    o áudio caber (com margem), limitado a 1500. `minimum` 0 desliga a redução (contexto completo)."""
    if minimum <= 0:
        return 0
    needed = int(math.ceil(seconds * AUDIO_CTX_PER_S)) + AUDIO_CTX_MARGIN
    return min(AUDIO_CTX_MAX, max(minimum, needed))


def clean_text(text: str) -> str:
    """Remove marcadores de não-fala e espaços repetidos."""
    return " ".join(_NON_SPEECH.sub(" ", text).split())
