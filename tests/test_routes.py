"""Kör webbplatsens och API:ts rutter (se webstub.py för hur det görs med och utan riktig FastAPI)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
import db
import webstub  # noqa: E402  (installerar ersättning vid behov, före import av api)

import api  # noqa: E402


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old = (config.DATA, db.DATA, db.DB_PATH, db._initialized)
        config.DATA = db.DATA = root
        db.DB_PATH = root / "news.db"
        db._initialized = False
        con = db.connect()
        for i in range(60):
            con.execute("INSERT INTO articles(source,url,status,title,body,published,article_type,category,author) "
                        "VALUES ('svt',?, 'parsed',?,?,?, 'nyhet','Inrikes','Anna')",
                        (f"https://www.svt.se/nyheter/inrikes/a{i}", f"Rubrik {i}", f"Text {i}\nStycke två.",
                         f"2026-10-{1 + i % 28:02d}T10:00:00+02:00"))
        con.commit()
        con.close()
        self.client = webstub.Client(api)

    def tearDown(self):
        config.DATA, db.DATA, db.DB_PATH, db._initialized = self.old
        self.tmp.cleanup()

    def test_start_page(self):
        r = self.client.get("/")
        self.assertEqual(r.status, 200)
        self.assertIn("Välkommen till Nyhetsarkivet", r.text)
        self.assertIn("Nordfront", r.text)
        self.assertIn(">60<", r.text)
        self.assertNotIn('href="/admin"', r.text)                      # ingen adminlänk för besökare

    def test_articles_page_paging_and_page_size(self):
        self.assertIn("Visar 1-50 av 60", self.client.get("/artiklar").text)
        r = self.client.get("/artiklar", sida=2)
        self.assertIn("Visar 51-60 av 60", r.text)
        self.assertIn("[2]", r.text)
        r = self.client.get("/artiklar", per_sida=25, sida=3)
        self.assertIn("Visar 51-60 av 60", r.text)
        self.assertIn('<option value="25" selected>', r.text)
        r = self.client.get("/artiklar", sida=999)
        self.assertEqual(r.status, 200)
        self.assertIn("(sida 2 av 2)", r.text)
        self.assertIn("av 1 träffar", self.client.get("/artiklar", q="Rubrik 7").text)
        self.assertNotIn('type="checkbox"', self.client.get("/artiklar").text)       # inga raderingsrutor för besökare

    def test_sources_pages(self):
        self.assertIn("Public service", self.client.get("/kallor").text)
        r = self.client.get("/kalla/svt")
        self.assertEqual(r.status, 200)
        self.assertIn("Karin Ekman", r.text)
        self.assertIn("60 artiklar", r.text)
        self.assertIn("ännu inte tillagd i arkivet", self.client.get("/kalla/nordfront").text)
        self.assertEqual(self.client.get("/kalla/finns-inte").status, 404)

    def test_article_and_snapshot_pages(self):
        r = self.client.get("/artikel/1")
        self.assertEqual(r.status, 200)
        self.assertIn("Rubrik 0", r.text)
        self.assertIn('href="/kalla/svt"', r.text)
        self.assertEqual(self.client.get("/artikel/99999").status, 404)
        self.assertEqual(self.client.get("/fangster").status, 200)

    def test_api_endpoints(self):
        r = self.client.get("/api/articles", limit=5, offset=10)
        self.assertEqual((r.data["total"], len(r.data["items"]), r.data["offset"]), (60, 5, 10))
        names = {s["key"]: s for s in self.client.get("/api/sources").data}
        self.assertTrue(names["svt"]["in_archive"] and names["svt"]["articles"] == 60)
        self.assertFalse(names["nordfront"]["in_archive"])
        self.assertEqual(self.client.get("/api/articles/1").data["title"], "Rubrik 0")
        self.assertEqual(self.client.get("/api/stats").status, 200)


if __name__ == "__main__":
    unittest.main()
