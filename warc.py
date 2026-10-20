"""Minimal WARC 1.0-skrivare utan beroenden. Varje post är en egen gzip-medlem (.warc.gz),
som är standardformatet för webbarkiv (Internet Archive, pywb, ReplayWeb.page)."""
import base64
import datetime as dt
import gzip
import hashlib
import http
import re
import uuid
import zlib
from pathlib import Path

PROFILE_REVISIT = "http://netpreserve.org/warc/1.0/revisit/identical-payload-digest"
# Rubriker som inte hör hemma i en lagrad, redan avkodad respons.
_DROP = {"transfer-encoding", "content-encoding", "content-length", "connection",
         "keep-alive", "set-cookie"}


def iso(ts):
    return ts.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha1_digest(data):
    return "sha1:" + base64.b32encode(hashlib.sha1(data).digest()).decode("ascii")


def new_id():
    return f"<urn:uuid:{uuid.uuid4()}>"


def _phrase(status):
    try:
        return http.HTTPStatus(status).phrase
    except ValueError:
        return "Unknown"


def http_head(status, reason, headers, content_length):
    lines = [f"HTTP/1.1 {status} {reason or _phrase(status)}"]
    for name, value in headers:
        n = str(name).strip()
        if not n or n.startswith(":") or n.lower() in _DROP:
            continue
        v = str(value).replace("\r", " ").replace("\n", " ")
        lines.append(f"{n}: {v}")
    lines.append(f"Content-Length: {content_length}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8", "replace")


def _record(headers, block):
    head = "WARC/1.0\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers)
    head += f"Content-Length: {len(block)}\r\n\r\n"
    return head.encode("utf-8") + block + b"\r\n\r\n"


class WarcWriter:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.pos = self.path.stat().st_size if self.path.exists() else 0
        self._f = open(self.path, "ab")

    def _write(self, headers, block):
        data = gzip.compress(_record(headers, block), compresslevel=6)
        self._f.write(data)
        self.pos += len(data)

    def warcinfo(self, fields, date=None):
        date = date or dt.datetime.now(dt.timezone.utc)
        block = "".join(f"{k}: {v}\r\n" for k, v in fields.items()).encode("utf-8")
        self._write([("WARC-Type", "warcinfo"), ("WARC-Date", iso(date)),
                     ("WARC-Record-ID", new_id()), ("WARC-Filename", self.path.name),
                     ("Content-Type", "application/warc-fields")], block)

    def response(self, url, date, status, reason, headers, body, digest=None):
        rid = new_id()
        digest = digest or sha1_digest(body)
        block = http_head(status, reason, headers, len(body)) + body
        self._write([("WARC-Type", "response"), ("WARC-Record-ID", rid), ("WARC-Date", iso(date)),
                     ("WARC-Target-URI", url), ("Content-Type", "application/http; msgtype=response"),
                     ("WARC-Payload-Digest", digest)], block)
        return rid

    def revisit(self, url, date, status, reason, headers, body_len, digest,
                refers_to_id, refers_to_date):
        """Identisk nyttolast som en tidigare post i samma fil: spara bara rubrikerna."""
        block = http_head(status, reason, headers, body_len)
        self._write([("WARC-Type", "revisit"), ("WARC-Record-ID", new_id()), ("WARC-Date", iso(date)),
                     ("WARC-Target-URI", url), ("Content-Type", "application/http; msgtype=response"),
                     ("WARC-Profile", PROFILE_REVISIT), ("WARC-Payload-Digest", digest),
                     ("WARC-Refers-To", refers_to_id), ("WARC-Refers-To-Target-URI", url),
                     ("WARC-Refers-To-Date", refers_to_date)], block)

    def close(self):
        self._f.flush()
        self._f.close()


# ---- Läsning (används av `verify`) -------------------------------------------

def iter_members(raw):
    """Varje WARC-post är en egen gzip-medlem; plocka isär dem."""
    while raw:
        d = zlib.decompressobj(31)
        data = d.decompress(raw)
        raw = d.unused_data
        yield data


def parse_record(data):
    j = data.index(b"\r\n\r\n")
    lines = data[:j].decode("utf-8", "replace").split("\r\n")
    headers = dict(line.split(": ", 1) for line in lines[1:] if ": " in line)
    n = int(headers.get("Content-Length", 0))
    return headers, data[j + 4:j + 4 + n]


def read_slice(path, offset=0, length=None):
    """Läs en sammanhängande del av en WARC-fil (t.ex. en fångst). Returnerar [(rubriker, block)]."""
    with open(path, "rb") as f:
        f.seek(offset)
        raw = f.read(length) if length else f.read()
    return [parse_record(d) for d in iter_members(raw)]


def load_bodies(path, ids):
    """Hämta innehållet för givna post-id:n (för dubblettposter som pekar på en tidigare post)."""
    wanted, found = set(ids), {}
    if not wanted:
        return found
    for data in iter_members(Path(path).read_bytes()):
        m = re.search(rb"WARC-Record-ID: (<[^>]+>)", data[:1000])
        if m and m.group(1).decode() in wanted:
            headers, block = parse_record(data)
            if headers.get("WARC-Type") == "response":
                found[m.group(1).decode()] = block.partition(b"\r\n\r\n")[2]
    return found
