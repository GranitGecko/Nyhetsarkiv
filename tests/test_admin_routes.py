"""Adminflödet via webbplatsens rutter: inloggning, spärr, CSRF, bekräftelse, radering, utloggning."""
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import admin
import config
import db
import webstub  # noqa: E402

import api  # noqa: E402

PASSWORD = "ett-långt-lösenord"


class AdminRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old = (config.DATA, config.RAW_DIR, db.DATA, db.DB_PATH, db._initialized, admin.ITERATIONS, api._limiter)
        config.DATA = db.DATA = root
        config.RAW_DIR = root / "raw"
        db.DB_PATH = root / "news.db"
        db._initialized = False
        admin.ITERATIONS = 1000
        api._limiter = admin.RateLimiter()
        con = db.connect()
        for i in range(10):
            con.execute("INSERT INTO articles(source,url,status,title,body,published,article_type,category) "
                        "VALUES ('svt',?, 'parsed',?,'Text','2026-10-01T10:00:00Z','nyhet','Inrikes')",
                        (f"https://www.svt.se/nyheter/inrikes/a{i}", f"Rubrik {i}"))
        con.commit()
        con.close()
        self.client = webstub.Client(api)

    def tearDown(self):
        (config.DATA, config.RAW_DIR, db.DATA, db.DB_PATH, db._initialized, admin.ITERATIONS, api._limiter) = self.old
        self.tmp.cleanup()

    # ---- hjälpare ----
    def enable(self):
        admin.set_password(PASSWORD)

    def login(self, password=PASSWORD):
        return self.client.post("/admin/login", {"losenord": password})

    def csrf(self):
        page = self.client.get("/admin")
        self.assertEqual(page.status, 200, "förväntade en inloggad adminsida")
        return re.search(r'name="csrf" value="([0-9a-f]+)"', page.text).group(1)

    def statuses(self):
        con = db.connect()
        try:
            return {r["id"]: r["status"] for r in con.execute("SELECT id, status FROM articles")}
        finally:
            con.close()

    def log_actions(self):
        con = db.connect()
        try:
            return [r["action"] for r in con.execute("SELECT action FROM admin_log ORDER BY id")]
        finally:
            con.close()

    # ---- avstängt som standard ----
    def test_admin_is_off_until_a_password_is_set(self):
        r = self.client.get("/admin/login")
        self.assertEqual(r.status, 200)
        self.assertIn("Administrationen är avstängd", r.text)
        self.assertIn("admin-password", r.text)
        self.assertEqual(self.client.post("/admin/login", {"losenord": PASSWORD}).status, 403)
        self.assertEqual((self.client.get("/admin").status, self.client.get("/admin").location), (303, "/admin/login"))
        r = self.client.post("/admin/radera", {"id": "1", "bekrafta": "ja", "csrf": "x"})
        self.assertEqual(r.status, 303)
        self.assertEqual(set(self.statuses().values()), {"parsed"})

    # ---- inloggning ----
    def test_wrong_password_and_correct_password(self):
        self.enable()
        r = self.login("fel-lösenord-här")
        self.assertEqual(r.status, 401)
        self.assertIn("Fel lösenord", r.text)
        self.assertIsNone(self.client.get_cookie(api.COOKIE))
        self.assertEqual(self.client.get("/admin").status, 303)
        r = self.login()
        self.assertEqual((r.status, r.location), (303, "/admin"))
        self.assertTrue(self.client.get_cookie(api.COOKIE))
        self.assertIn("Radera artiklar", self.client.get("/admin").text)
        self.assertEqual(self.log_actions(), ["misslyckad inloggning", "inloggning"])
        con = db.connect()
        detail = " ".join(f"{r['detail']}" for r in con.execute("SELECT detail FROM admin_log"))
        con.close()
        self.assertNotIn(PASSWORD, detail)                              # lösenordet loggas aldrig

    def test_rate_limit_after_five_failures(self):
        self.enable()
        for _ in range(5):
            self.assertEqual(self.login("fel-lösenord-här").status, 401)
        r = self.login()                                                 # rätt lösenord men spärrad
        self.assertEqual(r.status, 429)
        self.assertIn("För många försök", r.text)
        self.assertIsNone(self.client.get_cookie(api.COOKIE))

    def test_forged_and_stale_cookies_are_rejected(self):
        self.enable()
        self.client.set_cookie(api.COOKIE, "bara.skräp")
        self.assertEqual(self.client.get("/admin").status, 303)
        self.login()
        old_cookie = self.client.get_cookie(api.COOKIE)
        admin.set_password("ett-helt-nytt-lösenord")                    # nytt lösenord loggar ut alla
        self.client.set_cookie(api.COOKIE, old_cookie)
        self.assertEqual(self.client.get("/admin").status, 303)
        r = self.client.post("/admin/radera", {"id": "1", "bekrafta": "ja", "csrf": "x"})
        self.assertEqual(r.status, 303)
        self.assertEqual(set(self.statuses().values()), {"parsed"})

    # ---- vad besökare respektive admin ser ----
    def test_admin_controls_only_visible_when_logged_in(self):
        self.enable()
        for path in ("/artiklar", "/artikel/1", "/"):
            html = self.client.get(path).text
            self.assertNotIn("/admin/radera", html, path)
            self.assertNotIn('name="csrf"', html, path)
        self.login()
        self.assertIn('name="id" value="1"', self.client.get("/artiklar").text)
        self.assertIn("Radera markerade", self.client.get("/artiklar").text)
        self.assertIn("Radera den här artikeln", self.client.get("/artikel/1").text)
        self.assertIn('href="/admin"', self.client.get("/").text)

    # ---- radering ----
    def test_delete_flow(self):
        self.enable()
        self.login()
        token = self.csrf()
        r = self.client.post("/admin/radera", {"csrf": token, "id": ["1", "2"], "bekrafta": "ja", "tillbaka": "/artiklar?sida=1"})
        self.assertEqual(r.status, 303)
        self.assertTrue(r.location.startswith("/artiklar?sida=1&meddelande="), r.location)
        self.assertIn("Raderade", r.location.replace("%20", " "))
        st = self.statuses()
        self.assertEqual((st[1], st[2], st[3]), ("deleted", "deleted", "parsed"))
        self.assertEqual(self.client.get("/artikel/1").status, 404)         # raderat finns inte ens för admin
        self.assertEqual(self.client.get("/api/articles/1").status, 404)
        self.assertEqual(self.client.get("/api/articles", limit=50).data["total"], 8)
        self.assertIn("radera", self.log_actions())
        page = self.client.get("/admin")
        self.assertIn("a0", page.text)                                       # syns under 'Senast raderade'
        self.assertIn("deleted", page.text)

    def test_confirmation_is_required(self):
        self.enable()
        self.login()
        token = self.csrf()
        r = self.client.post("/admin/radera", {"csrf": token, "id": "1", "tillbaka": "/artiklar"})
        self.assertEqual(r.status, 303)
        self.assertIn("bekr", r.location)
        self.assertEqual(self.statuses()[1], "parsed")
        r = self.client.post("/admin/radera", {"csrf": token, "bekrafta": "ja", "tillbaka": "/artiklar"})   # inga id
        self.assertEqual(self.statuses()[1], "parsed")
        self.assertIn("meddelande", r.location)

    def test_csrf_is_required_and_bound_to_the_session(self):
        self.enable()
        self.login()
        self.csrf()
        for data in ({"id": "1", "bekrafta": "ja"}, {"id": "1", "bekrafta": "ja", "csrf": "fel"},
                     {"id": "1", "bekrafta": "ja", "csrf": ""}):
            self.assertEqual(self.client.post("/admin/radera", data).status, 403)
        other = webstub.Client(api, ip="other")                              # en annan inloggning har en annan token
        other.post("/admin/login", {"losenord": PASSWORD})
        page = other.get("/admin")
        other_token = re.search(r'name="csrf" value="([0-9a-f]+)"', page.text).group(1)
        self.assertEqual(self.client.post("/admin/radera", {"id": "1", "bekrafta": "ja", "csrf": other_token}).status, 403)
        self.assertEqual(set(self.statuses().values()), {"parsed"})

    def test_return_url_cannot_redirect_off_site(self):
        self.enable()
        self.login()
        token = self.csrf()
        for bad in ("https://evil.example/", "//evil.example", "javascript:alert(1)"):
            r = self.client.post("/admin/radera", {"csrf": token, "id": "3", "bekrafta": "ja", "tillbaka": bad})
            self.assertTrue(r.location.startswith("/artiklar?"), (bad, r.location))

    def test_ids_from_text_field_and_garbage_input(self):
        self.enable()
        self.login()
        token = self.csrf()
        r = self.client.post("/admin/radera", {"csrf": token, "ids": "4, 5  abc 6;7 -1", "bekrafta": "ja", "tillbaka": "/admin"})
        st = self.statuses()
        self.assertEqual([st[4], st[5]], ["deleted", "deleted"])
        self.assertEqual([st[6], st[7]], ["parsed", "parsed"])               # '6;7' och '-1' är inte giltiga id
        self.assertEqual(self.client.post("/admin/radera", {"csrf": token, "id": ["99999"], "bekrafta": "ja", "tillbaka": "/admin"}).status, 303)

    # ---- utloggning och utdata ----
    def test_logout(self):
        self.enable()
        self.login()
        token = self.csrf()
        self.client.post("/admin/logga-ut", {"csrf": "fel"})                 # fel token loggar inte ut
        self.assertEqual(self.client.get("/admin").status, 200)
        r = self.client.post("/admin/logga-ut", {"csrf": token})
        self.assertEqual((r.status, r.location), (303, "/"))
        if not webstub.REAL:
            self.assertIsNone(self.client.get_cookie(api.COOKIE))
        else:
            self.client.clear_cookies()
        self.assertEqual(self.client.get("/admin").status, 303)

    def test_message_parameter_is_admin_only_and_escaped(self):
        evil = "<script>alert(1)</script>"
        html = self.client.get("/artiklar", meddelande=evil).text                 # besökare: texten visas inte alls
        self.assertNotIn("alert(1)", html)
        self.enable()
        self.login()
        html = self.client.get("/artiklar", meddelande=evil).text                 # admin: visas, men escapat
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()
