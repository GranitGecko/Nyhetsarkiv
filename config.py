"""Grundinställningar. Personliga inställningar (kontaktadress) ligger i data/settings.json,
så att det räcker att kopiera data-mappen när du uppdaterar programmet."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
DB_PATH = DATA / "news.db"
RAW_DIR = DATA / "raw"

DEFAULT_USER_AGENT = "Nyhetsarkivet/0.1 (+kontakt: du@example.se)"


def _settings():
    path = DATA / "settings.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except ValueError:
        return {}


# Sätts med: python cli.py contact din@epost.se   (sparas i data/settings.json)
USER_AGENT = _settings().get("user_agent") or os.environ.get("NYHETSARKIVET_USER_AGENT") or DEFAULT_USER_AGENT
REQUEST_DELAY = 2.0   # sekunder mellan anrop mot samma värd
TIMEOUT = 20
