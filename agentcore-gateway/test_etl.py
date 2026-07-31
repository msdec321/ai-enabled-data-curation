#!/usr/bin/env python3
"""Smoke test the ETL tools through the AgentCore Gateway, end to end:

  Cognito token -> MCP -> the five ETL tools -> Lambda -> sandbox microVM ->
  git clone of the SYNTHETIC ETL repo from GitLab over SSH.

Also re-checks that the denylist + path jail hold through the FULL cloud path.
Needs no local process: the bridge and its ngrok tunnel are retired, and so is the
snapshot that used to be baked into the image at /opt/etl. What it DOES need is
GITLAB_SECRET_ARN + GITLAB_ETL_REPO set on the Lambda (setup_gateway.py does both)
and network reachability from the sandbox to gitpapl1 over the VPC egress connector.

    ../.venv/bin/python test_etl.py
"""
import asyncio
import json
import sys
import urllib.parse
import urllib.request
import uuid
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


def denial(out):
    """Classify a security probe: '' if properly refused, else why it does not count.

    Distinguishes a REFUSAL from a TRANSPORT FAILURE on purpose. Asserting only
    "no content came back" would let a dead sandbox pass every security check in this
    file — the probes would look green precisely when nothing ran.
    """
    if not isinstance(out, dict):
        return f"expected an error envelope, got {str(out)[:120]}"
    if out.get("content"):
        return f"CONTENT SERVED: {str(out['content'])[:120]}"
    err = str(out.get("error", ""))
    if "not served" not in err:
        return f"refused, but not by the path jail/denylist: {err[:160]}"
    return ""


# One ephemeral container shared by every call below, but UNIQUE PER RUN — same as the
# runtime injects. A fixed name looks tidier and is a trap: the run ends in
# destroy_sandbox, so the next run replays a clientToken whose VM is TERMINATED, and
# run-microvm's idempotency hands the dead VM straight back.
SESSION = f"etl-smoketest-{uuid.uuid4().hex[:8]}"

# Where the clone lands inside the sandbox (etl_sandbox.ETL_ROOT). Asserted rather than
# assumed: if a stale Lambda were still serving the retired baked snapshot, every other
# check here would pass while the agent read ETL that predates the last git push.
EXPECT_ROOT = "/tmp/etl-repo"
MIN_FILES = 150  # the synthetic tree is ~204 served files; a 13-file answer means the
                 # long-dead Lambda-bundled corpus somehow won again.


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

            # 1. list_etl -> structured {root, count, files, _clone}. The first call
            #    cold-starts the sandbox AND clones the repo, so it is the slow one;
            #    every later call in this session reuses both.
            print(f"(first call cold-starts container {SESSION!r} and clones the repo...)")
            out = decode(text_of(await s.call_tool(tool("list_etl"), {"session": SESSION})))
            if isinstance(out, dict) and out.get("count"):
                sample = out["files"][0]["path"]
                clone = out.get("_clone") or {}
                print(f"list_etl -> {out['count']} files under {out.get('root')}; first = {sample}")
                print(f"  clone: {clone}")
                if out.get("root") != EXPECT_ROOT:
                    fails.append(f"served from {out.get('root')!r}, expected {EXPECT_ROOT!r} — "
                                 "a stale Lambda is still reading the retired baked snapshot")
                if not clone.get("cloned"):
                    fails.append(f"first call did not clone (got {clone!r}) — the repo should "
                                 "not already exist in a cold container")
                if out["count"] < MIN_FILES:
                    fails.append(f"only {out['count']} files served, expected >= {MIN_FILES}")
                if any(f["path"].startswith(".git/") for f in out["files"]):
                    fails.append("DENYLIST BREACH: .git contents are being served")
            else:
                fails.append(f"list_etl unexpected: {str(out)[:300]}")
                sample = "CDW/views/etl.DEMOGRAPHIC.sql"

            # 2. grep_etl q=SEX -> matches (the data-quality -> ETL trail). Also proves
            #    the clone is REUSED rather than re-cloned on every tool call.
            out = decode(text_of(await s.call_tool(tool("grep_etl"), {"q": "SEX", "session": SESSION})))
            if isinstance(out, dict) and out.get("count"):
                m = out["matches"][0]
                print(f"grep_etl q=SEX -> {out['count']} matches; first = {m['path']}:{m['line']}")
                if not (out.get("_clone") or {}).get("reused"):
                    fails.append(f"second call re-cloned instead of reusing: {out.get('_clone')!r}")
            else:
                fails.append(f"grep_etl unexpected: {str(out)[:200]}")

            # 3. read_etl a real file (reuses the same warm container). Returns the full
            #    envelope {path, bytes, truncated, content}, not a bare string — the
            #    bridge-era shape this test used to assert.
            out = decode(text_of(await s.call_tool(tool("read_etl"), {"path": sample, "session": SESSION})))
            body = out.get("content") if isinstance(out, dict) else None
            if body:
                print(f"read_etl {sample} -> {out['bytes']} bytes; "
                      f"first non-empty line: {next(l for l in body.splitlines() if l.strip())[:60]!r}")
            else:
                fails.append(f"read_etl unexpected: {str(out)[:200]}")

            # 3b. The other two ETL tools read the SAME clone. They used to read a
            #     13-file Lambda-bundled corpus with an incompatible path layout, so a
            #     path from list_etl was unusable here — check that stays fixed.
            out = decode(text_of(await s.call_tool(tool("read_etl_file"),
                                                   {"path": sample, "max_lines": 5, "session": SESSION})))
            if isinstance(out, dict) and out.get("lines_returned"):
                print(f"read_etl_file {sample} -> {out['lines_returned']}/{out.get('total_lines')} lines")
            else:
                fails.append(f"read_etl_file could not read a path list_etl returned: {str(out)[:200]}")

            out = decode(text_of(await s.call_tool(tool("search_etl"),
                                                   {"query": "EpicOnly", "session": SESSION})))
            hits = out.get("count") if isinstance(out, dict) else None
            print(f"search_etl q=EpicOnly -> {hits} matches")
            if not hits:
                fails.append(f"search_etl found no EpicOnly hits: {str(out)[:200]}")

            # 4-5. SECURITY: every one of these must come back refused BY THE JAIL /
            #      DENYLIST, through the full cloud path.
            #        - the hash-token procedure carries the Datavant de-id token
            #        - traversal must not escape the clone root
            #        - .git must stay unreadable: the clone is --depth 1 precisely so
            #          history cannot narrate the injected defects, and serving objects
            #          or packed refs would hand over the answer key by another route
            #        - the GitLab private key is written into this VM to clone with. It
            #          is removed in a finally block; confirm the reader cannot reach it
            #          even if that ever regressed.
            probes = [
                ("denylist (hash-token)", "procedures/etl.load_hash_token"),
                ("traversal (/etc/passwd)", "../../../../etc/passwd"),
                ("git history", ".git/config"),
                ("git history", ".git/packed-refs"),
                ("git history", ".git/HEAD"),
                ("gitlab private key", "../../root/.ssh/autodqa_gitlab"),
            ]
            for label, probe in probes:
                out = decode(text_of(await s.call_tool(tool("read_etl"), {"path": probe, "session": SESSION})))
                why = denial(out)
                print(f"  {label:<24} {probe:<38} -> {'refused' if not why else why[:90]}")
                if why:
                    fails.append(f"{label} probe {probe!r}: {why}")

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
