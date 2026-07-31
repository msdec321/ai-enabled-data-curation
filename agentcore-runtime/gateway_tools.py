"""Tools for the AutoDQA agent, sourced from the AgentCore Gateway over MCP.

Ports the plumbing the TS Worker did inline (orchestrator-worker/src/index.ts)
onto the Python/LangGraph agent:

  - Cognito client-credentials OAuth (getOAuthToken, index.ts:95)  -> get_oauth_token()
  - MCP streamable-HTTP tool catalog (McpClient/listTools)         -> load_gateway_tools()
  - de-prefix `<target>___<tool>` for the model (toBedrockTools)
  - inject a per-run `session` arg into every call (index.ts:388)  -> current_session + wrapper
  - keep `destroy_sandbox` out of the model's hands (INTERNAL_TOOLS, index.ts:185)
  - tear the per-run sandbox down at the end (teardown, index.ts:321) -> teardown()

The MCP wire protocol itself is handled by langchain-mcp-adapters; we add the
session-injection / internal-tool layer on top.
"""
import contextvars
import os
import time

import requests
from langchain_core.tools import StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient

# Lifecycle tools the orchestrator drives directly — never shown to the model.
INTERNAL_TOOLS = {"destroy_sandbox"}

# The current run's sandbox session id. Set per invocation in entrypoint.py and
# read by the tool wrappers below at call time, so each run routes to its own
# ephemeral sandbox container. (contextvars keeps this correct under concurrency.)
current_session = contextvars.ContextVar("gateway_session", default="autodqa")

# (Concurrent sandbox calls no longer need a lock: on Lambda MicroVMs the per-run
# VM launch is idempotent by session `clientToken`, and the sandbox server is
# single-threaded with per-request env save/restore — so batched calls can't clash
# on the process-global os.environ. The old lock existed only to serialize the
# now-removed cold `pip install python-tds`.)

_token_cache = {"token": None, "exp": 0.0}


def get_oauth_token() -> str:
    """Cognito client-credentials grant for the AgentCore Gateway (mirrors index.ts:95)."""
    now = time.time()
    if _token_cache["token"] and _token_cache["exp"] > now + 60:
        return _token_cache["token"]
    data = {
        "grant_type": "client_credentials",
        "client_id": os.environ["COGNITO_CLIENT_ID"],
        "client_secret": os.environ["COGNITO_CLIENT_SECRET"],
    }
    scope = os.environ.get("COGNITO_SCOPE")
    if scope:
        data["scope"] = scope
    resp = requests.post(os.environ["COGNITO_TOKEN_ENDPOINT"], data=data, timeout=15)
    resp.raise_for_status()
    body = resp.json()
    _token_cache["token"] = body["access_token"]
    _token_cache["exp"] = now + body.get("expires_in", 3600)
    return _token_cache["token"]


def _with_session(tool: StructuredTool, display_name: str) -> StructuredTool:
    """Wrap a gateway tool so the per-run `session` is injected at call time and
    the model-facing name is de-prefixed. Any `session` the model sets is
    overridden — the orchestrator owns sandbox routing, not the model.

    VERIFY on first live deploy: we keep the original args_schema, so the model
    still *sees* a `session` field. The Worker strips it (ORCHESTRATOR_ARGS,
    index.ts:187). Harmless here because we override it, but worth hiding once we
    confirm the gateway tools' inputSchema shape.
    """

    async def _call(**kwargs):
        kwargs["session"] = current_session.get()
        return await tool.ainvoke(kwargs)

    return StructuredTool(
        name=display_name,
        description=tool.description or "",
        args_schema=tool.args_schema,
        coroutine=_call,
    )


async def load_gateway_tools():
    """Return (model_tools, internal_tools) from the gateway's MCP catalog.

    VERIFY on first live deploy: the streamable_http connection dict shape and
    that custom `headers` (our bearer token) are honored by the installed
    langchain-mcp-adapters version.
    """
    client = MultiServerMCPClient(
        {
            "agentcore": {
                "transport": "streamable_http",
                "url": os.environ["AGENTCORE_GATEWAY_URL"],
                "headers": {"Authorization": f"Bearer {get_oauth_token()}"},
            }
        }
    )
    model_tools, internal = [], {}
    for tool in await client.get_tools():
        short = tool.name.split("___")[-1]
        if short in INTERNAL_TOOLS:
            internal[short] = tool
            continue
        model_tools.append(_with_session(tool, short))
    return model_tools, internal


async def teardown(internal: dict, session_id: str) -> None:
    """Best-effort destroy of this run's sandbox container (mirrors index.ts:321).

    A failure here is swallowed: the sandbox's own sleepAfter reclaims it, exactly
    as in the Worker.
    """
    destroy = internal.get("destroy_sandbox")
    if destroy is None:
        return
    try:
        await destroy.ainvoke({"session": session_id})
    except Exception:
        pass
