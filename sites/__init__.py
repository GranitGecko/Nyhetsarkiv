"""Registret över sajter: tre med egna filer, resten datastyrda via sources.json."""
from config import ROOT

from .aftonbladet import Aftonbladet
from .dn import Dn
from .generic import load_sources
from .svt import Svt

SITES = {s.key: s for s in (Svt(), Dn(), Aftonbladet())}
BUILTIN = frozenset(SITES)
_NEW, _OLD = ROOT / "data" / "sources.json", ROOT / "sources.json"
SOURCES_PATH = _OLD if (_OLD.exists() and not _NEW.exists()) else _NEW      # data/sources.json är standard

for _site in load_sources(SOURCES_PATH, taken=set(SITES)):
    SITES[_site.key] = _site
