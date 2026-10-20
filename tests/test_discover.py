import gzip
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import discover
from sites import SITES

AB = SITES["aftonbladet"]
INDEX = b"""<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>https://www.aftonbladet.se/sm/2026-08.xml</loc><lastmod>2026-08-31</lastmod></sitemap>
<sitemap><loc>https://www.aftonbladet.se/sm/2026-10.xml</loc><lastmod>2026-10-10</lastmod></sitemap>
<sitemap><loc>https://www.aftonbladet.se/sm/2026-09.xml</loc><lastmod>2026-09-30</lastmod></sitemap>
</sitemapindex>"""


def urlset(*items):
    body = "".join(f"<url><loc>{u}</loc><lastmod>{d}</lastmod></url>" for u, d in items)
    return ('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + body + "</urlset>").encode()


class Resp:
    def __init__(self, content, status=200):
        self.content, self.status_code = content, status
        self.text = content.decode("utf-8", "replace")


class FakeFetcher:
    def __init__(self, files):
        self.files, self.calls = files, []

    def get(self, url, timeout=None):
        self.calls.append(url)
        return Resp(self.files[url]) if url in self.files else Resp(b"", 404)


FILES = {
    "https://www.aftonbladet.se/robots.txt": b"User-agent: *\nSitemap: https://www.aftonbladet.se/sitemap.xml\n",
    "https://www.aftonbladet.se/sitemap.xml": INDEX,
    "https://www.aftonbladet.se/sm/2026-10.xml": urlset(
        ("https://www.aftonbladet.se/nyheter/a/new1/slug?x=1", "2026-10-05T10:00:00+02:00"),
        ("https://www.aftonbladet.se/nyheter", "2026-10-05")),          # inte en artikel
    "https://www.aftonbladet.se/sm/2026-09.xml": gzip.compress(urlset(
        ("https://www.aftonbladet.se/nyheter/a/sep1/slug", "2026-09-15"))),
    "https://www.aftonbladet.se/sm/2026-08.xml": urlset(
        ("https://www.aftonbladet.se/nyheter/a/aug1/slug", "2026-08-20")),
}


def memdb():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(db.SCHEMA)
    return con


class DiscoverTests(unittest.TestCase):
    def test_parse_sitemap(self):
        kind, entries = discover.parse_sitemap(INDEX)
        self.assertEqual(kind, "sitemapindex")
        self.assertEqual(entries[0], ("https://www.aftonbladet.se/sm/2026-08.xml", "2026-08-31"))
        kind, entries = discover.parse_sitemap(gzip.compress(urlset(("https://a.se/x", "2026-01-01"))))
        self.assertEqual((kind, entries), ("urlset", [("https://a.se/x", "2026-01-01")]))

    def test_roots_from_robots(self):
        self.assertEqual(discover.sitemap_roots(FakeFetcher(FILES), AB),
                         ["https://www.aftonbladet.se/sitemap.xml"])

    def test_backfill_all_and_since(self):
        con = memdb()
        r = discover.backfill_site(con, AB, FakeFetcher(FILES))
        self.assertEqual((r["urls"], r["new"], r["left"]), (3, 3, 0))
        urls = [x[0] for x in con.execute("SELECT url FROM articles ORDER BY id")]
        # äldst först, så att nyaste får högst id; frågetecken-delen rensas bort
        self.assertEqual(urls, ["https://www.aftonbladet.se/nyheter/a/aug1/slug",
                                "https://www.aftonbladet.se/nyheter/a/sep1/slug",
                                "https://www.aftonbladet.se/nyheter/a/new1/slug"])
        self.assertEqual(discover.backfill_site(con, AB, FakeFetcher(FILES))["new"], 0)   # idempotent
        con = memdb()
        f = FakeFetcher(FILES)
        r = discover.backfill_site(con, AB, f, since="2026-09-01")
        self.assertEqual(r["urls"], 2)
        self.assertNotIn("https://www.aftonbladet.se/sm/2026-08.xml", f.calls)   # augusti hämtas inte alls

    def test_max_sitemaps(self):
        con = memdb()
        r = discover.backfill_site(con, AB, FakeFetcher(FILES), max_sitemaps=2)
        self.assertEqual(r["sitemaps"], 2)
        self.assertEqual(r["left"], 2)        # två underfiler väntar
        self.assertEqual(r["urls"], 1)        # den nyaste (oktober) lästes först

    def test_snowball(self):
        con = memdb()
        with tempfile.TemporaryDirectory() as d:
            old = discover.RAW_DIR
            discover.RAW_DIR = Path(d)
            try:
                html = ('<a href="/nyheter/a/rel1/slug">a</a><a href="https://www.aftonbladet.se/nyheter/a/rel2/x?y=1#z">b</a>'
                        '<a href="/nyheter">sektion</a><a href="https://annan.se/a/b/c">annan</a>'
                        '<a href="/nyheter/a/self/slug">själv</a>')
                (Path(d) / "ab").mkdir()
                with gzip.open(Path(d) / "ab" / "x.html.gz", "wt", encoding="utf-8") as fh:
                    fh.write(html)
                con.execute("INSERT INTO articles(source,url,status,raw_path,published) VALUES "
                            "('aftonbladet','https://www.aftonbladet.se/nyheter/a/self/slug','parsed','ab/x.html.gz',"
                            "'2026-10-08T12:00:00+02:00')")
                con.commit()
                r = discover.snowball_site(con, AB)
                self.assertEqual(r, {"scanned": 1, "new": 2})
                rows = con.execute("SELECT url, hint_date, status FROM articles WHERE id>1 ORDER BY url").fetchall()
                self.assertEqual([x["url"] for x in rows], ["https://www.aftonbladet.se/nyheter/a/rel1/slug",
                                                            "https://www.aftonbladet.se/nyheter/a/rel2/x"])
                self.assertTrue(all(x["hint_date"].startswith("2026-10-08") and x["status"] == "discovered" for x in rows))
                self.assertEqual(discover.snowball_site(con, AB), {"scanned": 0, "new": 0})   # genomsökt en gång
            finally:
                discover.RAW_DIR = old

    def test_cdx_paging_and_filtering(self):
        import json
        from urllib.parse import parse_qs, urlsplit as us

        class Cdx:
            calls = []

            def get(self, url, timeout=None):
                q = parse_qs(us(url).query)
                self.calls.append(q)
                if "resumeKey" not in q:
                    rows = [["original", "timestamp"],
                            ["http://www.aftonbladet.se:80/nyheter/a/old1/slug?x=1", "20200105101112"],
                            ["https://www.aftonbladet.se/nyheter", "20200105101112"],          # sektionssida
                            ["https://annan.se/nyheter/a/x/y", "20200105101112"],              # fel värd
                            [], ["NYCKEL123"]]
                else:
                    rows = [["original", "timestamp"],
                            ["https://www.aftonbladet.se/nyheter/a/old2/slug", "20200301000000"]]
                return Resp(json.dumps(rows).encode())

        con = memdb()
        f = Cdx()
        r = discover.wayback_site(con, AB, f, since="2020-01", until="2020-12", max_urls=100)
        self.assertEqual((r["scanned"], r["articles"], r["new"], r["more"]), (4, 2, 2, False))
        q1, q2 = f.calls
        self.assertEqual(q1["url"], ["www.aftonbladet.se/"])
        self.assertEqual(q1["matchType"], ["prefix"])
        self.assertEqual(q1["from"], ["202001"])
        self.assertEqual(q1["to"], ["202012"])
        self.assertEqual(q1["collapse"], ["urlkey"])
        self.assertIn("statuscode:200", q1["filter"])
        self.assertIn("original:.*/a/.+", q1["filter"])          # sajtens eget serverfilter
        self.assertEqual(q2["resumeKey"], ["NYCKEL123"])
        rows = con.execute("SELECT url, hint_date, status FROM articles ORDER BY id").fetchall()
        self.assertEqual([(x["url"], x["hint_date"]) for x in rows],
                         [("https://www.aftonbladet.se/nyheter/a/old1/slug", "2020-01-05T10:11:12Z"),
                          ("https://www.aftonbladet.se/nyheter/a/old2/slug", "2020-03-01T00:00:00Z")])

    def test_cdx_max_and_errors(self):
        import json

        class Many:
            def get(self, url, timeout=None):
                rows = [["original", "timestamp"]] + [
                    [f"https://www.aftonbladet.se/nyheter/a/id{i}/slug", "20200101000000"] for i in range(10)]
                return Resp(json.dumps(rows + [[], ["MER"]]).encode())

        found, more = discover.cdx_urls(Many(), "www.aftonbladet.se/", max_urls=5)
        self.assertEqual((len(found), more), (5, True))

        class Err:
            def get(self, url, timeout=None):
                return Resp(b"for manga anrop", 429)

        msgs = []
        found, more = discover.cdx_urls(Err(), "x/", log=msgs.append)
        self.assertEqual((found, more), ([], False))
        self.assertTrue(any("429" in m for m in msgs))
        self.assertTrue(any("frågar Wayback" in m for m in msgs))       # förloppsutskrift finns

    def test_svt_prefixes(self):
        self.assertIn("www.svt.se/nyheter/", SITES["svt"].cdx_prefixes)
        self.assertIsNone(SITES["dn"].cdx_prefixes)


if __name__ == "__main__":
    unittest.main()
