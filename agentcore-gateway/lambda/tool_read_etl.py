"""read_etl tool: read one ETL source file by its repo-relative path via the
bridge. The bridge jails the path to the ETL root and refuses secret/data files."""
import etl_client
import registry


def handle(event):
    path = (event.get("path") or "").strip()
    if not path:
        return {"error": "no path provided (use a repo-relative path from list_etl)"}
    session = event.get("session") or "etl"
    try:
        return etl_client.call("read_etl", "/read", {"path": path}, session=session)
    except registry.NotAuthorized as e:
        return {"error": f"not authorized: {e}"}
    except etl_client.EtlBridgeError as e:
        return {"error": f"etl bridge: {e}"}
    except Exception as e:
        return {"error": f"read_etl failed: {type(e).__name__}: {e}"}
