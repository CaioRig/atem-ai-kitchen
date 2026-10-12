"""Cache persistente da previsão do tempo (SQLite, biblioteca padrão), no mesmo arquivo da memória.

Uma linha por local (`location_key`): nunca acumula registros. Guarda o local consultado, a hora da
consulta à fonte (`fetched_at`), o período a que a previsão se refere (`period_start`..`period_end`), os
dados recebidos (JSON de `WeatherData`) e o resultado da última tentativa (`last_status`, `last_error`,
`last_attempt_at`). Uma falha nunca apaga dados válidos: só atualiza as colunas da tentativa.

Conexão própria (o SQLite em modo WAL aceita várias conexões no mesmo arquivo), para não misturar com
a `MemoryStore`. Qualquer erro do SQLite vira `WeatherCacheError`, tratado sem derrubar o servidor.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from server.services.weather import WeatherData

log = logging.getLogger("jarvis.weather")

STATUS_OK = "ok"
STATUS_FAILED = "failed"

_CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS weather_cache (
        location_key TEXT PRIMARY KEY,
        location_name TEXT NOT NULL,
        latitude REAL NOT NULL,
        longitude REAL NOT NULL,
        timezone TEXT NOT NULL,
        fetched_at REAL,
        period_start TEXT,
        period_end TEXT,
        payload TEXT,
        last_attempt_at REAL,
        last_status TEXT,
        last_error TEXT,
        updated_at REAL NOT NULL
    )
"""


class WeatherCacheError(RuntimeError):
    """Falha ao ler ou gravar o cache (disco, arquivo corrompido, banco fechado)."""


@dataclass(frozen=True)
class CacheEntry:
    location_key: str
    location_name: str
    latitude: float
    longitude: float
    timezone: str
    fetched_at: float | None  # hora da última consulta bem-sucedida à fonte (epoch); None = nunca
    period_start: str  # primeira data coberta pela previsão guardada ("AAAA-MM-DD"), "" sem dados
    period_end: str
    data: WeatherData | None
    last_attempt_at: float | None  # última tentativa (sucesso ou falha)
    last_status: str  # "ok" | "failed" | ""
    last_error: str  # mensagem amigável da última falha ("" se a última tentativa deu certo)

    @property
    def has_data(self) -> bool:
        return self.data is not None and self.fetched_at is not None

    def age_s(self, now: float) -> float | None:
        return None if self.fetched_at is None else max(0.0, now - self.fetched_at)


def location_key(latitude: float, longitude: float) -> str:
    return f"{latitude:.4f},{longitude:.4f}"


class WeatherCache:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=5.0)
            self._conn.row_factory = sqlite3.Row
            with self._conn:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=NORMAL")
                self._conn.execute(_CREATE_TABLE)
        except (sqlite3.Error, OSError) as exc:
            raise WeatherCacheError(f"não foi possível abrir o cache do tempo em {self.path}: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    def get(self, latitude: float, longitude: float) -> CacheEntry | None:
        key = location_key(latitude, longitude)
        with self._guard():
            row = self._conn.execute("SELECT * FROM weather_cache WHERE location_key = ?", (key,)).fetchone()
        return self._row(row) if row else None

    def store(self, data: WeatherData, *, latitude: float | None = None, longitude: float | None = None) -> CacheEntry:
        """Grava uma consulta bem-sucedida: substitui os dados do local e marca a tentativa como "ok". A chave é o
        local configurado (`latitude`/`longitude`), não as coordenadas da célula da grade que a fonte devolve."""
        key_lat = data.latitude if latitude is None else latitude
        key_lon = data.longitude if longitude is None else longitude
        key = location_key(key_lat, key_lon)
        if not data.daily and data.current is None:
            raise ValueError("dados vazios não substituem o cache")
        payload = json.dumps(data.as_dict(), ensure_ascii=False)
        now = time.time()
        with self._guard():
            with self._conn:
                self._conn.execute(
                    """INSERT INTO weather_cache (location_key, location_name, latitude, longitude, timezone, fetched_at,
                           period_start, period_end, payload, last_attempt_at, last_status, last_error, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?)
                       ON CONFLICT(location_key) DO UPDATE SET
                           location_name = excluded.location_name, latitude = excluded.latitude, longitude = excluded.longitude,
                           timezone = excluded.timezone, fetched_at = excluded.fetched_at, period_start = excluded.period_start,
                           period_end = excluded.period_end, payload = excluded.payload, last_attempt_at = excluded.last_attempt_at,
                           last_status = excluded.last_status, last_error = '', updated_at = excluded.updated_at""",
                    (
                        key, data.location_name, key_lat, key_lon, data.timezone, data.fetched_at,
                        data.period_start, data.period_end, payload, data.fetched_at, STATUS_OK, now,
                    ),
                )
            row = self._conn.execute("SELECT * FROM weather_cache WHERE location_key = ?", (key,)).fetchone()
        return self._row(row)

    def record_failure(self, latitude: float, longitude: float, *, location_name: str, timezone: str, error: str, at: float | None = None) -> CacheEntry:
        """Anota uma tentativa que falhou sem tocar nos dados guardados (se houver)."""
        key = location_key(latitude, longitude)
        at = time.time() if at is None else at
        with self._guard():
            with self._conn:
                self._conn.execute(
                    """INSERT INTO weather_cache (location_key, location_name, latitude, longitude, timezone,
                           last_attempt_at, last_status, last_error, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(location_key) DO UPDATE SET
                           last_attempt_at = excluded.last_attempt_at, last_status = excluded.last_status,
                           last_error = excluded.last_error, updated_at = excluded.updated_at""",
                    (key, location_name, latitude, longitude, timezone, at, STATUS_FAILED, error[:300], at),
                )
            row = self._conn.execute("SELECT * FROM weather_cache WHERE location_key = ?", (key,)).fetchone()
        return self._row(row)

    def clear(self) -> int:
        with self._guard():
            with self._conn:
                cur = self._conn.execute("DELETE FROM weather_cache")
            return int(cur.rowcount)

    def _guard(self):
        return _Guard(self)

    @staticmethod
    def _row(row: sqlite3.Row) -> CacheEntry:
        data: WeatherData | None = None
        payload = row["payload"]
        if payload:
            try:
                data = WeatherData.from_dict(json.loads(payload))
            except (ValueError, KeyError, TypeError) as exc:  # payload corrompido: trata como sem dados
                log.warning("cache do tempo com dados ilegíveis; ignorando: %s", exc)
        fetched_at = row["fetched_at"]
        return CacheEntry(
            location_key=str(row["location_key"]),
            location_name=str(row["location_name"]),
            latitude=float(row["latitude"]),
            longitude=float(row["longitude"]),
            timezone=str(row["timezone"]),
            fetched_at=float(fetched_at) if fetched_at is not None and data is not None else None,
            period_start=str(row["period_start"] or "") if data is not None else "",
            period_end=str(row["period_end"] or "") if data is not None else "",
            data=data,
            last_attempt_at=float(row["last_attempt_at"]) if row["last_attempt_at"] is not None else None,
            last_status=str(row["last_status"] or ""),
            last_error=str(row["last_error"] or ""),
        )


class _Guard:
    """Lock + conversão de erros do SQLite em WeatherCacheError."""

    def __init__(self, cache: WeatherCache) -> None:
        self.cache = cache

    def __enter__(self):
        self.cache._lock.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.cache._lock.release()
        if exc_type is not None and issubclass(exc_type, (sqlite3.Error, OSError)):
            raise WeatherCacheError(f"falha no cache do tempo: {exc}") from exc
        return False


__all__ = ["STATUS_FAILED", "STATUS_OK", "CacheEntry", "WeatherCache", "WeatherCacheError", "location_key"]
