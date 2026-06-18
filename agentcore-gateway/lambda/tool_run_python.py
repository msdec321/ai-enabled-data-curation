"""run_python tool: execute arbitrary Python in the sandbox (no data access)."""
import sandbox_client


def handle(event):
    data = sandbox_client.run_in_sandbox(event.get("code", ""), session=event.get("session", "agentcore"))
    if data.get("error"):
        e = data["error"]
        if isinstance(e, dict):
            return f"Error: {e.get('name')}: {e.get('value') or e.get('message')}"
        return f"Error: {e}"
    texts = [r["text"] for r in data.get("results", []) if r.get("text")]
    return "\n".join(filter(None, [data.get("stdout", ""), *texts])).strip() or "(no output)"
