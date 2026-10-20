"""Kör webbplatsens och API:ts rutter. Använder riktiga FastAPI + TestClient om de finns, annars en minimal
ersättning som ändå renderar de riktiga mallarna, så att kopplingen mellan rutter, logik och mallar testas överallt."""
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import db

try:
    import fastapi  # noqa: F401
    from fastapi.testclient import TestClient
    REAL = True
except Exception:                                    # FastAPI eller dess testklient saknas
    REAL = False


def install_stubs():
    from jinja2 import Environment, FileSystemLoader

    class HTTPException(Exception):
        def __init__(self, status_code, detail=""):
            self.status_code, self.detail = status_code, detail

    class FastAPI:
        def __init__(self, **kw):
            self.routes = {}

        def get(self, path):
            def deco(fn):
                self.routes[path] = fn
                return fn
            return deco

        def mount(self, *a, **k):
            pass

    class Rendered:
        def __init__(self, html):
            self.body = html

    class Jinja2Templates:
        def __init__(self, directory):
            self.env = Environment(loader=FileSystemLoader(directory), autoescape=True)

        def TemplateResponse(self, request, name, context):
            return Rendered(self.env.get_template(name).render(**context))

    fa = types.ModuleType("fastapi")
    fa.FastAPI, fa.HTTPException, fa.Query, fa.Request = FastAPI, HTTPException, (lambda default=None, **k: default), object
    st = types.ModuleType("fastapi.staticfiles"); st.StaticFiles = lambda **k: None
    tp = types.ModuleType("fastapi.templating"); tp.Jinja2Templates = Jinja2Templates
    sys.modules.update({"fastapi": fa, "fastapi.staticfiles": st, "fastapi.templating": tp})


if not REAL:
    install_stubs()
import api  # noqa: E402


class Req:
    def __init__(self, **params):
        self.query_params = {k: str(v) for k, v in params.items()}


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

    def tearDown(self):
        config.DATA, db.DATA, db.DB_PATH, db._initialized = self.old
        self.tmp.cleanup()

    def get(self, path, **params):
        """Returnerar (status, html eller json)."""
        if REAL:
            r = TestClient(api.app).get(path, params=params)
            return r.status_code, (r.json() if "json" in r.headers.get("content-type", "") else r.text)
        qp = Req(**params)
        route = path.split("?")[0]
        if route.startswith("/kalla/"):
            fn, args = api.app.routes["/kalla/{key}"], {"key": route.split("/")[2]}
        elif route.startswith("/artikel/"):
            fn, args = api.app.routes["/artikel/{article_id}"], {"article_id": int(route.split("/")[2])}
        elif route.startswith("/api/articles/"):
            fn, args = api.app.routes["/api/articles/{article_id}"], {"article_id": int(route.split("/")[3])}
        else:
            fn, args = api.app.routes[route], {}
        import inspect
        sig = inspect.signature(fn).parameters
        kwargs = dict(args)
        if "request" in sig:
            kwargs["request"] = qp
        else:
            kwargs.update({k: v for k, v in params.items() if k in sig})
        try:
            out = fn(**kwargs)
        except api.HTTPException as exc:
            return exc.status_code, exc.detail
        return 200, getattr(out, "body", out)

    def test_start_page(self):
        code, html = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn("Välkommen till Nyhetsarkivet", html)
        self.assertIn("Nordfront", html)                       # profil för källa som inte lagts till
        self.assertIn(">60<", html)

    def test_articles_page_paging_and_page_size(self):
        code, html = self.get("/artiklar")
        self.assertEqual(code, 200)
        self.assertIn("Visar 1-50 av 60", html)
        code, html = self.get("/artiklar", sida=2)
        self.assertIn("Visar 51-60 av 60", html)
        self.assertIn("[2]", html)
        code, html = self.get("/artiklar", per_sida=25, sida=3)
        self.assertIn("Visar 51-60 av 60", html)
        self.assertIn('<option value="25" selected>', html)
        code, html = self.get("/artiklar", sida=999)           # för hög sida: sista sidan, inget fel
        self.assertEqual(code, 200)
        self.assertIn("(sida 2 av 2)", html)
        code, html = self.get("/artiklar", q="Rubrik 7")
        self.assertIn("av 1 träffar", html)

    def test_sources_pages(self):
        code, html = self.get("/kallor")
        self.assertEqual(code, 200)
        self.assertIn("Public service", html)
        code, html = self.get("/kalla/svt")
        self.assertEqual(code, 200)
        self.assertIn("Karin Ekman", html)
        self.assertIn("60 artiklar", html)
        code, html = self.get("/kalla/nordfront")
        self.assertEqual(code, 200)
        self.assertIn("ännu inte tillagd i arkivet", html)
        code, _ = self.get("/kalla/finns-inte")
        self.assertEqual(code, 404)

    def test_article_and_snapshot_pages(self):
        code, html = self.get("/artikel/1")
        self.assertEqual(code, 200)
        self.assertIn("Rubrik 0", html)
        self.assertIn('href="/kalla/svt"', html)
        self.assertIn("Alla artiklar", html)
        code, _ = self.get("/artikel/99999")
        self.assertEqual(code, 404)
        code, html = self.get("/fangster")
        self.assertEqual(code, 200)

    def test_api_endpoints(self):
        code, data = self.get("/api/articles", limit=5, offset=10)
        self.assertEqual(code, 200)
        self.assertEqual((data["total"], len(data["items"]), data["offset"]), (60, 5, 10))
        code, data = self.get("/api/sources")
        names = {s["key"]: s for s in data}
        self.assertTrue(names["svt"]["in_archive"] and names["svt"]["articles"] == 60)
        self.assertFalse(names["nordfront"]["in_archive"])
        self.assertIsInstance(json.dumps(data, default=str), str)
        code, data = self.get("/api/articles/1")
        self.assertEqual(data["title"], "Rubrik 0")
        code, data = self.get("/api/stats")
        self.assertEqual(code, 200)


if __name__ == "__main__":
    unittest.main()
