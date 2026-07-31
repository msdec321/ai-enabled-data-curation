#!/usr/bin/env python3
"""Code-exec server for the AutoDQA sandbox microVM.

Replaces the Cloudflare sandbox-worker: accepts POST {code, env} over HTTP, runs
the Python in a fresh namespace with `env` injected into os.environ, and returns
{stdout, stderr, results, error} — the same response contract sandbox_client.py
already expects, so nothing above the sandbox changes.

Single-threaded on purpose: one request at a time per microVM, so concurrent tool
calls in a run can't clash on the process-global os.environ (each request sets it,
runs, then restores it). pytds is baked into the image (see Dockerfile), so
query_cdw's runner no longer pip-installs at runtime — the cold-start bottleneck
that forced the timeout bump + serialization lock on the old sandbox.

The MicroVM service terminates the whole VM per session (Phase 2: terminate-microvm),
so there's no in-process "destroy" action to handle here.
"""
# Amazon Linux 2023's `dnf install python3` is <3.10, so keep annotations lazy
# (this file must stay 3.9-compatible: no `X | Y` unions evaluated at runtime).
from __future__ import annotations

import io
import json
import os
import traceback
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("PORT", "8080"))

# Lambda MicroVM build lifecycle hooks (only called if configured with a port at
# build time). Answering 200 lets Lambda snapshot / validate the image.
HOOK_PATHS = (
    "/aws/lambda-microvms/runtime/v1/ready",
    "/aws/lambda-microvms/runtime/v1/validate",
)


def run_code(code: str, env: dict | None) -> dict:
    """Exec `code` with `env` injected into os.environ; capture stdout/stderr."""
    saved = {}
    for k, v in (env or {}).items():
        saved[k] = os.environ.get(k)
        os.environ[k] = str(v)
    out, err = io.StringIO(), io.StringIO()
    result = {"stdout": "", "stderr": "", "results": [], "error": None}
    try:
        with redirect_stdout(out), redirect_stderr(err):
            exec(compile(code, "<sandbox>", "exec"), {"__name__": "__main__"})
    except Exception as e:
        result["error"] = {"name": type(e).__name__, "value": str(e),
                           "traceback": traceback.format_exc()}
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    result["stdout"] = out.getvalue()
    result["stderr"] = err.getvalue()
    return result


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # Health check + build-hook readiness.
        self._send(200, {"status": "ok"})

    def do_POST(self):
        if self.path in HOOK_PATHS:
            return self._send(200, {"status": "ready"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            return self._send(400, {"error": {"name": "BadRequest", "value": str(e)}})
        code = payload.get("code", "")
        if not code:
            return self._send(400, {"error": {"name": "BadRequest", "value": "no code provided"}})
        self._send(200, run_code(code, payload.get("env")))

    def log_message(self, *args):  # keep the VM logs quiet
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
