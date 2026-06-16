#!/usr/bin/env python3
"""Smoke test the AgentCore Gateway end to end, no AWS credentials needed:

  Cognito token (client credentials) -> MCP initialize -> tools/list ->
  tools/call run_python -> Cloudflare sandbox -> result.

    ../.venv/bin/python test_gateway.py
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
    data = {
        "grant_type": "client_credentials",
        "client_id": cog["client_id"],
        "client_secret": cog["client_secret"],
    }
    if cog.get("scope"):
        data["scope"] = cog["scope"]
    req = urllib.request.Request(
        cog["token_endpoint"],
        data=urllib.parse.urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["access_token"]


async def main() -> None:
    if not CONFIG.exists():
        sys.exit(f"{CONFIG} not found — run setup_gateway.py first")
    cfg = json.loads(CONFIG.read_text())
    token = fetch_token(cfg["cognito"])
    print("got Cognito access token")

    headers = {"Authorization": f"Bearer {token}"}
    failure = None
    async with streamablehttp_client(cfg["gateway_url"], headers=headers) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print("tools/list:", names)

            run_python = next((n for n in names if n.endswith("run_python")), None)
            if not run_python:
                failure = "run_python tool not found in gateway catalog"
            else:
                result = await session.call_tool(run_python, {"code": "print(21 * 2)"})
                text = "".join(c.text for c in result.content if hasattr(c, "text"))
                print(f"tools/call {run_python} ->", text)
                # the gateway JSON-encodes the Lambda's return value, so a
                # string result arrives as '"42"' — decode before comparing
                out = text.strip()
                try:
                    out = json.loads(out)
                except ValueError:
                    pass
                if result.isError or any(m in text for m in ('"errorMessage"', '"errorType"', '"error"')):
                    failure = "tool call returned an error payload (see above)"
                elif not (isinstance(out, str) and out.strip() == "42"):
                    failure = f"expected sandbox output '42', got {text!r}"

    # exit outside the async context managers to avoid exception-group noise
    if failure:
        sys.exit(f"FAIL: {failure}")
    print("OK: gateway -> lambda -> sandbox round trip works")


if __name__ == "__main__":
    asyncio.run(main())
