"""Administratör: lösenord, inloggningssessioner och radering av artiklar. Inget webbramverk här.

Säkra standardvärden: administration är avstängd tills ett lösenord satts (python cli.py admin-password).
Lösenordet sparas som PBKDF2-hash i data/settings.json. Sessioner är signerade (HMAC) och går ut efter 8 timmar.
'Radera' tar bort artikelns innehåll och rå-HTML men behåller adressen (status 'deleted') så att den inte hämtas igen.
WARC-filer är append-only arkiv och ändras inte; vilka filer som berörs rapporteras vid radering.
"""
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

import config

ITERATIONS = 600_000
SESSION_SECONDS = 8 * 3600
MIN_PASSWORD = 10
WIPE = ("canonical_url", "title", "ingress", "body", "author", "published", "updated", "category", "tags", "topic",
        "article_type", "image", "lang", "paywalled", "country", "city", "locations", "content_hash", "raw_path", "error")


# ---- inställningar -----------------------------------------------------------

def _path():
    return config.DATA / "settings.json"


def load_settings():
    try:
        return json.loads(_path().read_text(encoding="utf-8")) if _path().exists() else {}
    except ValueError:
        return {}


def save_settings(data):
    config.DATA.mkdir(parents=True, exist_ok=True)
    tmp = _path().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, _path())


def _eq(a, b):
    """Konstanttidsjämförelse som tål icke-ASCII-tecken (hmac.compare_digest på str kraschar annars)."""
    return hmac.compare_digest(str(a).encode("utf-8"), str(b).encode("utf-8"))


def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def _hash(password, salt, iterations):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


def admin_enabled():
    a = load_settings().get("admin") or {}
    return bool(a.get("hash") and a.get("salt"))


def set_password(password):
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"Lösenordet måste vara minst {MIN_PASSWORD} tecken")
    salt = os.urandom(16)
    s = load_settings()
    s["admin"] = {"salt": _b64(salt), "iterations": ITERATIONS, "hash": _b64(_hash(password, salt, ITERATIONS))}
    s["session_secret"] = secrets.token_hex(32)          # nytt lösenord loggar ut alla gamla sessioner
    save_settings(s)


def disable_admin():
    s = load_settings()
    s.pop("admin", None)
    s["session_secret"] = secrets.token_hex(32)
    save_settings(s)


def check_password(password):
    a = load_settings().get("admin") or {}
    if not (a.get("hash") and a.get("salt")):
        return False
    try:
        salt, want = base64.b64decode(a["salt"]), base64.b64decode(a["hash"])
        got = _hash(password, salt, int(a.get("iterations", ITERATIONS)))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, want)


# ---- sessioner och CSRF ------------------------------------------------------

def _secret():
    s = load_settings().get("session_secret")
    return s.encode("utf-8") if s else b""


def _sign(payload):
    return hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def make_session(now=None):
    if not admin_enabled():
        return None
    body = json.dumps({"exp": int((now or time.time()) + SESSION_SECONDS), "sid": secrets.token_hex(8)})
    payload = base64.urlsafe_b64encode(body.encode()).decode("ascii")
    return f"{payload}.{_sign(payload)}"


def parse_session(token, now=None):
    """Giltig session som dict, annars None. Kräver att administration är påslagen och att signaturen stämmer."""
    if not token or not admin_enabled() or "." not in token:
        return None
    payload, _, sig = token.rpartition(".")
    if not _eq(sig, _sign(payload)):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except (ValueError, TypeError):
        return None
    return data if data.get("exp", 0) > (now or time.time()) else None


def csrf_token(session):
    return hmac.new(_secret(), f"csrf:{session['sid']}".encode(), hashlib.sha256).hexdigest()[:32] if session else ""


def csrf_ok(session, token):
    return bool(session) and _eq(csrf_token(session), token or "")


class RateLimiter:
    """Efter 5 felaktiga försök från samma adress spärras den i 60 sekunder."""

    def __init__(self, tries=5, block_seconds=60):
        self.tries, self.block, self._state, self._lock = tries, block_seconds, {}, threading.Lock()

    def allowed(self, key, now=None):
        now = now or time.time()
        with self._lock:
            fails, until = self._state.get(key, (0, 0))
            return now >= until

    def fail(self, key, now=None):
        now = now or time.time()
        with self._lock:
            fails, until = self._state.get(key, (0, 0))
            fails += 1
            if fails >= self.tries:
                fails, until = 0, now + self.block
            self._state[key] = (fails, until)

    def success(self, key):
        with self._lock:
            self._state.pop(key, None)


def safe_return(url, default="/artiklar"):
    """Tillåt bara lokala sökvägar som återvändsadress (skydd mot öppna omdirigeringar)."""
    url = (url or "").strip()
    return url if url.startswith("/") and not url.startswith("//") and "\\" not in url and "\n" not in url else default


# ---- radering ----------------------------------------------------------------

def log(con, action, detail="", ip=None, actor="admin"):
    con.execute("INSERT INTO admin_log(at, actor, action, detail, ip) VALUES (?,?,?,?,?)",
                (dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), actor, action, detail[:1000], ip))
    con.commit()


def delete_articles(con, ids, actor="admin", ip=None, raw_dir=None):
    """Radera artiklarnas innehåll. Returnerar {'deleted': [...], 'skipped': [...], 'raw_removed': n, 'warc_files': [...]}."""
    raw_dir = raw_dir or config.RAW_DIR
    ids = sorted({int(i) for i in ids})
    result = {"deleted": [], "skipped": [], "raw_removed": 0, "warc_files": []}
    if not ids:
        return result
    marks = ",".join("?" for _ in ids)
    rows = con.execute(f"SELECT id, source, url, raw_path, status FROM articles WHERE id IN ({marks})", ids).fetchall()
    found = {r["id"] for r in rows}
    result["skipped"] += [i for i in ids if i not in found]
    warc = set()
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    for r in rows:
        if r["status"] == "deleted":
            result["skipped"].append(r["id"])
            continue
        for s in con.execute("SELECT warc_path FROM snapshots WHERE article_id=? AND status='ok'", (r["id"],)):
            if s["warc_path"]:
                warc.add(s["warc_path"])
        con.execute("UPDATE snapshots SET status='deleted' WHERE article_id=? AND status='ok'", (r["id"],))
        if r["raw_path"]:
            shared = con.execute("SELECT COUNT(*) FROM articles WHERE raw_path=? AND id<>?", (r["raw_path"], r["id"])).fetchone()[0]
            if not shared:
                try:
                    (raw_dir / r["raw_path"]).unlink()
                    result["raw_removed"] += 1
                except OSError:
                    pass
        sets = ", ".join(f"{c}=NULL" for c in WIPE)
        con.execute(f"UPDATE articles SET {sets}, status='deleted', deleted_at=? WHERE id=?", (now, r["id"]))
        result["deleted"].append(r["id"])
    con.commit()
    result["warc_files"] = sorted(warc)
    if result["deleted"]:
        log(con, "radera", f"artiklar: {result['deleted']}; WARC-filer som fortfarande innehåller sidorna: {result['warc_files']}", ip, actor)
    return result
