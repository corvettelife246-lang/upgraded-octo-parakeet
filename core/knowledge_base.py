"""
RAG Knowledge Base — FAISS-backed semantic document store.

Separate from core/memory.py (short-term agent working memory). This is for
ingested reference documents, manuals, code bases, and long-term knowledge.

Backend priority:
  1. FAISS + sentence-transformers (GPU/CPU — pip install faiss-cpu)
  2. BM25 keyword fallback (always available, no extra deps)

Chunks are persisted to data/knowledge_base.json.
FAISS index is rebuilt from JSON on startup (not persisted to avoid
version-mismatch issues across restarts).
"""
import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_KB_FILE = Path(__file__).resolve().parent.parent / "data" / "knowledge_base.json"


class KnowledgeEntry:
    def __init__(
        self,
        entry_id: str,
        text: str,
        title: str = "",
        source: str = "",
        tags: list[str] = None,
    ):
        self.entry_id = entry_id
        self.text     = text
        self.title    = title
        self.source   = source
        self.tags     = tags or []

    def to_dict(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "text":     self.text,
            "title":    self.title,
            "source":   self.source,
            "tags":     self.tags,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "KnowledgeEntry":
        return cls(
            entry_id=d["entry_id"],
            text=d["text"],
            title=d.get("title", ""),
            source=d.get("source", ""),
            tags=d.get("tags", []),
        )


class KnowledgeBase:
    def __init__(self) -> None:
        self._entries: dict[str, KnowledgeEntry] = {}
        self._model   = None
        self._index   = None   # faiss.Index
        self._id_list: list[str] = []   # parallel to FAISS rows
        self._load()
        self._init_model()

    # ------------------------------------------------------------------ setup
    def _init_model(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer("all-MiniLM-L6-v2")
            logger.info("KnowledgeBase: sentence-transformers ready")
            if self._entries:
                self._rebuild_index()
        except Exception as exc:
            logger.warning("KnowledgeBase: sentence-transformers unavailable (%s) — BM25 fallback", exc)

    def _rebuild_index(self) -> None:
        if not self._model or not self._entries:
            return
        try:
            import faiss
            import numpy as np
            self._id_list = list(self._entries.keys())
            texts = [self._entries[eid].text for eid in self._id_list]
            embs  = self._model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
            dim   = embs.shape[1]
            idx   = faiss.IndexFlatIP(dim)
            idx.add(embs.astype(np.float32))
            self._index = idx
            logger.info("KnowledgeBase: FAISS index built (%d entries)", len(texts))
        except Exception as exc:
            logger.warning("KnowledgeBase: FAISS build failed (%s) — BM25 fallback", exc)
            self._index = None

    # ------------------------------------------------------------------ write
    def add(
        self,
        text: str,
        title: str = "",
        source: str = "",
        tags: list[str] = None,
        chunk_size: int = 0,
    ) -> list[str]:
        """Ingest text, optionally splitting into chunks. Returns list of entry IDs."""
        chunks = (
            [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
            if chunk_size > 0
            else [text]
        )
        ids: list[str] = []
        for i, chunk in enumerate(chunks):
            chunk = chunk.strip()
            if not chunk:
                continue
            e = KnowledgeEntry(
                entry_id=str(uuid.uuid4()),
                text=chunk,
                title=f"{title} [{i+1}/{len(chunks)}]" if len(chunks) > 1 else title,
                source=source,
                tags=tags or [],
            )
            self._entries[e.entry_id] = e
            ids.append(e.entry_id)
        self._save()
        self._rebuild_index()
        return ids

    def delete(self, entry_id: str) -> bool:
        if entry_id not in self._entries:
            return False
        del self._entries[entry_id]
        self._save()
        self._rebuild_index()
        return True

    # ------------------------------------------------------------------ read
    async def search(self, query: str, top_k: int = 5) -> list[dict]:
        if self._model and self._index and self._id_list:
            return await asyncio.to_thread(self._faiss_search, query, top_k)
        return self._bm25_search(query, top_k)

    def _faiss_search(self, query: str, top_k: int) -> list[dict]:
        try:
            import numpy as np
            q_emb = self._model.encode([query], convert_to_numpy=True, normalize_embeddings=True)
            k     = min(top_k, len(self._id_list))
            D, I  = self._index.search(q_emb.astype(np.float32), k)
            results = []
            for score, idx in zip(D[0], I[0]):
                if 0 <= idx < len(self._id_list):
                    e = self._entries.get(self._id_list[idx])
                    if e:
                        r = e.to_dict()
                        r["score"] = round(float(score), 4)
                        results.append(r)
            return results
        except Exception as exc:
            logger.warning("FAISS search error: %s", exc)
            return self._bm25_search(query, top_k)

    def _bm25_search(self, query: str, top_k: int) -> list[dict]:
        qwords = set(query.lower().split())
        scored: list[tuple[float, KnowledgeEntry]] = []
        for e in self._entries.values():
            words = e.text.lower().split()
            hits  = sum(1 for w in words if w in qwords)
            score = hits / (len(words) ** 0.5 + 1)
            scored.append((score, e))
        scored.sort(key=lambda x: -x[0])
        results = []
        for score, e in scored[:top_k]:
            r = e.to_dict()
            r["score"] = round(score, 4)
            results.append(r)
        return results

    def list_all(self) -> list[dict]:
        return [e.to_dict() for e in self._entries.values()]

    def count(self) -> int:
        return len(self._entries)

    # ------------------------------------------------------------------ persist
    def _save(self) -> None:
        try:
            _KB_FILE.parent.mkdir(parents=True, exist_ok=True)
            _KB_FILE.write_text(
                json.dumps([e.to_dict() for e in self._entries.values()], indent=2)
            )
        except Exception as exc:
            logger.warning("KnowledgeBase save failed: %s", exc)

    def _load(self) -> None:
        if not _KB_FILE.exists():
            return
        try:
            for d in json.loads(_KB_FILE.read_text()):
                e = KnowledgeEntry.from_dict(d)
                self._entries[e.entry_id] = e
            logger.info("KnowledgeBase: loaded %d entries", len(self._entries))
        except Exception as exc:
            logger.warning("KnowledgeBase load failed: %s", exc)


_kb: Optional[KnowledgeBase] = None


def get_kb() -> KnowledgeBase:
    global _kb
    if _kb is None:
        _kb = KnowledgeBase()
    return _kb
