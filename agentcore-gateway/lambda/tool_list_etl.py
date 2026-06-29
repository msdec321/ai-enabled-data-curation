"""list_etl tool: list the ETL repo's files (paths + sizes) via the bridge.
Use to understand the ETL codebase structure before reading or searching."""
import json

import etl_client
import registry


def handle(event):
    session = event.get("session") or "etl"
    try:
        body = etl_client.call("list_etl", "/list", session=session)
        try:
            return json.loads(body)  # return structured JSON, not a re-encoded string
        except ValueError:
            return body
    except registry.NotAuthorized as e:
        return {"error": f"not authorized: {e}"}
    except etl_client.EtlBridgeError as e:
        return {"error": f"etl bridge: {e}"}
    except Exception as e:
        return {"error": f"list_etl failed: {type(e).__name__}: {e}"}
