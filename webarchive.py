"""Fullständig arkivering av webbsidor till WARC, som Internet Archive.

Webbläsaren (Playwright) laddar sidan på riktigt, med JavaScript, bilder, CSS och typsnitt.
Playwright spelar in all trafik som HAR; den görs sedan om till WARC-poster.
En WARC-fil per källa och dag. Identiskt innehåll (samma URL + samma innehåll) som redan
finns i dagens fil sparas bara som en liten 'revisit'-post.
"""
import contextlib
import datetime as dt
import json
import os
import re
import time
import uuid

import db
from archive_settings import (ARCHIVE_DELAY, CLICK_CONSENT, DAILY_LIMIT_PER_SITE, DEDUPLICATE, EXTRA_WAIT_SECONDS,
                              FETCH_FAVICON, FORCE_EAGER_LOADING, MAX_SCROLL_STEPS, MAX_WARC_GB, NETWORK_IDLE_SECONDS, TMP_DIR, WARC_DIR)
from config import DATA, USER_AGENT
from harconv import entries_from_har
from warc import WarcWriter, iso, sha1_digest

_CONSENT_RX = re.compile(
    r"^(godkänn alla|acceptera alla|godkänn|acceptera|jag godkänner|tillåt alla|accept all|i agree|agree)\b",
    re.I)

_EAGER_JS = """() => {
  document.querySelectorAll('img[loading="lazy"], iframe[loading="lazy"]').forEach(e => e.setAttribute('loading', 'eager'));
  document.querySelectorAll('img[data-src], img[data-srcset], source[data-srcset]').forEach(e => {
    if (e.dataset.src) e.setAttribute('src', e.dataset.src);
    if (e.dataset.srcset) e.setAttribute('srcset', e.dataset.srcset);
  });
}"""

_FAVICON_JS = """() => Promise.all(
  [...document.querySelectorAll('link[rel~="icon"], link[rel="apple-touch-icon"]')].map(l => new Promise(done => {
    const img = new Image();
    img.onload = img.onerror = () => done();
    img.src = l.href;
    setTimeout(done, 5000);
  })))"""

_SCROLL_JS = """async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  let y = 0, steps = 0;
  while (y < document.body.scrollHeight && steps < __STEPS__) {
    window.scrollTo(0, y); await sleep(250); y += 800; steps++;
  }
  window.scrollTo(0, 0);
}"""


def write_snapshot(con, warc_rel, entries, now):
    """Skriv alla poster för en fångst till dagens WARC-fil. Returnerar statistik."""
    w = WarcWriter(WARC_DIR / warc_rel)
    start = w.pos
    local, pending = {}, []
    n_resp = n_rev = 0
    try:
        if start == 0:
            w.warcinfo({"software": "Nyhetsarkivet/0.1", "format": "WARC File Format 1.0",
                        "description": "Webbarkiv av svenska nyhetssajter"}, now)
        for e in entries:
            body = e["body"]
            digest = sha1_digest(body)
            ref = local.get((e["url"], digest)) if (body and DEDUPLICATE) else None
            if ref is None and body and DEDUPLICATE:
                row = con.execute(
                    "SELECT record_id, rec_date FROM warc_seen WHERE warc_path=? AND url=? AND digest=?",
                    (warc_rel, e["url"], digest)).fetchone()
                if row:
                    ref = (row["record_id"], row["rec_date"])
            if ref:
                w.revisit(e["url"], e["date"], e["status"], e["reason"], e["headers"],
                          len(body), digest, ref[0], ref[1])
                n_rev += 1
            else:
                rid = w.response(e["url"], e["date"], e["status"], e["reason"], e["headers"],
                                 body, digest)
                n_resp += 1
                if body:
                    local[(e["url"], digest)] = (rid, iso(e["date"]))
                    pending.append((warc_rel, e["url"], digest, rid, iso(e["date"])))
    finally:
        w.close()
    # Registrera först när filen är skriven, så att en revisit aldrig pekar på en post som saknas.
    con.executemany("INSERT OR IGNORE INTO warc_seen VALUES (?,?,?,?,?)", pending)
    con.commit()
    return {"offset": start, "length": w.pos - start, "bytes": w.pos - start,
            "n_records": n_resp + n_rev, "n_revisits": n_rev}


def click_consent(page):
    for frame in page.frames:
        try:
            btn = frame.get_by_role("button", name=_CONSENT_RX).first
            if btn.count() and btn.is_visible():
                btn.click(timeout=2000)
                return True
        except Exception:
            continue
    return False


def _user_agent(browser):
    ctx = browser.new_context()
    ua = ctx.new_page().evaluate("navigator.userAgent")
    ctx.close()
    return f"{ua} {USER_AGENT}"


def _browse(browser, ua, site, url, har_path):
    from playwright.sync_api import TimeoutError as PWTimeout
    ctx = browser.new_context(
        viewport={"width": 1366, "height": 900}, locale="sv-SE", timezone_id="Europe/Stockholm",
        user_agent=ua, service_workers="block",
        record_har_path=str(har_path), record_har_content="embed")
    status = None
    try:
        page = ctx.new_page()
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        status = resp.status if resp else None
        try:
            page.wait_for_load_state("load", timeout=15000)
        except PWTimeout:
            pass
        if CLICK_CONSENT:
            if site.dismiss_consent(page) or click_consent(page):
                page.wait_for_timeout(1000)
        if FORCE_EAGER_LOADING:
            page.evaluate(_EAGER_JS)                   # ladda lazy-bilder även utanför bildområdet
        page.evaluate(_SCROLL_JS.replace("__STEPS__", str(MAX_SCROLL_STEPS)))   # utlös lazy-loadade bilder
        if FORCE_EAGER_LOADING:
            page.evaluate(_EAGER_JS)                   # sådant som skripten lagt till under scrollningen
        try:
            page.wait_for_load_state("networkidle", timeout=int(NETWORK_IDLE_SECONDS * 1000))
        except PWTimeout:
            pass
        if FETCH_FAVICON:
            page.evaluate(_FAVICON_JS)                 # Chromium utan fönster hämtar aldrig favicon själv
        page.wait_for_timeout(int(EXTRA_WAIT_SECONDS * 1000))                   # sena skript och bilder
    finally:
        ctx.close()                                     # skriver ut HAR-filen
    if status and status >= 400:
        raise RuntimeError(f"HTTP {status}")
    return status


def capture_one(browser, ua, site, con, kind, article_id, url):
    taken = dt.datetime.now(dt.timezone.utc)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    har = TMP_DIR / f"{site.key}-{uuid.uuid4().hex}.har"
    try:
        status = _browse(browser, ua, site, url, har)
        detail = []
        entries, skipped = entries_from_har(json.loads(har.read_text(encoding="utf-8")), detail=detail)
        if not entries:
            raise RuntimeError("inga resurser fångades")
        rel = f"{site.key}/{taken:%Y-%m-%d}.warc.gz"
        s = write_snapshot(con, rel, entries, taken)
        con.execute(
            "INSERT INTO snapshots(source,kind,article_id,url,taken_at,status,http_status,warc_path,"
            "warc_offset,warc_length,n_records,n_revisits,bytes,notes) VALUES (?,?,?,?,?,'ok',?,?,?,?,?,?,?,?)",
            (site.key, kind, article_id, url, iso(taken), status, rel, s["offset"], s["length"],
             s["n_records"], s["n_revisits"], s["bytes"],
             json.dumps({"counts": skipped, "urls": detail}, ensure_ascii=False)))
        con.commit()
        s["skipped"] = skipped
        return s
    except Exception as exc:
        con.execute("INSERT INTO snapshots(source,kind,article_id,url,taken_at,status,error) "
                    "VALUES (?,?,?,?,?,'error',?)",
                    (site.key, kind, article_id, url, iso(taken), str(exc)[:300]))
        con.commit()
        return {"error": str(exc)}
    finally:
        with contextlib.suppress(OSError):
            har.unlink()


@contextlib.contextmanager
def _lock(key):
    """Förhindrar att två körningar skriver till samma sajts WARC-fil samtidigt."""
    path = DATA / f"archive-{key}.lock"
    if path.exists() and time.time() - path.stat().st_mtime > 3 * 3600:
        path.unlink()                                   # gammalt lås efter en krasch
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        yield False
        return
    os.close(fd)
    try:
        yield True
    finally:
        with contextlib.suppress(OSError):
            path.unlink()


def warc_bytes():
    if not WARC_DIR.exists():
        return 0
    return sum(p.stat().st_size for p in WARC_DIR.rglob("*.warc.gz"))


def stop_reason(con, key, deadline, daily_limit=None):
    """Varför arkiveringen ska stanna nu, eller None om den får fortsätta."""
    if deadline is not None and time.monotonic() > deadline:
        return "tidsgränsen nådd"
    gb = warc_bytes() / 1e9
    if gb >= MAX_WARC_GB:
        return f"WARC-mappen är {gb:.1f} GB, gränsen är {MAX_WARC_GB} GB"
    today = f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%d}%"
    n = con.execute("SELECT COUNT(*) FROM snapshots WHERE source=? AND status='ok' AND taken_at LIKE ?",
                    (key, today)).fetchone()[0]
    limit = daily_limit or DAILY_LIMIT_PER_SITE
    if n >= limit:
        return f"dagens gräns på {limit} fångster är nådd"
    return None


def archive_site(key, limit=20, front=True, articles=True, minutes=None, since=None, until=None, ids=None):
    from net import Fetcher
    from playwright.sync_api import sync_playwright
    from sites import SITES

    site = SITES[key]
    if not getattr(site, "archive_enabled", True):
        print(f"{key}: webbarkivering är avstängd för denna sajt")
        return
    delay = getattr(site, "archive_delay", None) or ARCHIVE_DELAY
    deadline = time.monotonic() + minutes * 60 if minutes else None
    con = db.connect()
    with _lock(key) as ok:
        if not ok:
            print(f"{key}: en arkivering pågår redan, hoppar över")
            return
        todo = []
        if front and not (since or until or ids):          # startsidan är 'nu', inte ett förflutet tidsfönster
            todo.append(("front", None, site.home))
        if ids:                                    # arkivera om vissa artiklar, även om de redan är arkiverade
            marks = ",".join("?" for _ in ids)
            rows = con.execute(f"SELECT id, url FROM articles WHERE source=? AND id IN ({marks}) "
                               "AND status IN ('fetched','parsed')", (key, *ids)).fetchall()
            todo += [("article", r["id"], r["url"]) for r in rows]
        elif articles:
            # Samma fönster som vid wayback/fetch: det datum artikeln hittades för (hint_date), annars publiceringsdatum.
            when = "COALESCE(a.hint_date, a.published, a.discovered_at)"
            extra, eparams = "", []
            if since:
                extra += f" AND {when} >= ?"
                eparams.append(since)
            if until:
                extra += f" AND {when} < ?"
                eparams.append(until)
            rows = con.execute(
                "SELECT a.id, a.url FROM articles a WHERE a.source=? AND a.status IN ('fetched','parsed')" + extra + " "
                "AND NOT EXISTS (SELECT 1 FROM snapshots s WHERE s.article_id=a.id AND s.status='ok') "
                "AND (SELECT COUNT(*) FROM snapshots s WHERE s.article_id=a.id AND s.status='error') < 3 "
                "ORDER BY COALESCE(a.published, a.hint_date, a.discovered_at) DESC, a.id DESC LIMIT ?",
                (key, *eparams, limit)).fetchall()
            todo += [("article", r["id"], r["url"]) for r in rows]
        fetcher = Fetcher()
        done = 0
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            ua = _user_agent(browser)
            for i, (kind, aid, url) in enumerate(todo, 1):
                why = stop_reason(con, key, deadline, getattr(site, "daily_limit", None))
                if why:
                    print(f"{key}: stoppar ({why}); {len(todo) - i + 1} sidor väntar till nästa körning")
                    break
                if not fetcher.allowed(url):
                    print(f"{key}: [{i}/{len(todo)}] robots.txt tillåter inte {url}")
                    continue
                print(f"{key}: [{i}/{len(todo)}] arkiverar {url[:100]} ...", flush=True)
                res = capture_one(browser, ua, site, con, kind, aid, url)
                if "error" in res:
                    print(f"{key}: [{i}/{len(todo)}] FEL {kind} {url}: {res['error']}")
                else:
                    done += 1
                    skipped = ", ".join(f"{k} {v}" for k, v in sorted(res["skipped"].items()))
                    what = f"{kind} #{aid}" if aid else kind
                    print(f"{key}: [{i}/{len(todo)}] {what}: {res['n_records']} poster "
                          f"({res['n_revisits']} dubbletter), {res['bytes'] / 1e6:.2f} MB"
                          + (f"; hoppade över: {skipped}" if skipped else ""))
                time.sleep(delay)
            browser.close()
        print(f"{key}: klart, {done} av {len(todo)} fångster")
