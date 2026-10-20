import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import enrich
from discover import extract_links
from sites import SITES

LD = {
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "Bråk i Göteborg efter beslut",
    "description": "Ingressen står här.",
    "author": [{"@type": "Person", "name": "Anna Andersson"}, {"@type": "Person", "name": "Bo Berg"}],
    "datePublished": "2026-10-08T12:30:00+02:00",
    "dateModified": "2026-10-08T13:00:00+02:00",
    "articleSection": "Inrikes",
    "keywords": "politik, kommun",
    "image": {"@type": "ImageObject", "url": "https://example.com/bild.jpg"},
    "isAccessibleForFree": False,
}


def page(ld=LD):
    return f"""<html lang="sv"><head><title>x</title>
    <link rel="canonical" href="https://example.com/a">
    <script type="application/ld+json">{json.dumps(ld)}</script></head>
    <body><article><p>{'Första stycket om Göteborg och Sverige, långt nog att räknas. ' * 2}</p>
    <p>{'Andra stycket handlar om Stockholm men mest om Göteborg igen. ' * 2}</p></article></body></html>"""


class ParseTests(unittest.TestCase):
    def test_generic_fields(self):
        d = SITES["svt"].parse(page(), "https://www.svt.se/nyheter/inrikes/test-abc123")
        self.assertEqual(d["title"], "Bråk i Göteborg efter beslut")
        self.assertEqual(d["author"], "Anna Andersson, Bo Berg")
        self.assertEqual(d["category"], "Inrikes")
        self.assertEqual(d["tags"], ["politik", "kommun"])
        self.assertEqual(d["image"], "https://example.com/bild.jpg")
        self.assertEqual(d["paywalled"], 1)
        self.assertEqual(d["article_type"], "nyhet")
        self.assertEqual(d["source_political_label"], "Obunden (public service)")
        self.assertIn("Göteborg", d["body"])

    def test_article_types(self):
        dn = SITES["dn"]
        self.assertEqual(dn.parse(page(), "https://www.dn.se/ledare/rubrik/")["article_type"], "ledare")
        self.assertEqual(dn.parse(page(), "https://www.dn.se/debatt/rubrik/")["article_type"], "debatt")
        op = dict(LD, **{"@type": "OpinionNewsArticle"})
        self.assertEqual(dn.parse(page(op), "https://www.dn.se/sverige/rubrik/")["article_type"], "debatt")

    def test_category_fallback_from_url(self):
        ld = {k: v for k, v in LD.items() if k != "articleSection"}
        d = SITES["aftonbladet"].parse(page(ld), "https://www.aftonbladet.se/nyheter/a/abc/slug")
        self.assertEqual(d["category"], "nyheter")

    def test_url_filters(self):
        self.assertTrue(SITES["aftonbladet"].is_article_url("https://www.aftonbladet.se/nyheter/a/abc/slug"))
        self.assertFalse(SITES["aftonbladet"].is_article_url("https://www.aftonbladet.se/nyheter"))
        self.assertTrue(SITES["svt"].is_article_url("https://www.svt.se/nyheter/inrikes/slug-1"))
        self.assertFalse(SITES["svt"].is_article_url("https://www.svt.se/nyheter/inrikes"))
        self.assertTrue(SITES["dn"].is_article_url("https://www.dn.se/sverige/slug/"))
        self.assertFalse(SITES["dn"].is_article_url("https://www.dn.se/rss/"))

    def test_enrich(self):
        d = SITES["svt"].parse(page(), "https://www.svt.se/nyheter/inrikes/test-abc123")
        e = enrich.enrich(d)
        self.assertEqual(e["city"], "Göteborg")
        self.assertEqual(e["country"], "Sverige")
        self.assertEqual(e["topic"], "politik")

    def test_archive_settings_exist_on_all_sites(self):
        for key, site in SITES.items():
            self.assertTrue(hasattr(site, "archive_enabled"), key)
            self.assertTrue(hasattr(site, "archive_delay"), key)
        self.assertFalse(SITES["dn"].archive_enabled)
        self.assertTrue(SITES["svt"].archive_enabled)
        self.assertTrue(SITES["aftonbladet"].archive_enabled)

    def test_feed_links(self):
        rss = b"<rss><channel><item><link>https://a.se/x</link></item></channel></rss>"
        links, kind = extract_links(rss)
        self.assertEqual(links, ["https://a.se/x"])
        self.assertEqual(kind, "rss")


if __name__ == "__main__":
    unittest.main()
