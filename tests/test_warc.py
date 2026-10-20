import base64
import datetime as dt
import gzip
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import webarchive
from harconv import entries_from_har
from warc import WarcWriter, sha1_digest

NOW = dt.datetime(2026, 10, 10, 5, 30, tzinfo=dt.timezone.utc)


def read_warc(path):
    data = gzip.open(path, "rb").read()          # flera gzip-medlemmar läses som en ström
    recs, i = [], 0
    while i < len(data):
        j = data.index(b"\r\n\r\n", i)
        lines = data[i:j].decode("utf-8").split("\r\n")
        assert lines[0] == "WARC/1.0"
        h = dict(line.split(": ", 1) for line in lines[1:])
        n = int(h["Content-Length"])
        block = data[j + 4:j + 4 + n]
        assert data[j + 4 + n:j + 8 + n] == b"\r\n\r\n"
        recs.append((h, block))
        i = j + 8 + n
    return recs


def entry(url, body=b"hej", status=200, headers=None, mime="text/html"):
    return {"url": url, "date": NOW, "status": status, "reason": "", "mime": mime, "body": body,
            "headers": headers or [("Content-Type", "text/html; charset=utf-8")]}


def har_entry(url, text=None, encoding=None, status=200, mime="text/html", method="GET", size=None,
              headers=None):
    content = {"mimeType": mime, "size": size if size is not None else len(text or "")}
    if text is not None:
        content["text"] = text
    if encoding:
        content["encoding"] = encoding
    return {"startedDateTime": "2026-10-10T05:30:00.123Z",
            "request": {"method": method, "url": url},
            "response": {"status": status, "statusText": "", "content": content,
                         "headers": headers or [{"name": "Content-Type", "value": mime}]}}


class WarcTests(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "a.warc.gz"
            w = WarcWriter(path)
            w.warcinfo({"software": "test"}, NOW)
            body = "Räksmörgås".encode("utf-8")
            rid = w.response("https://x.se/a", NOW, 200, "", [
                ("Content-Type", "text/html"), ("Content-Encoding", "gzip"),
                ("Content-Length", "999"), (":status", "200"), ("Set-Cookie", "a=b")], body)
            w.revisit("https://x.se/a", NOW, 200, "", [("Content-Type", "text/html")],
                      len(body), sha1_digest(body), rid, "2026-10-10T05:30:00Z")
            w.close()
            recs = read_warc(path)
            self.assertEqual([r[0]["WARC-Type"] for r in recs], ["warcinfo", "response", "revisit"])
            head, payload = recs[1][1].split(b"\r\n\r\n", 1)
            self.assertEqual(payload, body)
            text = head.decode()
            self.assertTrue(text.startswith("HTTP/1.1 200 OK"))
            self.assertIn(f"Content-Length: {len(body)}", text)
            for banned in ("gzip", "999", ":status", "Set-Cookie"):
                self.assertNotIn(banned, text)
            self.assertEqual(recs[1][0]["WARC-Payload-Digest"], sha1_digest(body))
            self.assertEqual(recs[2][0]["WARC-Refers-To"], rid)
            self.assertEqual(recs[2][1].split(b"\r\n\r\n", 1)[1], b"")

    def test_append_keeps_offsets(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "a.warc.gz"
            w = WarcWriter(path); w.response("https://x.se/1", NOW, 200, "", [], b"1"); w.close()
            w = WarcWriter(path)
            start = w.pos
            self.assertEqual(start, path.stat().st_size)
            w.response("https://x.se/2", NOW, 200, "", [], b"2"); w.close()
            # varje fångst är en sammanhängande, giltig del av filen
            part = gzip.decompress(path.read_bytes()[start:start + (path.stat().st_size - start)])
            self.assertIn(b"https://x.se/2", part)
            self.assertEqual(len(read_warc(path)), 2)

    def test_har_conversion(self):
        png = base64.b64encode(b"\x89PNG\x00\x01").decode()
        har = {"log": {"entries": [
            har_entry("https://a.se/", "<html>åäö</html>",
                      headers=[{"name": "content-type", "value": "text/html; charset=iso-8859-1"}]),
            har_entry("https://a.se/s.css", "body{}", mime="text/css"),
            har_entry("https://a.se/l.png", png, "base64", mime="image/png"),
            har_entry("https://a.se/old", None, status=301, size=0,
                      headers=[{"name": "Location", "value": "/new"}]),
            har_entry("https://a.se/post", "x", method="POST"),
            har_entry("https://a.se/fail", None, status=0),
            har_entry("https://a.se/v.mp4", base64.b64encode(b"v").decode(), "base64", mime="video/mp4"),
            har_entry("https://www.googletagmanager.com/gtm.js", "x", mime="text/javascript"),
            har_entry("https://a.se/stor.bin", base64.b64encode(b"x" * 50).decode(), "base64",
                      mime="application/octet-stream"),
            har_entry("data:image/png;base64,AAAA", "x"),
            har_entry("https://a.se/saknas", None, size=500),
        ]}}
        entries, skipped = entries_from_har(har, max_bytes=40)
        urls = [e["url"] for e in entries]
        self.assertEqual(urls, ["https://a.se/", "https://a.se/s.css", "https://a.se/l.png", "https://a.se/old"])
        self.assertEqual(entries[0]["body"], "<html>åäö</html>".encode("utf-8"))
        ct = dict(entries[0]["headers"])["content-type"]
        self.assertIn("charset=utf-8", ct)
        self.assertNotIn("8859", ct)
        self.assertEqual(entries[2]["body"], b"\x89PNG\x00\x01")
        self.assertEqual(entries[3]["status"], 301)
        for k in ("ej_get", "ingen_respons", "media", "blockerad_värd", "för_stor", "ej_http", "saknar_innehåll"):
            self.assertEqual(skipped.get(k), 1, k)

    def test_snapshot_dedup(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript(db.SCHEMA)
        with tempfile.TemporaryDirectory() as d:
            webarchive.WARC_DIR = Path(d)
            css = entry("https://a.se/s.css", b"body{color:red}", mime="text/css")
            first = webarchive.write_snapshot(con, "t/2026-10-10.warc.gz",
                                              [entry("https://a.se/1", b"ett"), css, css], NOW)
            second = webarchive.write_snapshot(con, "t/2026-10-10.warc.gz",
                                               [entry("https://a.se/2", b"tva"), css], NOW)
            self.assertEqual(first["n_revisits"], 1)    # samma CSS två gånger i första fångsten
            self.assertEqual(second["n_revisits"], 1)   # och igen i den andra
            self.assertEqual(second["offset"], first["offset"] + first["length"])
            recs = read_warc(Path(d) / "t/2026-10-10.warc.gz")
            self.assertEqual([r[0]["WARC-Type"] for r in recs],
                             ["warcinfo", "response", "response", "revisit", "response", "revisit"])
            # en annan dag får en ny fil och därmed en full kopia
            third = webarchive.write_snapshot(con, "t/2026-10-11.warc.gz", [css], NOW)
            self.assertEqual(third["n_revisits"], 0)

    def test_deduplicate_can_be_turned_off(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript(db.SCHEMA)
        old = webarchive.WARC_DIR, webarchive.DEDUPLICATE
        try:
            with tempfile.TemporaryDirectory() as d:
                webarchive.WARC_DIR = Path(d)
                webarchive.DEDUPLICATE = False
                css = entry("https://a.se/s.css", b"body{color:red}", mime="text/css")
                first = webarchive.write_snapshot(con, "t/x.warc.gz", [entry("https://a.se/1", b"ett"), css, css], NOW)
                second = webarchive.write_snapshot(con, "t/x.warc.gz", [entry("https://a.se/2", b"tva"), css], NOW)
                self.assertEqual((first["n_revisits"], second["n_revisits"]), (0, 0))
                recs = read_warc(Path(d) / "t/x.warc.gz")
                self.assertNotIn("revisit", [r[0]["WARC-Type"] for r in recs])
        finally:
            webarchive.WARC_DIR, webarchive.DEDUPLICATE = old

    def test_stop_reason(self):
        import time
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript(db.SCHEMA)
        old = (webarchive.WARC_DIR, webarchive.MAX_WARC_GB, webarchive.DAILY_LIMIT_PER_SITE)
        try:
            with tempfile.TemporaryDirectory() as d:
                webarchive.WARC_DIR = Path(d)
                self.assertIsNone(webarchive.stop_reason(con, "svt", None))
                self.assertIn("tidsgränsen", webarchive.stop_reason(con, "svt", time.monotonic() - 1))
                (Path(d) / "x.warc.gz").write_bytes(b"x" * 2000)
                webarchive.MAX_WARC_GB = 1000 / 1e9
                self.assertIn("GB", webarchive.stop_reason(con, "svt", None))
                webarchive.MAX_WARC_GB = 20
                webarchive.DAILY_LIMIT_PER_SITE = 2
                today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                for _ in range(2):
                    con.execute("INSERT INTO snapshots(source,kind,url,taken_at,status) "
                                "VALUES ('svt','article','u',?, 'ok')", (today,))
                self.assertIn("dagens gräns", webarchive.stop_reason(con, "svt", None))
                self.assertIsNone(webarchive.stop_reason(con, "dn", None))
        finally:
            webarchive.WARC_DIR, webarchive.MAX_WARC_GB, webarchive.DAILY_LIMIT_PER_SITE = old


if __name__ == "__main__":
    unittest.main()
