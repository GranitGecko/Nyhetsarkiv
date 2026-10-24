"""Statisk version av webbplatsen för GitHub Pages (eller annan statisk värd). Inga skript, bara HTML-filer.

Standard: startsida, källöversikt och en sida per källa (profiler med källhänvisningar). Artikeltexter, ingresser och
webbarkiv ingår aldrig. Med with_articles läggs även listor med rubrik, datum och länk till originalet till (ingen text).
"""
import datetime as dt
import json
import math
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

import webui
from config import ROOT

MARKER = ".nyhetsarkivet-export"


class ExportError(Exception):
    pass


def slug(key):
    return re.sub(r"[^a-z0-9_-]+", "-", str(key).lower()).strip("-") or "kalla"


def _slugs(entries):
    out, used = {}, set()
    for e in entries:
        base = slug(e["key"])
        s, n = base, 2
        while s in used:
            s, n = f"{base}-{n}", n + 1
        used.add(s)
        out[e["key"]] = s
    return out


def _clean_previous(out):
    manifest = out / MARKER
    if not manifest.exists():
        return
    try:
        old = json.loads(manifest.read_text(encoding="utf-8"))
    except ValueError:
        old = []
    for rel in old:
        path = (out / rel).resolve()
        if out.resolve() in path.parents and path.is_file():
            path.unlink()
    dirs = set()
    for rel in old:
        for parent in (out / rel).parents:
            if parent == out or out not in parent.parents:
                break
            dirs.add(parent)
    for d in sorted(dirs, key=lambda p: -len(p.parts)):          # tomma undermappar, djupast först
        try:
            d.rmdir()
        except OSError:
            pass


def _rows(con, source=None):
    sql = ("SELECT id, source, url, title, published, article_type, category, author, paywalled FROM articles "
           "WHERE status='parsed'" + (" AND source=?" if source else "") + " ORDER BY published DESC, id DESC")
    rows = []
    for r in con.execute(sql, (source,) if source else ()):
        d = dict(r)
        d["link"] = d["url"] if str(d["url"]).startswith(("http://", "https://")) else None
        rows.append(d)
    return rows


def export_site(con, sites, data, out_dir, with_articles=False, per_page=100, repo_url=None, force=False, today=None):
    out = Path(out_dir)
    if out.exists() and any(out.iterdir()) and not (out / MARKER).exists() and not force:
        raise ExportError(f"{out} finns och innehåller filer som inte kommer från en tidigare export. "
                          "Välj en tom mapp, eller kör med --force om du vet att innehållet kan skrivas över.")
    _clean_previous(out)
    out.mkdir(parents=True, exist_ok=True)
    env = Environment(loader=FileSystemLoader(str(ROOT / "templates")), autoescape=select_autoescape(["html"]))
    ctx = webui.start_context(con, sites, data)
    entries = [e for _, group in ctx["groups"] for e in group]
    slugs = _slugs(entries)
    names = {e["key"]: e["name"] for e in entries}
    common = {"with_articles": with_articles, "repo_url": repo_url, "slugs": slugs, "names": names,
              "generated": (today or dt.date.today()).isoformat(), "noindex": False}
    written = []

    def write(rel, html):
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        written.append(rel)

    def render(name, root, **extra):
        return env.get_template(name).render(root=root, **{**common, **extra})

    write("index.html", render("static/start.html", "", **ctx))
    write("kallor.html", render("static/kallor.html", "", **ctx))
    counts = {}
    for e in entries:
        recent = []
        if with_articles and e["in_archive"]:
            recent = _rows(con, e["key"])
            counts[e["key"]] = len(recent)
            recent = recent[:10]
        write(f"kalla/{slugs[e['key']]}.html",
              render("static/kalla.html", "../", e=e, p=e["profile"], recent=recent, checked=ctx["checked"]))

    def write_list(scope_dir, scope_name, rows):
        pages = max(1, math.ceil(len(rows) / per_page))
        for page in range(1, pages + 1):
            chunk = rows[(page - 1) * per_page: page * per_page]
            c = {"rows": chunk, "total": len(rows), "page": page, "pages": pages,
                 "first": (page - 1) * per_page + 1 if rows else 0, "last": min(len(rows), page * per_page),
                 "pager": webui.pager(page, pages, lambda p: f"{p}.html")}
            write(f"artiklar/{scope_dir}/{page}.html",
                  render("static/artiklar.html", "../../", c=c, scope_name=scope_name, noindex=True))

    if with_articles:
        write_list("alla", "alla källor", _rows(con))
        for e in entries:
            if counts.get(e["key"]):
                write_list(slugs[e["key"]], e["name"], _rows(con, e["key"]))
    write(".nojekyll", "")
    (out / MARKER).write_text(json.dumps(written + [MARKER], ensure_ascii=False), encoding="utf-8")
    return {"out": str(out), "pages": len([w for w in written if w.endswith(".html")]), "files": written}
