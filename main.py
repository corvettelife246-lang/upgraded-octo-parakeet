#!/usr/bin/env python3
"""Entry point — loads .env then starts the FastAPI server."""
import sys
from pathlib import Path

# Load .env before importing any config
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

from config.settings import HOST, PORT, DEBUG

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "ui.app:app",
        host=HOST,
        port=PORT,
        reload=DEBUG,
        log_level="info",
    )
