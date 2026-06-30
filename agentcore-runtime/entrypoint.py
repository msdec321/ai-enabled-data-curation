"""AgentCore Runtime entrypoint for the AutoDQA agent.

Wraps the LangGraph agent (agent.py) in the bedrock-agentcore Runtime contract:
one @app.entrypoint async generator, auto-served as POST /invocations (SSE) and
/ws. Test locally with `agentcore launch -l` or remotely with
`agentcore invoke '{"task": "..."}'`.

Per invocation we mint a fresh per-run `session` id, inject it into every gateway
tool call (so the broker routes to this run's ephemeral sandbox), then best-effort
tear that sandbox down — the lifecycle the TS Worker's loop owned (index.ts),
ported onto LangGraph.
"""
import uuid

from bedrock_agentcore import BedrockAgentCoreApp

from agent import build_agent, build_llm
from gateway_tools import current_session, load_gateway_tools, teardown

app = BedrockAgentCoreApp()


@app.entrypoint
async def invoke(payload, context=None):
    task = (payload or {}).get("task") or (payload or {}).get("prompt") or ""
    if not task.strip():
        yield {"type": "error", "message": "missing 'task' in payload"}
        return

    # Fresh per-run sandbox session, threaded into every tool call via the
    # contextvar that gateway_tools' wrappers read.
    session_id = (payload or {}).get("session") or f"run-{uuid.uuid4()}"

    # Re-mint the OAuth token + reload the catalog each invocation so a warm
    # instance can't outlive its ~1h Cognito token. (Later optimization: cache the
    # agent and refresh the token on expiry instead of rebuilding every call.)
    tools, internal = await load_gateway_tools()
    agent = build_agent(build_llm(), tools)

    token = current_session.set(session_id)
    try:
        # stream_mode="values" yields the full message list each step (same as the
        # notebook); we surface the newest message. Refine to token-level streaming
        # ("messages" mode) when we wire a UI.
        async for chunk in agent.astream({"messages": [("user", task)]}, stream_mode="values"):
            msg = chunk["messages"][-1]
            yield {"type": type(msg).__name__, "content": getattr(msg, "content", "")}
    finally:
        current_session.reset(token)
        await teardown(internal, session_id)


if __name__ == "__main__":
    app.run()
