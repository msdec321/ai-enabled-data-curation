"""destroy_sandbox tool: tear down the ephemeral container for a run's session.

Internal lifecycle tool — the orchestrator calls it when an agent run finishes,
not the model. Goes through the broker like every other sandbox interaction.
"""
import sandbox_client


def handle(event):
    session = event.get("session")
    if not session:
        return {"error": "no session provided"}
    data = sandbox_client.destroy_sandbox(session)
    if data.get("error"):
        return {"error": f"sandbox: {data['error']}"}
    return f"destroyed sandbox session {session}" if data.get("destroyed") else str(data)
