import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_publish


class PublishCheckTests(unittest.TestCase):
    def test_repository_itself_is_clean(self):
        self.assertEqual(check_publish.check(), [])

    def test_catches_private_data_paths_emails_and_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "data").mkdir()
            (root / "data" / "news.db").write_bytes(b"x")
            (root / "sources.json").write_text("[]", encoding="utf-8")
            (root / "a.py").write_text("p = r'C:\\Users\\anna\\Downloads'\nmail = '" + "anna@" + "gmail.com" + "'\n", encoding="utf-8")
            (root / "b.py").write_text("t = 'ghp_" + "a" * 30 + "'\nok = 'du@example.se'\n", encoding="utf-8")
            (root / "stor.txt").write_text("x" * 2_000_000, encoding="utf-8")
            (root / "bra.py").write_text("print('hej')\n", encoding="utf-8")
            files = [p for p in root.rglob("*") if p.is_file()]
            problems = " | ".join(check_publish.check(root, files))
        for expected in ("data/news.db", "sources.json", "personlig Windows-sökväg", "anna@" + "gmail.com", "GitHub-token", "större än"):
            self.assertIn(expected, problems.replace("\\", "/"))
        self.assertNotIn("du@example.se", problems)
        self.assertNotIn("bra.py", problems)


if __name__ == "__main__":
    unittest.main()
