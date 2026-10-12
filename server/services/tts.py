"""Síntese de voz local (texto -> PCM int16 mono), 100 % offline, no servidor Ryzen.

Dois motores, ambos ONNX em CPU, com o espeak-ng embutido nas próprias wheels (nada para instalar
no sistema):

- Kokoro-82M (`kokoro-onnx`, MIT; pesos Apache-2.0), 24 kHz. É o padrão porque tem a única voz
  feminina pt-BR de código aberto que encontramos: `pf_dora`. Medido no Ryzen 5 5600GT: ~0,3 s de
  CPU por segundo de áudio (RTF 0,26-0,30), ~2 s de carga, ~330 MB em disco.
- Piper (`piper-tts`, GPL-3.0), 22,05 kHz. As vozes oficiais pt-BR (faber, cadu, edresson, jeff)
  são todas masculinas; fica como alternativa mais leve (RTF ~0,2, 63 MB).

O texto é falado frase a frase: cada trecho pronto é entregue na hora, então a fala começa antes de
toda a resposta estar sintetizada. Nenhum arquivo de áudio é gravado. Nenhum modelo é baixado aqui:
os arquivos vêm de scripts/prepare_models.py.
"""
from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import Iterator, Protocol

import numpy as np

log = logging.getLogger("jarvis.tts")

KOKORO_SAMPLE_RATE = 24000
KOKORO_PT_VOICES = ("pf_dora", "pm_alex", "pm_santa")  # vozes em portugues do Kokoro v1.0 (pf = feminina)
SENTENCE_PAUSE_S = 0.35  # silencio acrescentado apos cada frase (os motores cortam o silencio final)
CLAUSE_PAUSE_S = 0.12
MAX_SENTENCE_CHARS = 160  # frases maiores sao divididas em oracoes: a primeira fala sai antes
MIN_SENTENCE_CHARS = 16  # frases muito curtas ("Claro!") sao faladas junto com a seguinte
MAX_TEXT_CHARS = 1500

_SENTENCE_END = re.compile(r"(?<=[.!?…])(?<!\d\.)\s+")
_CLAUSE_SPLIT = re.compile(r"(?<=[,;:])\s+")
_MARKDOWN_MARKS = re.compile(r"[*_`#>~|\\]+")
_LINE_BULLET = re.compile(r"^\s*(?:[-•▪◦]+|\d+[.)])\s+", re.MULTILINE)
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")
_URL = re.compile(r"https?://\S+")


class TtsUnavailable(RuntimeError):
    """Biblioteca ou modelo ausente. O servidor continua funcionando sem voz."""


class TtsError(RuntimeError):
    """Falha ao sintetizar. `code` identifica o tipo; `message` é amigável, em pt-BR."""

    def __init__(self, code: str, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail  # informacao tecnica so para o log


class Synthesizer(Protocol):
    name: str  # motor/modelo
    voice: str
    sample_rate: int

    def synthesize(self, text: str, cancel: threading.Event | None = None) -> Iterator[bytes]:
        """Texto -> trechos PCM int16 LE mono em `sample_rate`, um por frase, conforme ficam prontos.
        Para de produzir quando `cancel` é acionado. Levanta TtsError em falhas."""
        ...

    def warmup(self) -> None:
        """Primeira inferência (mais lenta) fora do caminho da primeira resposta."""
        ...


def prepare_text(text: str, max_chars: int = MAX_TEXT_CHARS) -> str:
    """Texto da IA -> texto para falar: sem marcas de Markdown, marcadores de lista, emojis e URLs;
    quebras de linha viram fim de frase. Textos muito longos são cortados no fim de uma frase."""
    text = _URL.sub("", text)
    text = _EMOJI.sub("", text)
    text = _LINE_BULLET.sub("", text)
    text = _MARKDOWN_MARKS.sub(" ", text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    out = "\n".join(line for line in lines if line).strip()
    if len(out) <= max_chars:
        return out
    cut = out[:max_chars]
    boundary = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "), cut.rfind("\n"))
    if boundary > max_chars // 2:
        cut = cut[: boundary + 1]
    return cut.strip()


def split_sentences(text: str, max_chars: int = MAX_SENTENCE_CHARS, min_chars: int = MIN_SENTENCE_CHARS) -> list[tuple[str, float]]:
    """Divide em frases (e orações, se a frase for muito longa; frases muito curtas juntam-se à seguinte).
    Devolve (trecho, pausa_depois_s)."""
    pieces: list[tuple[str, float]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        sentences = [s.strip() for s in _SENTENCE_END.split(line) if s.strip()]
        merged: list[str] = []
        for sentence in sentences:
            if merged and len(merged[-1]) < min_chars and len(merged[-1]) + 1 + len(sentence) <= max_chars:
                merged[-1] = f"{merged[-1]} {sentence}"
            else:
                merged.append(sentence)
        for sentence in merged:
            if len(sentence) <= max_chars:
                pieces.append((sentence, SENTENCE_PAUSE_S))
                continue
            clauses = [c.strip() for c in _CLAUSE_SPLIT.split(sentence) if c.strip()]
            chunk = ""
            for clause in clauses:
                if chunk and len(chunk) + 1 + len(clause) > max_chars:
                    pieces.append((chunk, CLAUSE_PAUSE_S))
                    chunk = clause
                else:
                    chunk = f"{chunk} {clause}".strip()
            if chunk:
                pieces.append((chunk, SENTENCE_PAUSE_S))
    return pieces


def _pcm16(audio: np.ndarray, volume: float, sample_rate: int, pause_s: float) -> bytes:
    samples = np.asarray(audio, dtype=np.float32) * volume
    peak = float(np.abs(samples).max()) if samples.size else 0.0
    if peak > 0.98:
        samples *= 0.98 / peak  # ganho pedido alto demais: evita distorcer
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
    if pause_s > 0:
        pcm = np.concatenate([pcm, np.zeros(int(pause_s * sample_rate), dtype=np.int16)])
    return pcm.tobytes()


class KokoroSynthesizer:
    """Kokoro-82M via kokoro-onnx (CPU). Voz padrão `pf_dora` (feminina, pt-BR)."""

    def __init__(
        self,
        model_path: Path,
        voices_path: Path,
        *,
        voice: str = "pf_dora",
        language: str = "pt-br",
        speed: float = 1.0,
        volume: float = 1.0,
    ) -> None:
        try:
            from kokoro_onnx import Kokoro
        except ImportError as exc:  # pragma: no cover - depende do ambiente
            raise TtsUnavailable("kokoro-onnx nao instalado (pip install -r requirements.txt)") from exc
        for p in (model_path, voices_path):
            if not p.is_file():
                raise TtsUnavailable(f"modelo nao encontrado: {p.name}; execute scripts/prepare_models.py kokoro")
        self._kokoro = Kokoro(str(model_path), str(voices_path))
        if voice not in self._kokoro.voices:
            pt = [v for v in self._kokoro.get_voices() if v in KOKORO_PT_VOICES]
            raise TtsUnavailable(f"voz '{voice}' nao existe em {voices_path.name}; em portugues: {', '.join(pt) or '-'}")
        self.name = f"kokoro/{model_path.stem}"
        self.voice = voice
        self.language = language
        self.speed = speed
        self.volume = volume
        self.sample_rate = KOKORO_SAMPLE_RATE
        self._lock = threading.Lock()  # o espeak-ng tem estado global; uma sintese por vez

    def synthesize(self, text: str, cancel: threading.Event | None = None) -> Iterator[bytes]:
        for sentence, pause in split_sentences(text):
            if cancel is not None and cancel.is_set():
                return
            with self._lock:
                try:
                    audio, rate = self._kokoro.create(sentence, voice=self.voice, speed=self.speed, lang=self.language)
                except ValueError as exc:  # trecho sem fonemas (so simbolos): pula
                    log.debug("trecho ignorado: %s", exc)
                    continue
                except Exception as exc:
                    raise TtsError("engine", "A voz falhou ao gerar o áudio. Tente de novo.", f"{type(exc).__name__}: {exc}") from exc
            yield _pcm16(audio, self.volume, rate, pause)

    def warmup(self) -> None:
        for _ in self.synthesize("Olá."):
            pass


class PiperSynthesizer:
    """Piper (piper-tts, CPU). O arquivo .onnx.json deve estar ao lado do .onnx."""

    def __init__(self, model_path: Path, *, speed: float = 1.0, volume: float = 1.0) -> None:
        try:
            from piper import PiperVoice, SynthesisConfig
        except ImportError as exc:  # pragma: no cover - depende do ambiente
            raise TtsUnavailable("piper-tts nao instalado (pip install piper-tts)") from exc
        config_path = Path(f"{model_path}.json")
        for p in (model_path, config_path):
            if not p.is_file():
                raise TtsUnavailable(f"modelo nao encontrado: {p.name}; execute scripts/prepare_models.py piper-{model_path.stem}")
        self._voice = PiperVoice.load(str(model_path), config_path=str(config_path))
        self._config = SynthesisConfig(length_scale=1.0 / speed, volume=volume)
        self.name = f"piper/{model_path.stem}"
        self.voice = model_path.stem
        self.sample_rate = int(self._voice.config.sample_rate)
        self._lock = threading.Lock()

    def synthesize(self, text: str, cancel: threading.Event | None = None) -> Iterator[bytes]:
        for sentence, pause in split_sentences(text):
            if cancel is not None and cancel.is_set():
                return
            with self._lock:
                try:
                    chunks = [c.audio_float_array for c in self._voice.synthesize(sentence, syn_config=self._config)]
                except Exception as exc:
                    raise TtsError("engine", "A voz falhou ao gerar o áudio. Tente de novo.", f"{type(exc).__name__}: {exc}") from exc
            if not chunks:
                continue
            yield _pcm16(np.concatenate(chunks), 1.0, self.sample_rate, pause)  # o volume ja foi aplicado pelo Piper

    def warmup(self) -> None:
        for _ in self.synthesize("Olá."):
            pass
