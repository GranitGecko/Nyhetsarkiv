"""Artig HTTP-hämtning: robots.txt, fördröjning per värd, egen User-Agent."""
import threading
import time
import urllib.robotparser
from urllib.parse import urlsplit

import httpx

from config import REQUEST_DELAY, TIMEOUT, USER_AGENT


class Fetcher:
    def __init__(self):
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "sv-SE,sv;q=0.9"},
            timeout=TIMEOUT,
            follow_redirects=True,
        )
        self._last = {}
        self._robots = {}
        self._lock = threading.Lock()

    def _wait(self, host):
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._last.get(host, 0) + REQUEST_DELAY)
            self._last[host] = slot
        time.sleep(max(0, slot - now))

    def allowed(self, url):
        parts = urlsplit(url)
        host = parts.netloc
        if host not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                self._wait(host)
                r = self.client.get(f"{parts.scheme}://{host}/robots.txt")
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            except Exception:
                rp.parse([])
            self._robots[host] = rp
        return self._robots[host].can_fetch(USER_AGENT, url)

    def get(self, url, timeout=None):
        self._wait(urlsplit(url).netloc)
        if timeout:
            return self.client.get(url, timeout=timeout)
        return self.client.get(url)
