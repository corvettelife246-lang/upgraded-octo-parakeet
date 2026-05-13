"""
Document ingestion — extract text from PDF, DOCX, XLSX, TXT, Markdown.

All extraction runs locally; no network calls needed.

Usage:
  text = await read_document("report.pdf")
  text = await read_document_bytes(raw_bytes, filename="report.pdf")
"""
import asyncio
import io
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


async def read_document(path: str | Path, max_chars: int = 80000) -> str:
    path = Path(path)
    raw  = path.read_bytes()
    return await read_document_bytes(raw, filename=path.name, max_chars=max_chars)


async def read_document_bytes(data: bytes, filename: str = "", max_chars: int = 80000) -> str:
    ext = Path(filename).suffix.lower()
    try:
        if ext == ".pdf":
            text = await asyncio.to_thread(_read_pdf, data)
        elif ext in (".docx", ".doc"):
            text = await asyncio.to_thread(_read_docx, data)
        elif ext in (".xlsx", ".xls"):
            text = await asyncio.to_thread(_read_xlsx, data)
        elif ext in (".md", ".txt", ".csv", ".json", ".yaml", ".yml",
                     ".py", ".js", ".ts", ".html", ".css", ".sh",
                     ".rs", ".go", ".c", ".cpp", ".java"):
            text = data.decode(errors="replace")
        else:
            # Try UTF-8 decode for any unknown text format
            try:
                text = data.decode(errors="replace")
            except Exception:
                return f"[Cannot read binary file: {filename}]"
    except ImportError as exc:
        return f"[Missing library: {exc}. Install with: pip install {_suggest_pkg(ext)}]"
    except Exception as exc:
        logger.warning("Document read failed for %s: %s", filename, exc)
        return f"[Read error: {exc}]"

    return text[:max_chars]


# ── Backends ──────────────────────────────────────────────────────────────────
def _read_pdf(data: bytes) -> str:
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(data))
        pages  = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages)
    except ImportError:
        pass
    # Fallback: pdfminer
    from pdfminer.high_level import extract_text_to_fp
    from pdfminer.layout import LAParams
    out = io.StringIO()
    extract_text_to_fp(io.BytesIO(data), out, laparams=LAParams())
    return out.getvalue()


def _read_docx(data: bytes) -> str:
    from docx import Document
    doc  = Document(io.BytesIO(data))
    return "\n".join(p.text for p in doc.paragraphs)


def _read_xlsx(data: bytes) -> str:
    import openpyxl
    wb   = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    rows = []
    for sheet in wb.worksheets:
        rows.append(f"=== Sheet: {sheet.title} ===")
        for row in sheet.iter_rows(values_only=True):
            rows.append("\t".join(str(c) if c is not None else "" for c in row))
    return "\n".join(rows)


def _suggest_pkg(ext: str) -> str:
    return {".pdf": "pypdf", ".docx": "python-docx", ".doc": "python-docx",
            ".xlsx": "openpyxl", ".xls": "openpyxl"}.get(ext, "the appropriate parser")
