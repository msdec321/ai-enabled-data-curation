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
import os
import uuid

from bedrock_agentcore import BedrockAgentCoreApp
from langgraph.errors import GraphRecursionError

from agent import build_agent, build_llm
from gateway_tools import current_session, load_gateway_tools, teardown

app = BedrockAgentCoreApp()

# Emit a keepalive at least this often so the SSE stream never goes silent during
# a long tool call. Clients ignore `heartbeat` events.
HEARTBEAT_SECS = 10

# Max GRAPH STEPS per run, passed to LangGraph as recursion_limit.
#
# Steps are not conversational turns: a ReAct cycle traverses the model node then the
# tool node, so one reason->act->observe cycle costs ~2 steps. 100 therefore allows
# roughly 50 tool-using cycles. Double it if you want ~100 full cycles.
#
# LangGraph's default is 25, which silently truncated column-by-column profiling runs:
# the model would emit its next tool calls, the graph would refuse to continue, and the
# stream just stopped with no explanation. Set explicitly so the ceiling is visible and
# tunable rather than an invisible library default -- and so hitting it is REPORTED
# (see the GraphRecursionError branch in produce()).
STEP_LIMIT = int(os.environ.get("AUTODQA_STEP_LIMIT", "100"))

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
    # One microVM sandbox is launched lazily inside the broker Lambda on the first
    # sandbox tool call and released at teardown. `up` gates the spin-up/down
    # lifecycle lines so each is emitted exactly once per run.
    lifecycle = {"up": False}

    # Run-level counters, reported in the terminal `done` event so a run's ending is
    # always explainable: how much work it did, and why it stopped.
    stats = {"model_turns": 0, "tool_calls": 0, "pending_tool_calls": 0}

    async def produce():
        # Every exit path sets this, so the client is never left guessing.
        outcome = {"reason": "completed", "detail": None}
        try:
            async for ev in agent.astream_events(
                {"messages": [("user", task)]},
                version="v2",
                config={"recursion_limit": STEP_LIMIT},
            ):
                et = ev["event"]
                name = ev.get("name", "")
                if et == "on_chat_model_start":
                    active, label = REASONING_TRACE
                    await queue.put({"type": "trace", "active": active, "label": label})
                elif et == "on_chat_model_end":
                    out = ev["data"].get("output")
                    content = getattr(out, "content", out)
                    normalized = _normalize_ai_content(content)
                    stats["model_turns"] += 1
                    # Track tool calls the model REQUESTED but that have not been
                    # observed finishing. If a run ends with these outstanding it was
                    # cut off mid-cycle rather than having produced a final answer —
                    # which is exactly what the silent step-limit truncation looked like.
                    if isinstance(normalized, list):
                        stats["pending_tool_calls"] += sum(
                            1 for b in normalized if b.get("type") == "tool_use")
                    await queue.put({"type": "AIMessage", "content": normalized})
                elif et == "on_tool_start" and "___" not in name:  # outer wrapper, not the MCP tool
                    if not lifecycle["up"]:  # first sandbox tool call = the microVM cold-launches
                        lifecycle["up"] = True
                        await queue.put({"type": "sandbox", "phase": "up", "session": session_id})
                    active, label = TOOL_TRACE.get(name, DEFAULT_TOOL_TRACE)
                    await queue.put({"type": "trace", "active": active, "label": label or f"Running {name}"})
                elif et == "on_tool_end" and "___" not in name:
                    out = ev["data"].get("output")
                    content = getattr(out, "content", out)
                    stats["tool_calls"] += 1
                    stats["pending_tool_calls"] = max(0, stats["pending_tool_calls"] - 1)
                    await queue.put({"type": "ToolMessage", "content": content})
        except GraphRecursionError:
            # The graph hit its step ceiling. Previously this surfaced as the stream
            # simply ending after the model's last tool request, with no explanation.
            outcome = {
                "reason": "step_limit",
                "detail": (f"Stopped after reaching the {STEP_LIMIT}-step limit — the agent "
                           f"had not finished. Raise AUTODQA_STEP_LIMIT, or narrow the task "
                           f"(e.g. fewer columns per run)."),
            }
            await queue.put({"type": "error", "message": outcome["detail"]})
        except asyncio.CancelledError:
            outcome = {"reason": "cancelled", "detail": "Run cancelled (client disconnected or timed out)."}
            raise
        except Exception as e:  # surface to the client instead of stalling
            outcome = {"reason": "error", "detail": f"{type(e).__name__}: {e}"}
            await queue.put({"type": "error", "message": outcome["detail"]})
        finally:
            await queue.put({"type": "trace", "active": []})  # clear the diagram
            if lifecycle["up"]:  # mirror the spin-up; actual terminate runs in the outer finally
                await queue.put({"type": "sandbox", "phase": "down", "session": session_id})
            # A run that completed "normally" but still has tool calls outstanding was
            # truncated by something upstream of us, not finished — say so rather than
            # letting it read as success.
            if outcome["reason"] == "completed" and stats["pending_tool_calls"]:
                outcome = {
                    "reason": "truncated",
                    "detail": (f"Stream ended with {stats['pending_tool_calls']} tool call(s) "
                               f"still outstanding — the run was cut short before finishing."),
                }
            await queue.put({
                "type": "done",
                "reason": outcome["reason"],
                "message": outcome["detail"],
                "model_turns": stats["model_turns"],
                "tool_calls": stats["tool_calls"],
                "step_limit": STEP_LIMIT,
                "session": session_id,
            })
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
