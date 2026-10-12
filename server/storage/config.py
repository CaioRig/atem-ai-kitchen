"""Carregamento da configuração (TOML). Sem dependências externas."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from server.services.llm import local_url_problem

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "settings.toml"


class ConfigError(RuntimeError):
    pass


@dataclass
class Settings:
    bind_host: str = "127.0.0.1"
    port: int = 8800
    # HTTPS opcional (certificado + chave PEM). Navegadores so liberam o microfone em contexto
    # seguro (HTTPS ou localhost); gere o par com scripts/make-cert.ps1.
    tls_certfile: Path | None = None
    tls_keyfile: Path | None = None
    log_level: str = "INFO"
    debug_allow_state_override: bool = False
    auth_token: str = ""
    wake_word: str = "hey Luna"
    client_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "client" / "public")
    log_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "logs")
    # Palavra de ativacao (openWakeWord). Padrao: hey_luna (comunidade, scripts/prepare_models.py).
    # A meta "hey Atem" exige modelo customizado treinado conforme docs/palavra-de-ativacao.md.
    wake_model: Path = field(default_factory=lambda: PROJECT_ROOT / "models" / "openwakeword" / "hey_luna.onnx")
    wake_features_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "models" / "openwakeword")
    wake_threshold: float = 0.5
    wake_cooldown_s: float = 2.0
    # Fim da fala por voz (Silero VAD, ONNX, baixado com os modelos do openWakeWord). Ausente = limiar de energia.
    wake_vad_model: Path = field(default_factory=lambda: PROJECT_ROOT / "models" / "openwakeword" / "silero_vad.onnx")
    listen_max_s: float = 8.0
    listen_silence_s: float = 1.2
    listen_preroll_s: float = 0.32  # audio anterior a deteccao que entra na captura (inicio da frase)
    # Transcricao local (whisper.cpp via pywhispercpp). Modelo ggml multilingue em models/whisper.
    stt_model: Path = field(default_factory=lambda: PROJECT_ROOT / "models" / "whisper" / "ggml-small-q8_0.bin")
    stt_vad_model: Path = field(default_factory=lambda: PROJECT_ROOT / "models" / "whisper" / "ggml-silero-v5.1.2.bin")
    stt_language: str = "pt"
    stt_threads: int = 0  # 0 = todos os nucleos logicos
    stt_audio_ctx: int = 768  # contexto minimo do encoder (0 = completo, 1500); ver server/services/transcriber.py
    stt_done_hold_s: float = 4.0  # tempo que "Transcricao concluida" fica na tela antes de voltar a espera
    # IA local via Ollama, na rede local (sem nuvem, sem chaves). URL e modelo vem de [llm] no TOML.
    llm_enabled: bool = True
    llm_url: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen2.5:7b"
    llm_system_prompt: str = ""  # vazio = instrucao padrao da Luna (server/services/llm.py)
    llm_connect_timeout_s: float = 5.0
    llm_timeout_s: float = 60.0
    llm_keep_alive: str = "10m"
    llm_temperature: float = 0.7
    llm_max_tokens: int = 300
    # Voz da Luna (sintese local no servidor; reproducao no quiosque). Motor "kokoro" (padrao, voz feminina
    # pt-BR pf_dora) ou "piper" (vozes oficiais pt-BR masculinas). Arquivos: scripts/prepare_models.py kokoro
    tts_enabled: bool = True
    tts_engine: str = "kokoro"
    tts_model: Path = field(default_factory=lambda: PROJECT_ROOT / "models" / "tts" / "kokoro-v1.0.onnx")
    tts_voices: Path = field(default_factory=lambda: PROJECT_ROOT / "models" / "tts" / "voices-v1.0.bin")  # so kokoro
    tts_voice: str = "pf_dora"  # so kokoro; no piper a voz e o proprio modelo
    tts_language: str = "pt-br"  # so kokoro (codigo do espeak-ng)
    tts_speed: float = 1.0  # 0.5 (lenta) a 2.0 (rapida)
    tts_volume: float = 1.0
    tts_barge_in: bool = True  # "hey Luna" durante a fala interrompe o audio e abre a escuta
    tts_max_chars: int = 1500  # respostas maiores sao faladas ate o fim de uma frase antes desse limite
    # Memoria persistente local (SQLite). So guarda o que a pessoa pede ("lembre que..."); nada de audio.
    memory_enabled: bool = True
    memory_path: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "memory.sqlite3")
    memory_max_context: int = 8  # maximo de memorias relevantes enviadas a IA por pergunta (alem das instrucoes)
    # Assistente de receitas (Fase 7): orientacao extra para a IA em pedidos de receita, estoque de ingredientes
    # no contexto, continuacao da ultima receita e fala resumida de respostas longas. Tudo local (Ollama).
    recipes_enabled: bool = True
    recipes_max_tokens: int = 600  # limite de tokens para receitas completas (o padrao [llm] max_tokens e curto)
    recipes_spoken_max_chars: int = 420  # receitas maiores que isso sao faladas em resumo; 0 = falar tudo
    recipes_followup_ttl_s: float = 1800.0  # por quanto tempo "essa receita" se refere a ultima resposta

    @property
    def tls_enabled(self) -> bool:
        return self.tls_certfile is not None

    @property
    def scheme(self) -> str:
        return "https" if self.tls_enabled else "http"

    def validate(self) -> None:
        if (self.tls_certfile is None) != (self.tls_keyfile is None):
            raise ConfigError("server.tls_certfile e server.tls_keyfile devem ser definidos juntos")
        for label, path in (("tls_certfile", self.tls_certfile), ("tls_keyfile", self.tls_keyfile)):
            if path is not None and not path.is_file():
                raise ConfigError(f"server.{label} nao encontrado em {path}: execute scripts/make-cert.ps1")
        if not self.auth_token or self.auth_token == "TROQUE-ESTE-TOKEN" or len(self.auth_token) < 16:
            raise ConfigError("auth.token ausente ou fraco: gere um token com scripts/setup-server.ps1")
        if self.bind_host == "0.0.0.0":
            raise ConfigError("bind_host 0.0.0.0 nao e permitido: use o IP da interface local")
        if not 0.0 < self.wake_threshold <= 1.0:
            raise ConfigError("wake.threshold deve estar entre 0 e 1")
        if self.listen_silence_s <= 0 or self.listen_max_s <= 0 or self.listen_preroll_s < 0:
            raise ConfigError("wake.listen_silence_s e wake.listen_max_s devem ser positivos; wake.preroll_s nao pode ser negativo")
        if not 0 <= self.stt_audio_ctx <= 1500:
            raise ConfigError("transcription.audio_ctx deve estar entre 0 (completo) e 1500")
        if self.llm_enabled:
            problem = local_url_problem(self.llm_url)
            if problem:
                raise ConfigError(f"llm.url invalida: {problem}")
            if not self.llm_model.strip():
                raise ConfigError("llm.model vazio: informe o modelo do Ollama (ex.: qwen2.5:7b)")
            if self.llm_connect_timeout_s <= 0 or self.llm_timeout_s <= 0:
                raise ConfigError("llm.connect_timeout_s e llm.timeout_s devem ser maiores que zero")
            if self.llm_max_tokens <= 0:
                raise ConfigError("llm.max_tokens deve ser maior que zero")
        if self.tts_enabled:
            if self.tts_engine not in ("kokoro", "piper"):
                raise ConfigError(f"tts.engine invalido: {self.tts_engine!r}; use \"kokoro\" ou \"piper\"")
            if not 0.5 <= self.tts_speed <= 2.0:
                raise ConfigError("tts.speed deve estar entre 0.5 e 2.0")
            if not 0.1 <= self.tts_volume <= 4.0:
                raise ConfigError("tts.volume deve estar entre 0.1 e 4.0")
            if self.tts_max_chars <= 0:
                raise ConfigError("tts.max_chars deve ser maior que zero")
            if self.tts_engine == "kokoro" and not self.tts_voice.strip():
                raise ConfigError("tts.voice vazio: informe a voz do Kokoro (ex.: pf_dora)")
        if self.memory_enabled and self.memory_max_context <= 0:
            raise ConfigError("memory.max_context deve ser maior que zero")
        if self.recipes_enabled:
            if self.recipes_max_tokens <= 0:
                raise ConfigError("recipes.max_tokens deve ser maior que zero")
            if self.recipes_spoken_max_chars < 0:
                raise ConfigError("recipes.spoken_max_chars nao pode ser negativo (0 = falar tudo)")
            if self.recipes_followup_ttl_s <= 0:
                raise ConfigError("recipes.followup_ttl_s deve ser maior que zero")

    @classmethod
    def from_toml(cls, path: Path) -> "Settings":
        if not path.exists():
            raise ConfigError(
                f"configuracao nao encontrada em {path}. Copie config/settings.example.toml "
                "para config/settings.toml ou execute scripts/setup-server.ps1"
            )
        with path.open("rb") as f:
            raw = tomllib.load(f)
        srv = raw.get("server", {})
        auth = raw.get("auth", {})
        asst = raw.get("assistant", {})
        wake = raw.get("wake", {})
        stt = raw.get("transcription", {})
        llm = raw.get("llm", {})
        tts = raw.get("tts", {})
        mem = raw.get("memory", {})
        recipes = raw.get("recipes", {})
        models_dir = PROJECT_ROOT / "models" / "openwakeword"
        tts_engine = str(tts.get("engine", "kokoro")).strip().lower()
        tts_default_model = "models/tts/pt_BR-faber-medium.onnx" if tts_engine == "piper" else "models/tts/kokoro-v1.0.onnx"

        def optional_path(value: object) -> Path | None:
            text = str(value or "").strip()
            return PROJECT_ROOT / text if text else None

        return cls(
            bind_host=str(srv.get("bind_host", "127.0.0.1")),
            port=int(srv.get("port", 8800)),
            tls_certfile=optional_path(srv.get("tls_certfile")),
            tls_keyfile=optional_path(srv.get("tls_keyfile")),
            log_level=str(srv.get("log_level", "INFO")).upper(),
            debug_allow_state_override=bool(srv.get("debug_allow_state_override", False)),
            auth_token=str(auth.get("token", "")),
            wake_word=str(asst.get("wake_word", "hey Luna")),
            wake_model=PROJECT_ROOT / str(wake.get("model", models_dir / "hey_luna.onnx")),
            wake_features_dir=PROJECT_ROOT / str(wake.get("features_dir", models_dir)),
            wake_threshold=float(wake.get("threshold", 0.5)),
            wake_cooldown_s=float(wake.get("cooldown_s", 2.0)),
            wake_vad_model=PROJECT_ROOT / str(wake.get("vad_model", models_dir / "silero_vad.onnx")),
            listen_max_s=float(wake.get("listen_max_s", 8.0)),
            listen_silence_s=float(wake.get("listen_silence_s", 1.2)),
            listen_preroll_s=float(wake.get("preroll_s", 0.32)),
            stt_model=PROJECT_ROOT / str(stt.get("model", "models/whisper/ggml-small-q8_0.bin")),
            stt_vad_model=PROJECT_ROOT / str(stt.get("vad_model", "models/whisper/ggml-silero-v5.1.2.bin")),
            stt_language=str(stt.get("language", "pt")),
            stt_threads=int(stt.get("threads", 0)),
            stt_audio_ctx=int(stt.get("audio_ctx", 768)),
            stt_done_hold_s=float(stt.get("done_hold_s", 4.0)),
            llm_enabled=bool(llm.get("enabled", True)),
            llm_url=str(llm.get("url", "http://127.0.0.1:11434")).strip().rstrip("/"),
            llm_model=str(llm.get("model", "qwen2.5:7b")).strip(),
            llm_system_prompt=str(llm.get("system_prompt", "")).strip(),
            llm_connect_timeout_s=float(llm.get("connect_timeout_s", 5.0)),
            llm_timeout_s=float(llm.get("timeout_s", 60.0)),
            llm_keep_alive=str(llm.get("keep_alive", "10m")),
            llm_temperature=float(llm.get("temperature", 0.7)),
            llm_max_tokens=int(llm.get("max_tokens", 300)),
            tts_enabled=bool(tts.get("enabled", True)),
            tts_engine=tts_engine,
            tts_model=PROJECT_ROOT / str(tts.get("model", tts_default_model)),
            tts_voices=PROJECT_ROOT / str(tts.get("voices", "models/tts/voices-v1.0.bin")),
            tts_voice=str(tts.get("voice", "pf_dora")).strip(),
            tts_language=str(tts.get("language", "pt-br")).strip(),
            tts_speed=float(tts.get("speed", 1.0)),
            tts_volume=float(tts.get("volume", 1.0)),
            tts_barge_in=bool(tts.get("barge_in", True)),
            tts_max_chars=int(tts.get("max_chars", 1500)),
            memory_enabled=bool(mem.get("enabled", True)),
            memory_path=PROJECT_ROOT / str(mem.get("path", "data/memory.sqlite3")),
            memory_max_context=int(mem.get("max_context", 8)),
            recipes_enabled=bool(recipes.get("enabled", True)),
            recipes_max_tokens=int(recipes.get("max_tokens", 600)),
            recipes_spoken_max_chars=int(recipes.get("spoken_max_chars", 420)),
            recipes_followup_ttl_s=float(recipes.get("followup_ttl_s", 1800.0)),
        )


def load_settings() -> Settings:
    path = Path(os.environ.get("JARVIS_CONFIG", DEFAULT_CONFIG))
    settings = Settings.from_toml(path)
    settings.validate()
    return settings
