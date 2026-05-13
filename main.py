#!/usr/bin/env python3
"""Entry point — loads .env, runs health check, then starts the FastAPI server."""
import sys
from pathlib import Path

# Load .env before importing any config
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

from config.settings import DEBUG, HOST, PORT

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "ui.app:app",
        host=HOST,
        port=PORT,
        reload=DEBUG,
        log_level="info",
    )
