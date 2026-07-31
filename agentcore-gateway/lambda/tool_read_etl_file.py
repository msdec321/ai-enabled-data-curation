"""read_etl_file tool: read a file from the ETL source, by line window.

Reads the SAME source as read_etl -- the synthetic ETL repo the sandbox clones from
GitLab -- so a path returned by list_etl or grep_etl works here. It previously read a
13-file corpus bundled into the Lambda with an entirely different path layout, which
made the two families of ETL tools mutually incompatible.

Differs from read_etl only in supporting start_line / max_lines pagination, which is
useful for long stored procedures. Contract preserved so the gateway catalog and the
agent's prompt are unchanged.
"""
import etl_sandbox


def handle(event):
    path = (event.get("path") or "").strip()
    if not path:
        return {"error": "no path provided (use a path from list_etl, grep_etl or search_etl)"}
    session = event.get("session") or "etl"
    env = {
        "ETL_PATH": path,
        # Always set both so the reader takes its line-window branch, giving this tool
        # the total_lines / lines_returned / truncated metadata its contract promises.
        "ETL_START_LINE": str(int(event.get("start_line") or 0)),
        "ETL_MAX_LINES": str(int(event.get("max_lines") or 400)),
    }
    try:
        return etl_sandbox.call("read", env, session=session)
    except etl_sandbox.EtlSandboxError as e:
        return {"error": f"etl source: {e}"}
    except Exception as e:
        return {"error": f"read_etl_file failed: {type(e).__name__}: {e}"}
