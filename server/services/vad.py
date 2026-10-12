"""Detecção de fala (VAD) por quadro com o Silero VAD (ONNX, CPU), via a classe já incluída no openWakeWord.

Usado para decidir o fim da fala depois da palavra de ativação. Um limiar de energia (RMS) confunde
ruído de fundo com fala e perde fala baixa; o Silero responde à presença de voz, não ao volume
(medido: fala a 15 % do volume normal → probabilidade 0,99; ruído rosa alto → 0,17). Custo: ~0,4 ms por
quadro de 80 ms. Processamento 100 % local.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

import numpy as np


class SpeechDetector(Protocol):
    """Um quadro int16 de 1280 amostras entra, a probabilidade de haver fala (0..1) sai."""

    name: str

    def predict(self, frame: np.ndarray) -> float: ...

    def reset(self) -> None: ...


class VadUnavailable(RuntimeError):
    """Biblioteca ou modelo ausente. O fim da fala volta a usar o limiar de energia."""


class SileroVad:
    def __init__(self, model_path: Path) -> None:
        try:
            from openwakeword.vad import VAD
        except ImportError as exc:  # pragma: no cover - depende do ambiente
            raise VadUnavailable("openwakeword nao instalado (pip install -r requirements.txt)") from exc
        if not model_path.is_file():
            raise VadUnavailable(f"modelo nao encontrado: {model_path.name}; execute scripts/prepare_models.py")
        self._vad = VAD(str(model_path))
        self.name = model_path.stem

    def predict(self, frame: np.ndarray) -> float:
        # O quadro inteiro (80 ms) como uma unica janela: dentro da faixa recomendada (30-96 ms) e uma inferencia so.
        return float(self._vad.predict(frame, frame_size=len(frame)))

    def reset(self) -> None:
        self._vad.reset_states()
