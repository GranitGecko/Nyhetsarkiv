import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import profiles as prof
import webui
from sites import SITES

ROOT = Path(__file__).resolve().parent.parent


def memdb(n_svt=130, n_dn=7):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(db.SCHEMA)
    for i in range(n_svt):
        con.execute("INSERT INTO articles(source,url,status,title,published,article_type,category) VALUES "
                    "('svt',?, 'parsed',?,?, 'nyhet','Inrikes')",
                    (f"https://www.svt.se/nyheter/inrikes/a{i}", f"Rubrik {i}", f"2026-10-{1 + i % 28:02d}T10:00:00+02:00"))
    for i in range(n_dn):
        con.execute("INSERT INTO articles(source,url,status,title,published,article_type,category) VALUES "
                    "('dn',?, 'parsed',?,?, 'ledare','Debatt')",
                    (f"https://www.dn.se/debatt/a{i}/", f"Debatt {i}", f"2026-09-{1 + i:02d}T10:00:00+02:00"))
    con.execute("INSERT INTO snapshots(source,kind,url,taken_at,status,bytes) VALUES ('svt','article','u','2026-10-10T00:00:00Z','ok',2097152)")
    con.commit()
    return con


class PagerTests(unittest.TestCase):
    def labels(self, page, pages):
        return [(i["label"], i["url"]) for i in webui.pager(page, pages, lambda p: f"/p{p}")]

    def test_middle_page_has_all_jumps(self):
        got = dict(self.labels(10, 30))
        self.assertEqual(got["« Första"], "/p1")
        self.assertEqual(got["-5"], "/p5")
        self.assertEqual(got["‹ Föregående"], "/p9")
        self.assertEqual(got["Nästa ›"], "/p11")
        self.assertEqual(got["+5"], "/p15")
        self.assertEqual(got["Sista »"], "/p30")
        self.assertIsNone(got["10"])                                  # nuvarande sida är ingen länk
        self.assertEqual(got["7"], "/p7")
        self.assertIn("...", got)                                     # glapp mellan 1 och 7, och mellan 13 och 30

    def test_edges_clamp_and_disable(self):
        first = dict(self.labels(1, 30))
        self.assertIsNone(first["« Första"])
        self.assertIsNone(first["-5"])
        self.assertIsNone(first["‹ Föregående"])
        self.assertEqual(first["+5"], "/p6")
        last = dict(self.labels(30, 30))
        self.assertIsNone(last["Sista »"])
        self.assertIsNone(last["Nästa ›"])
        self.assertEqual(last["-5"], "/p25")
        near_end = dict(self.labels(28, 30))
        self.assertEqual(near_end["+5"], "/p30")                      # hoppar aldrig förbi sista sidan
        near_start = dict(self.labels(3, 30))
        self.assertEqual(near_start["-5"], "/p1")

    def test_numbers_window_without_gaps_for_few_pages(self):
        nums = [i["label"] for i in webui.pager(2, 4, lambda p: f"/p{p}") if i["label"].isdigit()]
        self.assertEqual(nums, ["1", "2", "3", "4"])


class ArticlesContextTests(unittest.TestCase):
    def setUp(self):
        self.con = memdb()

    def ctx(self, **params):
        return webui.articles_context(self.con, {k: str(v) for k, v in params.items()}, SITES)

    def test_default_page_size_and_counts(self):
        c = self.ctx()
        self.assertEqual((c["total"], c["per"], c["pages"], c["page"]), (137, 50, 3, 1))
        self.assertEqual((c["first"], c["last"], len(c["rows"])), (1, 50, 50))

    def test_page_size_options_and_invalid_values(self):
        self.assertEqual(self.ctx(per_sida=25)["pages"], 6)
        self.assertEqual(self.ctx(per_sida=200)["pages"], 1)
        self.assertEqual(self.ctx(per_sida=37)["per"], 50)            # ogiltigt värde faller tillbaka
        self.assertEqual(self.ctx(per_sida="abc")["per"], 50)

    def test_paging_and_clamping(self):
        c = self.ctx(sida=3, per_sida=50)
        self.assertEqual((c["first"], c["last"], len(c["rows"])), (101, 137, 37))
        self.assertEqual(self.ctx(sida=99)["page"], 3)                # för hög sida hamnar på sista
        self.assertEqual(self.ctx(sida=-4)["page"], 1)
        self.assertEqual(self.ctx(sida="x")["page"], 1)
        pages = [i["url"] for i in c["pager"] if i["label"] == "-5"]
        self.assertIn("sida=1", pages[0])
        self.assertIn("per_sida=50", pages[0])

    def test_filters_are_kept_in_links_and_applied(self):
        c = self.ctx(kalla="dn", per_sida=25)
        self.assertEqual((c["total"], c["pages"]), (7, 1))
        c = self.ctx(kalla="svt", per_sida=25, sida=2)
        self.assertEqual(c["total"], 130)
        link = next(i["url"] for i in c["pager"] if i["label"] == "Nästa ›")
        self.assertIn("kalla=svt", link)
        self.assertIn("sida=3", link)
        self.assertEqual(self.ctx(typ="ledare")["total"], 7)
        self.assertEqual(self.ctx(q="Debatt 3")["total"], 1)

    def test_empty_result(self):
        c = self.ctx(q="finns-inte")
        self.assertEqual((c["total"], c["pages"], c["first"], c["last"]), (0, 1, 0, 0))


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.con = memdb()
        self.data = prof.load()

    def test_sites_are_joined_with_profiles_by_host(self):
        entries = {e["key"]: e for e in webui.build_catalog(SITES, self.data, webui.source_stats(self.con))}
        self.assertEqual(entries["dn"]["type"], "Rikstäckande dagspress")
        self.assertTrue(entries["dn"]["in_archive"])
        self.assertEqual(entries["svt"]["stats"]["articles"], 130)
        self.assertEqual(entries["svt"]["stats"]["snapshots"], 1)
        self.assertIn("Karin Ekman", [p["name"] for p in entries["svt"]["profile"]["people"]])

    def test_profiles_without_site_are_listed_as_not_added(self):
        entries = {e["key"]: e for e in webui.build_catalog(SITES, self.data, {})}
        self.assertFalse(entries["nordfront"]["in_archive"])
        self.assertEqual(entries["nordfront"]["stats"], {})

    def test_added_site_is_matched_to_profile_by_host_even_with_other_key(self):
        from sites.generic import GenericSite
        sites = dict(SITES)
        sites["100"] = GenericSite(key="100", name="100 procent", home="https://www.100.se/", index_pages=["https://www.100.se/artiklar/debatt"])
        sites["lokal"] = GenericSite(key="lokal", name="Lokal Tidning", home="https://lokal.example.se/")
        entries = {e["key"]: e for e in webui.build_catalog(sites, self.data, {})}
        self.assertTrue(entries["100"]["in_archive"])
        self.assertEqual(entries["100"]["name"], "100%")                  # profilens namn används
        self.assertEqual(entries["100"]["type"], "Nätmedia och opinion")
        self.assertNotIn("100procent", entries)                           # profilen listas inte två gånger
        self.assertEqual(entries["lokal"]["type"], webui.NO_PROFILE)
        self.assertIsNone(entries["lokal"]["profile"])
        groups = [t for t, _ in webui.group_catalog(list(entries.values()))]
        self.assertEqual(groups[-1], webui.NO_PROFILE)

    def test_groups_in_logical_order_and_unprofiled_last(self):
        entries = webui.build_catalog(SITES, self.data, {})
        entries.append({"key": "x", "name": "X", "home": "https://x.se/", "type": webui.NO_PROFILE, "profile": None,
                        "in_archive": True, "political_label": None, "stats": {}, "web_archive": True})
        groups = [t for t, _ in webui.group_catalog(entries)]
        self.assertEqual(groups[0], "Public service")
        self.assertEqual(groups[-1], webui.NO_PROFILE)
        self.assertLess(groups.index("Rikstäckande dagspress"), groups.index("Nätmedia och opinion"))

    def test_contexts(self):
        start = webui.start_context(self.con, SITES, self.data)
        self.assertEqual(start["total"], 137)
        self.assertEqual(start["n_snapshots"], 1)
        self.assertAlmostEqual(start["warc_mb"], 2.0)
        self.assertGreaterEqual(start["n_sources"], len(SITES))
        src = webui.source_context(self.con, SITES, self.data, "svt")
        self.assertEqual(len(src["recent"]), 10)
        pending = webui.source_context(self.con, SITES, self.data, "nordfront")
        self.assertEqual(pending["recent"], [])
        self.assertIsNone(webui.source_context(self.con, SITES, self.data, "finns-inte"))


class ProfileDataTests(unittest.TestCase):
    def test_bundled_profiles_are_valid_and_sourced(self):
        data = prof.load([ROOT / "content" / "profiles.json"])
        self.assertGreaterEqual(len(data["profiles"]), 25)
        self.assertEqual(prof.validate(data), [])

    def test_no_duplicate_hosts(self):
        data = prof.load([ROOT / "content" / "profiles.json"])
        hosts = [prof.host_key(p["home"]) for p in data["profiles"].values()]
        self.assertEqual(len(hosts), len(set(hosts)))

    def test_every_listed_person_has_an_https_source(self):
        data = prof.load([ROOT / "content" / "profiles.json"])
        for key, p in data["profiles"].items():
            for person in p["people"]:
                self.assertTrue(person["source"].startswith("http"), (key, person["name"]))

    def test_validation_catches_problems(self):
        bad = {"profiles": {"x": {"name": "X", "home": "ftp://x", "type": "T",
                                   "people": [{"name": "A", "role": "Chef", "source": ""}],
                                   "described": [{"text": "påstående", "source": "inte-en-länk"}],
                                   "facts": {"Ägare": "Y"}}}}
        problems = " | ".join(prof.validate(bad))
        self.assertIn("home måste börja", problems)
        self.assertIn("A saknar källa", problems)
        self.assertIn("beskrivning saknar källa", problems)
        self.assertIn("varken källor eller", problems)

    def test_local_overrides_win(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "a.json"
            over = Path(d) / "b.json"
            base.write_text(json.dumps({"checked": "2026-01-01", "profiles": {"x": {"name": "Gammal", "home": "https://x.se", "type": "T"}}}), encoding="utf-8")
            over.write_text(json.dumps({"checked": "2026-02-02", "profiles": {"x": {"name": "Ny", "home": "https://x.se", "type": "T"}}}), encoding="utf-8")
            merged = prof.load([base, over, Path(d) / "finns-inte.json"])
        self.assertEqual(merged["profiles"]["x"]["name"], "Ny")
        self.assertEqual(merged["checked"], "2026-02-02")


class TemplateRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from jinja2 import Environment, FileSystemLoader, select_autoescape
        except ImportError:
            raise unittest.SkipTest("jinja2 saknas")
        cls.env = Environment(loader=FileSystemLoader(str(ROOT / "templates")), autoescape=select_autoescape(["html"]))

    def setUp(self):
        self.con = memdb()
        self.data = prof.load()

    def render(self, name, **ctx):
        return self.env.get_template(name).render(count=None, **ctx)

    def test_start_page(self):
        html = self.render("start.html", **webui.start_context(self.con, SITES, self.data))
        self.assertIn("Välkommen till Nyhetsarkivet", html)
        self.assertIn("bevara och göra sökbart", html)
        self.assertIn('href="/kalla/svt"', html)
        self.assertIn("ännu inte tillagd i arkivet", html)             # profiler för källor som inte lagts till
        self.assertIn("Att en källa finns med betyder", html)

    def test_sources_page(self):
        html = self.render("kallor.html", **webui.start_context(self.con, SITES, self.data))
        self.assertIn("Public service", html)
        self.assertIn("Bonnier", html)

    def test_source_page_with_profile_people_and_sources(self):
        html = self.render("kalla.html", **webui.source_context(self.con, SITES, self.data, "svt"))
        self.assertIn("Karin Ekman", html)
        self.assertIn("Nyckelpersoner", html)
        self.assertIn("Källor för uppgifterna", html)
        self.assertIn("130 artiklar", html)
        self.assertIn("Senaste artiklarna", html)

    def test_source_page_for_not_added_source_and_warning(self):
        html = self.render("kalla.html", **webui.source_context(self.con, SITES, self.data, "nordfront"))
        self.assertIn("ännu inte tillagd i arkivet", html)
        self.assertIn("Nordiska motståndsrörelsen", html)
        self.assertNotIn("Senaste artiklarna", html)

    def test_articles_page_has_pagination_controls(self):
        c = webui.articles_context(self.con, {"sida": "2", "per_sida": "25"}, SITES)
        html = self.render("artiklar.html", c=c)
        for text in ("« Första", ">-5<", "‹ Föregående", "Nästa ›", ">+5<", "Sista »", "Gå till sida", "artiklar per sida"):
            self.assertIn(text, html)
        self.assertIn("[2]", html)
        self.assertIn('<option value="25" selected>', html)
        self.assertIn("Visar 26-50 av 137", html)


if __name__ == "__main__":
    unittest.main()
