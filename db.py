import sqlite3
import threading
import time

from config import DB_PATH, DATA

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    canonical_url TEXT,
    status TEXT NOT NULL DEFAULT 'discovered',   -- discovered|fetched|parsed|error|blocked
    discovered_at TEXT, fetched_at TEXT, parsed_at TEXT,
    http_status INTEGER, content_hash TEXT, raw_path TEXT, error TEXT,
    title TEXT, ingress TEXT, body TEXT, author TEXT,
    published TEXT, updated TEXT,
    category TEXT, tags TEXT, topic TEXT, article_type TEXT,
    image TEXT, lang TEXT, paywalled INTEGER,
    country TEXT, city TEXT, locations TEXT,
    source_political_label TEXT,
    hint_date TEXT,                               -- ungefärlig tid för artiklar hittade via sitemap/länkar
    deleted_at TEXT                               -- satt när en admin raderat innehållet (status 'deleted')
);
CREATE INDEX IF NOT EXISTS ix_status   ON articles(source, status);
CREATE INDEX IF NOT EXISTS ix_pub      ON articles(published);
CREATE INDEX IF NOT EXISTS ix_type     ON articles(article_type);
CREATE INDEX IF NOT EXISTS ix_category ON articles(category);
CREATE INDEX IF NOT EXISTS ix_city     ON articles(city);

-- Fullständiga fångster (WARC). En rad per sidfångst.
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    kind TEXT NOT NULL,                  -- front|article
    article_id INTEGER,
    url TEXT NOT NULL,
    taken_at TEXT NOT NULL,
    status TEXT NOT NULL,                -- ok|error
    error TEXT,
    http_status INTEGER,
    warc_path TEXT, warc_offset INTEGER, warc_length INTEGER,
    n_records INTEGER, n_revisits INTEGER, bytes INTEGER,
    notes TEXT                           -- JSON: vad som hoppades över och varför
);
CREATE INDEX IF NOT EXISTS ix_snap_article ON snapshots(article_id, status);
CREATE INDEX IF NOT EXISTS ix_snap_source  ON snapshots(source, taken_at);

-- Logg över administratörens ändringar.
CREATE TABLE IF NOT EXISTS admin_log (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    actor TEXT,
    action TEXT NOT NULL,
    detail TEXT,
    ip TEXT
);

-- Artiklar vars länkar redan genomsökts efter fler artiklar ('snowball').
CREATE TABLE IF NOT EXISTS links_scanned (article_id INTEGER PRIMARY KEY);

-- Vilka (URL, innehåll) som redan finns i en viss WARC-fil, så att dubbletter blir 'revisit'-poster.
CREATE TABLE IF NOT EXISTS warc_seen (
    warc_path TEXT NOT NULL,
    url TEXT NOT NULL,
    digest TEXT NOT NULL,
    record_id TEXT NOT NULL,
    rec_date TEXT NOT NULL,
    PRIMARY KEY (warc_path, url, digest)
);
"""

_init_lock = threading.Lock()
_initialized = False


def _init():
    """Körs en gång per process: WAL-läge och tabeller (med omförsök om en annan process är först)."""
    global _initialized
    with _init_lock:
        if _initialized:
            return
        DATA.mkdir(parents=True, exist_ok=True)
        for attempt in range(8):
            try:
                con = sqlite3.connect(DB_PATH, timeout=30)
                con.execute("PRAGMA journal_mode=WAL")
                con.executescript(SCHEMA)
                cols = {r[1] for r in con.execute("PRAGMA table_info(articles)")}
                if "hint_date" not in cols:          # äldre databaser
                    con.execute("ALTER TABLE articles ADD COLUMN hint_date TEXT")
                if "deleted_at" not in cols:
                    con.execute("ALTER TABLE articles ADD COLUMN deleted_at TEXT")
                snap_cols = {r[1] for r in con.execute("PRAGMA table_info(snapshots)")}
                if "notes" not in snap_cols:         # äldre databaser
                    con.execute("ALTER TABLE snapshots ADD COLUMN notes TEXT")
                con.commit()
                con.close()
                break
            except sqlite3.OperationalError:
                if attempt == 7:
                    raise
                time.sleep(0.5 * (attempt + 1))
        _initialized = True


def connect():
    _init()
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA synchronous=NORMAL")
    return con
