import datetime as dt
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import verify
import webarchive

NOW = dt.datetime(2026, 10, 10, 5, 30, tzinfo=dt.timezone.utc)
BASE = "https://www.exempel.se"

HTML = f"""<html><head>
<link rel="stylesheet" href="/s.css">
<link rel="stylesheet" href="https://ads.doubleclick.net/t.css">
<link rel="icon" href="/favicon.ico">
<script src="/a.js"></script><script src="/saknas.js"></script>
</head><body>
<img src="/i.png" srcset="/i2.png 2x, /i3.png 3x">
<img src="data:image/gif;base64,AAAA">
<picture><source srcset="/p1.webp 1x, /p2.webp 2x"><img src="/p.jpg"></picture>
<video poster="/poster.jpg"><source src="/v.mp4" type="video/mp4"></video>
<iframe src="https://embed.annan.se/x"></iframe>
</body></html>""".encode()

CSS = (b'@import "/imp.css"; body{background:url(/bg.png)} '
       b'@font-face{src:url("/f.woff2") format("woff2")} .x{background:url(data:image/png;base64,AA)}')


def entry(path, body, mime, status=200, host=BASE):
    return {"url": host + path, "date": NOW, "status": status, "reason": "", "body": body, "mime": mime,
            "headers": [("Content-Type", mime)]}


def memdb():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(db.SCHEMA)
    return con


def snapshot(con, rel, stats, url):
    con.execute("INSERT INTO snapshots(source,kind,article_id,url,taken_at,status,warc_path,warc_offset,"
                "warc_length,n_records,n_revisits,bytes) VALUES ('t','article',1,?,?,'ok',?,?,?,?,?,?)",
                (url, "2026-10-10T05:30:00Z", rel, stats["offset"], stats["length"], stats["n_records"],
                 stats["n_revisits"], stats["bytes"]))
    return con.execute("SELECT * FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()


class VerifyTests(unittest.TestCase):
    def test_missing_resources_and_reasons(self):
        con = memdb()
        old = webarchive.WARC_DIR, verify.cfg.WARC_DIR
        with tempfile.TemporaryDirectory() as d:
            webarchive.WARC_DIR = verify.cfg.WARC_DIR = Path(d)
            try:
                entries = [entry("/artikel", HTML, "text/html"), entry("/s.css", CSS, "text/css"),
                           entry("/a.js", b"1", "application/javascript"), entry("/favicon.ico", b"i", "image/x-icon"),
                           entry("/i2.png", b"x", "image/png"), entry("/p2.webp", b"x", "image/webp"),
                           entry("/p.jpg", b"x", "image/jpeg"), entry("/poster.jpg", b"x", "image/jpeg"),
                           entry("/bg.png", b"x", "image/png"), entry("/f.woff2", b"x", "font/woff2"),
                           entry("/gammal.png", b"", "text/html", status=404)]
                stats = webarchive.write_snapshot(con, "t/a.warc.gz", entries, NOW)
                snap = snapshot(con, "t/a.warc.gz", stats, BASE + "/artikel")
                res = verify.analyze(snap)
            finally:
                webarchive.WARC_DIR, verify.cfg.WARC_DIR = old
        missing = {(m[1], m[2].replace(BASE, "")): m[3] for m in res["missing"]}
        self.assertIn(("js", "/saknas.js"), missing)
        self.assertIn("fångades inte", missing[("js", "/saknas.js")])
        self.assertIn("BLOCK_HOSTS", missing[("css", "https://ads.doubleclick.net/t.css")])
        self.assertIn(("css", "/imp.css"), missing)                      # @import i CSS-filen
        self.assertTrue(missing[("media", "/v.mp4")].startswith("media"))
        # alternativen i srcset/picture räcker: finns i2.png / p2.webp / p.jpg är bilderna inte saknade
        self.assertNotIn(("bild", "/i.png"), missing)
        self.assertNotIn(("bild", "/p1.webp"), missing)
        self.assertEqual(res["iframes_missing"], ["https://embed.annan.se/x"])
        self.assertEqual(res["errors"], [(404, BASE + "/gammal.png")])
        self.assertEqual(res["counts"]["css"], 1)
        text = verify.report(res)
        self.assertIn("SAKNAS (3)", text)
        self.assertIn("saknade CSS/JS-filer", text)
        self.assertIn("BLOCK_HOSTS = []", text)
        self.assertNotIn("ALLT som", text)

    def test_picture_and_icons_are_alternatives(self):
        """<picture> och favicons: webbläsaren hämtar bara en av flera, så de övriga är inte 'saknade'."""
        con = memdb()
        old = webarchive.WARC_DIR, verify.cfg.WARC_DIR
        html = (b'<html><head><link rel="icon" href="/icon-16.png"><link rel="icon" href="/icon-32.png">'
                b'<link rel="apple-touch-icon" href="/apple.png"></head><body>'
                b'<picture><source media="(min-width: 600px)" srcset="/w/992.jpg"><source srcset="/s/600.jpg">'
                b'<img src="/w/992.jpg?format=auto"></picture>'
                b'<picture><source srcset="/a.jpg"><source srcset="/b.jpg"><img src="/c.jpg"></picture>'
                b'<img src="/enkel.png"></body></html>')
        with tempfile.TemporaryDirectory() as d:
            webarchive.WARC_DIR = verify.cfg.WARC_DIR = Path(d)
            try:
                entries = [entry("/a", html, "text/html"), entry("/icon-32.png", b"x", "image/png"),
                           entry("/w/992.jpg?format=auto", b"x", "image/jpeg")]
                stats = webarchive.write_snapshot(con, "t/a.warc.gz", entries, NOW)
                res = verify.analyze(snapshot(con, "t/a.warc.gz", stats, BASE + "/a"))
            finally:
                webarchive.WARC_DIR, verify.cfg.WARC_DIR = old
        missing = [m[2].replace(BASE, "") for m in res["missing"]]
        # favicon: en av två räcker; apple-touch-icon kontrolleras inte; första <picture> är uppfylld av <img>.
        # Andra <picture> har inget av alternativen i arkivet och enkel.png saknas: de två riktiga luckorna.
        self.assertEqual(sorted(missing), ["/a.jpg", "/enkel.png"])
        self.assertEqual(res["checked"], 4)

    def test_noscript_fontface_alternatives_imageset_and_lazy(self):
        con = memdb()
        old = webarchive.WARC_DIR, verify.cfg.WARC_DIR
        html = (b'<html><head><link rel="stylesheet" href="/s.css"></head><body>'
                b'<noscript><img src="/ns1.png"><picture><source srcset="/ns2.png"><img src="/ns3.png"></picture></noscript>'
                b'<img src="/lazy.png" loading="lazy"><img data-src="/ds.png" src="/ph.gif">'
                b'<img src="/fin.png"></body></html>')
        css = (b'@font-face{font-family:A;src:url(/a.woff2) format("woff2"),url(/a.woff) format("woff")}'
               b'@font-face{font-family:B;src:url(/b.woff2) format("woff2"),url(/b.woff) format("woff")}'
               b'.x{background:image-set(url(/x1.png) 1x, url("/x2.png") 2x)} .y{background:-webkit-image-set("/y1.png" 1x,"/y2.png" 2x)}'
               b'@import url(/imp.css); .z{background:url(/z.png)}')
        with tempfile.TemporaryDirectory() as d:
            webarchive.WARC_DIR = verify.cfg.WARC_DIR = Path(d)
            try:
                entries = [entry("/a", html, "text/html"), entry("/s.css", css, "text/css"),
                           entry("/a.woff2", b"x", "font/woff2"), entry("/x2.png", b"x", "image/png"),
                           entry("/y1.png", b"x", "image/png"), entry("/imp.css", b"", "text/css"),
                           entry("/ph.gif", b"x", "image/gif"), entry("/fin.png", b"x", "image/png")]
                stats = webarchive.write_snapshot(con, "t/a.warc.gz", entries, NOW)
                res = verify.analyze(snapshot(con, "t/a.warc.gz", stats, BASE + "/a"))
            finally:
                webarchive.WARC_DIR, verify.cfg.WARC_DIR = old
        missing = {m[2].replace(BASE, ""): m for m in res["missing"]}
        # <noscript> ignoreras, typsnittet B saknas helt (både woff2 och woff), A är uppfyllt av woff2,
        # image-set-grupperna är uppfyllda av en bild vardera, lazy-bilden saknas och är märkt som lazy
        self.assertEqual(sorted(missing), ["/lazy.png", "/z.png"])
        self.assertEqual(res["unused_fonts"], [BASE + "/b.woff2"])      # deklarerat men oanvänt: ingen lucka
        self.assertTrue(missing["/lazy.png"][4].get("lazy"))
        self.assertFalse(missing["/z.png"][4].get("lazy"))
        self.assertEqual(res["noscript_skipped"], 3)
        text = verify.report(res)
        self.assertIn("lazy-loadad", text)
        self.assertIn("<noscript>", text)
        self.assertIn("varav 1 lazy-loadade", text)

    def test_font_skipped_on_purpose_is_a_real_gap(self):
        import json
        con = memdb()
        old = webarchive.WARC_DIR, verify.cfg.WARC_DIR
        html = b'<html><head><link rel="stylesheet" href="/s.css"><link rel="preload" as="font" href="/pre.woff2"></head></html>'
        css = b"@font-face{src:url(/stor.woff2)} @font-face{src:url(/oanvand.woff2)}"
        with tempfile.TemporaryDirectory() as d:
            webarchive.WARC_DIR = verify.cfg.WARC_DIR = Path(d)
            try:
                stats = webarchive.write_snapshot(con, "t/a.warc.gz", [entry("/a", html, "text/html"), entry("/s.css", css, "text/css")], NOW)
                snapshot(con, "t/a.warc.gz", stats, BASE + "/a")
                con.execute("UPDATE snapshots SET notes=?", (json.dumps({"counts": {"för_stor": 1}, "urls": [[BASE + "/stor.woff2", "för_stor"]]}),))
                res = verify.analyze(con.execute("SELECT * FROM snapshots").fetchone())
            finally:
                webarchive.WARC_DIR, verify.cfg.WARC_DIR = old
        gaps = {m[2].replace(BASE, ""): m[3] for m in res["missing"]}
        self.assertIn("MAX_RESOURCE_BYTES", gaps["/stor.woff2"])           # hoppades över med flit: riktig lucka
        self.assertIn("/pre.woff2", gaps)                                  # <link rel=preload as=font> i HTML: förväntas laddas
        self.assertEqual(res["unused_fonts"], [BASE + "/oanvand.woff2"])
        self.assertIn("deklarerar men sidan inte använde", verify.report(res))

    def test_article_text_is_checked(self):
        import json
        con = memdb()
        text = ("Det här är en ganska lång brödtext om en artikel i Värmland. Den innehåller flera meningar, "
                "med åäö och citattecken, så att jämförelsen måste tåla både taggar och specialtecken. " * 2)
        con.execute("INSERT INTO articles(id,source,url,status,title,body) VALUES (1,'t','u','parsed',?,?)",
                    ("Höstrusket kommer tillbaka", text))
        old = webarchive.WARC_DIR, verify.cfg.WARC_DIR
        cases = {
            # texten ligger i HTML:en, men uppdelad av taggar och entiteter
            "html": ("<html><title>Höstrusket kommer tillbaka</title><body><p>" + text.replace("artikel", "<b>artikel</b>").replace("åäö", "&aring;&auml;&ouml;")
                     + "</p></body></html>", []),
            # texten finns bara i ett JSON-svar med \\u-escapes (klientritad sida)
            "json": ("<html><title>Höstrusket kommer tillbaka</title><body><div id=app></div></body></html>",
                     [entry("/api/a.json", json.dumps({"text": text}).encode(), "application/json")]),
            # texten finns ingenstans (betalvägg)
            "nej": ("<html><title>Höstrusket kommer tillbaka</title><body><p>Logga in för att läsa.</p></body></html>", []),
        }
        got, results = {}, {}
        with tempfile.TemporaryDirectory() as d:
            webarchive.WARC_DIR = verify.cfg.WARC_DIR = Path(d)
            try:
                for name, (html, extra) in cases.items():
                    rel = f"t/{name}.warc.gz"
                    stats = webarchive.write_snapshot(con, rel, [entry("/a", html.encode(), "text/html")] + extra, NOW)
                    snap = snapshot(con, rel, stats, BASE + "/a")
                    con.execute("UPDATE snapshots SET article_id=1 WHERE id=?", (snap["id"],))
                    snap = con.execute("SELECT * FROM snapshots WHERE id=?", (snap["id"],)).fetchone()
                    results[name] = verify.analyze(snap, con=con)
                    got[name] = results[name]["content"]
                    if name == "nej":
                        no_con = verify.analyze(snap)
            finally:
                webarchive.WARC_DIR, verify.cfg.WARC_DIR = old
        self.assertEqual((got["html"]["title"], got["html"]["text_in_html"], got["html"]["text_in_json"]), (True, True, False))
        self.assertEqual((got["json"]["text_in_html"], got["json"]["text_in_json"]), (False, True))
        self.assertEqual((got["nej"]["title"], got["nej"]["text_in_html"], got["nej"]["text_in_json"]), (True, False, False))
        self.assertEqual([m[0] for m in got["json"]["matches"]], ["json"])
        self.assertEqual(got["nej"]["matches"], [])
        self.assertIsNone(got["nej"]["raw_has"])                      # ingen rå-HTML kopplad till raden
        self.assertIn("DETALJER", verify.report(results["nej"], text=True))
        self.assertIn("rå-HTML finns inte kvar", verify.report(results["nej"], text=True))
        self.assertIn("NEJ. Det kan bero på betalvägg", verify.report(results["nej"]))
        self.assertIn("ja, men i ett JSON-svar", verify.report(results["json"]))
        self.assertIn("(i HTML:en)", verify.report(results["html"]))
        self.assertIsNone(no_con["content"])                          # utan databasanslutning hoppas kontrollen över

    def test_text_details_compare_with_raw_html(self):
        import gzip as gz
        con = memdb()
        text = ("Ordentlig brödtext som finns i den rå-HTML som sparades vid vanlig hämtning, men inte i webbläsarens "
                "version av sidan, till exempel bakom en betalvägg. " * 3)
        old = webarchive.WARC_DIR, verify.cfg.WARC_DIR, verify.config.RAW_DIR
        with tempfile.TemporaryDirectory() as d:
            webarchive.WARC_DIR = verify.cfg.WARC_DIR = Path(d) / "warc"
            verify.config.RAW_DIR = Path(d) / "raw"
            (Path(d) / "raw" / "t").mkdir(parents=True)
            with gz.open(Path(d) / "raw" / "t" / "x.html.gz", "wt", encoding="utf-8") as fh:
                fh.write(f"<html><body><p>{text}</p></body></html>")
            con.execute("INSERT INTO articles(id,source,url,status,title,body,raw_path) VALUES (1,'t','u','parsed','Rubrik',?,?)",
                        (text, "t/x.html.gz"))
            try:
                stats = webarchive.write_snapshot(con, "t/a.warc.gz", [entry("/a", b"<html><title>Rubrik</title><body>Logga in</body></html>", "text/html")], NOW)
                snap = snapshot(con, "t/a.warc.gz", stats, BASE + "/a")
                con.execute("UPDATE snapshots SET article_id=1, notes=?", (__import__("json").dumps(
                    {"counts": {"ej_get": 2}, "urls": [[BASE + "/graphql", "ej_get"], [BASE + "/stats", "ej_get"]]}),))
                res = verify.analyze(con.execute("SELECT * FROM snapshots").fetchone(), con=con)
            finally:
                webarchive.WARC_DIR, verify.cfg.WARC_DIR, verify.config.RAW_DIR = old
        c = res["content"]
        self.assertTrue(c["raw_has"])
        self.assertFalse(c["text_in_html"])
        out = verify.report(res, text=True)
        self.assertIn("vanlig hämtning (rå-HTML", out)
        self.assertIn("innehåller texten: ja", out)
        self.assertIn("anrop som inte sparas (POST m.m.): 2 st:", out)
        self.assertIn(BASE + "/graphql", out)
        self.assertIn("servern gav texten till vanlig hämtning men inte till webbläsaren", out)

    def test_listing_option(self):
        con = memdb()
        old = webarchive.WARC_DIR, verify.cfg.WARC_DIR
        with tempfile.TemporaryDirectory() as d:
            webarchive.WARC_DIR = verify.cfg.WARC_DIR = Path(d)
            try:
                stats = webarchive.write_snapshot(con, "t/a.warc.gz", [entry("/a", b"<html></html>", "text/html"),
                                                                       entry("/x.css", b"a{}", "text/css")], NOW)
                res = verify.analyze(snapshot(con, "t/a.warc.gz", stats, BASE + "/a"))
            finally:
                webarchive.WARC_DIR, verify.cfg.WARC_DIR = old
        self.assertNotIn("ALLA RESURSER", verify.report(res))
        text = verify.report(res, listing=True)
        self.assertIn("ALLA RESURSER I FÅNGSTEN", text)
        self.assertIn("css", text)
        self.assertIn(BASE + "/x.css", text)

    def test_complete_page_and_revisit_css(self):
        con = memdb()
        old = webarchive.WARC_DIR, verify.cfg.WARC_DIR
        html = b'<html><head><link rel="stylesheet" href="/s.css"></head><body><img src="/bg.png"></body></html>'
        css = b"body{background:url(/bg.png)}"
        with tempfile.TemporaryDirectory() as d:
            webarchive.WARC_DIR = verify.cfg.WARC_DIR = Path(d)
            try:
                common = [entry("/s.css", css, "text/css"), entry("/bg.png", b"x", "image/png")]
                webarchive.write_snapshot(con, "t/a.warc.gz", [entry("/ett", html, "text/html")] + common, NOW)
                # andra fångsten: CSS och bild är nu dubblettposter, HTML:en är ny
                stats = webarchive.write_snapshot(con, "t/a.warc.gz", [entry("/tva", html + b" ", "text/html")] + common, NOW)
                self.assertEqual(stats["n_revisits"], 2)
                snap = snapshot(con, "t/a.warc.gz", stats, BASE + "/tva")
                res = verify.analyze(snap)
            finally:
                webarchive.WARC_DIR, verify.cfg.WARC_DIR = old
        self.assertEqual(res["missing"], [])
        self.assertEqual(res["checked"], 3)          # <link>, <img> och url() i CSS:en (läst via dubblettposten)
        self.assertIn("ALLT som HTML:en och CSS-filerna pekar på finns i arkivet", verify.report(res))

    def test_exact_reasons_from_notes(self):
        import json
        con = memdb()
        old = webarchive.WARC_DIR, verify.cfg.WARC_DIR
        html = b'<html><body><script src="/stor.js"></script><script src="/borta.js"></script></body></html>'
        with tempfile.TemporaryDirectory() as d:
            webarchive.WARC_DIR = verify.cfg.WARC_DIR = Path(d)
            try:
                stats = webarchive.write_snapshot(con, "t/a.warc.gz", [entry("/a", html, "text/html")], NOW)
                snap = snapshot(con, "t/a.warc.gz", stats, BASE + "/a")
                con.execute("UPDATE snapshots SET notes=?", (json.dumps(
                    {"counts": {"för_stor": 1}, "urls": [[BASE + "/stor.js", "för_stor"]]}),))
                res = verify.analyze(con.execute("SELECT * FROM snapshots").fetchone())
            finally:
                webarchive.WARC_DIR, verify.cfg.WARC_DIR = old
        why = {m[2].replace(BASE, ""): m[3] for m in res["missing"]}
        self.assertIn("MAX_RESOURCE_BYTES", why["/stor.js"])            # exakt orsak, inte en gissning
        self.assertIn("fångades inte", why["/borta.js"])
        self.assertIn("höj MAX_RESOURCE_BYTES", verify.report(res))

    def test_find_snapshot_and_helpers(self):
        con = memdb()
        con.execute("INSERT INTO snapshots(source,kind,article_id,url,taken_at,status,warc_path) VALUES "
                    "('svt','front',NULL,'https://www.svt.se/nyheter/','2026-10-10T05:00:00Z','ok','x')")
        con.execute("INSERT INTO snapshots(source,kind,article_id,url,taken_at,status,warc_path) VALUES "
                    "('svt','article',7,'https://www.svt.se/nyheter/a/b','2026-10-10T05:05:00Z','ok','x')")
        self.assertEqual(verify.find_snapshot(con, "7")["article_id"], 7)
        self.assertEqual(verify.find_snapshot(con, "svt")["kind"], "front")
        self.assertEqual(verify.find_snapshot(con, "https://www.svt.se/nyheter/a/b")["article_id"], 7)
        self.assertIsNone(verify.find_snapshot(con, "999"))
        self.assertEqual(verify.key("http://WWW.X.se/a?b=1#frag"), verify.key("https://www.x.se/a?b=1"))
        self.assertEqual(verify.category("font/woff2"), "typsnitt")
        self.assertEqual(verify.category("application/octet-stream", "https://x/a.woff"), "typsnitt")


if __name__ == "__main__":
    unittest.main()
