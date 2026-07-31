"""grep_etl tool: search the ETL codebase for a pattern, returning matching
file/line/text. Reads the clone the sandbox takes from GitLab. The workhorse for
tracing a data-quality finding to the ETL logic that produced it (grep a column or
table name)."""
import etl_sandbox


def handle(event):
    q = (event.get("q") or event.get("query") or "").strip()
    if not q:
        return {"error": "no search pattern provided (set q)"}
    session = event.get("session") or "etl"
    env = {"ETL_Q": q}
    if event.get("regex"):
        env["ETL_REGEX"] = "1"
    if event.get("max"):
        env["ETL_MAX"] = str(event["max"])
    try:
        return etl_sandbox.call("grep", env, session=session)
    except etl_sandbox.EtlSandboxError as e:
        return {"error": f"etl source: {e}"}
    except Exception as e:
        return {"error": f"grep_etl failed: {type(e).__name__}: {e}"}
