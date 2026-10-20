import contextlib
import io
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    import httpx  # noqa: F401
except ImportError:                       # testmiljön behöver inte ha nätverksbiblioteket
    sys.modules["httpx"] = types.ModuleType("httpx")

import cli


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    code = None
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            cli.main(argv)
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


class HelpTests(unittest.TestCase):
    def test_main_help_in_all_spellings(self):
        for argv in ([], ["-h"], ["--help"], ["-help"], ["/?"], ["help"], ["hjälp"]):
            code, out, _ = run(argv)
            self.assertEqual(code, 0, argv)
            self.assertIn("VANLIGT ARBETSFLÖDE", out, argv)
            self.assertIn("användning:", out, argv)

    def test_all_commands_are_listed_in_main_help(self):
        _, out, _ = run(["--help"])
        for name in cli.COMMANDS:
            self.assertIn(f"    {name}", out, name)

    def test_every_command_has_detailed_help_with_examples(self):
        for name in cli.COMMANDS:
            for argv in ([name, "--help"], [name, "-help"], [name, "-h"], ["help", name], ["hjälp", name]):
                code, out, _ = run(argv)
                self.assertEqual(code, 0, argv)
                self.assertIn("användning:", out, argv)
                self.assertIn("Exempel:", out, argv)
                self.assertIn(f"python cli.py {name}", out, argv)

    def test_swedish_error_message(self):
        code, _, err = run(["finns-inte"])
        self.assertEqual(code, 2)
        self.assertIn("Fel: okänt kommando 'finns-inte'", err)
        self.assertIn("python cli.py hjälp", err)
        code, _, err = run(["add-site"])                        # saknar adress
        self.assertEqual(code, 2)
        self.assertIn("följande argument saknas", err)
        code, _, err = run(["archive", "--limit", "abc"])
        self.assertIn("ogiltigt heltal", err)
        code, _, err = run(["archive", "--bogus"])
        self.assertIn("okända argument", err)


class ContactTests(unittest.TestCase):
    def test_contact_saves_settings_and_warning(self):
        import json
        import tempfile
        import config
        old = config.DATA
        with tempfile.TemporaryDirectory() as d:
            config.DATA = Path(d) / "data"
            try:
                code, out, _ = run(["contact", "jag@exempel.se"])
                self.assertIn("jag@exempel.se", out)
                saved = json.loads((config.DATA / "settings.json").read_text(encoding="utf-8"))
                self.assertEqual(saved["user_agent"], "Nyhetsarkivet/0.1 (+kontakt: jag@exempel.se)")
            finally:
                config.DATA = old
        err = io.StringIO()
        old_ua = config.USER_AGENT
        try:
            config.USER_AGENT = config.DEFAULT_USER_AGENT
            with contextlib.redirect_stderr(err):
                cli.warn_contact("fetch")
                cli.warn_contact("status")
            self.assertEqual(err.getvalue().count("OBS:"), 1)           # bara nätkommandon varnar
            config.USER_AGENT = "Nyhetsarkivet/0.1 (+kontakt: jag@exempel.se)"
            err2 = io.StringIO()
            with contextlib.redirect_stderr(err2):
                cli.warn_contact("fetch")
            self.assertEqual(err2.getvalue(), "")
        finally:
            config.USER_AGENT = old_ua


class ArgsTests(unittest.TestCase):
    def parse(self, *argv):
        return cli.build_parser().parse_args(cli.normalize_argv(list(argv)))

    def test_archive_flags(self):
        a = self.parse("archive", "--limit", "5", "--since", "2025-01-01", "--until", "2025-04-01",
                       "--no-front", "--minutes", "30", "--site", "svt", "dn")
        self.assertEqual((a.limit, a.since, a.until, a.no_front, a.minutes, a.site),
                         (5, "2025-01-01", "2025-04-01", True, 30.0, ["svt", "dn"]))
        self.assertEqual(self.parse("archive").limit, 20)
        self.assertEqual(self.parse("fetch").limit, 50)

    def test_other_commands(self):
        a = self.parse("add-site", "https://x.se", "--key", "x", "--name", "X", "--label", "L")
        self.assertEqual((a.url, a.key, a.name, a.label, a.feed), ("https://x.se", "x", "X", "L", None))
        a = self.parse("add-site", "https://x.se", "--feed", "https://x.se/a.rss", "--feed", "https://x.se/b.rss")
        self.assertEqual(a.feed, ["https://x.se/a.rss", "https://x.se/b.rss"])
        self.assertEqual(self.parse("add-sites", "lista.txt").file, "lista.txt")
        a = self.parse("add-site", "https://www.100.se", "--index", "https://www.100.se/a", "--index", "https://www.100.se/b",
                       "--article-regex", "^x$")
        self.assertEqual((a.index, a.article_regex), (["https://www.100.se/a", "https://www.100.se/b"], "^x$"))
        self.assertEqual(self.parse("profiles").cmd, "profiles")
        w = self.parse("wayback", "--since", "2025-09", "--until", "2025-09", "--max", "100")
        self.assertEqual((w.since, w.until, w.max), ("2025-09", "2025-09", 100))
        b = self.parse("backfill", "--since", "2026-09-01", "--max-sitemaps", "5")
        self.assertEqual((b.since, b.max_sitemaps), ("2026-09-01", 5))
        self.assertEqual(self.parse("snowball", "--pages", "9").pages, 9)
        self.assertTrue(self.parse("parse", "--reparse").reparse)
        self.assertIsNone(self.parse("status").site)

    def test_unknown_site_is_reported(self):
        with self.assertRaises(SystemExit) as cm:
            cli.selected(self.parse("status", "--site", "finns-inte"))
        self.assertIn("Okänd sajt", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
