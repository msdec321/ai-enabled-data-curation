"""grep_etl tool: search the ETL codebase for a pattern via the bridge, returning
matching file/line/text. The workhorse for tracing a data-quality finding to the
ETL logic that produced it (grep a column or table name)."""
import json

import etl_client
import registry


def handle(event):
    q = (event.get("q") or event.get("query") or "").strip()
    if not q:
        return {"error": "no search pattern provided (set q)"}
    session = event.get("session") or "etl"
    params = {"q": q}
    if event.get("regex"):
        params["regex"] = "1"
    if event.get("max"):
        params["max"] = str(event["max"])
    try:
        body = etl_client.call("grep_etl", "/grep", params, session=session)
        try:
            return json.loads(body)  # return structured JSON, not a re-encoded string
        except ValueError:
            return body
    except registry.NotAuthorized as e:
        return {"error": f"not authorized: {e}"}
    except etl_client.EtlBridgeError as e:
        return {"error": f"etl bridge: {e}"}
    except Exception as e:
        return {"error": f"grep_etl failed: {type(e).__name__}: {e}"}
