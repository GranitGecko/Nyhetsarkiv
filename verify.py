"""Kontrollera att en arkiverad sida har med sina CSS-, JavaScript-, bild- och typsnittsfiler.

Läser sidans HTML ur WARC-filen, listar allt den pekar på (<link>, <script>, <img>, ...), läser även
CSS-filerna (url(...) och @import), och jämför med vad som faktiskt finns i fångsten.
"""
import gzip
import html as htmllib
import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import archive_settings as cfg
import config
from warc import load_bodies, read_slice

_SKIP_SCHEMES = ("data:", "javascript:", "about:", "blob:", "mailto:", "tel:", "#")
_CSS_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I)
_CSS_IMPORT = re.compile(r"@import\s+(?:url\(\s*)?(['\"])(.*?)\1", re.I)
_FONT_EXT = re.compile(r"\.(woff2?|ttf|otf|eot)(\?|#|$)", re.I)
_LABELS = {"html": "html", "css": "css", "js": "js", "bild": "bild", "typsnitt": "typsnitt",
           "json": "json", "media": "media", "övrigt": "övrigt"}


def key(url):
    """Jämförbar nyckel: ignorerar schema (http/https) och #-fragment."""
    p = urlsplit(url)
    return f"{p.netloc.lower()}{p.path or '/'}" + (f"?{p.query}" if p.query else "")


def category(mime, url=""):
    mime = (mime or "").lower()
    if mime in ("text/html", "application/xhtml+xml"):
        return "html"
    if mime == "text/css":
        return "css"
    if "javascript" in mime or "ecmascript" in mime:
        return "js"
    if mime.startswith("image/"):
        return "bild"
    if mime.startswith("font/") or "font" in mime or _FONT_EXT.search(url):
        return "typsnitt"
    if "json" in mime:
        return "json"
    if mime.startswith(("video/", "audio/")):
        return "media"
    return "övrigt"


def _srcset(value):
    out = []
    for cand in (value or "").split(","):
        parts = cand.strip().split()
        if parts and not parts[0].lower().startswith(_SKIP_SCHEMES):
            out.append(parts[0])
    return out


class _Refs(HTMLParser):
    """Samlar vad HTML:en pekar på. Varje post är (typ, [alternativ], flaggor); det räcker att ett alternativ finns.

    Innehåll i <noscript> hoppas över: en webbläsare med JavaScript hämtar det aldrig.
    """

    def __init__(self):
        super().__init__()
        self.refs = []
        self.noscript_skipped = 0
        self._noscript = 0
        self._picture = None          # alternativ i en <picture>: webbläsaren hämtar bara EN av dem
        self._picture_flags = {}
        self._icons = []              # ikoner i <head>: webbläsaren hämtar bara EN favicon

    def _add(self, kind, *values, lazy=False):
        alts = [v for v in values if v and not v.lower().startswith(_SKIP_SCHEMES)]
        if not alts:
            return
        if kind == "bild" and self._picture is not None:
            self._picture.extend(alts)
            self._picture_flags["lazy"] = self._picture_flags.get("lazy", False) or lazy
        else:
            self.refs.append((kind, alts, {"lazy": lazy} if lazy else {}))

    def handle_endtag(self, tag):
        if tag == "noscript" and self._noscript:
            self._noscript -= 1
        elif tag == "picture" and self._picture is not None:
            if self._picture:
                self.refs.append(("bild", self._picture, dict(self._picture_flags) if self._picture_flags.get("lazy") else {}))
            self._picture, self._picture_flags = None, {}

    def done(self):
        """Anropas efter feed(): lägg till favicon-gruppen."""
        if self._icons:
            self.refs.append(("bild", self._icons, {}))
            self._icons = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "noscript":
            self._noscript += 1
            return
        if self._noscript:
            if a.get("src") or a.get("srcset") or a.get("href"):
                self.noscript_skipped += 1
            return
        lazy = (a.get("loading") or "").lower() == "lazy" or any(k.startswith("data-src") for k in a)
        if tag == "link" and a.get("href"):
            rel = (a.get("rel") or "").lower().split()
            as_ = (a.get("as") or "").lower()
            if "stylesheet" in rel:
                self._add("css", a["href"])
            elif "modulepreload" in rel or (("preload" in rel) and as_ == "script"):
                self._add("js", a["href"])
            elif "preload" in rel and as_ == "style":
                self._add("css", a["href"])
            elif "preload" in rel and as_ == "font":
                self._add("typsnitt", a["href"])
            elif "icon" in rel:                       # 'icon' / 'shortcut icon': bara en av dem hämtas
                if not a["href"].lower().startswith(_SKIP_SCHEMES):
                    self._icons.append(a["href"])
            elif "preload" in rel and as_ == "image":
                self._add("bild", a["href"])
            # apple-touch-icon och mask-icon hämtas inte vid en vanlig sidvisning, så de kontrolleras inte
        elif tag == "script" and a.get("src"):
            self._add("js", a["src"])
        elif tag == "picture":
            self._picture, self._picture_flags = [], {}
        elif tag == "img":
            self._add("bild", a.get("src"), *_srcset(a.get("srcset")), lazy=lazy)
        elif tag == "source":
            kind = "media" if (a.get("type") or "").lower().startswith(("video/", "audio/")) else "bild"
            self._add(kind, a.get("src"), *_srcset(a.get("srcset")), lazy=lazy)
        elif tag in ("video", "audio"):
            self._add("media", a.get("src"))
            self._add("bild", a.get("poster"))
        elif tag == "iframe":
            self._add("iframe", a.get("src"), lazy=lazy)


_FONT_FACE = re.compile(r"@font-face\s*\{(.*?)\}", re.I | re.S)


def _usable(url):
    url = url.strip()
    return url if url and not url.lower().startswith(_SKIP_SCHEMES) else None


def _strip_image_sets(text):
    """Plocka ut image-set(...) (balanserade parenteser): webbläsaren väljer en av bilderna."""
    groups, out, i = [], [], 0
    low = text.lower()
    while True:
        j = low.find("image-set(", i)
        if j < 0:
            out.append(text[i:])
            break
        out.append(text[i:j])
        k, depth = j + len("image-set("), 1
        while k < len(text) and depth:
            depth += {"(": 1, ")": -1}.get(text[k], 0)
            k += 1
        inner = text[j + len("image-set("):k - 1]
        urls = [m.group(2) for m in _CSS_URL.finditer(inner)] + \
               [m.group(2) for m in re.finditer(r"(['\"])(.*?)\1", _CSS_URL.sub(" ", inner))]
        urls = [u for u in (_usable(x) for x in urls) if u]
        if urls:
            groups.append(urls)
        i = k
    return "".join(out), groups


def css_refs(text):
    """Vad en CSS-fil pekar på. Typsnitt i samma src-lista och bilder i image-set är alternativ."""
    out = []
    for m in _FONT_FACE.finditer(text):
        for decl in re.finditer(r"src\s*:([^;]*)", m.group(1), re.I):
            urls = [u for u in (_usable(x.group(2)) for x in _CSS_URL.finditer(decl.group(1))) if u]
            if urls:
                out.append(("typsnitt", urls, {}))
    text = _FONT_FACE.sub(" ", text)
    text, groups = _strip_image_sets(text)
    out += [("bild", g, {}) for g in groups]
    for m in _CSS_IMPORT.finditer(text):
        if _usable(m.group(2)):
            out.append(("css", [m.group(2)], {}))
    text = _CSS_IMPORT.sub(" ", text)
    for m in _CSS_URL.finditer(text):
        ref = _usable(m.group(2))
        if ref:
            kind = "typsnitt" if _FONT_EXT.search(ref) else ("css" if re.search(r"\.css(\?|#|$)", ref, re.I) else "bild")
            out.append((kind, [ref], {}))
    return out


def _plain(text):
    """Bara små bokstäver och siffror: gör jämförelser okänsliga för blanksteg, citattecken och taggar."""
    return re.sub(r"[\W_]+", "", (text or "").lower())


def _unescape_js(text):
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)


def _html_text(raw):
    raw = re.sub(r"(?is)<(style|noscript).*?</\1>", " ", raw)       # skript behålls: där ligger ofta sidans data
    return _unescape_js(htmllib.unescape(re.sub(r"(?s)<[^>]+>", " ", raw)))


def content_check(con, snap, main, items):
    """Finns artikelns rubrik och text (som den står i databasen) i den arkiverade sidan?"""
    if con is None or not snap["article_id"] or main is None or main["body"] is None:
        return None
    art = con.execute("SELECT title, body, raw_path, paywalled FROM articles WHERE id=?", (snap["article_id"],)).fetchone()
    if not art:
        return None
    page = _plain(_html_text(main["body"].decode("utf-8", "replace")))
    body = _plain(art["body"])
    snippet = body[len(body) // 3:len(body) // 3 + 40] if len(body) >= 120 else body[:40]
    in_html = bool(snippet) and snippet in page
    matches = []                                   # var i fångsten texten finns (HTML-dokument, JSON, skript)
    if snippet:
        for i in items.values():
            cat = category(i["mime"], i["url"])
            if i["body"] is None or cat not in ("html", "json", "js", "övrigt"):
                continue
            raw = i["body"].decode("utf-8", "replace")
            if cat == "json":
                try:
                    raw = json.dumps(json.loads(raw), ensure_ascii=False)
                except ValueError:
                    pass
            text = _html_text(raw) if cat == "html" else _unescape_js(htmllib.unescape(raw))
            if snippet in _plain(text):
                matches.append((cat, i["url"], i["size"]))
    in_json = (not in_html) and any(m[0] in ("json", "js", "övrigt") for m in matches)
    raw_has = raw_len = None                       # texten i den rå-HTML som sparades vid vanlig hämtning
    if snippet and art["raw_path"]:
        try:
            with gzip.open(config.RAW_DIR / art["raw_path"], "rt", encoding="utf-8") as fh:
                rawtext = fh.read()
            raw_len = len(rawtext)
            raw_has = snippet in _plain(_html_text(rawtext))
        except OSError:
            pass
    return {"title": bool(art["title"]) and _plain(art["title"]) in page, "has_text": bool(snippet),
            "text_in_html": in_html, "text_in_json": in_json, "text_chars": len(art["body"] or ""),
            "matches": matches, "raw_has": raw_has, "raw_len": raw_len, "main_len": len(main["body"]),
            "excerpt": " ".join((art["body"] or "").split())[:160], "paywalled": art["paywalled"]}


def find_snapshot(con, target):
    from sites import SITES
    target = str(target)
    if target.isdigit():
        sql, arg = "SELECT * FROM snapshots WHERE article_id=? AND status='ok' ORDER BY taken_at DESC LIMIT 1", int(target)
    elif target in SITES:
        sql, arg = "SELECT * FROM snapshots WHERE source=? AND kind='front' AND status='ok' ORDER BY taken_at DESC LIMIT 1", target
    else:
        sql, arg = "SELECT * FROM snapshots WHERE url=? AND status='ok' ORDER BY taken_at DESC LIMIT 1", target
    return con.execute(sql, (arg,)).fetchone()


_SKIP_TEXT = {
    "blockerad_värd": "blockerad värd (BLOCK_HOSTS i archive_settings.py)",
    "för_stor": "större än MAX_RESOURCE_BYTES (archive_settings.py)",
    "media": "media sparas inte (inställning)",
    "saknar_innehåll": "webbläsaren gav inget innehåll för resursen",
    "ingen_respons": "resursen svarade inte (misslyckad förfrågan)",
}


def _reason(kind, url, block_hosts, skipped=None):
    host = urlsplit(url).netloc.lower()
    if skipped and key(url) in skipped:
        return _SKIP_TEXT.get(skipped[key(url)], skipped[key(url)])
    if kind == "media":
        return "media sparas inte (inställning)"
    if any(b in host for b in block_hosts):
        return "blockerad värd (BLOCK_HOSTS i archive_settings.py)"
    return "fångades inte under laddningen"


def analyze(snap, block_hosts=None, con=None):
    block_hosts = cfg.BLOCK_HOSTS if block_hosts is None else block_hosts
    path = cfg.WARC_DIR / snap["warc_path"]
    items = {}
    for headers, block in read_slice(path, snap["warc_offset"], snap["warc_length"]):
        kind = headers.get("WARC-Type")
        if kind not in ("response", "revisit"):
            continue
        head, _, body = block.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        hdrs = {k.lower(): v for k, v in (line.split(": ", 1) for line in lines[1:] if ": " in line)}
        mime = hdrs.get("content-type", "").split(";")[0].strip().lower()
        url = headers["WARC-Target-URI"]
        items[key(url)] = {
            "url": url, "status": int(lines[0].split()[1]), "mime": mime, "revisit": kind == "revisit",
            "size": len(body) if kind == "response" else int(hdrs.get("content-length", "0") or 0),
            "body": body if kind == "response" else None, "ref": headers.get("WARC-Refers-To")}
    # dubblettposter: hämta innehållet från den tidigare posten när vi behöver läsa det (HTML och CSS)
    need = {i["ref"] for i in items.values()
            if i["revisit"] and category(i["mime"], i["url"]) in ("html", "css") and i["ref"]}
    bodies = load_bodies(path, need)
    for i in items.values():
        if i["revisit"] and i["ref"] in bodies:
            i["body"] = bodies[i["ref"]]

    main = items.get(key(snap["url"])) or next((i for i in items.values() if category(i["mime"]) == "html"), None)
    counts, bytes_by = {}, {}
    for i in items.values():
        c = category(i["mime"], i["url"])
        counts[c] = counts.get(c, 0) + 1
        bytes_by[c] = bytes_by.get(c, 0) + i["size"]

    skipped = {}
    try:
        notes = json.loads(snap["notes"]) if "notes" in snap.keys() and snap["notes"] else {}
        skipped = {key(u): r for u, r in (notes.get("urls") or [])} if isinstance(notes, dict) else {}
    except (ValueError, TypeError):
        pass

    checks = []                                   # (källa, typ, alternativ, hittad_url eller None, flaggor)

    def check(source, base, refs):
        for kind, alts, flags in refs:
            urls = [urljoin(base, a) for a in alts]
            hit = next((u for u in urls if key(u) in items), None)
            checks.append((source, kind, urls, hit, flags))

    noscript_skipped = 0
    if main and main["body"] is not None:
        parser = _Refs()
        parser.feed(main["body"].decode("utf-8", "replace"))
        parser.done()
        noscript_skipped = parser.noscript_skipped
        check("HTML", main["url"], parser.refs)
    for i in list(items.values()):
        if category(i["mime"], i["url"]) == "css" and i["body"] is not None:
            check("CSS", i["url"], css_refs(i["body"].decode("utf-8", "replace")))

    missing, unused_fonts = [], []
    for src, kind, urls, hit, flags in checks:
        if hit is not None or kind == "iframe":
            continue
        # Typsnitt som bara deklareras i CSS hämtas av webbläsaren först när sidan använder dem. Saknas ett
        # utan att ha hoppats över med flit är det alltså troligen oanvänt, inte en lucka i arkivet.
        if kind == "typsnitt" and src == "CSS" and not any(key(u) in skipped for u in urls):
            unused_fonts.append(urls[0])
            continue
        missing.append((src, kind, urls[0], _reason(kind, urls[0], block_hosts, skipped), flags))
    iframes_missing = [urls[0] for src, kind, urls, hit, flags in checks if hit is None and kind == "iframe"]
    errors = [(i["status"], i["url"]) for i in items.values() if i["status"] >= 400]
    listing = sorted(((category(i["mime"], i["url"]), i["status"], i["size"], i["revisit"], i["url"])
                      for i in items.values()), key=lambda r: (r[0], r[4]))
    return {"snap": snap, "content": content_check(con, snap, main, items), "listing": listing, "items": len(items), "counts": counts, "bytes": bytes_by, "main": main,
            "checked": len(checks), "found": sum(1 for c in checks if c[3]), "noscript_skipped": noscript_skipped,
            "missing": missing, "unused_fonts": unused_fonts, "iframes_missing": iframes_missing, "errors": errors}


def report(res, show=15, listing=False, text=False):
    s = res["snap"]
    what = f"artikel #{s['article_id']}" if s["article_id"] else "startsida"
    lines = [f"Fångst: {s['source']} {what}, {s['taken_at'][:16].replace('T', ' ')} UTC, {s['url']}",
             f"Fil: {s['warc_path']}  ({res['items']} poster i fångsten)"]
    lines.append("Innehåll: " + " | ".join(f"{k} {v}" for k, v in sorted(res["counts"].items(), key=lambda kv: -kv[1])))
    if res["main"] is None:
        lines.append("\nHUVUDSIDAN HITTADES INTE i fångsten, så sidans referenser kan inte kontrolleras.")
        return "\n".join(lines)
    real = [m for m in res["missing"] if not m[3].startswith("media")]
    media = [m for m in res["missing"] if m[3].startswith("media")]
    lines.append(f"\nSidans referenser (HTML och CSS): {res['checked']} st, i arkivet {res['found']}, saknas {len(res['missing'])}")
    if real:
        lines.append(f"\nSAKNAS ({len(real)}):")
        for src, kind, url, why, flags in real[:show]:
            lazy = "; lazy-loadad (hämtas först när den scrollas fram)" if flags.get("lazy") else ""
            lines.append(f"  [{kind:8}] {url}\n             från {src}; orsak: {why}{lazy}")
        if len(real) > show:
            lines.append(f"  ... och {len(real) - show} till")
    if media:
        lines.append(f"\nMedia som inte sparas med flit: {len(media)} st")
    c = res.get("content")
    if c is not None:
        lines.append("\nArtikeltext (jämförd med det som lagrats i databasen):")
        lines.append(f"  rubriken finns i den arkiverade sidan: {'ja' if c['title'] else 'NEJ'}")
        if not c["has_text"]:
            lines.append("  brödtexten: databasen har ingen text att jämföra med")
        elif c["text_in_html"]:
            lines.append("  brödtexten finns i den arkiverade sidan: ja (i HTML:en)")
        elif c["text_in_json"]:
            lines.append("  brödtexten finns i den arkiverade sidan: ja, men i ett JSON-svar (sidan ritar texten med JavaScript)")
        else:
            lines.append("  brödtexten finns i den arkiverade sidan: NEJ. Det kan bero på betalvägg eller att texten visas först "
                         "efter inloggning eller klick.")
    if c is not None and text:
        lines.append("\nDETALJER OM ARTIKELTEXTEN:")
        lines.append(f"  utdrag ur databasens text ({c['text_chars']} tecken): \"{c['excerpt']}\"")
        pay = {1: "ja", 0: "nej"}.get(c.get("paywalled"), "okänt")
        lines.append(f"  artikeln är markerad som betalvägg i databasen (sajtens egen uppgift): {pay}")
        if c["raw_len"] is not None:
            lines.append(f"  vanlig hämtning (rå-HTML, {c['raw_len']} tecken) innehåller texten: {'ja' if c['raw_has'] else 'NEJ'}")
        else:
            lines.append("  vanlig hämtning: rå-HTML finns inte kvar att jämföra med")
        lines.append(f"  webbläsarens sida i arkivet ({c['main_len']} tecken) innehåller texten: {'ja' if c['text_in_html'] else 'NEJ'}")
        for cat, url, size in c["matches"]:
            lines.append(f"  texten finns i: [{cat}] {url} ({size / 1024:.1f} kB)")
        if not c["matches"] and c["has_text"]:
            lines.append("  texten hittades inte i något HTML-, JSON- eller skriptsvar i fångsten")
        try:
            skipped = json.loads(res["snap"]["notes"] or "{}")
        except (ValueError, TypeError, KeyError, IndexError):
            skipped = {}
        posts = [u for u, why in (skipped.get("urls") or []) if why == "ej_get"]
        n_posts = (skipped.get("counts") or {}).get("ej_get", 0)
        if n_posts:
            lines.append(f"  anrop som inte sparas (POST m.m.): {n_posts} st" + (":" if posts else " (arkivera om artikeln för att se adresserna)"))
            lines += [f"    {u[:120]}" for u in posts[:6]]
        if c["has_text"] and not c["text_in_html"] and not c["text_in_json"]:
            if c["raw_has"]:
                lines.append("  Tolkning: servern gav texten till vanlig hämtning men inte till webbläsaren. Vanliga orsaker är en betalvägg "
                             "eller samtyckesvägg som bara visas i webbläsare, eller att texten hämtas av ett anrop som inte sparas.")
            elif c["raw_has"] is False:
                lines.append("  Tolkning: texten finns inte heller i den rå-HTML som sparades vid hämtningen. Kontrollera artikeln i webbplatsen "
                             "(/artikel/<id>): databasens text kan ha tolkats fram ur något annat än sidans synliga text.")
    if res.get("unused_fonts"):
        lines.append(f"\nTypsnitt som CSS deklarerar men sidan inte använde: {len(res['unused_fonts'])} st "
                     "(webbläsaren hämtar bara typsnitt som behövs, så de räknas inte som saknade)")
    if res.get("noscript_skipped"):
        lines.append(f"\nIgnorerade referenser i <noscript> (hämtas inte av en webbläsare med JavaScript): {res['noscript_skipped']} st")
    if res["iframes_missing"]:
        lines.append(f"\nInbäddade ramar (iframe) som inte fångades: {len(res['iframes_missing'])} st (oftast annonser och inbäddningar)")
    if res["errors"]:
        lines.append(f"\nResurser som svarade med fel när sidan laddades: {len(res['errors'])} st")
        for status, url in res["errors"][:5]:
            lines.append(f"  HTTP {status} {url}")
    if listing:
        lines.append("\nALLA RESURSER I FÅNGSTEN:")
        for cat, status, size, revisit, url in res["listing"]:
            lines.append(f"  {cat:8} {status} {size / 1024:8.1f} kB {'dubbl.' if revisit else '      '} {url[:120]}")
    if not real:
        lines.append("\nALLT som HTML:en och CSS-filerna pekar på finns i arkivet.")
    else:
        css_js = [m for m in real if m[1] in ("css", "js")]
        lazy_n = sum(1 for m in real if m[4].get("lazy"))
        lines.append(f"\nSammanfattning: {len(css_js)} saknade CSS/JS-filer och {len(real) - len(css_js)} saknade bilder/typsnitt, "
                     f"varav {lazy_n} lazy-loadade och {len(real) - lazy_n} som inte är märkta som lazy.")
        if any("BLOCK_HOSTS" in m[3] for m in real):
            lines.append("Tips: vill du spara även annons-/spårningsvärdarna, sätt BLOCK_HOSTS = [] i archive_settings.py.")
        if any("MAX_RESOURCE_BYTES" in m[3] for m in real):
            lines.append("Tips: höj MAX_RESOURCE_BYTES i archive_settings.py för att spara större filer.")
        if any(m[4].get("lazy") and "fångades inte" in m[3] for m in real):
            lines.append("Tips: lazy-loadade bilder hämtas bara när de syns. Kontrollera att FORCE_EAGER_LOADING = True i archive_settings.py "
                         "och arkivera om artikeln: python cli.py archive --site <sajt> --again <id>")
        if any((not m[4].get("lazy")) and "fångades inte" in m[3] for m in real):
            lines.append("Tips: resurser som 'fångades inte' och inte är lazy laddas ofta sent eller bara vid klick/hover. "
                         "Höj NETWORK_IDLE_SECONDS/EXTRA_WAIT_SECONDS i archive_settings.py.")
    return "\n".join(lines)
