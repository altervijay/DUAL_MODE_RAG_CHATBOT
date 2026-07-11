"""
Centralized env config. Load once, import everywhere — don't scatter
os.environ.get() calls through the codebase.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Repo root .env (two levels up from backend/app/). No-op if absent — in
# Docker, compose's env_file injects the vars directly.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")
load_dotenv()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/emb_chatbot"
)

# Fixed by the brief — never datetime.now(). "Last month" style questions must
# resolve against this constant so answers stay stable over the fixed dataset.
ASSESSMENT_DATE = os.environ.get("ASSESSMENT_DATE", "2026-06-15")

EMBEDDING_MODEL_NAME = os.environ.get("EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")

# Sonnet tier per docs/ROADMAP.md §1 — tool routing + short-context RAG +
# narrow SQL generation doesn't need frontier-tier reasoning.
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
