"""search_etl tool: case-insensitive search over the ETL source.

Reads the SAME source as grep_etl -- the synthetic ETL repo the sandbox clones from
GitLab. It previously read a 13-file corpus bundled into the Lambda, whose paths used
a different layout entirely (etl/tables/etl.DEMOGRAPHIC.View.sql vs
CDW/views/etl.DEMOGRAPHIC.sql), so a path returned by list_etl was unusable here and
the two tools disagreed about what the ETL even contained.

Kept as a distinct tool because its contract (query / max_results) is already in the
gateway catalog and the agent's prompt. It is now a thin alias of grep_etl; worth
consolidating if the tool list is ever revised.
"""
import etl_sandbox


def handle(event):
    q = (event.get("query") or event.get("q") or "").strip()
    if not q:
        return {"error": "no search pattern provided (set query)"}
    session = event.get("session") or "etl"
    env = {"ETL_Q": q}
    if event.get("max_results"):
        env["ETL_MAX"] = str(event["max_results"])
    try:
        return etl_sandbox.call("grep", env, session=session)
    except etl_sandbox.EtlSandboxError as e:
        return {"error": f"etl source: {e}"}
    except Exception as e:
        return {"error": f"search_etl failed: {type(e).__name__}: {e}"}
