import gzip
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import admin
import config
import db


class AdminLogicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old = (config.DATA, config.RAW_DIR, db.DATA, db.DB_PATH, db._initialized)
        config.DATA = db.DATA = self.root / "data"
        config.RAW_DIR = config.DATA / "raw"
        db.DB_PATH = config.DATA / "news.db"
        db._initialized = False
        self.cons = []
        self.iterations = admin.ITERATIONS
        admin.ITERATIONS = 1000                     # snabbare lösenordshash i testerna (riktig körning använder 600 000)

    def connect(self):
        con = db.connect()
        self.cons.append(con)
        return con

    def tearDown(self):
        for con in self.cons:
            con.close()
        admin.ITERATIONS = self.iterations
        config.DATA, config.RAW_DIR, db.DATA, db.DB_PATH, db._initialized = self.old
        self.tmp.cleanup()

    # ---- lösenord och sessioner ----
    def test_disabled_by_default_and_password_rules(self):
        self.assertFalse(admin.admin_enabled())
        self.assertIsNone(admin.make_session())
        self.assertFalse(admin.check_password("något-lösenord"))
        with self.assertRaises(ValueError):
            admin.set_password("kort")
        admin.set_password("ett-långt-lösenord")
        self.assertTrue(admin.admin_enabled())
        self.assertTrue(admin.check_password("ett-långt-lösenord"))
        self.assertFalse(admin.check_password("ett-långt-lösenorD"))
        self.assertFalse(admin.check_password(""))
        saved = (config.DATA / "settings.json").read_text(encoding="utf-8")
        self.assertNotIn("ett-långt-lösenord", saved)                  # bara hash sparas
        self.assertEqual(json.loads(saved)["admin"]["iterations"], 1000)

    def test_settings_keep_other_keys(self):
        admin.save_settings({"user_agent": "Nyhetsarkivet/0.1 (+kontakt: jag@exempel.se)"})
        admin.set_password("ett-långt-lösenord")
        s = admin.load_settings()
        self.assertEqual(s["user_agent"], "Nyhetsarkivet/0.1 (+kontakt: jag@exempel.se)")
        admin.disable_admin()
        s = admin.load_settings()
        self.assertNotIn("admin", s)
        self.assertEqual(s["user_agent"], "Nyhetsarkivet/0.1 (+kontakt: jag@exempel.se)")
        self.assertFalse(admin.admin_enabled())

    def test_sessions_signature_expiry_and_revocation(self):
        admin.set_password("ett-långt-lösenord")
        tok = admin.make_session(now=1000)
        self.assertIsNotNone(admin.parse_session(tok, now=1000 + 60))
        self.assertIsNone(admin.parse_session(tok, now=1000 + admin.SESSION_SECONDS + 1))     # utgången
        payload, sig = tok.rsplit(".", 1)
        self.assertIsNone(admin.parse_session(payload + "." + "0" * len(sig), now=1001))      # fel signatur
        self.assertIsNone(admin.parse_session("skräp", now=1001))
        self.assertIsNone(admin.parse_session("", now=1001))
        forged = admin.make_session(now=1000).rsplit(".", 1)[0].replace("e", "f") + "." + sig
        self.assertIsNone(admin.parse_session(forged, now=1001))
        admin.set_password("ett-annat-lösenord")                                               # nytt lösenord: gamla sessioner dör
        self.assertIsNone(admin.parse_session(tok, now=1001))
        admin.disable_admin()
        self.assertIsNone(admin.parse_session(admin.make_session(), now=1001))

    def test_non_ascii_input_never_crashes(self):
        admin.set_password("ett-långt-lösenord")
        session = admin.parse_session(admin.make_session())
        for junk in ("bara.skräp", "åäö.åäö", "ä" * 50, "\u202e.\u202e"):
            self.assertIsNone(admin.parse_session(junk))
        self.assertFalse(admin.csrf_ok(session, "tokenåäö"))
        self.assertFalse(admin.check_password("fel-lösenord-åäö"))

    def test_csrf_tokens_are_bound_to_the_session(self):
        admin.set_password("ett-långt-lösenord")
        a, b = admin.parse_session(admin.make_session()), admin.parse_session(admin.make_session())
        self.assertTrue(admin.csrf_ok(a, admin.csrf_token(a)))
        self.assertFalse(admin.csrf_ok(a, admin.csrf_token(b)))
        self.assertFalse(admin.csrf_ok(a, ""))
        self.assertFalse(admin.csrf_ok(None, admin.csrf_token(a)))

    def test_rate_limiter_blocks_after_five_failures_and_recovers(self):
        rl = admin.RateLimiter(tries=5, block_seconds=60)
        for _ in range(4):
            rl.fail("1.2.3.4", now=100)
        self.assertTrue(rl.allowed("1.2.3.4", now=100))
        rl.fail("1.2.3.4", now=100)
        self.assertFalse(rl.allowed("1.2.3.4", now=130))
        self.assertTrue(rl.allowed("5.6.7.8", now=130))                 # andra adresser påverkas inte
        self.assertTrue(rl.allowed("1.2.3.4", now=161))
        rl.fail("9.9.9.9", now=0)
        rl.success("9.9.9.9")
        for _ in range(4):
            rl.fail("9.9.9.9", now=0)
        self.assertTrue(rl.allowed("9.9.9.9", now=0))                   # räknaren nollställdes vid lyckad inloggning

    def test_safe_return_only_allows_local_paths(self):
        for ok in ("/artiklar?sida=2", "/artikel/5", "/"):
            self.assertEqual(admin.safe_return(ok), ok)
        for bad in ("https://evil.example/", "//evil.example", "javascript:alert(1)", "/\\evil", "", None, "/a\nSet-Cookie: x"):
            self.assertEqual(admin.safe_return(bad), "/artiklar", bad)

    # ---- radering ----
    def make_db(self):
        con = self.connect()
        raw = config.RAW_DIR / "svt" / "ab"
        raw.mkdir(parents=True)
        for name in ("a.html.gz", "shared.html.gz"):
            with gzip.open(raw / name, "wt", encoding="utf-8") as fh:
                fh.write("<html>x</html>")
        rows = [("svt", "https://www.svt.se/nyheter/inrikes/ett", "svt/ab/a.html.gz"),
                ("svt", "https://www.svt.se/nyheter/inrikes/tva", "svt/ab/shared.html.gz"),
                ("svt", "https://www.svt.se/nyheter/inrikes/tre", "svt/ab/shared.html.gz"),
                ("dn", "https://www.dn.se/sverige/fyra/", None)]
        for src, url, raw_path in rows:
            con.execute("INSERT INTO articles(source,url,status,title,body,author,published,raw_path,category,discovered_at) "
                        "VALUES (?,?, 'parsed','Rubrik','Text','Anna','2026-10-01T10:00:00Z',?, 'Inrikes','2026-10-01T00:00:00Z')",
                        (src, url, raw_path))
        con.execute("INSERT INTO snapshots(source,kind,article_id,url,taken_at,status,warc_path) VALUES "
                    "('svt','article',1,'u','2026-10-10T00:00:00Z','ok','svt/2026-10-10.warc.gz')")
        con.commit()
        return con

    def test_delete_wipes_content_keeps_tombstone_and_reports_warc(self):
        con = self.make_db()
        res = admin.delete_articles(con, [1, 4, 99], ip="127.0.0.1")
        self.assertEqual(sorted(res["deleted"]), [1, 4])
        self.assertEqual(res["skipped"], [99])
        self.assertEqual(res["warc_files"], ["svt/2026-10-10.warc.gz"])
        r = con.execute("SELECT * FROM articles WHERE id=1").fetchone()
        self.assertEqual(r["status"], "deleted")
        self.assertIsNotNone(r["deleted_at"])
        for col in admin.WIPE:
            self.assertIsNone(r[col], col)
        self.assertEqual(r["url"], "https://www.svt.se/nyheter/inrikes/ett")        # adressen sparas
        self.assertEqual(r["source"], "svt")
        self.assertEqual(con.execute("SELECT status FROM snapshots WHERE article_id=1").fetchone()[0], "deleted")
        self.assertFalse((config.RAW_DIR / "svt/ab/a.html.gz").exists())
        self.assertEqual(res["raw_removed"], 1)
        log = con.execute("SELECT * FROM admin_log").fetchall()
        self.assertEqual((len(log), log[0]["action"], log[0]["ip"]), (1, "radera", "127.0.0.1"))
        self.assertIn("svt/2026-10-10.warc.gz", log[0]["detail"])

    def test_shared_raw_file_is_kept_until_last_reference_is_deleted(self):
        con = self.make_db()
        admin.delete_articles(con, [2])
        self.assertTrue((config.RAW_DIR / "svt/ab/shared.html.gz").exists())          # artikel 3 använder den fortfarande
        admin.delete_articles(con, [3])
        self.assertFalse((config.RAW_DIR / "svt/ab/shared.html.gz").exists())

    def test_delete_is_idempotent_and_tombstone_blocks_rediscovery(self):
        con = self.make_db()
        admin.delete_articles(con, [1])
        again = admin.delete_articles(con, [1])
        self.assertEqual((again["deleted"], again["skipped"]), ([], [1]))
        self.assertEqual(con.execute("SELECT COUNT(*) FROM admin_log").fetchone()[0], 1)    # ingen ny loggrad utan ändring
        con.execute("INSERT OR IGNORE INTO articles(source,url,status) VALUES ('svt','https://www.svt.se/nyheter/inrikes/ett','discovered')")
        self.assertEqual(con.execute("SELECT status FROM articles WHERE id=1").fetchone()[0], "deleted")   # discover lägger inte tillbaka den
        self.assertEqual(con.execute("SELECT COUNT(*) FROM articles WHERE url='https://www.svt.se/nyheter/inrikes/ett'").fetchone()[0], 1)

    def test_deleted_articles_disappear_from_search_and_stats(self):
        import queries
        import webui
        con = self.make_db()
        self.assertEqual(queries.search(con)[0], 4)
        admin.delete_articles(con, [1, 2])
        self.assertEqual(queries.search(con)[0], 2)
        self.assertEqual(webui.source_stats(con)["svt"]["articles"], 1)
        self.assertEqual(queries.snapshots(con, 1), [])

    def test_empty_input_and_old_database_migration(self):
        con = self.connect()
        self.assertEqual(admin.delete_articles(con, []), {"deleted": [], "skipped": [], "raw_removed": 0, "warc_files": []})
        # äldre databas utan deleted_at och admin_log får dem vid nästa anslutning
        db_path = config.DATA / "gammal.db"
        old = sqlite3.connect(db_path)
        old.executescript(db.SCHEMA)                       # som en databas från före admin-funktionen:
        old.execute("ALTER TABLE articles DROP COLUMN deleted_at")
        old.execute("DROP TABLE admin_log")
        old.commit()
        old.close()
        saved = (db.DB_PATH, db._initialized)
        try:
            db.DB_PATH, db._initialized = db_path, False
            c = self.connect()
            cols = {r[1] for r in c.execute("PRAGMA table_info(articles)")}
            self.assertIn("deleted_at", cols)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM admin_log").fetchone()[0], 0)
        finally:
            db.DB_PATH, db._initialized = saved


if __name__ == "__main__":
    unittest.main()


class CliAdminTests(unittest.TestCase):
    """Kommandona admin-password och delete."""

    def setUp(self):
        import contextlib
        import io
        import types
        try:
            import httpx  # noqa: F401
        except ImportError:
            sys.modules["httpx"] = types.ModuleType("httpx")
        import cli
        self.cli, self.io, self.contextlib = cli, io, contextlib
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old = (config.DATA, config.RAW_DIR, db.DATA, db.DB_PATH, db._initialized, admin.ITERATIONS, cli.RAW_DIR)
        config.DATA = db.DATA = root
        config.RAW_DIR = cli.RAW_DIR = root / "raw"
        db.DB_PATH = root / "news.db"
        db._initialized = False
        admin.ITERATIONS = 1000
        con = db.connect()
        for i, (src, pub) in enumerate([("svt", "2026-09-01"), ("svt", "2026-10-10"), ("dn", "2026-09-02")], 1):
            con.execute("INSERT INTO articles(source,url,status,title,published) VALUES (?,?, 'parsed','T',?)",
                        (src, f"https://example.se/{src}/{i}", pub))
        con.commit()
        con.close()

    def tearDown(self):
        (config.DATA, config.RAW_DIR, db.DATA, db.DB_PATH, db._initialized, admin.ITERATIONS, self.cli.RAW_DIR) = self.old
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out = self.io.StringIO()
        with self.contextlib.redirect_stdout(out):
            self.cli.main(list(argv))
        return out.getvalue()

    def statuses(self):
        con = db.connect()
        try:
            return {r["id"]: r["status"] for r in con.execute("SELECT id, status FROM articles")}
        finally:
            con.close()

    def test_delete_needs_a_target_and_dry_runs_by_default(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_cli("delete")
        self.assertIn("Ange artikel-id", str(cm.exception))
        out = self.run_cli("delete", "1", "2")
        self.assertIn("2 artiklar skulle raderas", out)
        self.assertIn("Inget raderades", out)
        self.assertEqual(set(self.statuses().values()), {"parsed"})

    def test_delete_with_yes_and_by_source_and_date(self):
        out = self.run_cli("delete", "1", "--yes")
        self.assertIn("Raderade 1 artiklar", out)
        self.assertEqual(self.statuses()[1], "deleted")
        con = db.connect()
        self.assertEqual(con.execute("SELECT actor FROM admin_log").fetchone()[0], "cli")
        con.close()
        out = self.run_cli("delete", "--site", "svt", "--before", "2026-10-01", "--yes")      # svt före 1 oktober: bara id 1 (redan raderad)
        self.assertIn("0 artiklar skulle raderas", out)
        out = self.run_cli("delete", "--site", "svt", "dn", "--before", "2026-10-05", "--yes")
        self.assertIn("Raderade 1 artiklar", out)                                              # dn id 3 (id 1 var redan raderad)
        self.assertEqual(self.statuses(), {1: "deleted", 2: "parsed", 3: "deleted"})

    def test_admin_password_command(self):
        import getpass
        answers = iter(["ett-långt-lösenord", "ett-långt-lösenord"])
        orig = getpass.getpass
        getpass.getpass = lambda prompt="": next(answers)
        try:
            self.run_cli("admin-password")
            self.assertTrue(admin.check_password("ett-långt-lösenord"))
            answers = iter(["ett-långt-lösenord", "något-annat-lösenord"])
            with self.assertRaises(SystemExit) as cm:
                self.run_cli("admin-password")
            self.assertIn("inte lika", str(cm.exception))
            answers = iter(["kort", "kort"])
            with self.assertRaises(SystemExit) as cm:
                self.run_cli("admin-password")
            self.assertIn("minst", str(cm.exception))
            self.assertTrue(admin.check_password("ett-långt-lösenord"))                     # oförändrat efter misslyckade försök
        finally:
            getpass.getpass = orig
        out = self.run_cli("admin-password", "--disable")
        self.assertIn("avstängd", out)
        self.assertFalse(admin.admin_enabled())
