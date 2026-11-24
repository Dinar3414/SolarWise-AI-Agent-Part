"""Central configuration. Secrets and model IDs come from .env, never from code."""
from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
CORPUS_DIR = DATA_DIR / "corpus"
CHUNKS_PATH = DATA_DIR / "chunks.jsonl"
MANIFEST_PATH = DATA_DIR / "corpus_manifest.json"
CATALOG_PATH = DATA_DIR / "catalog.json"
DB_PATH = Path(os.getenv("SOLARWISE_DB", str(DATA_DIR / "solarwise.sqlite")))
QDRANT_PATH = Path(os.getenv("QDRANT_PATH", str(DATA_DIR / "qdrant")))
QDRANT_URL = os.getenv("QDRANT_URL", "")  # empty -> embedded local mode

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
EMBED_MODEL = os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
RETRIEVER = os.getenv("RETRIEVER", "auto")  # auto | qdrant | lexical

MAX_LLM_CALLS_PER_SESSION = int(os.getenv("MAX_LLM_CALLS_PER_SESSION", "25"))
MAX_LLM_CALLS_PER_DAY = int(os.getenv("MAX_LLM_CALLS_PER_DAY", "200"))
REVIEWER_PIN = os.getenv("REVIEWER_PIN", "")  # required to mark a design "approved"

REPORT_STAMP = "PRELIMINARY — NOT FOR INSTALLATION"
