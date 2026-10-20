import argparse
import contextlib
import io
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    import httpx  # noqa: F401
except ImportError:
    sys.modules["httpx"] = types.ModuleType("httpx")

import config
import db
import progress
from progress import Progress, fmt_duration


class FmtTests(unittest.TestCase):
    def test_duration(self):
        self.assertEqual(fmt_duration(5), "5 s")
        self.assertEqual(fmt_duration(125), "2 min 05 s")
        self.assertEqual(fmt_duration(3 * 3600 + 7 * 60), "3 h 07 min")
        self.assertEqual(fmt_duration(-3), "0 s")


class ProgressTests(unittest.TestCase):
    def test_tty_line_bar_errors_eta_and_truncation(self):
        out = io.StringIO()
        p = Progress(10, "Hämtar", stream=out, enabled=True, width=100)
        p.t0 -= 30                                           # 30 s har gått
        p.set_current("svt: https://www.svt.se/nyheter/inrikes/en-mycket-lang-adress-" + "x" * 200)
        for i in range(5):
            p.advance(error=(i == 0))
        p.close()
        text = out.getvalue()
        last = [seg for seg in text.split("\r") if seg.strip()][-2] if text.count("\r") > 2 else text
        self.assertIn("5/10", text)
        self.assertIn(" 50%", text)
        self.assertIn("fel 1", text)
        self.assertIn("kvar", text)                          # tid kvar efter minst tre klara
        self.assertIn("[##########----------]", text)
        self.assertIn("...", text)                           # lång adress förkortas
        for seg in text.split("\r"):
            self.assertLessEqual(len(seg.rstrip("\n")), 100)
        self.assertEqual(p.done, 5)
        self.assertIn("5 av 10 klara", p.summary())
        self.assertIn("1 fel", p.summary())

    def test_non_tty_prints_plain_lines_every_five_percent(self):
        out = io.StringIO()
        p = Progress(40, "Tolkar", stream=out, enabled=False)
        for _ in range(40):
            p.advance()
        p.close()
        lines = out.getvalue().strip().splitlines()
        self.assertNotIn("\r", out.getvalue())
        self.assertTrue(5 <= len(lines) <= 25)
        self.assertTrue(lines[-1].startswith("Tolkar: 40/40 (100%)"))

    def test_log_inserts_permanent_line_and_is_thread_safe(self):
        out = io.StringIO()
        p = Progress(200, "Hämtar", stream=out, enabled=True, width=80)

        def work():
            for _ in range(50):
                p.advance()
        threads = [threading.Thread(target=work) for _ in range(4)]
        [t.start() for t in threads]
        p.log("svt: hämtade 50 av 50")
        [t.join() for t in threads]
        time.sleep(0.3)                                      # låt tickern rita minst en gång
        p.close()
        self.assertEqual(p.done, 200)
        self.assertIn("svt: hämtade 50 av 50\n", out.getvalue())
        self.assertTrue(p._ticker is None or not p._ticker.is_alive())


class FakeResp:
    def __init__(self, code, text=""):
        self.status_code, self.text = code, text


class FakeFetcher:
    def allowed(self, url):
        return "blockerad" not in url

    def get(self, url, timeout=None):
        if "fel" in url:
            return FakeResp(404)
        return FakeResp(200, f"<html><head><title>T {url}</title></head><body><article><p>{'Text om Örebro. ' * 8}</p></article></body></html>")


class CliIntegrationTests(unittest.TestCase):
    def setUp(self):
        import cli
        self.cli = cli
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old = (config.DATA, db.DATA, db.DB_PATH, cli.RAW_DIR, cli.Fetcher, db._initialized)
        config.DATA = db.DATA = root
        db.DB_PATH = root / "news.db"
        cli.RAW_DIR = root / "raw"
        cli.Fetcher = FakeFetcher
        db._initialized = False
        con = db.connect()
        for i, path in enumerate(["a", "b", "fel-c", "blockerad-d", "e"]):
            con.execute("INSERT INTO articles(source,url,status,discovered_at) VALUES ('svt',?, 'discovered', ?)",
                        (f"https://www.svt.se/nyheter/inrikes/{path}", f"2026-10-0{i + 1}T00:00:00Z"))
        con.commit()
        con.close()

    def tearDown(self):
        cli = self.cli
        config.DATA, db.DATA, db.DB_PATH, cli.RAW_DIR, cli.Fetcher, db._initialized = self.old
        cli.CANCEL.clear()
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.cli.main(list(argv))
        return out.getvalue(), err.getvalue()

    def test_fetch_and_parse_show_progress_and_counts(self):
        out, err = self.run_cli("fetch", "--site", "svt", "--limit", "10")
        self.assertIn("svt: hämtade 3 av 5", err)             # 2 misslyckas (404, blockerad); per-sajt-raden följer förloppsraden
        self.assertIn("Hämtar: 5 av 5 klara", out)
        self.assertIn("2 fel", out)
        self.assertIn("Hämtar: 5/5 (100%)", err)               # förlopp till stderr (ingen terminal här)
        out, err = self.run_cli("parse", "--site", "svt")
        self.assertIn("svt: tolkade 3 av 3", err)
        self.assertIn("Tolkar: 3 av 3 klara", out)
        con = db.connect()
        self.assertEqual(con.execute("SELECT COUNT(*) FROM articles WHERE status='parsed'").fetchone()[0], 3)

    def test_empty_queue_message(self):
        self.run_cli("fetch", "--site", "svt", "--limit", "10")
        out, _ = self.run_cli("fetch", "--site", "svt", "--limit", "10")
        self.assertIn("Inget att hämta", out)
        self.run_cli("parse", "--site", "svt")
        out, _ = self.run_cli("parse", "--site", "svt")
        self.assertIn("Inget att tolka", out)

    def test_cancel_stops_workers(self):
        self.cli.CANCEL.set()
        out, err = self.run_cli("fetch", "--site", "svt", "--limit", "10")
        self.assertIn("svt: hämtade 0 av 5", err)             # avbruten före första sidan
        con = db.connect()
        self.assertEqual(con.execute("SELECT COUNT(*) FROM articles WHERE status='discovered'").fetchone()[0], 5)

    def test_keyboard_interrupt_is_handled_without_traceback(self):
        orig = self.cli.cmd_status
        self.cli.COMMANDS["status"] = (lambda args: (_ for _ in ()).throw(KeyboardInterrupt()),) + self.cli.COMMANDS["status"][1:]
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    self.cli.main(["status"])
            self.assertEqual(cm.exception.code, 130)
            self.assertIn("Avbruten", err.getvalue())
        finally:
            self.cli.COMMANDS["status"] = (orig,) + self.cli.COMMANDS["status"][1:]


if __name__ == "__main__":
    unittest.main()
