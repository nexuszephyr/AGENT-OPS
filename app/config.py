"""Runtime settings, read from environment / .env."""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional at runtime
    pass

ROOT = Path(__file__).resolve().parent.parent

DB_PATH = os.getenv("DB_PATH", str(ROOT / "app" / "data" / "bank.db"))
OUT_DIR = Path(os.getenv("OUT_DIR", str(ROOT / "out")))

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").lower()
LLM_MODEL = os.getenv("LLM_MODEL", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "")

API_URL = os.getenv("API_URL", "http://localhost:8000")

# Stop rules (docs/ARCHITECTURE.md)
MAX_QUESTIONS = 5
MAX_TOOL_CALLS_PER_TURN = 8
MAX_REVISIONS = 2


def llm_configured() -> bool:
    return bool(LLM_API_KEY and LLM_MODEL)
