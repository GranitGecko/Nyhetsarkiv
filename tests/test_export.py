import json
import sqlite3
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import export_site
import profiles as prof
from sites import SITES

SECRET_BODY = "HEMLIG_BRODTEXT_SKA_ALDRIG_PUBLICERAS"
SECRET_INGRESS = "HEMLIG_INGRESS_SKA_ALDRIG_PUBLICERAS"


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.forms, self.scripts, self.meta = [], 0, 0, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "form":
            self.forms += 1
        elif tag == "script":
            self.scripts += 1
        elif tag == "meta":
            self.meta.append(a)


def memdb(n_svt=250):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(db.SCHEMA)
    for i in range(n_svt):
        title = "Rubrik <b>fet</b> & mer" if i == 0 else f"Rubrik {i}"
        con.execute("INSERT INTO articles(source,url,status,title,ingress,body,published,article_type,category,author) "
                    "VALUES ('svt',?, 'parsed',?,?,?,?, 'nyhet','Inrikes','Anna')",
                    (f"https://www.svt.se/nyheter/inrikes/a{i}", title, SECRET_INGRESS, SECRET_BODY,
                     f"2026-10-{1 + i % 28:02d}T10:00:00+02:00"))
    con.execute("INSERT INTO articles(source,url,status,title,published) VALUES ('dn','javascript:alert(1)','parsed','Dålig adress','2026-09-01T00:00:00Z')")
    con.commit()
    return con


def all_files(out):
    return sorted(p for p in Path(out).rglob("*") if p.is_file())


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "docs"
        self.con = memdb()
        self.data = prof.load()

    def tearDown(self):
        self.tmp.cleanup()

    def export(self, **kw):
        return export_site.export_site(self.con, SITES, self.data, self.out, **kw)

    def html(self, rel):
        return (self.out / rel).read_text(encoding="utf-8")

    def check_links(self):
        """Alla lokala länkar ska peka på filer som finns, och inga länkar får vara rotabsoluta."""
        broken = []
        for path in all_files(self.out):
            if path.suffix != ".html":
                continue
            p = _Links()
            p.feed(path.read_text(encoding="utf-8"))
            self.assertEqual((p.forms, p.scripts), (0, 0), f"{path}: formulär eller skript i en statisk sida")
            for href in p.links:
                parts = urlsplit(href)
                if parts.scheme in ("http", "https", "mailto") or href.startswith("#"):
                    self.assertFalse(parts.scheme == "javascript", href)
                    continue
                self.assertFalse(href.startswith("/"), f"{path.name}: rotabsolut länk {href}")
                if not (path.parent / parts.path).resolve().is_file():
                    broken.append((str(path.relative_to(self.out)), href))
        self.assertEqual(broken, [])

    def test_default_export_has_profiles_but_no_articles(self):
        res = self.export()
        for rel in ("index.html", "kallor.html", "kalla/svt.html", "kalla/dn.html", "kalla/nordfront.html", ".nojekyll", export_site.MARKER):
            self.assertTrue((self.out / rel).is_file(), rel)
        self.assertFalse((self.out / "artiklar").exists())
        self.assertGreater(res["pages"], 25)
        self.assertIn("Karin Ekman", self.html("kalla/svt.html"))
        self.assertIn("Nyckelpersoner", self.html("kalla/svt.html"))
        self.assertIn("251", self.html("index.html"))                            # antal artiklar visas, men inte artiklarna
        self.assertNotIn("artiklar/alla", self.html("index.html"))
        self.assertNotIn("Rubrik 1", self.html("kalla/svt.html"))
        self.check_links()

    def test_article_text_is_never_exported(self):
        for with_articles in (False, True):
            self.export(with_articles=with_articles, per_page=50)
            for path in all_files(self.out):
                text = path.read_text(encoding="utf-8", errors="ignore")
                self.assertNotIn(SECRET_BODY, text, path.name)
                self.assertNotIn(SECRET_INGRESS, text, path.name)

    def test_with_articles_lists_titles_with_paging_and_links_to_originals(self):
        self.export(with_articles=True, per_page=100)
        self.assertTrue((self.out / "artiklar/alla/1.html").is_file())
        self.assertTrue((self.out / "artiklar/alla/3.html").is_file())            # 251 artiklar / 100 = 3 sidor
        self.assertFalse((self.out / "artiklar/alla/4.html").exists())
        self.assertTrue((self.out / "artiklar/svt/3.html").is_file())
        self.assertFalse((self.out / "artiklar/svt/4.html").exists())
        page2 = self.html("artiklar/alla/2.html")
        for label in ("« Första", "-5", "‹ Föregående", "Nästa ›", "+5", "Sista »"):
            self.assertIn(label, page2)
        self.assertIn('href="1.html"', page2)
        self.assertIn('href="3.html"', page2)
        self.assertIn('rel="nofollow noopener"', page2)
        self.assertIn("https://www.svt.se/nyheter/inrikes/", page2)               # länkar till originalet
        self.assertIn('<meta name="robots" content="noindex, nofollow">', page2)  # listorna ska inte indexeras av sökmotorer
        self.assertNotIn("noindex", self.html("index.html"))
        self.assertIn("artiklar/alla/1.html", self.html("index.html"))
        self.assertIn("../artiklar/svt/1.html", self.html("kalla/svt.html"))
        self.check_links()

    def test_titles_are_escaped_and_unsafe_urls_are_not_linked(self):
        self.export(with_articles=True, per_page=500)
        html = self.html("artiklar/alla/1.html")
        self.assertIn("Rubrik &lt;b&gt;fet&lt;/b&gt; &amp; mer", html)
        self.assertNotIn("<b>fet</b>", html)
        self.assertIn("Dålig adress", html)
        self.assertNotIn("javascript:", html)                                    # bara http(s)-adresser blir länkar

    def test_repo_link_only_when_given(self):
        self.export()
        self.assertNotIn("Källkod på GitHub", self.html("index.html"))
        self.export(repo_url="https://github.com/exempel/Nyhetsarkiv")
        self.assertIn('href="https://github.com/exempel/Nyhetsarkiv"', self.html("kalla/svt.html"))

    def test_refuses_foreign_directory_but_not_own_or_forced(self):
        self.out.mkdir()
        (self.out / "min-egen-fil.txt").write_text("viktigt", encoding="utf-8")
        with self.assertRaises(export_site.ExportError) as cm:
            self.export()
        self.assertIn("--force", str(cm.exception))
        self.assertFalse((self.out / "index.html").exists())
        self.export(force=True)                                                    # med --force går det, främmande filer lämnas
        self.assertEqual((self.out / "min-egen-fil.txt").read_text(encoding="utf-8"), "viktigt")
        self.export()                                                              # markeringsfilen finns nu: ny export tillåts

    def test_reexport_removes_stale_files_but_keeps_foreign_ones(self):
        self.export(with_articles=True, per_page=100)
        (self.out / "min-egen-fil.txt").write_text("viktigt", encoding="utf-8")
        self.export(with_articles=False)
        self.assertFalse((self.out / "artiklar").exists())                         # gamla listor borta
        self.assertTrue((self.out / "min-egen-fil.txt").exists())
        manifest = json.loads((self.out / export_site.MARKER).read_text(encoding="utf-8"))
        self.assertNotIn("min-egen-fil.txt", manifest)
        self.assertIn("index.html", manifest)

    def test_manifest_cannot_be_used_to_delete_outside_the_folder(self):
        outside = Path(self.tmp.name) / "utanfor.txt"
        outside.write_text("ska finnas kvar", encoding="utf-8")
        self.out.mkdir()
        (self.out / export_site.MARKER).write_text(json.dumps(["../utanfor.txt"]), encoding="utf-8")
        self.export()
        self.assertTrue(outside.exists())

    def test_slug_and_profile_only_sources(self):
        self.assertEqual(export_site.slug("Åsa/../X Y"), "sa-x-y")
        self.export()
        self.assertTrue((self.out / "kalla/nordfront.html").is_file())             # profil för källa som inte lagts till
        self.assertIn("ännu inte tillagd i arkivet", self.html("kalla/nordfront.html"))
        self.check_links()


if __name__ == "__main__":
    unittest.main()
