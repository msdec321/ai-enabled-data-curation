"""AgentCore Runtime entrypoint for the AutoDQA agent.

Streams via LangGraph `astream_events` (not `astream(stream_mode="values")`), which
surfaces per-step hooks — on_chat_model_start/end, on_tool_start/end. We map those to:
  - AIMessage / ToolMessage events (the chat log the SPA already renders), and
  - `trace` events that drive the architecture-diagram glow: which nodes are on the
    active path right now (reasoning → Bedrock; a tool → its backend path).

Fidelity note: the gateway Lambda's internal steps (authorize → vault → sandbox → CDW)
are opaque to the runtime, so a tool lights its WHOLE known path at once rather than
sequencing the sub-steps.

Keeps the heartbeat (so a slow tool can't read-time-out the client) and per-run
sandbox session + teardown. Tool-call serialization lives in gateway_tools.py.
"""
import asyncio
import json
import uuid

from bedrock_agentcore import BedrockAgentCoreApp

from agent import build_agent, build_llm
from gateway_tools import current_session, load_gateway_tools, teardown

app = BedrockAgentCoreApp()

# Emit a keepalive at least this often so the SSE stream never goes silent during
# a long tool call. Clients ignore `heartbeat` events.
HEARTBEAT_SECS = 10

# Diagram node ids (must match the SVG data-id attributes) lit while the model reasons.
REASONING_TRACE = (["orchestrator", "aigw", "bedrock"], "Reasoning with the model")
# Per-tool active path + caption. Unknown tools fall back to DEFAULT_TOOL_TRACE.
_ETL = (["orchestrator", "broker", "vault", "sandbox", "docstore"], None)
TOOL_TRACE = {
    "query_cdw": (["orchestrator", "broker", "registry", "vault", "sandbox", "cdw"], "Querying the CDW"),
    "run_python": (["orchestrator", "broker", "vault", "sandbox"], "Running code in the sandbox"),
    "get_valuesets": (["orchestrator", "broker", "vault", "sandbox"], "Looking up CDM valuesets"),
    "list_etl": (_ETL[0], "Browsing the ETL codebase"),
    "read_etl": (_ETL[0], "Reading ETL code"),
    "read_etl_file": (_ETL[0], "Reading ETL code"),
    "grep_etl": (_ETL[0], "Searching the ETL codebase"),
    "search_etl": (_ETL[0], "Searching the ETL codebase"),
}
DEFAULT_TOOL_TRACE = (["orchestrator", "broker", "vault", "sandbox"], "Running a tool")


def _normalize_ai_content(content):
    """Match the message shape the SPA expects: text blocks kept as-is, tool_use
    blocks with `input` parsed from JSON string → dict and the streaming `index`
    dropped. A plain-string content (rare) passes through untouched."""
    if not isinstance(content, list):
        return content
    out = []
    for b in content:
        t = b.get("type")
        if t == "text" and b.get("text"):
            out.append({"type": "text", "text": b["text"]})
        elif t == "tool_use":
            inp = b.get("input")
            if isinstance(inp, str):
                try:
                    inp = json.loads(inp)
                except (ValueError, TypeError):
                    pass
            out.append({"type": "tool_use", "name": b.get("name"), "input": inp, "id": b.get("id")})
    return out


@app.entrypoint
async def invoke(payload, context=None):
    task = (payload or {}).get("task") or (payload or {}).get("prompt") or ""
    if not task.strip():
        yield {"type": "error", "message": "missing 'task' in payload"}
        return

    session_id = (payload or {}).get("session") or f"run-{uuid.uuid4()}"
    tools, internal = await load_gateway_tools()
    agent = build_agent(build_llm(), tools)

    token = current_session.set(session_id)  # captured by the producer task
    queue: asyncio.Queue = asyncio.Queue()

    async def produce():
        try:
            async for ev in agent.astream_events({"messages": [("user", task)]}, version="v2"):
                et = ev["event"]
                name = ev.get("name", "")
                if et == "on_chat_model_start":
                    active, label = REASONING_TRACE
                    await queue.put({"type": "trace", "active": active, "label": label})
                elif et == "on_chat_model_end":
                    out = ev["data"].get("output")
                    content = getattr(out, "content", out)
                    await queue.put({"type": "AIMessage", "content": _normalize_ai_content(content)})
                elif et == "on_tool_start" and "___" not in name:  # outer wrapper, not the MCP tool
                    active, label = TOOL_TRACE.get(name, DEFAULT_TOOL_TRACE)
                    await queue.put({"type": "trace", "active": active, "label": label or f"Running {name}"})
                elif et == "on_tool_end" and "___" not in name:
                    out = ev["data"].get("output")
                    content = getattr(out, "content", out)
                    await queue.put({"type": "ToolMessage", "content": content})
        except Exception as e:  # surface to the client instead of stalling
            await queue.put({"type": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            await queue.put({"type": "trace", "active": []})  # clear the diagram
            await queue.put(None)

    producer = asyncio.create_task(produce())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECS)
            except asyncio.TimeoutError:
                yield {"type": "heartbeat"}  # keepalive during long tool calls
                continue
            if item is None:
                break
            yield item
    finally:
        producer.cancel()
        current_session.reset(token)
        await teardown(internal, session_id)


if __name__ == "__main__":
    app.run()
