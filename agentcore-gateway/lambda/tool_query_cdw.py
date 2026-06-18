"""query_cdw tool: run read-only T-SQL against a registered dataset, executed in
the sandbox via pytds (pure-Python TDS — no ODBC driver needed, so no custom
sandbox image).

Broker flow: authorize (tool, dataset) against the registry → fetch the dataset's
DB login from the vault → run a pytds script in the sandbox with the login and
connection injected as env vars (never embedded in the code body). The DB login
never transits the gateway and is never persisted in the sandbox.
"""
import json

import registry
import sandbox_client
import vault

# Pure-Python; runs INSIDE the sandbox. Reads everything from env (injected),
# connects through the tunnel, runs ONE read-only query, prints rows as JSON.
_RUNNER = r"""
import json, os, sys
try:
    import pytds
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "python-tds"], check=True)
    import pytds

conn = pytds.connect(
    server=os.environ["CDW_SERVER"], port=int(os.environ["CDW_PORT"]),
    database=os.environ["CDW_DATABASE"],
    user=os.environ["CDW_UID"], password=os.environ["CDW_PWD"],
    login_timeout=10, timeout=60, autocommit=True,
)
try:
    with conn.cursor() as cur:
        cur.execute(os.environ["CDW_SQL"])
        if cur.description:
            cols = [c[0] for c in cur.description]
            cap = int(os.environ.get("CDW_MAXROWS", "100"))
            rows = [dict(zip(cols, r)) for r in cur.fetchmany(cap)]
            out = {"columns": cols, "row_count": len(rows), "rows": rows}
            if len(rows) == cap:
                out["note"] = f"truncated at {cap} rows; add TOP/WHERE or aggregate"
            print(json.dumps(out, default=str))
        else:
            print(json.dumps({"columns": [], "row_count": 0, "rows": []}))
finally:
    conn.close()
"""

# Defense in depth; the real read-only guarantee is the db_datareader login grant.
FORBIDDEN = ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "TRUNCATE",
             "EXEC", "EXECUTE", "MERGE", "GRANT", "REVOKE", "DENY", "BACKUP",
             "RESTORE", "SHUTDOWN", "DBCC")


def _check_readonly(sql: str) -> None:
    norm = " " + " ".join(sql.upper().split()) + " "
    for kw in FORBIDDEN:
        if f" {kw} " in norm:
            raise ValueError(f"keyword {kw!r} not allowed — read-only access only")


def handle(event):
    sql = (event.get("sql") or "").strip()
    dataset = event.get("dataset") or "CDW"
    if not sql:
        return {"error": "no sql provided"}
    try:
        _check_readonly(sql)
        grant = registry.authorize("query_cdw", dataset)
        login = json.loads(vault.default().fetch(grant["credential"]))  # {"uid":.., "pwd":..}
    except registry.NotAuthorized as e:
        return {"error": f"not authorized: {e}"}
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"vault fetch failed: {type(e).__name__}: {e}"}

    conn = grant["connection"]
    env = {
        "CDW_SERVER": str(conn["server"]),
        "CDW_PORT": str(conn["port"]),
        "CDW_DATABASE": str(conn["database"]),
        "CDW_UID": login["uid"],
        "CDW_PWD": login["pwd"],
        "CDW_SQL": sql,
        "CDW_MAXROWS": "100",
    }
    # Per-run session id (passed by the orchestrator) → one ephemeral container
    # per agent run; fall back to a dataset-scoped name if none is provided.
    session = event.get("session") or f"cdw-{dataset}"
    data = sandbox_client.run_in_sandbox(_RUNNER, session=session, env=env)
    if data.get("error"):
        e = data["error"]
        return {"error": f"sandbox: {e if not isinstance(e, dict) else e.get('message') or e}"}
    out = (data.get("stdout") or "").strip()
    if data.get("stderr") and not out:
        return {"error": f"query failed: {data['stderr'][:400]}"}
    return out or "(no rows)"
