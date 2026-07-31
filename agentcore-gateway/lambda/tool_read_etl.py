"""read_etl tool: read one ETL source file by its repo-relative path from the synthetic
ETL repo the sandbox clones from GitLab. The reader jails the path to the clone root and
refuses secret/data files (same allowlist/denylist the bridge enforced), plus .git — the
clone is --depth 1 so history cannot narrate the injected defects."""
import etl_sandbox


def handle(event):
    path = (event.get("path") or "").strip()
    if not path:
        return {"error": "no path provided (use a repo-relative path from list_etl)"}
    session = event.get("session") or "etl"
    try:
        return etl_sandbox.call("read", {"ETL_PATH": path}, session=session)
    except etl_sandbox.EtlSandboxError as e:
        return {"error": f"etl source: {e}"}
    except Exception as e:
        return {"error": f"read_etl failed: {type(e).__name__}: {e}"}
