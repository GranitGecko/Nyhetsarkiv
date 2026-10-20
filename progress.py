"""Enkel förloppsvisning i terminalen: en rad som uppdateras. Trådsäker, utan beroenden, ASCII-only.

I en riktig terminal: [spinner] Hämtar [#####-----] 123/500  25%  fel 3  1.9/s  ~12 min kvar | svt: https://...
Om utskriften går till en fil eller ett rör: en vanlig rad var 5:e procent.
"""
import shutil
import sys
import threading
import time

_SPIN = "|/-\\"


def fmt_duration(seconds):
    seconds = int(max(0, seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    if h:
        return f"{h} h {m:02d} min"
    if m:
        return f"{m} min {s:02d} s"
    return f"{s} s"


class Progress:
    def __init__(self, total, verb="Hämtar", stream=None, enabled=None, width=None):
        self.total, self.verb = total, verb
        self.stream = stream or sys.stderr
        self.tty = self.stream.isatty() if enabled is None else enabled
        self.fixed_width = width
        self.done = self.errors = 0
        self.current = ""
        self.t0 = time.monotonic()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._drawn = False
        self._next_plain = 0
        self._ticker = None
        if self.tty and total:
            self._ticker = threading.Thread(target=self._tick, daemon=True)
            self._ticker.start()

    # ---- uppdatering från arbetstrådar ----
    def set_current(self, label):
        self.current = label

    def advance(self, error=False):
        with self._lock:
            self.done += 1
            self.errors += 1 if error else 0
            if self.tty:
                self._draw()
            elif self.done >= self._next_plain or self.done == self.total:
                self._next_plain = self.done + max(1, self.total // 20)
                self.stream.write(self._plain_line() + "\n")
                self.stream.flush()

    def log(self, message):
        """Skriv en permanent rad ovanför förloppsraden."""
        with self._lock:
            if self.tty and self._drawn:
                self.stream.write("\r" + " " * self._width() + "\r")
            self.stream.write(message + "\n")
            self.stream.flush()
            self._drawn = False
            if self.tty:
                self._draw()

    def close(self):
        self._stop.set()
        if self._ticker:
            self._ticker.join(timeout=1)
        with self._lock:
            if self.tty and self._drawn:
                self.stream.write("\r" + " " * self._width() + "\r")
                self.stream.flush()
            self._drawn = False

    def summary(self):
        elapsed = time.monotonic() - self.t0
        err = f", {self.errors} fel" if self.errors else ""
        return f"{self.verb}: {self.done} av {self.total} klara på {fmt_duration(elapsed)}{err}"

    # ---- utritning ----
    def _width(self):
        return (self.fixed_width or shutil.get_terminal_size((100, 20)).columns) - 1

    def _stats(self):
        elapsed = max(time.monotonic() - self.t0, 1e-9)
        pct = 100 * self.done // self.total if self.total else 100
        rate = self.done / elapsed
        eta = ""
        if self.done >= 3 and self.done < self.total:
            eta = f"  ~{fmt_duration((self.total - self.done) * elapsed / self.done)} kvar"
        err = f"  fel {self.errors}" if self.errors else ""
        return pct, rate, eta, err

    def _plain_line(self):
        pct, rate, eta, err = self._stats()
        return f"{self.verb}: {self.done}/{self.total} ({pct}%){err}  {rate:.1f}/s{eta}"

    def _line(self, tick):
        pct, rate, eta, err = self._stats()
        filled = int(20 * self.done / self.total) if self.total else 20
        bar = "#" * filled + "-" * (20 - filled)
        spin = _SPIN[tick % 4]
        head = f"{spin} {self.verb} [{bar}] {self.done}/{self.total} {pct:3d}%{err}  {rate:.1f}/s{eta}"
        room = self._width() - len(head) - 3
        cur = self.current
        if room > 8 and cur:
            cur = cur if len(cur) <= room else cur[:room - 3] + "..."
            head += f" | {cur}"
        return head[: self._width()]

    def _draw(self, tick=None):
        tick = int((time.monotonic() - self.t0) * 4) if tick is None else tick
        line = self._line(tick)
        self.stream.write("\r" + line.ljust(self._width()))
        self.stream.flush()
        self._drawn = True

    def _tick(self):
        while not self._stop.wait(0.25):
            with self._lock:
                self._draw()
