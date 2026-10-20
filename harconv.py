"""Gör om Playwrights HAR-fil till en lista poster som kan skrivas till WARC."""
import base64
import datetime as dt
import re
from urllib.parse import urlsplit

import archive_settings as cfg


def _force_utf8(headers):
    """HAR innehåller text redan avkodad, och vi lagrar den som UTF-8. Då måste
    Content-Type säga det, annars blir åäö fel vid uppspelning."""
    out = []
    for k, v in headers:
        if k.lower() == "content-type":
            if re.search(r"charset=", v, re.I):
                v = re.sub(r"charset=[^;]+", "charset=utf-8", v, flags=re.I)
            else:
                v = v + "; charset=utf-8"
        out.append((k, v))
    return out


def _when(value):
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return dt.datetime.now(dt.timezone.utc)


def entries_from_har(har, max_bytes=None, block_hosts=None, detail=None):
    """detail: valfri lista som fylls med (url, orsak) för allt som hoppas över."""
    max_bytes = max_bytes or cfg.MAX_RESOURCE_BYTES
    block_hosts = cfg.BLOCK_HOSTS if block_hosts is None else block_hosts
    entries, skipped = [], {}

    def skip(reason, url=None):
        skipped[reason] = skipped.get(reason, 0) + 1
        if detail is not None and url and len(detail) < 500:
            detail.append((url, reason))

    for e in har.get("log", {}).get("entries", []):
        req, res = e.get("request", {}), e.get("response", {})
        url, status = req.get("url", ""), res.get("status", 0)
        if req.get("method") != "GET":
            skip("ej_get", url)
            continue
        if not url.startswith(("http://", "https://")):
            skip("ej_http", url)
            continue
        if not status or status >= 600 or status == 304:
            skip("ingen_respons", url)
            continue
        host = urlsplit(url).netloc.lower()
        if any(b in host for b in block_hosts):
            skip("blockerad_värd", url)
            continue
        content = res.get("content") or {}
        mime = (content.get("mimeType") or "").lower()
        if mime.startswith(("video/", "audio/")):
            skip("media", url)
            continue
        text = content.get("text")
        is_text = False
        if text is None:
            if (content.get("size") or 0) > 0 and status not in (301, 302, 303, 307, 308):
                skip("saknar_innehåll", url)
                continue
            body = b""
        elif content.get("encoding") == "base64":
            body = base64.b64decode(text)
        else:
            body, is_text = text.encode("utf-8"), True
        if len(body) > max_bytes:
            skip("för_stor", url)
            continue
        headers = [(h.get("name", ""), h.get("value", "")) for h in res.get("headers", [])]
        if is_text:
            headers = _force_utf8(headers)
        entries.append({"url": url, "date": _when(e.get("startedDateTime")), "status": status,
                        "reason": res.get("statusText") or "", "headers": headers,
                        "body": body, "mime": mime})
    return entries, skipped
