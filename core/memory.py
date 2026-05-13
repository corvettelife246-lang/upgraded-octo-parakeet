"""
Persistent agent memory — stores facts, summaries, and code snippets
as embeddings, then retrieves the most relevant ones at query time.

Storage: JSON on disk (no external vector DB needed).
Embeddings: sentence-transformers (offline, ~80 MB model).
Fallback: keyword BM25-style scoring when transformers unavailable.
"""
import asyncio
import json
import logging
import math
import re
import time
import uuid
from pathlib import Path
from typing import Optional

from config.settings import DATA_DIR

logger = logging.getLogger(__name__)

MEMORY_FILE = DATA_DIR / "memory.json"
_EMBED_MODEL = "all-MiniLM-L6-v2"   # 80 MB, fast, accurate


# ── Memory record ─────────────────────────────────────────────────────────────
class MemoryRecord:
    __slots__ = ("id", "text", "tags", "source", "created_at", "vector")

    def __init__(self, text: str, tags: list[str] = None, source: str = "user"):
        self.id         = str(uuid.uuid4())
        self.text       = text
        self.tags       = tags or []
        self.source     = source
        self.created_at = time.time()
        self.vector: Optional[list[float]] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id, "text": self.text, "tags": self.tags,
            "source": self.source, "created_at": self.created_at,
            "vector": self.vector,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryRecord":
        r = cls.__new__(cls)
        r.id         = d["id"]
        r.text       = d["text"]
        r.tags       = d.get("tags", [])
        r.source     = d.get("source", "user")
        r.created_at = d.get("created_at", 0.0)
        r.vector     = d.get("vector")
        return r


# ── Store ─────────────────────────────────────────────────────────────────────
class MemoryStore:
    def __init__(self) -> None:
        self._records: dict[str, MemoryRecord] = {}
        self._encoder = None
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def add(self, text: str, tags: list[str] = None, source: str = "user") -> MemoryRecord:
        rec = MemoryRecord(text, tags=tags, source=source)
        vec = await self._embed(text)
        rec.vector = vec
        self._records[rec.id] = rec
        self._save()
        return rec

    async def search(self, query: str, top_k: int = 5, tag_filter: list[str] = None) -> list[dict]:
        if not self._records:
            return []
        q_vec = await self._embed(query)
        scored = []
        for rec in self._records.values():
            if tag_filter and not any(t in rec.tags for t in tag_filter):
                continue
            score = self._similarity(q_vec, rec.vector) if q_vec and rec.vector else self._bm25_score(query, rec.text)
            scored.append((score, rec))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {"score": round(s, 4), "id": r.id, "text": r.text,
             "tags": r.tags, "source": r.source,
             "created_at": r.created_at}
            for s, r in scored[:top_k]
        ]

    async def summarize_for_context(self, query: str, top_k: int = 4) -> str:
        """Return a short memory block ready to inject into a system prompt."""
        hits = await self.search(query, top_k=top_k)
        if not hits:
            return ""
        lines = "\n".join(f"- [{h['source']}] {h['text'][:200]}" for h in hits)
        return f"Relevant memories:\n{lines}"

    def delete(self, memory_id: str) -> bool:
        if memory_id in self._records:
            del self._records[memory_id]
            self._save()
            return True
        return False

    def list_all(self, limit: int = 50) -> list[dict]:
        recs = sorted(self._records.values(), key=lambda r: r.created_at, reverse=True)
        return [{"id": r.id, "text": r.text[:120], "tags": r.tags,
                 "source": r.source, "created_at": r.created_at}
                for r in recs[:limit]]

    def count(self) -> int:
        return len(self._records)

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------
    async def _embed(self, text: str) -> Optional[list[float]]:
        try:
            return await asyncio.to_thread(self._embed_sync, text)
        except Exception as exc:
            logger.debug("Embedding unavailable (%s), using BM25 fallback", exc)
            return None

    def _embed_sync(self, text: str) -> list[float]:
        if self._encoder is None:
            from sentence_transformers import SentenceTransformer
            self._encoder = SentenceTransformer(_EMBED_MODEL)
        vec = self._encoder.encode(text, normalize_embeddings=True)
        return vec.tolist()

    # ------------------------------------------------------------------
    # Similarity / fallback
    # ------------------------------------------------------------------
    @staticmethod
    def _similarity(a: list[float], b: Optional[list[float]]) -> float:
        if not b:
            return 0.0
        return sum(x * y for x, y in zip(a, b))

    @staticmethod
    def _bm25_score(query: str, text: str) -> float:
        q_terms = set(re.findall(r"\w+", query.lower()))
        t_words = re.findall(r"\w+", text.lower())
        if not t_words:
            return 0.0
        tf = {w: t_words.count(w) / len(t_words) for w in q_terms}
        return sum(tf.get(t, 0) for t in q_terms)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _save(self) -> None:
        try:
            MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            MEMORY_FILE.write_text(json.dumps(
                [r.to_dict() for r in self._records.values()], indent=2
            ))
        except Exception as exc:
            logger.warning("Memory save failed: %s", exc)

    def _load(self) -> None:
        if not MEMORY_FILE.exists():
            return
        try:
            for d in json.loads(MEMORY_FILE.read_text()):
                r = MemoryRecord.from_dict(d)
                self._records[r.id] = r
            logger.info("Loaded %d memories from disk", len(self._records))
        except Exception as exc:
            logger.warning("Memory load failed: %s", exc)


# Module-level singleton
_store: Optional[MemoryStore] = None


def get_memory() -> MemoryStore:
    global _store
    if _store is None:
        _store = MemoryStore()
    return _store
