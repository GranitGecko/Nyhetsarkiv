"""Kommandorad: python cli.py <kommando>   (kör `python cli.py hjälp` för guiden)"""
import argparse
import sys
import threading
import datetime as dt
import gzip
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import db
import enrich
from config import RAW_DIR
from discover import (backfill_site, discover_site, extract_links, page_links, parse_sitemap, sitemap_roots,
                      snowball_site, wayback_site)
from net import Fetcher
from progress import Progress
from sites import BUILTIN, SITES

COLS = ["canonical_url", "title", "ingress", "body", "author", "published", "updated",
        "category", "topic", "article_type", "image", "lang", "paywalled",
        "country", "city", "source_political_label"]
DEFAULT_LIMIT = {"fetch": 50, "run": 50, "archive": 20, "all": 20}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def selected(args):
    keys = getattr(args, "site", None) or list(SITES)
    unknown = [k for k in keys if k not in SITES]
    if unknown:
        raise SystemExit(f"Okänd sajt: {', '.join(unknown)}. Kör `python cli.py sites` för listan.")
    return [SITES[k] for k in keys]


CANCEL = threading.Event()          # sätts vid Ctrl+C så att arbetstrådarna avslutar snyggt


def parallel(fn, sites):
    ex = ThreadPoolExecutor(max_workers=max(1, min(len(sites), 16)))
    try:
        list(ex.map(fn, sites))
    except KeyboardInterrupt:
        CANCEL.set()
        sys.stderr.write("\nAvbryter: slutför pågående anrop ...\n")
        ex.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        ex.shutdown()


def cmd_discover(args):
    fetcher = Fetcher()

    def work(site):
        con = db.connect()
        print(f"{site.key}: {discover_site(con, site, fetcher)} nya URL:er")
        con.close()
    parallel(work, selected(args))


def _window(args, expr):
    """SQL-villkor för --since/--until (datum som texter, t.ex. 2026-09-01)."""
    sql, params = "", []
    if getattr(args, "since", None):
        sql += f" AND {expr} >= ?"
        params.append(args.since)
    if getattr(args, "until", None):
        sql += f" AND {expr} < ?"
        params.append(args.until)
    return sql, params


def cmd_fetch(args):
    fetcher = Fetcher()
    sites = selected(args)
    plan = {}
    con0 = db.connect()
    for site in sites:
        extra, eparams = _window(args, "COALESCE(hint_date, discovered_at)")
        plan[site.key] = con0.execute(
            "SELECT id,url FROM articles WHERE source=? AND status='discovered'" + extra +
            " ORDER BY COALESCE(hint_date, discovered_at) DESC, id DESC LIMIT ?",
            (site.key, *eparams, args.limit)).fetchall()
    con0.close()
    total = sum(len(r) for r in plan.values())
    if not total:
        print("Inget att hämta: kön är tom för valda sajter/fönster. (discover, wayback och backfill fyller på kön.)")
        return
    prog = Progress(total, "Hämtar")

    def work(site):
        con = db.connect()
        rows, done = plan[site.key], 0
        for r in rows:
            if CANCEL.is_set():
                break
            prog.set_current(f"{site.key}: {r['url']}")
            failed = True
            try:
                if not fetcher.allowed(r["url"]):
                    con.execute("UPDATE articles SET status='blocked' WHERE id=?", (r["id"],))
                    con.commit()
                    continue
                try:
                    resp = fetcher.get(r["url"])
                except Exception as exc:
                    con.execute("UPDATE articles SET status='error', error=? WHERE id=?", (str(exc)[:300], r["id"]))
                    con.commit()
                    continue
                if resp.status_code != 200:
                    con.execute("UPDATE articles SET status='error', http_status=?, fetched_at=? WHERE id=?",
                                (resp.status_code, now(), r["id"]))
                    con.commit()
                    continue
                html = resp.text
                digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
                path = RAW_DIR / site.key / digest[:2] / f"{digest}.html.gz"
                path.parent.mkdir(parents=True, exist_ok=True)
                with gzip.open(path, "wt", encoding="utf-8") as fh:
                    fh.write(html)
                con.execute("UPDATE articles SET status='fetched', http_status=200, fetched_at=?, "
                            "content_hash=?, raw_path=? WHERE id=?",
                            (now(), digest, str(path.relative_to(RAW_DIR)), r["id"]))
                con.commit()
                done += 1
                failed = False
            finally:
                prog.advance(error=failed)
        prog.log(f"{site.key}: hämtade {done} av {len(rows)}")
        con.close()

    try:
        parallel(work, sites)
    finally:
        prog.close()
    print(prog.summary())


def cmd_parse(args):
    sites = selected(args)
    plan = {}
    con0 = db.connect()
    for site in sites:
        if args.reparse:
            con0.execute("UPDATE articles SET status='fetched' WHERE source=? AND status='parsed'", (site.key,))
            con0.commit()
        plan[site.key] = con0.execute("SELECT id,url,raw_path FROM articles WHERE source=? AND status='fetched'",
                                      (site.key,)).fetchall()
    con0.close()
    total = sum(len(r) for r in plan.values())
    if not total:
        print("Inget att tolka: inga hämtade artiklar väntar.")
        return
    prog = Progress(total, "Tolkar")

    def work(site):
        con = db.connect()
        rows, ok = plan[site.key], 0
        for r in rows:
            if CANCEL.is_set():
                break
            prog.set_current(f"{site.key}: {r['url']}")
            failed = True
            try:
                with gzip.open(RAW_DIR / r["raw_path"], "rt", encoding="utf-8") as fh:
                    html = fh.read()
                data = site.parse(html, r["url"])
                data.update(enrich.enrich(data))
                values = [data.get(c) for c in COLS]
                con.execute(
                    f"UPDATE articles SET {','.join(c + '=?' for c in COLS)}, tags=?, locations=?, "
                    "status='parsed', parsed_at=?, error=NULL WHERE id=?",
                    values + [json.dumps(data.get("tags") or [], ensure_ascii=False),
                              json.dumps(data.get("locations") or [], ensure_ascii=False),
                              now(), r["id"]])
                ok += 1
                failed = False
            except Exception as exc:
                con.execute("UPDATE articles SET status='error', error=? WHERE id=?",
                            (f"parse: {exc}"[:300], r["id"]))
            con.commit()
            prog.advance(error=failed)
        prog.log(f"{site.key}: tolkade {ok} av {len(rows)}")
        con.close()

    try:
        parallel(work, sites)
    finally:
        prog.close()
    print(prog.summary())


def cmd_run(args):
    cmd_discover(args)
    cmd_fetch(args)
    cmd_parse(args)


def cmd_archive(args):
    """Fullständig WARC-arkivering. En process per sajt (Playwright delas inte mellan trådar)."""
    from concurrent.futures import ProcessPoolExecutor

    import webarchive
    if getattr(args, "again", None) and not args.site:
        raise SystemExit("--again kräver --site, t.ex.: python cli.py archive --site svt --again 2011 2020")
    db.connect().close()                 # initiera databasen innan processerna startar
    sites = selected(args)
    from archive_settings import MAX_PARALLEL
    with ProcessPoolExecutor(max_workers=max(1, min(len(sites), MAX_PARALLEL))) as ex:
        g = lambda name: getattr(args, name, None)          # tål saknade flaggor när funktionen anropas direkt
        futures = [ex.submit(webarchive.archive_site, s.key, g("limit") or 20,
                             not g("no_front"), not g("front_only"), g("minutes"),
                             g("since"), g("until"), g("again")) for s in sites]
        for f in futures:
            f.result()


def cmd_all(args):
    cmd_run(args)
    cmd_archive(args)


def _dir_mb(path, pattern="*"):
    if not path.exists():
        return 0.0
    return sum(p.stat().st_size for p in path.rglob(pattern) if p.is_file()) / 1e6


def cmd_status(args):
    """Översikt: vad som finns, vad som väntar och hur mycket plats det tar."""
    import archive_settings as cfg
    con = db.connect()
    today = f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%d}%"
    print(f"{'sajt':16}{'totalt':>8}{'att hämta':>11}{'tolkade':>9}{'arkiverade':>12}{'väntar':>10}{'idag':>6}")
    waiting_total = 0
    for site in selected(args):
        k = site.key
        total = con.execute("SELECT COUNT(*) FROM articles WHERE source=?", (k,)).fetchone()[0]
        parsed = con.execute("SELECT COUNT(*) FROM articles WHERE source=? AND status='parsed'", (k,)).fetchone()[0]
        to_fetch = con.execute("SELECT COUNT(*) FROM articles WHERE source=? AND status='discovered'",
                               (k,)).fetchone()[0]
        done = con.execute("SELECT COUNT(DISTINCT article_id) FROM snapshots WHERE source=? AND kind='article' "
                           "AND status='ok'", (k,)).fetchone()[0]
        if site.archive_enabled:
            waiting = con.execute(
                "SELECT COUNT(*) FROM articles a WHERE a.source=? AND a.status IN ('fetched','parsed') "
                "AND NOT EXISTS (SELECT 1 FROM snapshots s WHERE s.article_id=a.id AND s.status='ok')",
                (k,)).fetchone()[0]
        else:
            waiting = None
        today_n = con.execute("SELECT COUNT(*) FROM snapshots WHERE source=? AND status='ok' AND taken_at LIKE ?",
                              (k, today)).fetchone()[0]
        waiting_total += waiting or 0
        print(f"{k:16}{total:>8}{to_fetch:>11}{parsed:>9}{done:>12}{('avstängd' if waiting is None else waiting):>10}{today_n:>6}")
    warc_mb, raw_mb = _dir_mb(cfg.WARC_DIR, "*.warc.gz"), _dir_mb(RAW_DIR)
    print(f"\nDisk: webbarkiv {warc_mb:.0f} MB av {cfg.MAX_WARC_GB * 1000:.0f} MB tillåtet, rå-HTML {raw_mb:.0f} MB")
    avg = con.execute("SELECT AVG(bytes) FROM snapshots WHERE kind='article' AND status='ok'").fetchone()[0]
    if avg:
        print(f"Snitt per arkiverad artikel: {avg / 1e6:.2f} MB")
        if waiting_total:
            print(f"Att arkivera alla {waiting_total} väntande: ungefär {waiting_total * avg / 1e6:.0f} MB "
                  f"(blir oftast lite mindre, dubbletterna ökar ju fler fångster per dag)")
    print(f"Gränser: {cfg.DAILY_LIMIT_PER_SITE} fångster per sajt och dygn, {cfg.MAX_WARC_GB} GB totalt")


def cmd_probe(args):
    """Undersök vilka sitemaps sajterna har och hur långt bakåt de sträcker sig."""
    fetcher = Fetcher()
    for site in selected(args):
        try:
            roots = sitemap_roots(fetcher, site)
        except Exception as exc:
            print(f"{site.key}: kunde inte läsa robots.txt ({exc})")
            continue
        print(f"{site.key}: {len(roots)} sitemap(s)")
        for root in roots[:6]:
            try:
                resp = fetcher.get(root)
                if resp.status_code != 200:
                    print(f"  {root}: HTTP {resp.status_code}")
                    continue
                kind, entries = parse_sitemap(resp.content)
            except Exception as exc:
                print(f"  {root}: FEL {exc}")
                continue
            dates = sorted(d for _, d in entries if d)
            hits = sum(1 for loc, _ in entries if site.is_article_url(loc))
            span = f"{dates[0][:10]} .. {dates[-1][:10]}" if dates else "saknar datum"
            print(f"  {root}\n    typ={kind} poster={len(entries)} datum={span} artikel-URL:er={hits}")
            for loc, d in entries[:3]:
                print("      ", loc, d or "")


def cmd_backfill(args):
    fetcher = Fetcher()

    def work(site):
        con = db.connect()
        try:
            r = backfill_site(con, site, fetcher, args.since, args.max_sitemaps)
        except Exception as exc:
            print(f"{site.key}: FEL {exc}")
            return
        more = f", {r['left']} sitemaps kvar (höj --max-sitemaps)" if r["left"] else ""
        print(f"{site.key}: {r['sitemaps']} sitemaps lästa, {r['urls']} artikel-URL:er funna, "
              f"{r['new']} nya{more}")
        con.close()
    parallel(work, selected(args))


def cmd_wayback(args):
    fetcher = Fetcher()

    def work(site):
        con = db.connect()
        try:
            r = wayback_site(con, site, fetcher, args.since, args.until, args.max)
        except Exception as exc:
            print(f"{site.key}: FEL {exc}")
            return
        more = " (fler finns: smalna av tidsfönstret med --since/--until)" if r["more"] else ""
        print(f"{site.key}: {r['scanned']} URL:er från Wayback, {r['articles']} artikel-URL:er, "
              f"{r['new']} nya{more}")
        con.close()
    parallel(work, selected(args))


def cmd_snowball(args):
    def work(site):
        con = db.connect()
        r = snowball_site(con, site, args.pages)
        print(f"{site.key}: genomsökte {r['scanned']} sparade artiklar, {r['new']} nya artikellänkar")
        con.close()
    parallel(work, selected(args))


def cmd_contact(args):
    import json
    import config
    config.DATA.mkdir(parents=True, exist_ok=True)
    path = config.DATA / "settings.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data["user_agent"] = f"Nyhetsarkivet/0.1 (+kontakt: {args.email})"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Sparat i {path}: {data['user_agent']}")


def cmd_profiles(args):
    """Visa vilka källor som saknar profil, vilka profiler som bör kontrolleras och eventuella fel i profildata."""
    import profiles as prof
    import webui
    data = prof.load()
    entries = webui.build_catalog(SITES, data, {})
    missing = [e for e in entries if e["in_archive"] and not e["profile"]]
    todo = [e for e in entries if e["profile"] and e["profile"].get("verify")]
    pending = [e for e in entries if not e["in_archive"]]
    print(f"{len(data['profiles'])} profiler (senast genomgångna {data.get('checked') or 'okänt'}), {len(entries)} källor\n")
    print(f"Källor i arkivet utan profil ({len(missing)}):")
    print("\n".join(f"  {e['key']:20}{e['name']}  {e['home']}" for e in missing) or "  inga")
    print(f"\nProfiler som bör kontrolleras eller kompletteras ({len(todo)}):")
    print("\n".join(f"  {e['name']}: {e['profile']['verify']}" for e in todo) or "  inga")
    print(f"\nProfiler för källor som ännu inte lagts till i arkivet ({len(pending)}):")
    print("\n".join(f"  {e['name']}  {e['home']}" for e in pending) or "  inga")
    problems = prof.validate(data)
    print(f"\nFel i profildata ({len(problems)}):")
    print("\n".join(f"  {p}" for p in problems) or "  inga")
    print("\nRätta eller lägg till profiler i data/profiles.json (formatet finns i content/profiles.json). Varje uppgift om en person kräver en källa.")


def cmd_sites(args):
    print(f"{'nyckel':18}{'namn':34}{'flöden':>7}  arkivering  startsida")
    for s in SITES.values():
        print(f"{s.key:18}{s.name[:32]:34}{len(s.feeds):>7}  {'på' if s.archive_enabled else 'av':10}  {s.home}")
    print(f"\n{len(SITES)} sajter ({len(SITES) - len(BUILTIN)} från sources.json)")


def cmd_add_site(args):
    import autodetect
    result = autodetect.detect_site(Fetcher(), args.url, args.key, args.name, args.label, taken=set(SITES),
                                    feeds=args.feed, index_pages=args.index, article_regex=args.article_regex)
    if "error" in result:
        raise SystemExit(f"{args.url}: {result['error']}")
    ok, why = autodetect.add_source(result)
    print(f"{result['key']} ({result['name']}): {why}; {len(result['feeds'])} flöde(n): {', '.join(result['feeds'])}")
    if ok:
        print(f"Prova: python cli.py check-feeds --site {result['key']}")


def cmd_add_sites(args):
    import autodetect
    from pathlib import Path
    fetcher, taken = Fetcher(), set(SITES)
    added, failed = 0, []
    for url, name in autodetect.parse_site_list(Path(args.file).read_text(encoding="utf-8")):
        result = autodetect.detect_site(fetcher, url, name=name, taken=taken)
        if "error" in result:
            failed.append((url, result["error"]))
            print(f"  MISSLYCKADES {url}: {result['error']}")
            continue
        ok, why = autodetect.add_source(result)
        print(f"  {'OK' if ok else 'hoppade över'} {result['key']} ({result['name']}): {why}")
        if ok:
            added += 1
            taken.add(result["key"])
    print(f"\n{added} tillagda, {len(failed)} misslyckades")


def cmd_verify(args):
    import verify
    con = db.connect()
    snap = verify.find_snapshot(con, args.target)
    if not snap:
        raise SystemExit(f"Ingen lyckad fångst hittades för '{args.target}'. Ange artikel-id (siffran i /artikel/<id>), "
                         "en sajtnyckel (startsidan) eller en exakt URL. Kör `python cli.py status` för att se vad som arkiverats.")
    print(verify.report(verify.analyze(snap, con=con), listing=args.list, text=args.text))


def cmd_check_feeds(args):
    fetcher = Fetcher()
    for site in selected(args):
        for url in site.feeds + site.sitemaps:
            try:
                resp = fetcher.get(url)
                links, _ = extract_links(resp.content) if resp.status_code == 200 else ([], "")
                hits = sum(1 for u in links if site.is_article_url(u))
                print(f"{site.key}: {resp.status_code} {url} -> {len(links)} länkar, {hits} artiklar")
            except Exception as exc:
                print(f"{site.key}: FEL {url} ({exc})")
        for url in getattr(site, "index_pages", []):
            try:
                resp = fetcher.get(url)
                links = page_links(resp.text, url) if resp.status_code == 200 else set()
                hits = sum(1 for u in links if site.is_article_url(u))
                print(f"{site.key}: {resp.status_code} {url} (listsida) -> {len(links)} länkar, {hits} artiklar")
            except Exception as exc:
                print(f"{site.key}: FEL {url} ({exc})")


# ---------------------------------------------------------------------------
# Kommandon och hjälptexter. En enda tabell, så att listan i --help aldrig går isär med verkligheten.
# ---------------------------------------------------------------------------

SITE_HELP = ("begränsa till vissa sajtnycklar, t.ex. --site svt dn (lista: python cli.py sites); "
             "utan flaggan körs alla sajter")

COMMANDS = {
    "contact": (cmd_contact, "ange din kontaktadress (visas för sajterna du hämtar från)", "python cli.py contact EPOST", """
Sajterna ser vem som hämtar från dem via en identifiering (User-Agent) som innehåller din kontaktadress.
Adressen sparas i data/settings.json och följer med när du kopierar data-mappen. Gör det en gång.

Exempel:
  python cli.py contact du@exempel.se"""),
    "profiles": (cmd_profiles, "visa vilka källor som saknar profil (ägare, nyckelpersoner)", "python cli.py profiles", """
Källsidorna på webbplatsen bygger på profildata (content/profiles.json plus dina egna rättelser i data/profiles.json).
Kommandot visar vilka källor som saknar profil, vilka profiler som är markerade för kontroll och om profildatan har fel.

Exempel:
  python cli.py profiles"""),
    "sites": (cmd_sites, "lista alla sajter", "python cli.py sites", """
Visar nyckel, namn, antal RSS-flöden och om webbarkivering är på för varje sajt, både de inbyggda
(SVT, DN, Aftonbladet) och de du lagt till i sources.json. Nycklarna används med --site.

Exempel:
  python cli.py sites"""),
    "add-site": (cmd_add_site, "lägg till en ny nyhetssajt (hittar RSS-flödet själv)",
                 "python cli.py add-site ADRESS [flaggor]", """
Läser sajtens startsida (om robots.txt tillåter), letar efter RSS/Atom-flöden, kontrollerar att flödet
har minst tre artiklar på sajten och skriver en post i sources.json. Hittas inget fungerande flöde
läggs sajten inte till.

Exempel:
  python cli.py add-site https://www.nyheteridag.se
  python cli.py add-site https://www.nwt.se --key nwt --name "Nya Wermlands-Tidningen"
  python cli.py add-site https://exempel.se --label "Obunden"     (bara om sajten själv publicerar en etikett)
  python cli.py add-site https://www.svd.se --name "Svenska Dagbladet" --feed https://www.svd.se/feed/articles.rss
                                                               (när flödet inte hittas automatiskt)
  python cli.py add-site https://www.100.se --key 100procent --name "100%" --index https://www.100.se/artiklar/debatt --index https://www.100.se/artiklar/samtal --article-regex '^https://www\\.100\\.se/artiklar/[^/]+/[^/?#]+$'
                                                               (sajt utan RSS: hämta artikellänkar från listsidor)"""),
    "add-sites": (cmd_add_sites, "lägg till många sajter från en textfil",
                  "python cli.py add-sites FIL", """
Filen har en adress per rad, valfritt 'adress|Namn'. Rader som börjar med # hoppas över.
Sajter som inte går att nå eller saknar flöde rapporteras, och inget annat påverkas.
En färdig utgångslista följer med: sources_seed.txt.

Exempel:
  python cli.py add-sites sources_seed.txt"""),
    "check-feeds": (cmd_check_feeds, "kontrollera att sajternas RSS-flöden fungerar", "python cli.py check-feeds [flaggor]", """
Hämtar varje flöde och visar HTTP-status, antal länkar och hur många som ser ut som artiklar.

Exempel:
  python cli.py check-feeds
  python cli.py check-feeds --site svt"""),
    "discover": (cmd_discover, "hitta nya artiklar via RSS (kön fylls på)", "python cli.py discover [flaggor]", """
Läser sajternas RSS-flöden och lägger nya artikel-URL:er i kön (status 'discovered'). Hämtar inga artiklar.
RSS visar bara de senaste artiklarna; för äldre se: backfill, snowball och wayback.

Exempel:
  python cli.py discover
  python cli.py discover --site dn svt"""),
    "fetch": (cmd_fetch, "hämta rå-HTML för artiklar i kön", "python cli.py fetch [flaggor]", """
Hämtar artiklar som väntar i kön, nyast först, med paus mellan anrop och med hänsyn till robots.txt.
Sidan sparas komprimerad i data/raw/. Standard är 50 artiklar per sajt och körning.

Exempel:
  python cli.py fetch --limit 200
  python cli.py fetch --since 2025-01-01 --until 2025-04-01 --limit 500      (bara ett tidsfönster)"""),
    "parse": (cmd_parse, "tolka sparad rå-HTML till fält i databasen", "python cli.py parse [flaggor]", """
Läser den sparade rå-HTML:en och fyller i titel, ingress, brödtext, författare, tid, kategori, taggar, typ,
stad och land. Inga nätanrop. Efter att du ändrat en parser eller en ordlista: kör med --reparse.

Exempel:
  python cli.py parse
  python cli.py parse --reparse --site dn"""),
    "run": (cmd_run, "discover + fetch + parse i ett svep", "python cli.py run [flaggor]", """
Hela insamlingen av text och data (utan webbarkivering). Bra att schemalägga ofta.

Exempel:
  python cli.py run --limit 50
  python cli.py run --site nyheteridag --limit 20"""),
    "archive": (cmd_archive, "fullständig webbarkivering (WARC) av startsidor och artiklar",
                "python cli.py archive [flaggor]", """
Laddar sidorna i en riktig webbläsare (Playwright) med JavaScript, bilder och CSS och sparar all trafik i
WARC-filer: data/warc/<sajt>/<datum>.warc.gz. Tar startsidan och sedan artiklar som inte är arkiverade, nyast först.
Gränser som skyddar mot för mycket: --limit, --minutes, samt MAX_WARC_GB och DAILY_LIMIT_PER_SITE i
archive_settings.py. Det som inte hinner med väntar till nästa körning.
Utskriften visar artikel-id och vad som hoppades över. Kontrollera resultatet med: python cli.py verify <id>.

Exempel:
  python cli.py archive --limit 30
  python cli.py archive --limit 100 --minutes 60
  python cli.py archive --front-only
  python cli.py archive --site svt --again 2011 2020          (arkivera om vissa artiklar, t.ex. efter ändrade inställningar)
  python cli.py archive --since 2025-01-01 --until 2025-04-01 --limit 100      (ett tidsfönster)"""),
    "all": (cmd_all, "run + archive: hämta nytt och arkivera det", "python cli.py all [flaggor]", """
Kör hela kedjan: discover, fetch, parse och därefter archive. Ett bra kommando att schemalägga.

Exempel:
  python cli.py all --limit 30
  python cli.py all --limit 30 --minutes 45"""),
    "verify": (cmd_verify, "kontrollera att en arkiverad sida har med alla CSS/JS/bilder/typsnitt",
               "python cli.py verify MÅL", """
Läser en fångst ur WARC-filen, listar allt sidans HTML och CSS pekar på (stilmallar, skript, bilder, typsnitt)
och visar vad som saknas i arkivet, med orsak. För artiklar kontrolleras också att rubriken och texten finns i den arkiverade sidan. MÅL är ett artikel-id (siffran i webbplatsens /artikel/<id>
och i utskriften från archive), en sajtnyckel (då kontrolleras senaste startsidan) eller en exakt URL.

Exempel:
  python cli.py verify 123
  python cli.py verify svt
  python cli.py verify 123 --list          (visa alla resurser i fångsten)
  python cli.py verify 123 --text          (varför saknas artikeltexten? var finns den?)
  python cli.py verify https://www.svt.se/nyheter/inrikes/en-artikel"""),
    "status": (cmd_status, "översikt: vad som finns, väntar, diskanvändning", "python cli.py status [flaggor]", """
Visar per sajt hur många artiklar som finns, väntar på hämtning, är tolkade, arkiverade och väntar på
arkivering, hur många fångster som tagits idag, samt diskanvändning och en uppskattning av vad allt som väntar tar.

Exempel:
  python cli.py status
  python cli.py status --site svt"""),
    "probe": (cmd_probe, "undersök sajternas sitemaps (hur långt bakåt går de?)", "python cli.py probe [flaggor]", """
Läser robots.txt och sitemaps och visar typ, antal poster och datumspann. Använd det innan backfill.

Exempel:
  python cli.py probe
  python cli.py probe --site aftonbladet"""),
    "backfill": (cmd_backfill, "hämta äldre artikel-URL:er från sajternas sitemaps", "python cli.py backfill [flaggor]", """
Går igenom sitemaps (nyaste först) och lägger artikel-URL:er i kön. Datumet är sitemapens 'lastmod',
alltså ändringsdatum. Därefter: fetch, parse och eventuellt archive.

Exempel:
  python cli.py backfill --since 2026-09-01 --max-sitemaps 30"""),
    "snowball": (cmd_snowball, "hitta äldre artiklar genom länkar i sparade sidor", "python cli.py snowball [flaggor]", """
Läser den sparade rå-HTML:en och lägger till artikellänkar som saknas (relaterade artiklar, 'läs också').
Inga nya nätanrop. Går bakåt steg för steg.

Exempel:
  python cli.py snowball
  python cli.py snowball --pages 1000 --site svt"""),
    "wayback": (cmd_wayback, "lista gamla artikel-URL:er via Internet Archives katalog", "python cli.py wayback [flaggor]", """
Frågar Wayback Machines katalog (CDX) vilka artikel-URL:er som sett under ett tidsfönster och lägger dem i
kön. Själva sidorna hämtas sedan från sajterna med fetch. Ta ett fönster i taget (en månad eller ett
kvartal). Skriver kommandot 'fler finns', smalna av fönstret. Datum är när Internet Archive såg sidan.

Exempel:
  python cli.py wayback --site svt --since 2025-09 --until 2025-09 --max 40         (snabbt test först)
  python cli.py wayback --since 2025-09 --until 2025-09 --max 2000
  python cli.py wayback --since 2025-01 --until 2025-03 --site svt"""),
}

GROUPS = [
    ("Inställningar", ["contact"]),
    ("Lägg till och se sajter", ["sites", "add-site", "add-sites", "check-feeds", "profiles"]),
    ("Samla in och arkivera", ["run", "archive", "all", "status", "verify"]),
    ("Enskilda steg", ["discover", "fetch", "parse"]),
    ("Gå bakåt i tiden", ["probe", "backfill", "snowball", "wayback"]),
]

MAIN_HELP = """\
Nyhetsarkivet: samla in, tolka och webbarkivera svenska nyhetssajter.

VANLIGT ARBETSFLÖDE
  1. python cli.py check-feeds      kontrollera att flödena fungerar
  2. python cli.py run              hämta och tolka de senaste artiklarna
  3. python cli.py archive          webbarkivera (WARC) startsidor och artiklar
     python cli.py verify <id>      kontrollera att en artikel fick med CSS/JS (id står i utskriften)
  4. python cli.py status           se vad som finns och hur mycket plats det tar
  5. uvicorn api:app --reload       starta webbplatsen och API:t (http://127.0.0.1:8000)

  Allt i ett (bra att schemalägga):   python cli.py all --limit 30

KOMMANDON
{commands}
HJÄLP
  python cli.py hjälp <kommando>    detaljerad hjälp med exempel (t.ex. python cli.py hjälp archive)
  python cli.py <kommando> --help   samma sak. Även -help, -h och /? fungerar.

Vanliga flaggor: --site <nyckel...> (begränsa till sajter), --limit N (max per sajt), --since/--until (tidsfönster).
Mer om format, gränser och uppspelning av arkivet: README.md.
"""

USAGE_MAIN = "python cli.py <kommando> [flaggor]"


class _Fmt(argparse.RawDescriptionHelpFormatter):
    def _format_usage(self, usage, actions, groups, prefix):
        return super()._format_usage(usage, actions, groups, prefix or "användning: ")


def _translate(message):
    """Översätt argparse vanligaste felmeddelanden till svenska."""
    import re
    m = re.search(r"invalid choice: '(.+?)' \(choose from (.*)\)", message)
    if m:
        return f"okänt kommando '{m.group(1)}'. Tillgängliga: {m.group(2).replace(chr(39), '')}"
    for en, sv in (("the following arguments are required:", "följande argument saknas:"),
                   ("unrecognized arguments:", "okända argument:"),
                   ("expected one argument", "ett värde saknas efter flaggan"),
                   ("expected at least one argument", "minst ett värde saknas efter flaggan"),
                   ("invalid int value:", "ogiltigt heltal:"),
                   ("invalid float value:", "ogiltigt tal:"),
                   ("ambiguous option:", "flaggan är tvetydig:"),
                   ("not allowed with argument", "kan inte användas tillsammans med")):
        message = message.replace(en, sv)
    return message


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write(f"Fel: {_translate(message)}\n\nHjälp: python cli.py hjälp   (eller: python cli.py <kommando> --help)\n")
        raise SystemExit(2)


def _swedish(parser):
    for group, title in ((getattr(parser, "_optionals", None), "flaggor"),
                         (getattr(parser, "_positionals", None), "argument")):
        if group is not None:
            group.title = title


def _commands_listing():
    lines = []
    for title, names in GROUPS:
        lines.append(f"  {title}:")
        lines += [f"    {n:<12}{COMMANDS[n][1]}" for n in names]
        lines.append("")
    return "\n".join(lines)


def add_args(name, p):
    if name not in ("sites", "add-site", "add-sites", "verify", "contact", "profiles"):
        p.add_argument("--site", nargs="*", metavar="NYCKEL", help=SITE_HELP)
    if name == "add-site":
        p.add_argument("url", help="sajtens adress, t.ex. https://www.nyheteridag.se")
        p.add_argument("--key", help="eget kortnamn (a-z, 0-9, bindestreck); annars härleds det ur adressen")
        p.add_argument("--name", help="sajtens namn; annars hämtas det från sajten")
        p.add_argument("--label", help="politisk etikett, bara om sajten själv publicerar en")
        p.add_argument("--feed", action="append", metavar="RSS-ADRESS",
                       help="ange RSS-flödet själv när det inte hittas automatiskt (kan upprepas)")
        p.add_argument("--index", action="append", metavar="LISTSIDA",
                       help="listsida att läsa artikellänkar från när RSS saknas, t.ex. en sektionssida (kan upprepas)")
        p.add_argument("--article-regex", metavar="MÖNSTER",
                       help="regex som artikeladresser ska matcha (behövs oftast med --index)")
    if name == "contact":
        p.add_argument("email", metavar="EPOST", help="din e-postadress eller en annan kontaktväg")
    if name == "verify":
        p.add_argument("target", metavar="MÅL", help="artikel-id, sajtnyckel (startsidan) eller exakt URL")
        p.add_argument("--list", action="store_true", help="visa också alla resurser i fångsten (typ, storlek, adress)")
        p.add_argument("--text", action="store_true", help="detaljer om artikeltexten: var den finns, och om den fanns vid vanlig hämtning")
    if name == "add-sites":
        p.add_argument("file", help="textfil med en adress per rad, valfritt 'adress|Namn'")
    if name in DEFAULT_LIMIT:
        p.add_argument("--limit", type=int, default=DEFAULT_LIMIT[name], metavar="N",
                       help=f"max antal artiklar per sajt och körning (standard {DEFAULT_LIMIT[name]})")
    if name in ("fetch", "run", "archive", "all"):
        p.add_argument("--since", metavar="ÅÅÅÅ-MM-DD", help="bara artiklar från och med detta datum")
        p.add_argument("--until", metavar="ÅÅÅÅ-MM-DD", help="bara artiklar före detta datum")
    if name in ("archive", "all"):
        p.add_argument("--front-only", action="store_true", help="arkivera bara startsidorna")
        if name == "archive":
            p.add_argument("--again", nargs="+", type=int, metavar="ID",
                           help="arkivera om dessa artikel-id:n (kräver --site), även om de redan är arkiverade")
        p.add_argument("--no-front", action="store_true", help="hoppa över startsidorna")
        p.add_argument("--minutes", type=float, default=None, metavar="M",
                       help="avsluta arkiveringen efter så här många minuter")
    if name == "parse":
        p.add_argument("--reparse", action="store_true", help="tolka om redan tolkade artiklar (efter ändrad parser)")
    if name == "backfill":
        p.add_argument("--since", metavar="ÅÅÅÅ-MM-DD", help="bara sitemap-poster från detta datum")
        p.add_argument("--max-sitemaps", type=int, default=30, metavar="N", help="max antal sitemap-filer per sajt (standard 30)")
    if name == "wayback":
        p.add_argument("--since", metavar="ÅÅÅÅ[-MM]", help="från och med (när Wayback såg sidan)")
        p.add_argument("--until", metavar="ÅÅÅÅ[-MM]", help="till och med")
        p.add_argument("--max", type=int, default=5000, metavar="N",
                       help="max antal katalograder per sajt (standard 5000); prova först med t.ex. --max 40 för ett snabbt test")
    if name == "snowball":
        p.add_argument("--pages", type=int, default=300, metavar="N", help="max antal sparade artiklar att läsa per sajt (standard 300)")


def build_parser():
    ap = _Parser(prog="python cli.py", usage=USAGE_MAIN, formatter_class=_Fmt, add_help=False,
                 description=MAIN_HELP.format(commands=_commands_listing()))
    ap.add_argument("-h", "--help", "-help", action="help", help="visa den här hjälpen")
    _swedish(ap)
    sub = ap.add_subparsers(dest="cmd", metavar="<kommando>", parser_class=_Parser)
    sub.required = True
    sub.help = argparse.SUPPRESS
    for name, (fn, short, usage, details) in COMMANDS.items():
        p = sub.add_parser(name, usage=usage, help=short, description=f"{short[0].upper() + short[1:]}.\n{details}",
                           formatter_class=_Fmt, add_help=False)
        p.add_argument("-h", "--help", "-help", action="help", help="visa hjälp för det här kommandot")
        add_args(name, p)
        _swedish(p)
        p.set_defaults(fn=fn)
    return ap


def normalize_argv(argv):
    """Gör att -help, /?, 'help' och 'hjälp' fungerar, och att tomt anrop visar guiden."""
    if not argv:
        return ["--help"]
    argv = ["--help" if a in ("-help", "/?", "-?", "/h", "/help", "--hjälp", "-hjälp") else a for a in argv]
    if argv[0] in ("help", "hjälp", "hjalp"):
        return argv[1:2] + ["--help"] if len(argv) > 1 else ["--help"]
    return argv


NETWORK_COMMANDS = {"discover", "fetch", "run", "archive", "all", "check-feeds", "add-site", "add-sites",
                    "probe", "backfill", "wayback"}


def warn_contact(cmd):
    import config
    if cmd in NETWORK_COMMANDS and "du@example.se" in config.USER_AGENT:
        sys.stderr.write("OBS: du har inte angett någon kontaktadress, så sajterna ser 'du@example.se'.\n"
                         "     Gör det en gång:  python cli.py contact din@epost.se\n\n")


def main(argv=None):
    args = build_parser().parse_args(normalize_argv(sys.argv[1:] if argv is None else argv))
    warn_contact(args.cmd)
    if args.cmd in ("run", "all"):
        args.reparse = False
    try:
        args.fn(args)
    except KeyboardInterrupt:
        sys.stderr.write("\nAvbruten. Det som redan hämtats ligger kvar; kör kommandot igen för att fortsätta.\n")
        raise SystemExit(130)


if __name__ == "__main__":
    main()
