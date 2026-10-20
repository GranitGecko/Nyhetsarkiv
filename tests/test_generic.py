import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import autodetect
import db
import discover
from sites.generic import GenericSite, load_sources


def rss(host, n=5, path="nyheter/rubrik-{i}"):
    items = "".join(f"<item><title>t{i}</title><link>https://{host}/{path.format(i=i)}</link></item>"
                    for i in range(n))
    return (f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>'
            f"<link>https://{host}/</link>{items}</channel></rss>").encode()


class Resp:
    def __init__(self, content, status=200, url=None):
        self.content, self.status_code = content, status
        self.text = content.decode("utf-8", "replace")
        if url:
            self.url = url


class Fetcher:
    def __init__(self, files, disallow=()):
        self.files, self.disallow, self.calls = files, set(disallow), []

    def allowed(self, url):
        return url not in self.disallow

    def get(self, url, timeout=None):
        self.calls.append(url)
        f = self.files.get(url)
        if f is None:
            return Resp(b"", 404)
        return f if isinstance(f, Resp) else Resp(f)


def memdb():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(db.SCHEMA)
    return con


class GenericSiteTests(unittest.TestCase):
    def setUp(self):
        self.s = GenericSite(key="nwt", name="NWT", home="https://www.nwt.se/")

    def test_article_heuristics(self):
        for ok in ["https://www.nwt.se/nyheter/rubrik-om-nagot-123", "https://www.nwt.se/2026/10/09/slug/",
                   "https://nwt.se/nyheter/karlstad/en-lang-rubrik", "https://www.nwt.se/artikel/1234567"]:
            self.assertTrue(self.s.is_article_url(ok), ok)
        for bad in ["https://www.nwt.se/", "https://www.nwt.se/tag/karlstad-1", "https://www.nwt.se/sport",
                    "https://www.nwt.se/bild.jpg", "https://annan.se/nyheter/rubrik-123",
                    "https://www.nwt.se/sok/rubrik-123", "https://www.nwt.se/nyheter"]:
            self.assertFalse(self.s.is_article_url(bad), bad)

    def test_feed_items_are_trusted(self):
        self.assertTrue(self.s.is_feed_article("https://www.nwt.se/artikel/abcd"))   # kort, men från flödet
        self.assertFalse(self.s.is_feed_article("https://www.nwt.se/"))
        self.assertFalse(self.s.is_feed_article("https://annan.se/artikel/abcd"))

    def test_custom_regex(self):
        s = GenericSite(key="x", home="https://x.se/", article_regex=r"/a/\d+", exclude_regex=r"/a/999")
        self.assertTrue(s.is_article_url("https://x.se/a/123"))
        self.assertFalse(s.is_article_url("https://x.se/a/abc"))
        self.assertFalse(s.is_article_url("https://x.se/a/999"))

    def test_parse_and_category_from_url(self):
        html = ('<html lang="sv"><head><title>T</title></head><body><article><p>' + "Brödtext om Karlstad. " * 6 +
                "</p></article></body></html>")
        d = self.s.parse(html, "https://www.nwt.se/nyheter/rubrik-123")
        self.assertEqual(d["category"], "nyheter")
        self.assertEqual(d["article_type"], "nyhet")
        self.assertIsNone(d["source_political_label"])
        d = self.s.parse(html, "https://www.nwt.se/2026/10/09/slug")
        self.assertIsNone(d["category"])                    # datum är ingen kategori

    def test_feed_discovery_uses_trusted_feed_links(self):
        con = memdb()
        f = Fetcher({"https://www.nwt.se/feed/": rss("www.nwt.se", 4, "artikel/ab{i}")})
        self.s.feeds = ["https://www.nwt.se/feed/"]
        self.assertEqual(discover.discover_site(con, self.s, f), 4)          # korta slugs släpps igenom


class LoadSourcesTests(unittest.TestCase):
    def test_load_and_validation(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sources.json"
            p.write_text(json.dumps([
                {"key": "bra", "name": "Bra", "home": "https://bra.se/", "feeds": ["https://bra.se/feed/"],
                 "daily_limit": 50, "okänt_fält": 1},
                {"key": "Fel Nyckel", "home": "https://a.se/"},
                {"key": "svt", "home": "https://b.se/"},
                {"key": "utan-home"},
                {"key": "bra", "home": "https://c.se/"},
            ]), encoding="utf-8")
            sites = load_sources(p, taken={"svt"})
            self.assertEqual([s.key for s in sites], ["bra"])
            self.assertEqual(sites[0].daily_limit, 50)
            self.assertTrue(sites[0].archive_enabled)
            self.assertEqual(load_sources(Path(d) / "finns-inte.json"), [])
            p.write_text("{trasig", encoding="utf-8")
            self.assertEqual(load_sources(p), [])


class AutodetectTests(unittest.TestCase):
    HOME = ('<html><head><title>Nyheter Idag - Startsida</title>'
            '<meta property="og:site_name" content="Nyheter Idag">'
            '<link rel="alternate" type="application/rss+xml" href="/feed/"></head><body></body></html>')

    def test_detect_from_link_tag(self):
        f = Fetcher({"https://www.exempel.se/": Resp(self.HOME.encode(), url="https://www.exempel.se/"),
                     "https://www.exempel.se/feed/": rss("www.exempel.se")})
        r = autodetect.detect_site(f, "www.exempel.se", taken=set())
        self.assertEqual((r["key"], r["name"], r["home"]), ("exempel", "Nyheter Idag", "https://www.exempel.se/"))
        self.assertEqual(r["feeds"], ["https://www.exempel.se/feed/"])

    def test_detect_common_path_and_redirect(self):
        f = Fetcher({"https://exempel.se/": Resp(b"<html><title>Lokaltidningen | Hem</title></html>",
                                                 url="https://www.exempel.se/"),
                     "https://www.exempel.se/rss": rss("www.exempel.se")})
        r = autodetect.detect_site(f, "https://exempel.se", taken=set())
        self.assertEqual((r["home"], r["name"], r["feeds"]), ("https://www.exempel.se/", "Lokaltidningen",
                                                              ["https://www.exempel.se/rss"]))

    def test_detect_errors(self):
        self.assertIn("robots", autodetect.detect_site(
            Fetcher({}, disallow={"https://a.se/"}), "https://a.se")["error"])
        self.assertIn("404", autodetect.detect_site(Fetcher({}), "https://a.se")["error"])
        r = autodetect.detect_site(Fetcher({"https://a.se/": b"<html></html>"}), "https://a.se")
        self.assertIn("ingen RSS", r["error"])
        # ett flöde med för få poster, eller bara andra sajters länkar, räknas inte
        f = Fetcher({"https://a.se/": b"<html></html>", "https://a.se/feed/": rss("a.se", 2),
                     "https://a.se/rss": rss("annan.se", 6)})
        self.assertIn("ingen RSS", autodetect.detect_site(f, "https://a.se")["error"])

    def test_manual_feed(self):
        f = Fetcher({"https://www.svd.se/feed/articles.rss": rss("www.svd.se")})
        r = autodetect.detect_site(f, "https://www.svd.se", name="Svenska Dagbladet", taken=set(),
                                   feeds=["https://www.svd.se/feed/articles.rss"])
        self.assertEqual(r, {"key": "svd", "name": "Svenska Dagbladet", "home": "https://www.svd.se/",
                             "feeds": ["https://www.svd.se/feed/articles.rss"]})
        r = autodetect.detect_site(f, "https://www.svd.se", taken=set(), feeds=["/feed/articles.rss"])   # relativ adress
        self.assertEqual((r["name"], r["feeds"]), ("svd.se", ["https://www.svd.se/feed/articles.rss"]))
        self.assertIn("giltigt", autodetect.detect_site(f, "https://www.svd.se", feeds=["https://www.svd.se/nej"])["error"])
        self.assertIn("robots", autodetect.detect_site(
            Fetcher({}, disallow={"https://www.svd.se/feed/x"}), "https://www.svd.se",
            feeds=["https://www.svd.se/feed/x"])["error"])

    def test_index_pages_detection_and_discovery(self):
        def page(*slugs):
            return ("<html><body>" + "".join(f'<a href="/artiklar/debatt/{s}">x</a>' for s in slugs) +
                    '<a href="/artiklar/debatt">sektion</a><a href="/om-oss">om</a></body></html>').encode()
        files = {"https://www.100.se/artiklar/debatt": page("ett", "tva", "tre", "fyra"),
                 "https://www.100.se/artiklar/samtal": page("fem")}
        regex = r"^https://www\.100\.se/artiklar/[^/]+/[^/?#]+$"
        r = autodetect.detect_site(Fetcher(files), "https://www.100.se", key="100procent", name="100%", taken=set(),
                                   index_pages=["/artiklar/debatt", "https://www.100.se/artiklar/samtal"], article_regex=regex)
        self.assertEqual(r["index_pages"], ["https://www.100.se/artiklar/debatt", "https://www.100.se/artiklar/samtal"])
        self.assertEqual((r["key"], r["name"], r["feeds"], r["article_regex"]), ("100procent", "100%", [], regex))
        # för få artikellänkar: tydligt fel som pekar på --article-regex
        bad = autodetect.detect_site(Fetcher({"https://www.100.se/artiklar/debatt": page("ett")}), "https://www.100.se",
                                     index_pages=["https://www.100.se/artiklar/debatt"], article_regex=regex)
        self.assertIn("--article-regex", bad["error"])
        self.assertIn("404", autodetect.detect_site(Fetcher({}), "https://www.100.se", index_pages=["https://www.100.se/x"])["error"])
        # upptäckt: länkar från listsidorna läggs i kön, sektionssidan och 'om-oss' gör det inte
        site = GenericSite(**{k: v for k, v in r.items() if k not in ("name",)}, name="100%")
        con = memdb()
        self.assertEqual(discover.discover_site(con, site, Fetcher(files)), 5)
        urls = sorted(x[0] for x in con.execute("SELECT url FROM articles"))
        self.assertEqual(urls[0], "https://www.100.se/artiklar/debatt/ett")
        self.assertNotIn("https://www.100.se/artiklar/debatt", urls)
        self.assertEqual(discover.discover_site(con, site, Fetcher(files)), 0)          # inga dubbletter

    def test_key_collisions_and_add_source(self):
        self.assertEqual(autodetect.make_key("https://www.gp.se/", {"gp"}), "gp-se")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sources.json"
            e = {"key": "gp", "name": "GP", "home": "https://www.gp.se/", "feeds": ["https://www.gp.se/rss"]}
            self.assertEqual(autodetect.add_source(e, p), (True, "tillagd"))
            self.assertFalse(autodetect.add_source(e, p)[0])
            self.assertFalse(autodetect.add_source(dict(e, key="gp2"), p)[0])       # samma värd
            self.assertEqual(len(json.loads(p.read_text(encoding="utf-8"))), 1)
            self.assertIn("Göteborg", json.dumps({"n": "Göteborg"}, ensure_ascii=False))

    def test_parse_site_list(self):
        text = "# kommentar\n\nhttps://a.se|Namn A\n  https://b.se  \n"
        self.assertEqual(autodetect.parse_site_list(text), [("https://a.se", "Namn A"), ("https://b.se", None)])


if __name__ == "__main__":
    unittest.main()
