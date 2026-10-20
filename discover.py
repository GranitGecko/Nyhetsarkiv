"""Upptäck artikel-URL:er via RSS och sitemaps (billigare än att crawla startsidor)."""
import datetime as dt
import gzip
import json
import re
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit

from config import RAW_DIR


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def extract_links(xml_bytes):
    root = ET.fromstring(xml_bytes)
    out = []
    for el in root.iter():
        name = _local(el.tag)
        if name == "link" and el.get("href"):
            out.append(el.get("href"))
        elif name in ("link", "loc", "guid") and el.text and el.text.strip().startswith("http"):
            out.append(el.text.strip())
    return list(dict.fromkeys(out)), _local(root.tag)


def _clean(url):
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}{p.path}"


def _collect(fetcher, url, depth=0):
    resp = fetcher.get(url)
    if resp.status_code != 200:
        return set()
    links, kind = extract_links(resp.content)
    if kind == "sitemapindex" and depth < 1:
        found = set()
        for child in links[-3:]:          # de senaste sitemaparna
            found |= _collect(fetcher, child, depth + 1)
        return found
    return set(links)


def page_links(html, base_url):
    parser = _Hrefs()
    parser.feed(html)
    return {urljoin(base_url, h) for h in parser.hrefs}


def discover_site(con, site, fetcher):
    found = set()
    for src in list(site.feeds) + list(site.sitemaps):
        try:
            found |= _collect(fetcher, src)
        except Exception as exc:
            print(f"  [{site.key}] kunde inte läsa {src}: {exc}")
    for page in getattr(site, "index_pages", []):          # sajter utan RSS: artikellänkar från listsidor
        try:
            resp = fetcher.get(page)
            if resp.status_code == 200:
                found |= {u for u in page_links(resp.text, page) if site.is_article_url(u)}
        except Exception as exc:
            print(f"  [{site.key}] kunde inte läsa {page}: {exc}")
    urls = {_clean(u) for u in found if site.is_feed_article(u)}
    before = con.total_changes
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    con.executemany(
        "INSERT OR IGNORE INTO articles(source,url,status,discovered_at,source_political_label)"
        " VALUES (?,?, 'discovered', ?, ?)",
        [(site.key, u, now, site.political_label) for u in sorted(urls)],
    )
    con.commit()
    return con.total_changes - before


# ---- Bakåt i tiden: sitemaps ------------------------------------------------

def parse_sitemap(data):
    """Returnerar (typ, [(url, datum eller None)]). Typ är 'urlset' eller 'sitemapindex'."""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    root = ET.fromstring(data)
    entries = []
    for node in root:
        loc = date = None
        for el in node.iter():
            name = _local(el.tag)
            if name == "loc" and el.text and loc is None:
                loc = el.text.strip()
            elif name in ("lastmod", "publication_date") and el.text and date is None:
                date = el.text.strip()
        if loc:
            entries.append((loc, date))
    return _local(root.tag), entries


def sitemap_roots(fetcher, site):
    """Sajtens sitemaps: de som står i sajtfilen, annars de som robots.txt pekar ut."""
    if site.sitemaps:
        return list(site.sitemaps)
    p = urlsplit(site.home)
    resp = fetcher.get(f"{p.scheme}://{p.netloc}/robots.txt")
    if resp.status_code != 200:
        return []
    return [m.group(1) for line in resp.text.splitlines()
            if (m := re.match(r"(?i)\s*sitemap:\s*(\S+)", line))]


def walk_sitemaps(fetcher, roots, since=None, max_sitemaps=30, log=print):
    """Går igenom sitemaps (index -> underfiler, nyaste först). Returnerar (url->datum, hämtade, kvar)."""
    queue, seen, found, fetched = list(roots), set(), {}, 0
    while queue and fetched < max_sitemaps:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            resp = fetcher.get(url)
        except Exception as exc:
            log(f"  kunde inte hämta {url}: {exc}")
            continue
        fetched += 1
        if resp.status_code != 200:
            log(f"  {url}: HTTP {resp.status_code}")
            continue
        try:
            kind, entries = parse_sitemap(resp.content)
        except Exception as exc:
            log(f"  kunde inte tolka {url}: {exc}")
            continue
        if kind == "sitemapindex":
            entries = sorted(entries, key=lambda e: e[1] or "", reverse=True)
            if since:
                entries = [e for e in entries if not e[1] or e[1][:10] >= since]
            queue.extend(loc for loc, _ in entries)
        else:
            for loc, date in entries:
                if since and date and date[:10] < since:
                    continue
                found[loc] = date
    return found, fetched, len([u for u in queue if u not in seen])


def backfill_site(con, site, fetcher, since=None, max_sitemaps=30):
    roots = sitemap_roots(fetcher, site)
    found, fetched, left = walk_sitemaps(fetcher, roots, since, max_sitemaps,
                                         log=lambda m: print(f"{site.key}:{m}"))
    urls = {}
    for loc, date in found.items():
        if site.is_article_url(loc):
            urls[_clean(loc)] = date
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    rows = [(site.key, u, now, d, site.political_label)
            for u, d in sorted(urls.items(), key=lambda kv: kv[1] or "")]   # äldst först
    before = con.total_changes
    con.executemany("INSERT OR IGNORE INTO articles(source,url,status,discovered_at,hint_date,"
                    "source_political_label) VALUES (?,?, 'discovered', ?, ?, ?)", rows)
    con.commit()
    return {"roots": len(roots), "sitemaps": fetched, "left": left, "urls": len(urls),
            "new": con.total_changes - before}


# ---- Bakåt i tiden: följ länkar i sparade artiklar ---------------------------

class _Hrefs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(href)


def snowball_site(con, site, max_pages=300):
    """Läs redan sparad rå-HTML och lägg till artikellänkar som saknas. Inga nya nätanrop."""
    rows = con.execute(
        "SELECT a.id, a.url, a.raw_path, a.published FROM articles a WHERE a.source=? "
        "AND a.status IN ('fetched','parsed') AND a.raw_path IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM links_scanned l WHERE l.article_id=a.id) "
        "ORDER BY COALESCE(a.published, a.discovered_at) DESC LIMIT ?", (site.key, max_pages)).fetchall()
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    before = con.total_changes
    for r in rows:
        try:
            with gzip.open(RAW_DIR / r["raw_path"], "rt", encoding="utf-8") as fh:
                parser = _Hrefs()
                parser.feed(fh.read())
        except Exception:
            parser = None
        if parser:
            urls = {_clean(urljoin(r["url"], h)) for h in parser.hrefs}
            urls = [u for u in urls if u != r["url"] and site.is_article_url(u)]
            con.executemany(
                "INSERT OR IGNORE INTO articles(source,url,status,discovered_at,hint_date,"
                "source_political_label) VALUES (?,?, 'discovered', ?, ?, ?)",
                [(site.key, u, now, r["published"] or "0000", site.political_label) for u in urls])
        con.execute("INSERT OR IGNORE INTO links_scanned(article_id) VALUES (?)", (r["id"],))
    con.commit()
    return {"scanned": len(rows), "new": con.total_changes - before - len(rows)}


# ---- Långt bakåt: Wayback Machines katalog (CDX) -----------------------------
# Ger bara en LISTA på URL:er som Internet Archive sett. Själva sidorna hämtas sedan från sajterna.

CDX_URL = "https://web.archive.org/cdx/search/cdx"


def cdx_urls(fetcher, prefix, since=None, until=None, filters=(), page=1000, max_urls=5000, log=print):
    """Returnerar ([(url, tidsstämpel)], fler_finns). Bläddrar med resumeKey."""
    params = [("url", prefix), ("matchType", "prefix"), ("output", "json"), ("fl", "original,timestamp"),
              ("collapse", "urlkey"), ("limit", min(page, max_urls)), ("showResumeKey", "true"),
              ("filter", "statuscode:200"), ("filter", "mimetype:text/html")]
    params += [("filter", f) for f in filters]
    if since:
        params.append(("from", re.sub(r"\D", "", since)))
    if until:
        params.append(("to", re.sub(r"\D", "", until)))
    out, resume, n = [], None, 0
    while len(out) < max_urls:
        query = params + ([("resumeKey", resume)] if resume else [])
        n += 1
        started = time.monotonic()
        log(f"  frågar Wayback om {prefix}, del {n} (svaret kan ta flera minuter, avbryt med Ctrl+C)")
        try:
            resp = fetcher.get(CDX_URL + "?" + urlencode(query), timeout=180)
        except Exception as exc:
            log(f"  katalogfrågan misslyckades efter {time.monotonic() - started:.0f} s: {exc}")
            break
        if resp.status_code != 200:
            log(f"  katalogen svarade HTTP {resp.status_code}: {resp.text[:120]!r}")
            break
        try:
            rows = json.loads(resp.text) if resp.text.strip() else []
        except ValueError:
            log("  katalogen gav ogiltigt JSON-svar")
            break
        resume, data = None, []
        for i, row in enumerate(rows):
            if row == ["original", "timestamp"]:
                continue
            if not row:                                     # tom rad: nästa rad är fortsättningsnyckeln
                nxt = rows[i + 1] if i + 1 < len(rows) else None
                if nxt and len(nxt) == 1:
                    resume = nxt[0]
                break
            data.append((row[0], row[1]))
        out.extend(data)
        log(f"  fick {len(data)} rader på {time.monotonic() - started:.0f} s, totalt {len(out)}")
        if not resume or not data:
            break
    return out[:max_urls], bool(resume) or len(out) > max_urls


def _norm_host(url, host):
    p = urlsplit(url)
    netloc = re.sub(r":(80|443)$", "", p.netloc.lower())
    return f"https://{netloc}{p.path}" if netloc == host else None


def _iso_ts(ts):
    if len(ts) >= 14:
        return f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}T{ts[8:10]}:{ts[10:12]}:{ts[12:14]}Z"
    return ts


def wayback_site(con, site, fetcher, since=None, until=None, max_urls=5000):
    host = urlsplit(site.home).netloc.lower()
    prefixes = site.cdx_prefixes or [host + "/"]
    per_prefix = max(1, max_urls // len(prefixes))
    scanned, more, urls = 0, False, {}
    for prefix in prefixes:
        found, more_p = cdx_urls(fetcher, prefix, since, until, site.cdx_filters, max_urls=per_prefix,
                                 log=lambda m: print(f"{site.key}:{m}", flush=True))
        scanned += len(found)
        more = more or more_p
        for original, ts in found:
            url = _norm_host(original, host)
            if url and site.is_article_url(url):
                urls.setdefault(url, _iso_ts(ts))
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    rows = [(site.key, u, now, d, site.political_label) for u, d in sorted(urls.items(), key=lambda kv: kv[1])]
    before = con.total_changes
    con.executemany("INSERT OR IGNORE INTO articles(source,url,status,discovered_at,hint_date,"
                    "source_political_label) VALUES (?,?, 'discovered', ?, ?, ?)", rows)
    con.commit()
    return {"scanned": scanned, "articles": len(urls), "new": con.total_changes - before, "more": more}
