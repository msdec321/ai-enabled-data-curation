"""clone_etl tool: clone the ETL repo from GitLab into the run's sandbox.

NOT yet exposed in the gateway catalog (see tool_router). Invoke the Lambda directly
to exercise it while the SSH path is being proven.
"""
import gitlab_clone


def handle(event):
    try:
        return gitlab_clone.clone(
            session=event.get("session") or "etl",
            repo=event.get("repo"),
            ref=event.get("ref"),
        )
    except gitlab_clone.GitLabError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"clone_etl failed: {type(e).__name__}: {e}"}
