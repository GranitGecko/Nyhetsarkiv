"""Källprofiler: ägare, nyckelpersoner och beskrivningar med källhänvisningar.

Grunddata finns i content/profiles.json (följer med programmet). Egna rättelser och tillägg läggs i
data/profiles.json, som åsidosätter grunddatan per nyckel och aldrig skrivs över vid uppdatering.
"""
import json
from pathlib import Path
from urllib.parse import urlsplit

from config import DATA, ROOT

PROFILE_FILES = [ROOT / "content" / "profiles.json", DATA / "profiles.json"]
REQUIRED = ("name", "home", "type")


def host_key(url):
    host = urlsplit(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def load(paths=None):
    """Returnerar {'checked': datum, 'profiles': {nyckel: profil}} med senare filer som åsidosätter tidigare."""
    merged = {"checked": "", "profiles": {}}
    for path in (PROFILE_FILES if paths is None else paths):
        path = Path(path)
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        merged["checked"] = data.get("checked") or merged["checked"]
        merged["profiles"].update(data.get("profiles", {}))
    return merged


def validate(data):
    """Lista med problem (tom lista = allt ser rätt ut). Varje påstående om en person ska ha en källa."""
    problems = []
    for key, p in data.get("profiles", {}).items():
        for field in REQUIRED:
            if not p.get(field):
                problems.append(f"{key}: saknar fältet '{field}'")
        if p.get("home") and not str(p["home"]).startswith(("http://", "https://")):
            problems.append(f"{key}: home måste börja med http(s)://")
        for person in p.get("people", []):
            if not person.get("name") or not person.get("role"):
                problems.append(f"{key}: person utan namn eller roll")
            if not str(person.get("source", "")).startswith(("http://", "https://")):
                problems.append(f"{key}: {person.get('name', '?')} saknar källa (länk)")
        for d in p.get("described", []):
            if not str(d.get("source", "")).startswith(("http://", "https://")):
                problems.append(f"{key}: beskrivning saknar källa: {str(d.get('text', ''))[:40]}")
        for url in p.get("sources", []):
            if not str(url).startswith(("http://", "https://")):
                problems.append(f"{key}: ogiltig källänk {url}")
        if (p.get("people") or p.get("described") or p.get("facts")) and not p.get("sources") and not p.get("verify"):
            problems.append(f"{key}: har uppgifter men varken källor eller en 'verify'-notering")
    return problems
