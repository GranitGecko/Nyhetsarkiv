"""Kontrollera att inget privat eller insamlat följer med när du publicerar på GitHub.

Kör:  python tools/check_publish.py
Kontrollerar filerna som skulle publiceras (enligt git, annars alla filer utom ignorerade mappar) efter
databaser och WARC-filer, personliga sökvägar och e-postadresser, hemligheter och stora filer.
Avslutas med felkod 1 om något hittas.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "data", ".venv", "venv", "__pycache__", ".pytest_cache", ".idea", ".vscode", "node_modules"}
FORBIDDEN_SUFFIXES = (".db", ".db-wal", ".db-shm", ".sqlite", ".warc", ".warc.gz", ".har", ".env")
FORBIDDEN_NAMES = {"settings.json", "sources.json", ".env", "news.db"}      # sources.json i roten är dina egna sajter
ALLOWED_EMAIL_DOMAINS = ("example.se", "exempel.se", "epost.se", "example.com")
MAX_BYTES = 1_500_000
PATTERNS = [
    (re.compile(r"[A-Za-z]:\\Users\\[A-Za-z0-9_.-]+", re.I), "personlig Windows-sökväg"),
    (re.compile(r"/home/(?!runner/)[a-z0-9_-]+/"), "personlig Linux-sökväg"),
    (re.compile(r"/Users/[A-Za-z0-9_.-]+/"), "personlig macOS-sökväg"),
    (re.compile(r"ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"), "GitHub-token"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS-nyckel"),
    (re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"), "privat nyckel"),
]
EMAIL = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")


def files_to_publish(root=ROOT):
    """Filer som skulle publiceras: git:s lista om repot finns, annars alla filer utom ignorerade mappar."""
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=root,
                             capture_output=True, text=True, check=True).stdout
        files = [root / line for line in out.splitlines() if line.strip()]
        if files:
            return [f for f in files if f.exists()]
    except (OSError, subprocess.CalledProcessError):
        pass
    return [p for p in root.rglob("*") if p.is_file() and not (set(p.relative_to(root).parts) & SKIP_DIRS)]


def check(root=ROOT, files=None):
    problems = []
    for path in files if files is not None else files_to_publish(root):
        rel = path.relative_to(root)
        name = path.name.lower()
        if (rel.parts and rel.parts[0] == "data") or name in FORBIDDEN_NAMES and len(rel.parts) == 1 \
                or name.endswith(FORBIDDEN_SUFFIXES):
            problems.append(f"{rel}: insamlad data eller privata inställningar ska inte publiceras")
            continue
        if path.stat().st_size > MAX_BYTES:
            problems.append(f"{rel}: filen är större än {MAX_BYTES // 1000} kB")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern, label in PATTERNS:
            if pattern.search(text):
                problems.append(f"{rel}: {label}")
        for m in EMAIL.finditer(text):
            if not m.group(1).lower().endswith(ALLOWED_EMAIL_DOMAINS):
                problems.append(f"{rel}: e-postadress {m.group(0)}")
    return problems


def main():
    problems = check()
    if problems:
        print("Hittade saker som inte bör publiceras:\n  " + "\n  ".join(problems))
        return 1
    print(f"OK: {len(files_to_publish())} filer kontrollerade, inget privat hittades.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
