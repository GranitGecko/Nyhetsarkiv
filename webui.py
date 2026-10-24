"""Logik för webbplatsen (startsida, källkatalog, artikellista med sidnavigering). Inget webbramverk här."""
import math
from urllib.parse import urlencode

import profiles as prof
from queries import search

PER_PAGE_OPTIONS = (25, 50, 100, 200)
DEFAULT_PER_PAGE = 50
TYPE_ORDER = ["Public service", "Rikstäckande dagspress", "Regional och lokal press", "Nätmedia och opinion",
              "Partinära media", "Organisationsmedia", "Faktagranskning"]
NO_PROFILE = "Utan profil"
FILTERS = ("kalla", "typ", "kategori", "forfattare", "stad", "q")


def to_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def per_page(value):
    n = to_int(value, DEFAULT_PER_PAGE)
    return n if n in PER_PAGE_OPTIONS else DEFAULT_PER_PAGE


def pager(page, pages, url_for):
    """Sidnavigering: första, 5 bakåt, föregående, sidnummer, nästa, 5 framåt, sista."""
    def item(label, target, title):
        return {"label": label, "title": title, "current": False,
                "url": url_for(target) if target is not None else None}
    back, fwd = page > 1, page < pages
    out = [item("« Första", 1 if back else None, "Till första sidan"),
           item("-5", max(1, page - 5) if back else None, "5 sidor bakåt"),
           item("‹ Föregående", page - 1 if back else None, "Föregående sida")]
    last = 0
    for n in sorted({1, pages, *range(max(1, page - 3), min(pages, page + 3) + 1)}):
        if n - last > 1:
            out.append({"label": "...", "url": None, "current": False, "title": ""})
        out.append({"label": str(n), "url": None if n == page else url_for(n), "current": n == page,
                    "title": f"Sida {n}"})
        last = n
    out += [item("Nästa ›", page + 1 if fwd else None, "Nästa sida"),
            item("+5", min(pages, page + 5) if fwd else None, "5 sidor framåt"),
            item("Sista »", pages if fwd else None, "Till sista sidan")]
    return out


def source_stats(con):
    stats = {}
    for r in con.execute("SELECT source, COUNT(*) n, MIN(published) first, MAX(published) last "
                         "FROM articles WHERE status='parsed' GROUP BY source"):
        stats[r["source"]] = {"articles": r["n"], "first": r["first"], "last": r["last"], "snapshots": 0}
    for r in con.execute("SELECT source, COUNT(*) n FROM snapshots WHERE status='ok' GROUP BY source"):
        stats.setdefault(r["source"], {"articles": 0, "first": None, "last": None})["snapshots"] = r["n"]
    return stats


def build_catalog(sites, data, stats):
    """En post per källa: sajterna i arkivet, ihopkopplade med profiler via webbadress, plus profiler för
    källor som ännu inte lagts till."""
    profiles = data.get("profiles", {})
    by_host = {prof.host_key(p["home"]): (k, p) for k, p in profiles.items()}
    entries, used = [], set()
    for site in sites.values():
        host = prof.host_key(site.home)
        pkey, p = by_host.get(host, (None, None))
        used.add(host)
        entries.append({"key": site.key, "name": (p or {}).get("name") or site.name, "home": site.home,
                        "type": (p or {}).get("type") or NO_PROFILE, "profile": p, "in_archive": True,
                        "political_label": site.political_label, "stats": stats.get(site.key, {}),
                        "web_archive": getattr(site, "archive_enabled", True)})
    for host, (pkey, p) in by_host.items():
        if host not in used:
            entries.append({"key": pkey, "name": p["name"], "home": p["home"], "type": p["type"], "profile": p,
                            "in_archive": False, "political_label": None, "stats": {}, "web_archive": False})
    return sorted(entries, key=lambda e: e["name"].lower())


def group_catalog(entries):
    groups = {}
    for e in entries:
        groups.setdefault(e["type"], []).append(e)
    order = [t for t in TYPE_ORDER if t in groups] + sorted(t for t in groups if t not in TYPE_ORDER and t != NO_PROFILE)
    if NO_PROFILE in groups:
        order.append(NO_PROFILE)
    return [(t, groups[t]) for t in order]


def start_context(con, sites, data):
    entries = build_catalog(sites, data, source_stats(con))
    total = con.execute("SELECT COUNT(*) FROM articles WHERE status='parsed'").fetchone()[0]
    snaps = con.execute("SELECT COUNT(*), COALESCE(SUM(bytes),0) FROM snapshots WHERE status='ok'").fetchone()
    latest = con.execute("SELECT MAX(published) FROM articles WHERE status='parsed'").fetchone()[0]
    return {"groups": group_catalog(entries), "n_sources": len(entries),
            "n_with_articles": sum(1 for e in entries if e["stats"].get("articles")),
            "total": total, "n_snapshots": snaps[0], "warc_mb": snaps[1] / 1024 / 1024, "latest": latest,
            "checked": data.get("checked", "")}


def source_context(con, sites, data, key):
    entries = build_catalog(sites, data, source_stats(con))
    entry = next((e for e in entries if e["key"] == key), None)
    if entry is None:
        return None
    recent = search(con, source=key, limit=10)[1] if entry["in_archive"] else []
    return {"e": entry, "p": entry["profile"], "recent": recent, "checked": data.get("checked", "")}


def admin_context(con):
    status = {r["status"]: r["n"] for r in con.execute("SELECT status, COUNT(*) n FROM articles GROUP BY status")}
    log = con.execute("SELECT at, actor, action, detail, ip FROM admin_log ORDER BY id DESC LIMIT 30").fetchall()
    deleted = con.execute("SELECT id, source, url, deleted_at FROM articles WHERE status='deleted' "
                          "ORDER BY deleted_at DESC, id DESC LIMIT 20").fetchall()
    return {"status": status, "log": [dict(r) for r in log], "deleted": [dict(r) for r in deleted]}


def articles_context(con, params, sites):
    per = per_page(params.get("per_sida"))
    filters = {k: (params.get(k) or "").strip() for k in FILTERS}
    page = max(1, to_int(params.get("sida"), 1))

    def run(p):
        return search(con, source=filters["kalla"] or None, category=filters["kategori"] or None,
                      author=filters["forfattare"] or None, city=filters["stad"] or None,
                      article_type=filters["typ"] or None, q=filters["q"] or None, limit=per, offset=(p - 1) * per)
    total, items = run(page)
    pages = max(1, math.ceil(total / per))
    if page > pages:
        page = pages
        total, items = run(page)
    base = {**{k: v for k, v in filters.items() if v}, "per_sida": per}
    url_for = lambda p: "/artiklar?" + urlencode({**base, "sida": p})
    types = [r[0] for r in con.execute(
        "SELECT DISTINCT article_type FROM articles WHERE article_type IS NOT NULL ORDER BY 1")]
    return {"rows": items, "total": total, "page": page, "pages": pages, "per": per,
            "per_options": PER_PAGE_OPTIONS, "first": (page - 1) * per + 1 if total else 0,
            "last": min(total, page * per), "pager": pager(page, pages, url_for), "filters": filters,
            "types": types, "sources": sorted(((s.key, s.name) for s in sites.values()), key=lambda x: x[1].lower())}
