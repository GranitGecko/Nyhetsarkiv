"""Databasfrågor som delas av API:t och webbplatsen (ingen webbramverksberoende)."""
import json
import re

from archive_settings import PYWB_URL

LIST_COLS = ("id,source,url,title,ingress,author,published,updated,category,topic,"
             "article_type,city,country,paywalled,image,source_political_label,"
             "(SELECT COUNT(*) FROM snapshots s WHERE s.article_id=articles.id AND s.status='ok') AS archived")


def row(r):
    d = dict(r)
    for k in ("tags", "locations"):
        if d.get(k):
            d[k] = json.loads(d[k])
    return d


def snap(r):
    d = dict(r)
    stamp = re.sub(r"\D", "", (d["taken_at"] or "")[:19])
    if d.get("warc_path"):
        d["warc_url"] = f"/filer/warc/{d['warc_path']}"
        d["replay_url"] = f"{PYWB_URL}/{stamp}/{d['url']}"
    return d


def snapshots(con, article_id):
    rows = con.execute("SELECT * FROM snapshots WHERE article_id=? AND status='ok' ORDER BY taken_at DESC",
                       (article_id,)).fetchall()
    return [snap(r) for r in rows]


def search(con, source=None, category=None, author=None, city=None, country=None,
           article_type=None, topic=None, q=None, date_from=None, date_to=None,
           limit=50, offset=0):
    where, args = ["status='parsed'"], []
    for col, val in (("source", source), ("category", category), ("city", city),
                     ("country", country), ("article_type", article_type), ("topic", topic)):
        if val:
            where.append(f"{col}=?")
            args.append(val)
    if author:
        where.append("author LIKE ?")
        args.append(f"%{author}%")
    if q:
        where.append("(title LIKE ? OR ingress LIKE ?)")
        args += [f"%{q}%", f"%{q}%"]
    if date_from:
        where.append("published >= ?")
        args.append(date_from)
    if date_to:
        where.append("published <= ?")
        args.append(date_to + "~")
    clause = " AND ".join(where)
    total = con.execute(f"SELECT COUNT(*) FROM articles WHERE {clause}", args).fetchone()[0]
    rows = con.execute(
        f"SELECT {LIST_COLS} FROM articles WHERE {clause} "
        "ORDER BY published DESC, id DESC LIMIT ? OFFSET ?",
        args + [min(limit, 200), max(0, offset)]).fetchall()
    return total, [row(r) for r in rows]
