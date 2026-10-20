"""Generisk artikeltolkning (endast standardbiblioteket + valfritt trafilatura).

Läser JSON-LD (schema.org NewsArticle), OpenGraph och <meta>. Sajtfilerna i
sites/ bygger på detta och justerar bara det som skiljer sig.
"""
import json
from html.parser import HTMLParser

ARTICLE_TYPES = {
    "NewsArticle", "Article", "ReportageNewsArticle", "OpinionNewsArticle",
    "AnalysisNewsArticle", "ReviewNewsArticle", "BlogPosting", "LiveBlogPosting",
}


class _Extractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.jsonld, self.meta = [], {}
        self.canonical = self.lang = None
        self.title = ""
        self._in_ld = self._in_title = self._in_p = False
        self._ld_buf, self._p = [], []
        self._skip = 0
        self._article_depth = 0
        self.paras, self.all_paras = [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.lang = a.get("lang")
        elif tag == "meta":
            key = a.get("property") or a.get("name")
            if key and a.get("content") is not None:
                self.meta.setdefault(key.lower(), a["content"])
        elif tag == "link" and a.get("rel") == "canonical":
            self.canonical = a.get("href")
        elif tag == "title":
            self._in_title = True
        elif tag == "script":
            if a.get("type") == "application/ld+json":
                self._in_ld, self._ld_buf = True, []
            else:
                self._skip += 1
        elif tag == "style":
            self._skip += 1
        elif tag == "article":
            self._article_depth += 1
        elif tag == "p":
            self._in_p, self._p = True, []

    def handle_endtag(self, tag):
        if tag == "script":
            if self._in_ld:
                self._in_ld = False
                try:
                    self.jsonld.append(json.loads("".join(self._ld_buf)))
                except ValueError:
                    pass
            else:
                self._skip = max(0, self._skip - 1)
        elif tag == "style":
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False
        elif tag == "article":
            self._article_depth = max(0, self._article_depth - 1)
        elif tag == "p" and self._in_p:
            self._in_p = False
            text = " ".join("".join(self._p).split())
            if text:
                self.all_paras.append(text)
                if self._article_depth:
                    self.paras.append(text)

    def handle_data(self, data):
        if self._in_ld:
            self._ld_buf.append(data)
        elif self._skip:
            return
        elif self._in_title:
            self.title += data
        elif self._in_p:
            self._p.append(data)


def _flatten(node):
    if isinstance(node, list):
        for n in node:
            yield from _flatten(n)
    elif isinstance(node, dict):
        yield node
        if "@graph" in node:
            yield from _flatten(node["@graph"])


def _find_article(blocks):
    for item in _flatten(blocks):
        t = item.get("@type")
        types = t if isinstance(t, list) else [t]
        if any(x in ARTICLE_TYPES for x in types):
            return item
    return {}


def _names(v):
    if not v:
        return None
    items = v if isinstance(v, list) else [v]
    out = [(i.get("name") if isinstance(i, dict) else str(i)) for i in items]
    out = [o.strip() for o in out if o and o.strip()]
    return ", ".join(out) or None


def _list(v):
    if not v:
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return [str(x).strip() for x in v if str(x).strip()]


def _first(v):
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _image(v):
    v = _first(v)
    if isinstance(v, dict):
        return v.get("url")
    return v


def _body(html, ex):
    try:
        import trafilatura
        text = trafilatura.extract(html, include_comments=False, include_tables=False)
        if text:
            return text
    except Exception:
        pass
    paras = [p for p in (ex.paras or ex.all_paras) if len(p) > 40]
    return "\n\n".join(paras) or None


def parse_generic(html, url):
    ex = _Extractor()
    ex.feed(html)
    art = _find_article(ex.jsonld)
    m = ex.meta
    ld_type = art.get("@type")
    if isinstance(ld_type, list):
        ld_type = ",".join(ld_type)
    free = art.get("isAccessibleForFree")
    return {
        "title": art.get("headline") or m.get("og:title") or ex.title.strip() or None,
        "ingress": art.get("description") or m.get("og:description") or m.get("description"),
        "body": art.get("articleBody") or _body(html, ex),
        "author": _names(art.get("author")) or m.get("author") or m.get("article:author"),
        "published": art.get("datePublished") or m.get("article:published_time"),
        "updated": art.get("dateModified") or m.get("article:modified_time"),
        "category": (_first(art.get("articleSection")) or m.get("article:section")
                     or m.get("articlesection")),
        "tags": _list(art.get("keywords")) or _list(m.get("news_keywords")),
        "image": _image(art.get("image")) or m.get("og:image"),
        "lang": ex.lang or m.get("og:locale"),
        "canonical_url": ex.canonical or m.get("og:url") or url,
        "paywalled": 1 if str(free).lower() == "false" else 0,
        "ld_type": ld_type,
    }
