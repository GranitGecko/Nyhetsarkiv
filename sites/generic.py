"""Datastyrda sajter: lägg till en tidning i sources.json i stället för att skriva en Python-fil.

Exempel på en post i sources.json (en lista med sådana poster):
  {"key": "nyheteridag", "name": "Nyheter Idag", "home": "https://www.nyheteridag.se/",
   "feeds": ["https://www.nyheteridag.se/feed/"]}
Valfria fält: political_label, article_regex, exclude_regex, index_pages (listsidor när RSS saknas),
cdx_prefixes, cdx_filters, archive_enabled, archive_delay, daily_limit, group.
"""
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from .base import Site

KEY_RX = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")
_STATIC = re.compile(r"\.(jpe?g|png|gif|webp|svg|ico|pdf|css|js|xml|json|txt|mp3|mp4|zip)$", re.I)
_NOT_ARTICLE_FIRST = {
    "tag", "tags", "taggar", "kategori", "category", "categories", "author", "forfattare", "skribent",
    "sok", "search", "om-oss", "om", "kontakt", "prenumerera", "annonsera", "feed", "rss", "wp-content",
    "wp-json", "wp-admin", "login", "logga-in", "mina-sidor", "e-tidning", "etidning", "tjanster",
    "vader", "press", "integritet", "cookies",
}


class GenericSite(Site):
    def __init__(self, key, home, name=None, feeds=None, sitemaps=None, political_label=None,
                 article_regex=None, exclude_regex=None, cdx_prefixes=None, cdx_filters=None,
                 archive_enabled=True, archive_delay=None, daily_limit=None, group=None, index_pages=None, **_unused):
        self.key = key
        self.name = name or key
        self.home = home
        self.feeds = list(feeds or [])
        self.sitemaps = list(sitemaps or [])
        self.index_pages = list(index_pages or [])
        self.political_label = political_label
        self.cdx_prefixes = cdx_prefixes
        self.cdx_filters = list(cdx_filters or [])
        self.archive_enabled = bool(archive_enabled)
        self.archive_delay = archive_delay
        self.daily_limit = daily_limit
        self.group = group
        self.host = urlsplit(home).netloc.lower()
        self.base_domain = re.sub(r"^www\.", "", self.host)
        self._article_rx = re.compile(article_regex) if article_regex else None
        self._exclude_rx = re.compile(exclude_regex) if exclude_regex else None

    def _same_host(self, host):
        host = host.lower()
        return host == self.base_domain or host.endswith("." + self.base_domain)

    def is_feed_article(self, url):
        """Allt som står i sajtens egna RSS-flöde räknas som artikel, om det ligger på sajten."""
        p = urlsplit(url)
        if not self._same_host(p.netloc) or not p.path.strip("/") or _STATIC.search(p.path):
            return False
        return not (self._exclude_rx and self._exclude_rx.search(url))

    def is_article_url(self, url):
        """Striktare test för URL:er från sitemaps, Wayback och länkar (där sektionssidor ingår)."""
        if not self.is_feed_article(url):
            return False
        if self._article_rx:
            return bool(self._article_rx.search(url))
        parts = [x for x in urlsplit(url).path.split("/") if x]
        if parts[0].lower() in _NOT_ARTICLE_FIRST:
            return False
        last = parts[-1].lower()
        return bool(re.search(r"[-_]|\d", last)) or len(last) >= 12 or len(parts) >= 3

    def postprocess(self, data, html, url):
        if not data.get("category"):
            parts = [x for x in urlsplit(url).path.split("/") if x]
            if len(parts) >= 2 and re.fullmatch(r"[a-zåäö-]{3,30}", parts[0].lower()) \
                    and parts[0].lower() not in _NOT_ARTICLE_FIRST:
                data["category"] = parts[0]
        return data


def load_sources(path, taken=()):
    """Läs sources.json. Felaktiga poster hoppas över med en förklaring; resten laddas."""
    path = Path(path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"sources.json: ogiltig JSON ({exc}); inga extra sajter laddas", file=sys.stderr)
        return []
    entries = data.get("sources", []) if isinstance(data, dict) else data
    out, seen = [], set(taken)
    for i, entry in enumerate(entries, 1):
        try:
            key = str(entry["key"])
            if not KEY_RX.fullmatch(key):
                raise ValueError("ogiltig nyckel (små bokstäver, siffror och bindestreck)")
            if key in seen:
                raise ValueError("nyckeln används redan")
            if not str(entry.get("home", "")).startswith(("http://", "https://")):
                raise ValueError("home måste börja med https://")
            out.append(GenericSite(**entry))
            seen.add(key)
        except Exception as exc:
            print(f"sources.json, post {i}: hoppar över ({exc})", file=sys.stderr)
    return out
