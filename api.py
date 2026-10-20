"""API (/api/...) och webbplatsen i 2005 års stil. Kör: uvicorn api:app --reload"""
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import db
import profiles as prof
import queries
import webui
from archive_settings import WARC_DIR
from config import ROOT
from sites import SITES

app = FastAPI(title="Nyhetsarkivet API", version="0.3")
WARC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/filer/warc", StaticFiles(directory=WARC_DIR), name="warc")
templates = Jinja2Templates(directory=str(ROOT / "templates"))


# ---- API ---------------------------------------------------------------------

@app.get("/api/articles")
def api_articles(source: str = None, category: str = None, author: str = None,
                 city: str = None, country: str = None, article_type: str = None,
                 topic: str = None, q: str = None,
                 date_from: str = Query(None, alias="from"), date_to: str = Query(None, alias="to"),
                 limit: int = 50, offset: int = 0):
    con = db.connect()
    try:
        total, items = queries.search(con, source, category, author, city, country, article_type,
                                      topic, q, date_from, date_to, limit, offset)
        return {"total": total, "limit": limit, "offset": offset, "items": items}
    finally:
        con.close()


@app.get("/api/articles/{article_id}")
def api_article(article_id: int):
    con = db.connect()
    try:
        r = con.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Artikeln finns inte")
        d = queries.row(r)
        d.pop("raw_path", None)
        d["snapshots"] = queries.snapshots(con, article_id)
        return d
    finally:
        con.close()


@app.get("/api/snapshots")
def api_snapshots(source: str = None, kind: str = None, article_id: int = None,
                  limit: int = 50, offset: int = 0):
    con = db.connect()
    try:
        where, args = ["status='ok'"], []
        for col, val in (("source", source), ("kind", kind), ("article_id", article_id)):
            if val is not None:
                where.append(f"{col}=?")
                args.append(val)
        rows = con.execute("SELECT * FROM snapshots WHERE " + " AND ".join(where) +
                           " ORDER BY taken_at DESC LIMIT ? OFFSET ?",
                           args + [min(limit, 200), offset]).fetchall()
        return [queries.snap(r) for r in rows]
    finally:
        con.close()


@app.get("/api/sources")
def api_sources():
    data = prof.load()
    con = db.connect()
    try:
        entries = webui.build_catalog(SITES, data, webui.source_stats(con))
    finally:
        con.close()
    return [{"key": e["key"], "name": e["name"], "home": e["home"], "type": e["type"], "in_archive": e["in_archive"],
             "political_label": e["political_label"], "web_archive": e["web_archive"],
             "articles": e["stats"].get("articles", 0), "profile": e["profile"]} for e in entries]


@app.get("/api/stats")
def api_stats():
    con = db.connect()
    try:
        by = con.execute("SELECT source,status,COUNT(*) n FROM articles GROUP BY source,status").fetchall()
        latest = con.execute("SELECT MAX(published) FROM articles WHERE status='parsed'").fetchone()[0]
        snaps = con.execute("SELECT source, COUNT(*) n, COALESCE(SUM(bytes),0) bytes FROM snapshots "
                            "WHERE status='ok' GROUP BY source").fetchall()
        return {"by_source_status": [dict(r) for r in by], "latest_published": latest,
                "snapshots": [dict(r) for r in snaps]}
    finally:
        con.close()


# ---- Webbplatsen -------------------------------------------------------------

def _page(request, name, context):
    return templates.TemplateResponse(request, name, {"count": None, **context})


@app.get("/")
def start(request: Request):
    con = db.connect()
    try:
        ctx = webui.start_context(con, SITES, prof.load())
    finally:
        con.close()
    return _page(request, "start.html", ctx)


@app.get("/artiklar")
def articles_page(request: Request):
    con = db.connect()
    try:
        ctx = webui.articles_context(con, dict(request.query_params), SITES)
        ctx["count"] = con.execute("SELECT COUNT(*) FROM articles WHERE status='parsed'").fetchone()[0]
    finally:
        con.close()
    return _page(request, "artiklar.html", {"c": ctx, "count": ctx["count"]})


@app.get("/kallor")
def sources_page(request: Request):
    con = db.connect()
    try:
        ctx = webui.start_context(con, SITES, prof.load())
    finally:
        con.close()
    return _page(request, "kallor.html", ctx)


@app.get("/kalla/{key}")
def source_page(request: Request, key: str):
    con = db.connect()
    try:
        ctx = webui.source_context(con, SITES, prof.load(), key)
    finally:
        con.close()
    if ctx is None:
        raise HTTPException(404, "Källan finns inte")
    return _page(request, "kalla.html", ctx)


@app.get("/artikel/{article_id}")
def article_page(request: Request, article_id: int):
    con = db.connect()
    try:
        r = con.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()
        snaps = queries.snapshots(con, article_id) if r else []
    finally:
        con.close()
    if not r:
        raise HTTPException(404, "Artikeln finns inte")
    a = queries.row(r)
    a["paragraphs"] = [p for p in (a.get("body") or "").split("\n") if p.strip()]
    a["source_name"] = SITES[a["source"]].name if a["source"] in SITES else a["source"]
    return _page(request, "article.html", {"a": a, "snaps": snaps})


@app.get("/fangster")
def snapshots_page(request: Request):
    con = db.connect()
    try:
        rows = con.execute(
            "SELECT s.*, a.title FROM snapshots s LEFT JOIN articles a ON a.id=s.article_id "
            "WHERE s.status='ok' ORDER BY s.taken_at DESC LIMIT 100").fetchall()
        total = con.execute("SELECT COUNT(*), COALESCE(SUM(bytes),0) FROM snapshots WHERE status='ok'").fetchone()
    finally:
        con.close()
    return _page(request, "fangster.html", {"snaps": [queries.snap(r) for r in rows], "n": total[0],
                                            "mb": total[1] / 1024 / 1024})
