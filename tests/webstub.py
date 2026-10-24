"""Gemensam testhjälp för webbplatsens rutter.

Med riktig FastAPI (och dess TestClient) används den. Saknas den installeras en minimal ersättning som ändå
renderar de riktiga mallarna och kör de riktiga rutterna, så att kopplingen mellan rutter, logik och mallar testas överallt.
"""
import asyncio
import inspect
import json
import re
import sys
import types
from urllib.parse import parse_qs, urlencode, urlsplit

try:
    import fastapi  # noqa: F401
    from fastapi.testclient import TestClient
    REAL = True
except Exception:
    REAL = False


class Result:
    def __init__(self, status, text="", data=None, location=None):
        self.status, self.text, self.data, self.location = status, text, data, location


def install_stubs():
    from jinja2 import Environment, FileSystemLoader

    class HTTPException(Exception):
        def __init__(self, status_code, detail=""):
            self.status_code, self.detail = status_code, detail

    class Response:
        def __init__(self, body="", status_code=200):
            self.body, self.status_code, self.headers = body, status_code, {}
            self.cookies, self.deleted = {}, []

        def set_cookie(self, key, value, **kw):
            self.cookies[key] = (value, kw)

        def delete_cookie(self, key, **kw):
            self.deleted.append(key)

    class RedirectResponse(Response):
        def __init__(self, url, status_code=307):
            super().__init__("", status_code)
            self.headers["location"] = url

    class FastAPI:
        def __init__(self, **kw):
            self.routes, self.posts = {}, {}

        def get(self, path):
            def deco(fn):
                self.routes[path] = fn
                return fn
            return deco

        def post(self, path):
            def deco(fn):
                self.posts[path] = fn
                return fn
            return deco

        def mount(self, *a, **k):
            pass

    class Jinja2Templates:
        def __init__(self, directory):
            self.env = Environment(loader=FileSystemLoader(directory), autoescape=True)

        def TemplateResponse(self, request, name, context, status_code=200):
            return Response(self.env.get_template(name).render(**context), status_code)

    fa = types.ModuleType("fastapi")
    fa.FastAPI, fa.HTTPException, fa.Query, fa.Request = FastAPI, HTTPException, (lambda default=None, **k: default), object
    rs = types.ModuleType("fastapi.responses"); rs.RedirectResponse = RedirectResponse
    st = types.ModuleType("fastapi.staticfiles"); st.StaticFiles = lambda **k: None
    tp = types.ModuleType("fastapi.templating"); tp.Jinja2Templates = Jinja2Templates
    sys.modules.update({"fastapi": fa, "fastapi.responses": rs, "fastapi.staticfiles": st, "fastapi.templating": tp})


if not REAL:
    install_stubs()


class _FakeRequest:
    def __init__(self, query, form, cookies, ip, scheme="http"):
        self.query_params, self.cookies = query, cookies
        self._body = urlencode(form, doseq=True).encode() if form is not None else b""
        self.headers = {"content-length": str(len(self._body))}
        self.client = types.SimpleNamespace(host=ip)
        self.url = types.SimpleNamespace(scheme=scheme)

    async def body(self):
        return self._body


class Client:
    """Enhetligt gränssnitt över TestClient (riktig FastAPI) eller ersättningen. Håller en egen kakburk."""

    def __init__(self, app_module, ip="testclient"):
        self.api, self.ip = app_module, ip
        self.jar = {}
        self._real = TestClient(app_module.app, follow_redirects=False) if REAL else None

    def set_cookie(self, name, value):
        if REAL:
            self._real.cookies.set(name, value)
        self.jar[name] = value

    def get_cookie(self, name):
        return (self._real.cookies.get(name) if REAL else self.jar.get(name))

    def clear_cookies(self):
        if REAL:
            self._real.cookies.clear()
        self.jar.clear()

    def get(self, path, **params):
        return self._do("GET", path, params, None)

    def post(self, path, data=None):
        return self._do("POST", path, {}, data or {})

    def _do(self, method, path, params, form):
        return self._real_do(method, path, params, form) if REAL else self._stub_do(method, path, params, form)

    def _real_do(self, method, path, params, form):
        r = self._real.get(path, params=params) if method == "GET" else self._real.post(path, data=form)
        is_json = "json" in r.headers.get("content-type", "")
        return Result(r.status_code, r.text, r.json() if is_json else None, r.headers.get("location"))

    def _stub_do(self, method, path, params, form):
        table = self.api.app.routes if method == "GET" else self.api.app.posts
        fn, groups = None, {}
        for pattern, f in table.items():
            m = re.fullmatch(re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern), path)
            if m:
                fn, groups = f, m.groupdict()
                break
        if fn is None:
            return Result(404)
        sig = inspect.signature(fn).parameters
        request = _FakeRequest(dict(params), form, dict(self.jar), self.ip)
        kwargs = {}
        for name, par in sig.items():
            if name == "request":
                kwargs[name] = request
            elif name in groups:
                kwargs[name] = int(groups[name]) if par.annotation is int else groups[name]
            elif name in params:
                kwargs[name] = int(params[name]) if par.annotation is int else params[name]
        try:
            out = fn(**kwargs)
            if inspect.iscoroutine(out):
                out = asyncio.run(out)
        except self.api.HTTPException as exc:
            return Result(exc.status_code, str(exc.detail))
        if hasattr(out, "headers") and hasattr(out, "cookies"):               # Response i ersättningen
            for key, (value, _kw) in out.cookies.items():
                self.jar[key] = value
            for key in out.deleted:
                self.jar.pop(key, None)
            return Result(out.status_code, out.body, None, out.headers.get("location"))
        return Result(200, json.dumps(out, default=str), out)                 # API-svar (dict/lista)
