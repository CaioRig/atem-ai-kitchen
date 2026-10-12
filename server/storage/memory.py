"""Memória local persistente da Luna (SQLite, biblioteca padrão).

Guarda só o que a pessoa pediu para lembrar: preferências alimentares, informações da cozinha,
instruções de resposta e lembretes informativos. Nada de áudio, nada de histórico de conversa.
O arquivo fica em `data/memory.sqlite3` (configurável em `[memory] path`) e nunca sai da máquina.

Todas as operações passam por `MemoryStore`; qualquer falha do SQLite vira `MemoryStorageError`,
para quem chama tratar sem derrubar o servidor. A tabela tem uma coluna `profile` para separar
memórias por pessoa no futuro; hoje há um único perfil ("default"), como há um único assistente.
"""
from __future__ import annotations

import logging
import math
import re
import sqlite3
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("jarvis.memory")

KINDS = ("preference", "kitchen", "instruction", "note")
KIND_LABELS = {
    "preference": "preferência",
    "kitchen": "cozinha",
    "instruction": "instrução",
    "note": "lembrete",
}
KIND_LABELS_PLURAL = {
    "preference": "preferências",
    "kitchen": "cozinha",
    "instruction": "instruções",
    "note": "lembretes",
}
DEFAULT_PROFILE = "default"
MAX_TEXT_CHARS = 300
MAX_ROWS = 500  # limite de segurança: a memória é de uma casa, não um banco de dados
FORGET_MIN_SCORE = 0.6  # similaridade mínima para "esqueça que ..." apagar algo

# Palavras sem conteúdo em pt-BR, ignoradas na busca por relevância.
STOPWORDS = frozenset(
    """a o as os um uma uns umas de do da dos das em no na nos nas por pelo pela pelos pelas para pra pro
    com sem sob sobre entre ate até e ou mas que se nao não sim eu tu ele ela nos nós voces vocês eles elas me te
    lhe meu minha meus minhas seu sua seus suas teu tua dele dela isso isto aquilo este esta esse essa aquele aquela
    ja já ainda tambem também muito muita muitos muitas pouco pouca bem mal mais menos so só tao tão como quando
    onde qual quais quem porque por que la lá aqui ali agora hoje ontem amanha amanhã sempre nunca ser estar ter
    sou es é somos sao são estou esta está estamos estao estão tenho tem temos tinha foi era fui vai vou ia
    luna atem oi ola olá hey ei por favor pode poderia quero queria gostaria preciso deve devo tudo nada algo alguma algum
    coisa coisas""".split()
)

# Palavras que indicam uma pergunta sobre comida/receitas: preferências e cozinha passam a contar como
# contexto relevante mesmo sem repetir as palavras da memória ("me sugere um jantar" <- "não gosto de cebola").
FOOD_WORDS = frozenset(
    """receita receitas cozinhar cozinho cozinha cozinhe prato pratos comida comidas comer jantar jantares almoco
    almoço lanche cafe café sobremesa sobremesas ingrediente ingredientes tempero temperos cardapio cardápio refeicao
    refeição refeicoes refeições sugere sugestao sugestão sugira sugestoes sugestões fazer faco faço preparar preparo
    assar fritar cozido assado frito grelhar grelhado marmita salada sopa molho bolo pao pão massa arroz feijao feijão
    carne frango peixe legumes verduras fruta frutas vegetariano vegano lista compras mercado despensa geladeira
    forno fogao fogão panela airfryer fryer micro-ondas microondas""".split()
)


class MemoryStorageError(RuntimeError):
    """Falha ao ler ou gravar a memória (disco, arquivo corrompido, banco fechado)."""


@dataclass(frozen=True)
class Memory:
    id: int
    kind: str
    text: str
    created_at: float
    updated_at: float

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "text": self.text,
            "created_at": round(self.created_at, 3),
            "updated_at": round(self.updated_at, 3),
        }


def fold(text: str) -> str:
    """Minúsculas sem acentos, preservando o comprimento: cada caractere vira exatamente um caractere.
    Assim, posições encontradas no texto "dobrado" valem para o texto original."""
    out: list[str] = []
    for ch in text:
        decomposed = unicodedata.normalize("NFKD", ch)
        base = next((c for c in decomposed if not unicodedata.combining(c)), ch)
        out.append(base.lower() if len(base) == 1 else ch.lower())
    return "".join(out)


def normalize(text: str) -> str:
    """Forma canônica para comparar memórias: sem acentos, minúsculas, só letras/dígitos e espaços simples."""
    folded = fold(" ".join(text.split()))
    folded = re.sub(r"[^a-z0-9\s]+", " ", folded)
    return " ".join(folded.split())


def tokens(text: str) -> list[str]:
    return [t for t in normalize(text).split() if t]


def content_tokens(text: str) -> list[str]:
    return [t for t in tokens(text) if t not in STOPWORDS and len(t) > 1]


def stem(token: str) -> str:
    """Radical grosseiro para plural/conjugação ("cebolas"/"cebola", "gosto"/"gosta")."""
    return token[:5] if len(token) >= 5 else token


def similarity(a: str, b: str) -> float:
    """Fração dos tokens de conteúdo de `a` presentes em `b` (por radical). 0..1."""
    ta = {stem(t) for t in content_tokens(a)}
    tb = {stem(t) for t in content_tokens(b)}
    if not ta or not tb:
        return 1.0 if normalize(a) == normalize(b) else 0.0
    return len(ta & tb) / len(ta)


def is_food_question(text: str) -> bool:
    return any(t in FOOD_WORDS for t in tokens(text))


def _negation_free(norm: str) -> str:
    return " ".join(t for t in norm.split() if t != "nao")


def _has_negation(norm: str) -> bool:
    return "nao" in norm.split()


class MemoryStore:
    """Operações seguras sobre a tabela `memories`. Uma conexão, protegida por lock (o servidor é
    assíncrono em uma thread, mas a loja também é usada por scripts)."""

    def __init__(self, path: Path | str, *, profile: str = DEFAULT_PROFILE, max_text: int = MAX_TEXT_CHARS) -> None:
        self.path = Path(path)
        self.profile = profile
        self.max_text = max_text
        self._lock = threading.Lock()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=5.0)
            self._conn.row_factory = sqlite3.Row
            with self._conn:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=NORMAL")
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS memories (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        profile TEXT NOT NULL DEFAULT 'default',
                        kind TEXT NOT NULL CHECK (kind IN ('preference', 'kitchen', 'instruction', 'note')),
                        text TEXT NOT NULL,
                        normalized TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        UNIQUE (profile, normalized)
                    )
                    """
                )
                self._conn.execute("CREATE INDEX IF NOT EXISTS memories_profile_kind ON memories (profile, kind)")
        except (sqlite3.Error, OSError) as exc:
            raise MemoryStorageError(f"não foi possível abrir a memória em {self.path}: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    # -- leitura -------------------------------------------------------------------------------

    def count(self, kind: str | None = None) -> int:
        with self._guard():
            if kind is None:
                row = self._conn.execute("SELECT COUNT(*) FROM memories WHERE profile = ?", (self.profile,)).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE profile = ? AND kind = ?", (self.profile, kind)
                ).fetchone()
            return int(row[0])

    def all(self, kind: str | None = None) -> list[Memory]:
        with self._guard():
            if kind is None:
                rows = self._conn.execute(
                    "SELECT * FROM memories WHERE profile = ? ORDER BY kind, created_at, id", (self.profile,)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM memories WHERE profile = ? AND kind = ? ORDER BY created_at, id", (self.profile, kind)
                ).fetchall()
            return [self._row(r) for r in rows]

    def get(self, memory_id: int) -> Memory | None:
        with self._guard():
            row = self._conn.execute(
                "SELECT * FROM memories WHERE profile = ? AND id = ?", (self.profile, int(memory_id))
            ).fetchone()
            return self._row(row) if row else None

    def search(self, query: str, limit: int = 8, kinds: tuple[str, ...] | None = None) -> list[Memory]:
        """Memórias relevantes para `query`, por sobreposição de palavras (radicais). Em perguntas sobre
        comida ou receitas, preferências e informações da cozinha contam como relevantes mesmo sem
        palavras em comum. Nunca devolve a base inteira indiscriminadamente."""
        if limit <= 0:
            return []
        qtokens = {stem(t) for t in content_tokens(query)}
        food = is_food_question(query)
        scored: list[tuple[float, Memory]] = []
        for mem in self.all():
            if kinds is not None and mem.kind not in kinds:
                continue
            mtokens = {stem(t) for t in content_tokens(mem.text)}
            overlap = len(qtokens & mtokens)
            score = overlap / math.sqrt(len(mtokens)) if mtokens else 0.0
            if food and mem.kind == "preference":
                score += 0.5
            elif food and mem.kind == "kitchen":
                score += 0.3
            if score > 0:
                scored.append((score, mem))
        scored.sort(key=lambda s: (-s[0], s[1].id))
        return [m for _, m in scored[:limit]]

    # -- escrita -------------------------------------------------------------------------------

    def add(self, kind: str, text: str) -> tuple[Memory, str, Memory | None]:
        """Guarda `text` do tipo `kind`. Retorna (memória, status, anterior), onde status é
        "created", "exists" (já havia o mesmo texto) ou "updated" (corrigiu uma memória contrária,
        p.ex. "não gosto de cebola" -> "gosto de cebola")."""
        if kind not in KINDS:
            raise ValueError(f"tipo de memória inválido: {kind!r}")
        text = " ".join(text.split()).strip(" .,;!")[: self.max_text]
        if not text:
            raise ValueError("texto vazio")
        norm = normalize(text)
        now = time.time()
        with self._guard():
            row = self._conn.execute(
                "SELECT * FROM memories WHERE profile = ? AND normalized = ?", (self.profile, norm)
            ).fetchone()
            if row:
                return self._row(row), "exists", None
            # Correção: a mesma frase com a negação invertida substitui a antiga.
            for old in self._conn.execute(
                "SELECT * FROM memories WHERE profile = ? AND kind = ?", (self.profile, kind)
            ).fetchall():
                if _negation_free(old["normalized"]) == _negation_free(norm) and _has_negation(old["normalized"]) != _has_negation(norm):
                    previous = self._row(old)
                    with self._conn:
                        self._conn.execute(
                            "UPDATE memories SET text = ?, normalized = ?, updated_at = ? WHERE id = ?",
                            (text, norm, now, old["id"]),
                        )
                    return Memory(previous.id, kind, text, previous.created_at, now), "updated", previous
            total = self._conn.execute("SELECT COUNT(*) FROM memories WHERE profile = ?", (self.profile,)).fetchone()[0]
            if total >= MAX_ROWS:
                raise MemoryStorageError(f"limite de {MAX_ROWS} memórias atingido")
            with self._conn:
                cur = self._conn.execute(
                    "INSERT INTO memories (profile, kind, text, normalized, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (self.profile, kind, text, norm, now, now),
                )
            log.info("memoria %d guardada (%s)", cur.lastrowid, kind)  # o texto nao vai ao log
            return Memory(int(cur.lastrowid or 0), kind, text, now, now), "created", None

    def update(self, memory_id: int, text: str) -> Memory | None:
        text = " ".join(text.split()).strip(" .,;!")[: self.max_text]
        if not text:
            raise ValueError("texto vazio")
        now = time.time()
        with self._guard():
            with self._conn:
                cur = self._conn.execute(
                    "UPDATE memories SET text = ?, normalized = ?, updated_at = ? WHERE profile = ? AND id = ?",
                    (text, normalize(text), now, self.profile, int(memory_id)),
                )
            if cur.rowcount == 0:
                return None
        return self.get(memory_id)

    def remove(self, memory_id: int) -> bool:
        with self._guard():
            with self._conn:
                cur = self._conn.execute(
                    "DELETE FROM memories WHERE profile = ? AND id = ?", (self.profile, int(memory_id))
                )
            removed = cur.rowcount > 0
        if removed:
            log.info("memoria %d apagada", memory_id)
        return removed

    def forget(self, text: str) -> Memory | None:
        """Apaga a memória mais parecida com `text` (similaridade >= FORGET_MIN_SCORE). Retorna a apagada."""
        best: tuple[float, Memory] | None = None
        norm = normalize(text)
        for mem in self.all():
            score = 1.0 if mem.text and normalize(mem.text) == norm else similarity(text, mem.text)
            if norm and norm in normalize(mem.text):
                score = max(score, 0.9)
            if score >= FORGET_MIN_SCORE and (best is None or score > best[0]):
                best = (score, mem)
        if best is None:
            return None
        return best[1] if self.remove(best[1].id) else None

    def clear(self) -> int:
        with self._guard():
            with self._conn:
                cur = self._conn.execute("DELETE FROM memories WHERE profile = ?", (self.profile,))
            n = cur.rowcount
        log.info("%d memorias apagadas", n)
        return n

    # -- interno -------------------------------------------------------------------------------

    def _guard(self):
        return _Guard(self)

    @staticmethod
    def _row(row: sqlite3.Row) -> Memory:
        return Memory(int(row["id"]), str(row["kind"]), str(row["text"]), float(row["created_at"]), float(row["updated_at"]))


class _Guard:
    """Lock + conversão de erros do SQLite em MemoryStorageError."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def __enter__(self):
        self.store._lock.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.store._lock.release()
        if exc is not None and isinstance(exc, (sqlite3.Error, OSError)):
            log.warning("falha na memoria: %s", exc)
            raise MemoryStorageError(f"falha ao acessar a memória: {exc}") from exc
        return False
