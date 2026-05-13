"""
Artifact tracker — every file created or modified by an agent is recorded
with metadata: path, agent, task_id, MIME type, size, and timestamp.

Records are persisted to data/artifacts.json.
scan_workspace() auto-discovers untracked files and registers them.
"""
import json
import logging
import mimetypes
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config.settings import WORKSPACE_DIR

logger = logging.getLogger(__name__)

_STORE_FILE = Path(__file__).resolve().parent.parent / "data" / "artifacts.json"


def _classify(path: Path) -> str:
    """Return a broad file category."""
    ext = path.suffix.lower()
    if ext in (".py", ".js", ".ts", ".rs", ".go", ".rb", ".c", ".cpp",
               ".java", ".sh", ".html", ".css", ".sql"):
        return "code"
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"):
        return "image"
    if ext in (".csv", ".json", ".jsonl", ".parquet", ".tsv", ".xlsx", ".db"):
        return "data"
    if ext in (".pdf", ".docx", ".doc", ".txt", ".md", ".rst"):
        return "document"
    if ext in (".pt", ".pth", ".ckpt", ".safetensors", ".onnx", ".pkl"):
        return "model"
    if ext in (".zip", ".tar", ".gz", ".bz2", ".7z"):
        return "archive"
    return "other"


class ArtifactRecord:
    def __init__(
        self,
        artifact_id: str,
        path: str,          # relative to WORKSPACE_DIR
        filename: str,
        agent: str,
        task_id: str,
        file_type: str,
        mime_type: str,
        size_bytes: int,
        created_at: str,
        tags: list[str],
    ):
        self.artifact_id = artifact_id
        self.path        = path
        self.filename    = filename
        self.agent       = agent
        self.task_id     = task_id
        self.file_type   = file_type
        self.mime_type   = mime_type
        self.size_bytes  = size_bytes
        self.created_at  = created_at
        self.tags        = tags

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "path":        self.path,
            "filename":    self.filename,
            "agent":       self.agent,
            "task_id":     self.task_id,
            "file_type":   self.file_type,
            "mime_type":   self.mime_type,
            "size_bytes":  self.size_bytes,
            "created_at":  self.created_at,
            "tags":        self.tags,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ArtifactRecord":
        return cls(
            artifact_id=d["artifact_id"],
            path=d["path"],
            filename=d.get("filename", Path(d["path"]).name),
            agent=d.get("agent", "unknown"),
            task_id=d.get("task_id", ""),
            file_type=d.get("file_type", "other"),
            mime_type=d.get("mime_type", "application/octet-stream"),
            size_bytes=d.get("size_bytes", 0),
            created_at=d.get("created_at", ""),
            tags=d.get("tags", []),
        )


class ArtifactStore:
    def __init__(self) -> None:
        self._records: dict[str, ArtifactRecord] = {}
        self._load()

    # ----------------------------------------------------------------- write
    def record(
        self,
        path: str,
        agent: str = "system",
        task_id: str = "",
        tags: list[str] = None,
    ) -> ArtifactRecord:
        """Record (or update) a file artifact."""
        abs_path = (WORKSPACE_DIR / path).resolve()
        try:
            rel_path  = str(abs_path.relative_to(WORKSPACE_DIR.resolve()))
            size      = abs_path.stat().st_size if abs_path.exists() else 0
        except (ValueError, OSError):
            rel_path = path
            size     = 0

        mime, _ = mimetypes.guess_type(str(abs_path))
        mime = mime or "application/octet-stream"

        # Reuse existing record for the same path (update metadata)
        existing = next(
            (r for r in self._records.values() if r.path == rel_path), None
        )
        if existing:
            existing.size_bytes = size
            existing.agent      = agent or existing.agent
            existing.task_id    = task_id or existing.task_id
            if tags:
                existing.tags = list(set(existing.tags + tags))
            self._save()
            return existing

        rec = ArtifactRecord(
            artifact_id=str(uuid.uuid4()),
            path=rel_path,
            filename=abs_path.name,
            agent=agent,
            task_id=task_id,
            file_type=_classify(abs_path),
            mime_type=mime,
            size_bytes=size,
            created_at=datetime.now(timezone.utc).isoformat(),
            tags=tags or [],
        )
        self._records[rec.artifact_id] = rec
        self._save()
        return rec

    def delete(self, artifact_id: str, delete_file: bool = False) -> bool:
        rec = self._records.pop(artifact_id, None)
        if not rec:
            return False
        if delete_file:
            try:
                (WORKSPACE_DIR / rec.path).unlink(missing_ok=True)
            except OSError:
                pass
        self._save()
        return True

    # ----------------------------------------------------------------- read
    def list_all(self, agent: str = "", file_type: str = "") -> list[dict]:
        records = list(self._records.values())
        if agent:
            records = [r for r in records if r.agent == agent]
        if file_type:
            records = [r for r in records if r.file_type == file_type]
        records.sort(key=lambda r: r.created_at, reverse=True)
        return [r.to_dict() for r in records]

    def count(self) -> int:
        return len(self._records)

    def stats(self) -> dict:
        records = list(self._records.values())
        by_type:  dict[str, int] = {}
        by_agent: dict[str, int] = {}
        total_bytes = 0
        for r in records:
            by_type[r.file_type]  = by_type.get(r.file_type, 0) + 1
            by_agent[r.agent]     = by_agent.get(r.agent, 0) + 1
            total_bytes          += r.size_bytes
        return {
            "total":       len(records),
            "total_bytes": total_bytes,
            "by_type":     by_type,
            "by_agent":    by_agent,
        }

    # ----------------------------------------------------------------- scan
    def scan_workspace(self, agent: str = "system") -> int:
        """Auto-register any untracked files currently in the workspace. Returns count added."""
        known_paths = {r.path for r in self._records.values()}
        added = 0
        try:
            for f in WORKSPACE_DIR.rglob("*"):
                if not f.is_file():
                    continue
                # Skip hidden dirs and common noisy files
                parts = f.parts
                if any(p.startswith(".") for p in parts):
                    continue
                if f.suffix in (".pyc", ".pyo") or "__pycache__" in parts:
                    continue
                rel = str(f.relative_to(WORKSPACE_DIR))
                if rel not in known_paths:
                    self.record(rel, agent=agent, tags=["scan"])
                    added += 1
        except Exception as exc:
            logger.warning("Artifact scan error: %s", exc)
        if added:
            self._save()
        return added

    # ----------------------------------------------------------------- persist
    def _save(self) -> None:
        try:
            _STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _STORE_FILE.write_text(
                json.dumps([r.to_dict() for r in self._records.values()], indent=2)
            )
        except Exception as exc:
            logger.warning("Artifact store save failed: %s", exc)

    def _load(self) -> None:
        if not _STORE_FILE.exists():
            return
        try:
            for d in json.loads(_STORE_FILE.read_text()):
                r = ArtifactRecord.from_dict(d)
                self._records[r.artifact_id] = r
            logger.info("Artifact store: loaded %d records", len(self._records))
        except Exception as exc:
            logger.warning("Artifact store load failed: %s", exc)


_store: Optional[ArtifactStore] = None


def get_artifacts() -> ArtifactStore:
    global _store
    if _store is None:
        _store = ArtifactStore()
    return _store
