"""Hitta RSS-flöden för en ny nyhetssajt och lägg till den i sources.json."""
import json
import os
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from discover import extract_links, page_links
from parse_base import _Extractor
from sites import SOURCES_PATH
from sites.generic import KEY_RX, GenericSite

COMMON_FEEDS = ["/feed/", "/rss", "/rss/", "/rss.xml", "/feed", "/feed.xml", "/atom.xml",
                "/rss/nyheter", "/nyheter/rss"]
_FEED_TYPES = ("application/rss+xml", "application/atom+xml", "application/rdf+xml",
               "application/xml", "text/xml")


class _AltLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.feeds = []

    def handle_starttag(self, tag, attrs):
        if tag == "link":
            a = dict(attrs)
            if ("alternate" in (a.get("rel") or "").lower()
                    and (a.get("type") or "").lower() in _FEED_TYPES and a.get("href")):
                self.feeds.append(a["href"])


def _host(url):
    return urlsplit(url).netloc.lower()


def normalize_home(url):
    url = url.strip()
    if not re.match(r"https?://", url):
        url = "https://" + url
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}/"


def make_key(home, taken):
    host = re.sub(r"^www\.", "", _host(home))
    short = re.sub(r"[^a-z0-9-]", "-", host.split(".")[0]).strip("-")
    if short and short not in taken and KEY_RX.fullmatch(short):
        return short
    full = re.sub(r"[^a-z0-9-]", "-", host).strip("-")[:40]
    return full if full not in taken else full + "-2"


def _feed_size(fetcher, url, base_domain):
    """Antal olika länkar på sajten i flödet, eller 0 om det inte är ett giltigt flöde."""
    try:
        resp = fetcher.get(url)
        if resp.status_code != 200:
            return 0
        links, kind = extract_links(resp.content)
    except Exception:
        return 0
    if kind not in ("rss", "feed", "RDF"):
        return 0
    return sum(1 for link in links
               if _host(link) == base_domain or _host(link).endswith("." + base_domain))


def detect_site(fetcher, url, key=None, name=None, label=None, taken=(), feeds=None, index_pages=None, article_regex=None):
    home = normalize_home(url)
    if feeds or index_pages:                             # flöde och/eller listsidor angivna för hand: kontrollera bara dem
        base = re.sub(r"^www\.", "", _host(home))
        good = []
        for feed in feeds or []:
            feed = urljoin(home, feed)
            try:
                if not fetcher.allowed(feed):
                    return {"error": f"robots.txt tillåter inte hämtning av {feed}"}
            except Exception as exc:
                return {"error": f"kunde inte nå sajten: {exc}"}
            if _feed_size(fetcher, feed, base) < 4:
                return {"error": f"{feed} är inget giltigt RSS/Atom-flöde med minst tre artiklar på {base}"}
            good.append(feed)
        pages, probe, n_links = [], GenericSite(key="probe", home=home, article_regex=article_regex), 0
        for page in index_pages or []:
            page = urljoin(home, page)
            try:
                if not fetcher.allowed(page):
                    return {"error": f"robots.txt tillåter inte hämtning av {page}"}
                resp = fetcher.get(page)
            except Exception as exc:
                return {"error": f"kunde inte nå {page}: {exc}"}
            if resp.status_code != 200:
                return {"error": f"HTTP {resp.status_code} på {page}"}
            n_links += sum(1 for u in page_links(resp.text, page) if probe.is_article_url(u))
            pages.append(page)
        if pages and n_links < 3:
            return {"error": f"hittade bara {n_links} artikellänkar på listsidorna; ange --article-regex som matchar artikeladresserna"}
        entry = {"key": key or make_key(home, taken), "name": name or base, "home": home, "feeds": good}
        if pages:
            entry["index_pages"] = pages
        if article_regex:
            entry["article_regex"] = article_regex
        if label:
            entry["political_label"] = label
        return entry
    try:
        if not fetcher.allowed(home):
            return {"error": "robots.txt tillåter inte hämtning av startsidan"}
        resp = fetcher.get(home)
    except Exception as exc:
        return {"error": f"kunde inte nå sajten: {exc}"}
    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code} på startsidan"}
    home = normalize_home(str(getattr(resp, "url", home)))          # följ omdirigering, t.ex. till www
    base = re.sub(r"^www\.", "", _host(home))
    alt, ex = _AltLinks(), _Extractor()
    alt.feed(resp.text)
    ex.feed(resp.text)
    site_name = name or ex.meta.get("og:site_name") or re.split(r"\s[-|–]\s", ex.title.strip())[0] or base

    feeds = []
    for href in dict.fromkeys(urljoin(home, h) for h in alt.feeds):
        if len(feeds) < 3 and _feed_size(fetcher, href, base) >= 4:       # minst 3 poster + kanalens länk
            feeds.append(href)
    if not feeds:                                                         # prova vanliga adresser
        for path in COMMON_FEEDS:
            href = urljoin(home, path)
            if _feed_size(fetcher, href, base) >= 4:
                feeds.append(href)
                break
    if not feeds:
        return {"error": "ingen RSS/Atom-feed hittades (lägg till feeds manuellt i sources.json)"}
    entry = {"key": key or make_key(home, taken), "name": site_name, "home": home, "feeds": feeds}
    if label:
        entry["political_label"] = label
    return entry


def add_source(entry, path=SOURCES_PATH):
    """Lägg till en post i sources.json. Returnerar (lades_till, förklaring)."""
    data = []
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("sources", [])
    for e in data:
        if e.get("key") == entry["key"]:
            return False, "nyckeln finns redan i sources.json"
        if _host(e.get("home", "")) == _host(entry["home"]):
            return False, "sajten finns redan i sources.json"
    data.append(entry)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return True, "tillagd"


def parse_site_list(text):
    """Rader som 'https://sajt.se' eller 'https://sajt.se|Namn'; tomma rader och #-rader hoppas över."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            url, _, name = line.partition("|")
            out.append((url.strip(), name.strip() or None))
    return out
