"""Detecção da palavra de ativação com openWakeWord (ONNX, CPU). Processamento 100 % local."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

import numpy as np

SAMPLE_RATE = 16000
FRAME_SAMPLES = 1280  # 80 ms a 16 kHz, tamanho de quadro recomendado pelo openWakeWord
FRAME_BYTES = FRAME_SAMPLES * 2  # int16 little-endian mono


class Detector(Protocol):
    """Interface mínima: um quadro int16 de 1280 amostras entra, um score 0..1 sai."""

    name: str

    def predict(self, frame: np.ndarray) -> float: ...

    def reset(self) -> None: ...


class WakeWordUnavailable(RuntimeError):
    """Biblioteca ou modelo ausente. O servidor continua funcionando sem detecção."""


class OpenWakeWordDetector:
    def __init__(self, model_path: Path, features_dir: Path) -> None:
        try:
            from openwakeword.model import Model
        except ImportError as exc:  # pragma: no cover - depende do ambiente
            raise WakeWordUnavailable("openwakeword nao instalado (pip install -r requirements.txt)") from exc
        mel = features_dir / "melspectrogram.onnx"
        emb = features_dir / "embedding_model.onnx"
        for p in (model_path, mel, emb):
            if not p.is_file():
                hint = (
                    "treine o modelo customizado (docs/palavra-de-ativacao.md)"
                    if p == model_path and "atem" in p.name
                    else "execute scripts/prepare_models.py"
                )
                raise WakeWordUnavailable(f"modelo nao encontrado: {p.name}; {hint}")
        self._model = Model(
            wakeword_models=[str(model_path)],
            inference_framework="onnx",
            melspec_model_path=str(mel),
            embedding_model_path=str(emb),
        )
        self.name: str = next(iter(self._model.models))

    def predict(self, frame: np.ndarray) -> float:
        return float(self._model.predict(frame)[self.name])

    def reset(self) -> None:
        self._model.reset()
