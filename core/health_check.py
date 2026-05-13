"""
Startup health check — verifies critical dependencies and services.
Prints a colour-coded report and returns a structured dict.
"""
import asyncio
import importlib
import logging
import sys
from typing import Optional

logger = logging.getLogger(__name__)

GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
RESET  = "\033[0m"

_REQUIRED = [
    ("fastapi",          "fastapi",           "required"),
    ("uvicorn",          "uvicorn",           "required"),
    ("anthropic",        "anthropic",         "required for Claude API"),
    ("openai",           "openai",            "required for Foundry Local"),
    ("yaml",             "pyyaml",            "required"),
    ("dotenv",           "python-dotenv",     "required"),
    ("websockets",       "websockets",        "required"),
    ("numpy",            "numpy",             "required"),
]

_OPTIONAL = [
    ("whisper",          "openai-whisper",    "voice STT"),
    ("edge_tts",         "edge-tts",          "neural TTS"),
    ("pyttsx3",          "pyttsx3",           "offline TTS"),
    ("cv2",              "opencv-python",     "camera/vision"),
    ("PIL",              "Pillow",            "image processing"),
    ("torch",            "torch",             "ML/DL"),
    ("transformers",     "transformers",      "ML/DL"),
    ("sentence_transformers","sentence-transformers","memory embeddings"),
    ("duckduckgo_search","duckduckgo-search", "web search"),
    ("pypdf",            "pypdf",             "PDF reading"),
    ("docx",             "python-docx",       "DOCX reading"),
    ("openpyxl",         "openpyxl",          "XLSX reading"),
    ("sklearn",          "scikit-learn",      "ML"),
    ("pandas",           "pandas",            "data analysis"),
]


async def run_health_check(verbose: bool = True) -> dict:
    results = {"required": [], "optional": [], "services": []}

    # --- Python packages ---
    for mod, pkg, note in _REQUIRED:
        ok = _check_import(mod)
        results["required"].append({"name": pkg, "ok": ok, "note": note})
        if verbose:
            sym  = f"{GREEN}✓{RESET}" if ok else f"{RED}✗{RESET}"
            warn = "" if ok else f"  → pip install {pkg}"
            print(f"  {sym} {pkg:<30} {note}{warn}")

    for mod, pkg, note in _OPTIONAL:
        ok = _check_import(mod)
        results["optional"].append({"name": pkg, "ok": ok, "note": note})
        if verbose:
            sym = f"{GREEN}✓{RESET}" if ok else f"{YELLOW}–{RESET}"
            print(f"  {sym} {pkg:<30} {note} {'(install to enable)' if not ok else ''}")

    # --- Services ---
    from config.settings import ANTHROPIC_API_KEY, FOUNDRY_LOCAL_URL, LLM_BACKEND

    # Anthropic API key
    has_key = bool(ANTHROPIC_API_KEY)
    results["services"].append({"name": "Anthropic API key", "ok": has_key})
    if verbose:
        sym = f"{GREEN}✓{RESET}" if has_key else f"{YELLOW}–{RESET}"
        print(f"  {sym} {'Anthropic API key':<30} {'set' if has_key else 'not set (ok if using Foundry)'}")

    # Foundry Local reachability
    if LLM_BACKEND == "foundry":
        fl_ok = await _check_foundry(FOUNDRY_LOCAL_URL)
        results["services"].append({"name": "Foundry Local", "ok": fl_ok, "url": FOUNDRY_LOCAL_URL})
        if verbose:
            sym = f"{GREEN}✓{RESET}" if fl_ok else f"{RED}✗{RESET}"
            print(f"  {sym} {'Foundry Local':<30} {FOUNDRY_LOCAL_URL} {'reachable' if fl_ok else '— run: foundry model run phi-4-mini'}")

    all_required_ok = all(r["ok"] for r in results["required"])
    results["ok"] = all_required_ok
    return results


def _check_import(module: str) -> bool:
    try:
        importlib.import_module(module)
        return True
    except ImportError:
        return False


async def _check_foundry(url: str) -> bool:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{url}/v1/models")
            return r.status_code < 500
    except Exception:
        return False


async def print_banner() -> None:
    from config.settings import FOUNDRY_LOCAL_MODEL, LLM_BACKEND, PORT
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║            AI Multi-Agent Admin  v1.0                       ║
║   WSL-2 · DL · LLM · ML · Reasoning · Voice · Vision       ║
╚══════════════════════════════════════════════════════════════╝
  Backend : {LLM_BACKEND.upper()} {'('+FOUNDRY_LOCAL_MODEL+')' if LLM_BACKEND=='foundry' else '(Claude API)'}
  Server  : http://0.0.0.0:{PORT}
  Open    : http://localhost:{PORT}
""")
    print("Checking dependencies…")
    await run_health_check(verbose=True)
    print()
