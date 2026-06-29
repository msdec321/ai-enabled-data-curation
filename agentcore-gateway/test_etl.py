#!/usr/bin/env python3
"""Smoke test the ETL tools through the AgentCore Gateway, end to end:

  Cognito token -> MCP -> list_etl / grep_etl / read_etl -> Lambda -> ETL bridge
  (ngrok) -> the local read-only file server.

Also re-checks that the denylist + path jail hold through the FULL cloud path,
not just at the bridge. Requires the bridge + `ngrok http 8000` running and
ETL_BRIDGE_URL set on the Lambda.

    ../.venv/bin/python test_etl.py
"""
import asyncio
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

CONFIG = Path(__file__).parent / ".gateway_config.json"


def fetch_token(cog: dict) -> str:
    data = {"grant_type": "client_credentials", "client_id": cog["client_id"],
            "client_secret": cog["client_secret"]}
    if cog.get("scope"):
        data["scope"] = cog["scope"]
    req = urllib.request.Request(
        cog["token_endpoint"], data=urllib.parse.urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["access_token"]


def text_of(result) -> str:
    return "".join(c.text for c in result.content if hasattr(c, "text"))


def decode(text: str):
    """Gateway JSON-encodes the Lambda return; decode once to the real value."""
    try:
        return json.loads(text)
    except ValueError:
        return text


SESSION = "etl-smoketest"  # one ephemeral container shared by every call below


async def main() -> None:
    cfg = json.loads(CONFIG.read_text())
    token = fetch_token(cfg["cognito"])
    print("got Cognito token")
    fails = []
    async with streamablehttp_client(cfg["gateway_url"], headers={"Authorization": f"Bearer {token}"}) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = [t.name for t in (await s.list_tools()).tools]
            print("tools/list:", names)
            tool = lambda suffix: next((n for n in names if n.endswith(suffix)), None)

            # 1. list_etl -> structured {root, count, files}. First call cold-starts
            #    the sandbox container (ETL reads now run IN the sandbox).
            print(f"(first call cold-starts container {SESSION!r}...)")
            out = decode(text_of(await s.call_tool(tool("list_etl"), {"session": SESSION})))
            if isinstance(out, dict) and out.get("count"):
                sample = out["files"][0]["path"]
                print(f"list_etl -> {out['count']} files; first = {sample}")
            else:
                fails.append(f"list_etl unexpected: {str(out)[:200]}")
                sample = "MapProviders.sql"

            # 2. grep_etl q=SEX -> matches (the data-quality -> ETL trail)
            out = decode(text_of(await s.call_tool(tool("grep_etl"), {"q": "SEX", "session": SESSION})))
            if isinstance(out, dict) and out.get("count"):
                m = out["matches"][0]
                print(f"grep_etl q=SEX -> {out['count']} matches; first = {m['path']}:{m['line']}")
            else:
                fails.append(f"grep_etl unexpected: {str(out)[:200]}")

            # 3. read_etl a real file (reuses the same warm container)
            out = decode(text_of(await s.call_tool(tool("read_etl"), {"path": sample, "session": SESSION})))
            if isinstance(out, str) and out and "error" not in out[:40].lower():
                print(f"read_etl {sample} -> {len(out)} chars; first line: {out.splitlines()[0][:60]!r}")
            else:
                fails.append(f"read_etl unexpected: {str(out)[:200]}")

            # 4. SECURITY: denylisted token file must NOT be served through the gateway
            out = decode(text_of(await s.call_tool(tool("read_etl"), {"path": "procedures/etl.load_hash_token", "session": SESSION})))
            print(f"read_etl token-file (expect denied) -> {str(out)[:120]}")
            if "root:" in str(out) or "CREATE" in str(out).upper() or (isinstance(out, str) and len(out) > 200):
                fails.append("DENYLIST BREACH: the hash-token file was served!")

            # 5. SECURITY: path traversal must be denied through the gateway
            out = decode(text_of(await s.call_tool(tool("read_etl"), {"path": "../../../../etc/passwd", "session": SESSION})))
            print(f"read_etl traversal (expect denied) -> {str(out)[:120]}")
            if "root:" in str(out):
                fails.append("TRAVERSAL BREACH: /etc/passwd was served!")

            # 6. ephemeral lifecycle: tear the container down (as the orchestrator does at run end)
            out = decode(text_of(await s.call_tool(tool("destroy_sandbox"), {"session": SESSION})))
            print(f"destroy_sandbox -> {str(out)[:120]}")
            if not (isinstance(out, str) and "destroyed" in out):
                fails.append(f"destroy_sandbox did not confirm teardown: {str(out)[:200]}")

    if fails:
        print("\nFAILURES:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("\nOK: ETL tools work end to end; denylist + jail hold through the gateway")


if __name__ == "__main__":
    asyncio.run(main())
