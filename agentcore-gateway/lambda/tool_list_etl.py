"""list_etl tool: list the ETL repo's files (paths + sizes) from the clone the sandbox
takes from GitLab. Use to understand the ETL codebase structure before reading or
searching."""
import etl_sandbox


def handle(event):
    session = event.get("session") or "etl"
    try:
        return etl_sandbox.call("list", session=session)
    except etl_sandbox.EtlSandboxError as e:
        return {"error": f"etl source: {e}"}
    except Exception as e:
        return {"error": f"list_etl failed: {type(e).__name__}: {e}"}
