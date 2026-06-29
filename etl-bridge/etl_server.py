#!/usr/bin/env python3
"""AutoDQA local ETL bridge — a read-only file server over the ETL repo.

This runs on YOUR machine (the trusted side, on the campus VPN) and exposes a
narrow read API over the ETL source tree. A second ngrok tunnel publishes it so
the cloud agent (Lambda -> this server) can read ETL code to put data-quality
findings in context. All the safety lives here, on the trusted side, so it does
not matter what the model asks for:

  * jail       — every path is resolved and must stay inside ROOT (no `..`)
  * denylist   — secret/data files are never served (see DENY_* below)
  * allowlist  — only text/code suffixes are served
  * auth       — every request needs the bearer token (the ngrok URL is public)

Endpoints (all require `Authorization: Bearer <token>` except /health):
  GET /health             -> liveness, no auth
  GET /list               -> {"root","count","files":[{"path","size"}]}
  GET /read?path=REL      -> raw file text (text/plain), capped at MAX_READ_BYTES
  GET /grep?q=PAT         -> {"query","count","truncated","matches":[{path,line,text}]}
                             optional: &regex=1  &max=N  &ignorecase=0

Run:
  ./.venv/bin/python etl-bridge/etl_server.py            # serves the default ROOT
  ETL_ROOT=/path/to/etl ./.venv/bin/python etl-bridge/etl_server.py --port 8000
then expose it:
  ngrok http 8000
and hand the public https URL + the printed token to the gateway wiring.
"""
import argparse
import fnmatch
import hmac
import json
import os
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

# --- what to serve -----------------------------------------------------------
DEFAULT_ROOT = os.environ.get("ETL_ROOT", "/home/atth/gitlab/cdw/etl")
DEFAULT_HOST = "127.0.0.1"  # ngrok dials localhost; do NOT expose on the LAN
DEFAULT_PORT = 8000

# Only these suffixes are ever listed/read (ETL code + docs, not data files).
ALLOWED_SUFFIXES = {".sql", ".txt", ".md", ".json", ".yaml", ".yml"}

# Directories pruned entirely. `datavant/` holds tokenized patient output and
# hashtoken inputs; `.git` holds history. Never served.
DENY_DIRS = {"datavant", ".git"}

# Filenames matching any of these (case-insensitive) are never served, even if
# their suffix is allowed. `etl.load_hash_token` is the de-identification token.
DENY_GLOBS = [
    "*load_hash_token*", "*hash_token*", "*token*",
    "*.env", "*secret*", "*password*", "*cred*",
    "*.pem", "*.pfx", "*.key", "*.p12", "*.pgp", "*.gpg",
]

MAX_READ_BYTES = 1_000_000   # cap a single /read so a huge file can't blow context
MAX_GREP_MATCHES = 200       # cap /grep results
MAX_LINE_LEN = 300           # trim long matched lines


def load_token() -> str:
    """Bearer token from $ETL_SERVER_TOKEN, else a persisted local file (created
    once, gitignored). Persisting it keeps the vaulted copy valid across restarts."""
    env = os.environ.get("ETL_SERVER_TOKEN")
    if env:
        return env.strip()
    tok_file = Path(__file__).resolve().parent / ".etl_server_token"
    if tok_file.exists():
        return tok_file.read_text().strip()
    tok = secrets.token_urlsafe(24)
    tok_file.write_text(tok)
    os.chmod(tok_file, 0o600)
    return tok


def is_served(rel: str) -> bool:
    """Denylist + allowlist on a ROOT-relative posix path."""
    parts = rel.split("/")
    if any(p in DENY_DIRS for p in parts[:-1]):
        return False
    name = parts[-1].lower()
    if any(fnmatch.fnmatch(name, g) for g in DENY_GLOBS):
        return False
    return Path(name).suffix in ALLOWED_SUFFIXES


class Bridge:
    def __init__(self, root: Path, token: str):
        self.root = root
        self.token = token

    def resolve(self, rel: str):
        """Map a client path to a real file inside ROOT, or None if it escapes
        the jail / is denied / is not a regular file."""
        rel = unquote(rel or "").lstrip("/")
        try:
            target = (self.root / rel).resolve()
        except (OSError, RuntimeError):
            return None
        if target != self.root and self.root not in target.parents:
            return None  # escaped the jail (covers symlinks: resolve() is real path)
        relposix = target.relative_to(self.root).as_posix()
        if not is_served(relposix) or not target.is_file():
            return None
        return target

    def files(self):
        """Yield (relposix, Path) for every served file, pruning denied dirs."""
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in DENY_DIRS]
            for fn in filenames:
                full = Path(dirpath) / fn
                rel = full.relative_to(self.root).as_posix()
                if not is_served(rel):
                    continue
                real = full.resolve()
                if real == self.root or self.root in real.parents:
                    yield rel, full


class Handler(BaseHTTPRequestHandler):
    bridge: Bridge = None  # injected in main()

    def log_message(self, fmt, *args):  # quieter, one line per request
        print(f"  {self.address_string()} {fmt % args}")

    def _send(self, code: int, body: bytes, ctype: str, extra: dict = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _authed(self) -> bool:
        hdr = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not hdr.startswith(prefix):
            return False
        return hmac.compare_digest(hdr[len(prefix):], self.bridge.token)

    def do_GET(self):
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)

        if path in ("/", "/health"):
            return self._json(200, {"service": "autodqa-etl-bridge", "ok": True})

        if not self._authed():
            return self._json(401, {"error": "missing or invalid bearer token"})

        if path == "/list":
            files = [{"path": rel, "size": full.stat().st_size}
                     for rel, full in sorted(self.bridge.files())]
            return self._json(200, {"root": str(self.bridge.root),
                                    "count": len(files), "files": files})

        if path == "/read":
            rel = (qs.get("path") or [""])[0]
            target = self.bridge.resolve(rel)
            if target is None:
                return self._json(404, {"error": f"not found or not permitted: {rel!r}"})
            data = target.read_bytes()
            truncated = len(data) > MAX_READ_BYTES
            text = data[:MAX_READ_BYTES].decode("utf-8", "replace")
            return self._send(200, text.encode(), "text/plain; charset=utf-8",
                              {"X-ETL-Path": rel, "X-ETL-Truncated": str(truncated).lower()})

        if path == "/grep":
            q = (qs.get("q") or [""])[0]
            if not q:
                return self._json(400, {"error": "missing q"})
            use_regex = (qs.get("regex") or ["0"])[0] == "1"
            ignorecase = (qs.get("ignorecase") or ["1"])[0] != "0"
            limit = min(int((qs.get("max") or [MAX_GREP_MATCHES])[0]), MAX_GREP_MATCHES)
            flags = re.IGNORECASE if ignorecase else 0
            try:
                pat = re.compile(q if use_regex else re.escape(q), flags)
            except re.error as e:
                return self._json(400, {"error": f"bad regex: {e}"})
            matches, truncated = [], False
            for rel, full in sorted(self.bridge.files()):
                try:
                    lines = full.read_text("utf-8", "replace").splitlines()
                except OSError:
                    continue
                for n, line in enumerate(lines, 1):
                    if pat.search(line):
                        matches.append({"path": rel, "line": n, "text": line[:MAX_LINE_LEN]})
                        if len(matches) >= limit:
                            truncated = True
                            break
                if truncated:
                    break
            return self._json(200, {"query": q, "count": len(matches),
                                    "truncated": truncated, "matches": matches})

        return self._json(404, {"error": f"unknown endpoint {path!r}"})


def main():
    ap = argparse.ArgumentParser(description="AutoDQA read-only ETL bridge")
    ap.add_argument("--root", default=DEFAULT_ROOT, help=f"ETL tree (default {DEFAULT_ROOT})")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        raise SystemExit(f"ETL root not found: {root}")
    token = load_token()
    Handler.bridge = Bridge(root, token)

    served = sum(1 for _ in Handler.bridge.files())
    print("AutoDQA ETL bridge")
    print(f"  root         {root}")
    print(f"  serving      {served} files  (suffixes: {', '.join(sorted(ALLOWED_SUFFIXES))})")
    print(f"  denied dirs  {', '.join(sorted(DENY_DIRS))}")
    print(f"  denied names {', '.join(DENY_GLOBS)}")
    print(f"  listening    http://{args.host}:{args.port}")
    print(f"  bearer token {token}")
    print("  next: `ngrok http %d`, then give me the https URL + this token.\n" % args.port)

    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
