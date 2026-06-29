#!/usr/bin/env python3
"""Smoke test get_valuesets through the AgentCore Gateway (no AWS creds needed):
Cognito token -> MCP -> get_valuesets -> Lambda (bundled catalog) -> result.

    ../.venv/bin/python test_valuesets.py
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


def fetch_token(cog):
    data = {"grant_type": "client_credentials", "client_id": cog["client_id"],
            "client_secret": cog["client_secret"]}
    if cog.get("scope"):
        data["scope"] = cog["scope"]
    req = urllib.request.Request(cog["token_endpoint"],
                                 data=urllib.parse.urlencode(data).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["access_token"]


def decode(result):
    txt = "".join(c.text for c in result.content if hasattr(c, "text"))
    try:
        return json.loads(txt)
    except ValueError:
        return txt


SESSION = "valuesets-smoketest"  # one ephemeral container shared by every call


async def main():
    cfg = json.loads(CONFIG.read_text())
    token = fetch_token(cfg["cognito"])
    print("got Cognito token")
    fails = []
    async with streamablehttp_client(cfg["gateway_url"], headers={"Authorization": f"Bearer {token}"}) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = [t.name for t in (await s.list_tools()).tools]
            tool = next((n for n in names if n.endswith("get_valuesets")), None)
            destroy = next((n for n in names if n.endswith("destroy_sandbox")), None)
            print("get_valuesets present:", bool(tool))
            if not tool:
                sys.exit("FAIL: get_valuesets not in gateway catalog")

            # 1. no args -> tables list. First call cold-starts the sandbox
            #    container (get_valuesets now runs IN the sandbox, like every tool).
            print(f"(first call cold-starts container {SESSION!r}...)")
            out = decode(await s.call_tool(tool, {"session": SESSION}))
            n = len(out.get("tables_with_valuesets", [])) if isinstance(out, dict) else 0
            print(f"get_valuesets() -> {n} tables")
            if n < 20:
                fails.append(f"expected many tables, got {out}")

            # 2. DEMOGRAPHIC -> SEX matches the user's reference exactly
            out = decode(await s.call_tool(tool, {"table": "DEMOGRAPHIC", "session": SESSION}))
            sex = out.get("valuesets", {}).get("SEX", {}).get("values") if isinstance(out, dict) else None
            print(f"DEMOGRAPHIC.SEX -> {sex}")
            if sex != {"A": "Ambiguous", "F": "Female", "M": "Male",
                       "NI": "No information", "UN": "Unknown", "OT": "Other"}:
                fails.append(f"SEX valueset wrong: {sex}")
            # large set summarized, not dumped
            lang = out.get("valuesets", {}).get("PAT_PREF_LANGUAGE_SPOKEN", {})
            if "values" in lang or "note" not in lang:
                fails.append(f"large set should be summarized at table level: {lang}")

            # 3. one large column on demand -> full list
            out = decode(await s.call_tool(tool, {"table": "ENCOUNTER", "column": "FACILITY_TYPE", "session": SESSION}))
            nv = len(out.get("values", {})) if isinstance(out, dict) else 0
            print(f"ENCOUNTER.FACILITY_TYPE (full) -> {nv} codes")
            if nv < 100:
                fails.append(f"expected full FACILITY_TYPE list, got {nv}")

            # 4. unconstrained column -> clear, non-crashing error
            out = decode(await s.call_tool(tool, {"table": "DEMOGRAPHIC", "column": "PATID", "session": SESSION}))
            print(f"DEMOGRAPHIC.PATID -> {out.get('error') if isinstance(out, dict) else out}")

            # 5. ephemeral lifecycle: tear the container down
            if destroy:
                out = decode(await s.call_tool(destroy, {"session": SESSION}))
                print(f"destroy_sandbox -> {out}")
                if not (isinstance(out, str) and "destroyed" in out):
                    fails.append(f"destroy_sandbox did not confirm teardown: {out}")

    if fails:
        print("\nFAILURES:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("\nOK: get_valuesets works end to end through the gateway")


if __name__ == "__main__":
    asyncio.run(main())
