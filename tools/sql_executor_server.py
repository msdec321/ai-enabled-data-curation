"""AutoDQA — SQL Executor MCP Server

Read-only T-SQL execution against a SQL Server clinical data warehouse.
All queries are validated to be read-only before execution.
"""

import json
import os
import sys
import pyodbc
import yaml
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("sql_executor")

_connection = None
_db_config = None

FORBIDDEN_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "TRUNCATE",
    "EXEC", "EXECUTE", "MERGE", "GRANT", "REVOKE", "DENY",
    "sp_", "xp_", "OPENROWSET", "OPENQUERY", "OPENDATASOURCE",
    "BULK", "INTO",  # SELECT INTO
    "DBCC", "BACKUP", "RESTORE", "SHUTDOWN",
]


def _load_config():
    global _db_config
    config_path = os.environ.get("DB_CONFIG")
    if not config_path:
        raise RuntimeError("DB_CONFIG environment variable not set")
    with open(config_path, "r") as f:
        _db_config = yaml.safe_load(f)
    return _db_config


def _get_connection():
    global _connection
    if _connection is not None:
        try:
            _connection.cursor().execute("SELECT 1")
            return _connection
        except Exception:
            _connection = None

    config = _db_config or _load_config()
    conn_cfg = config["connection"]

    conn_str = (
        f"DRIVER={{{conn_cfg['driver']}}};"
        f"SERVER={conn_cfg['server']};"
        f"DATABASE={conn_cfg['database']};"
    )

    if conn_cfg.get("uid"):
        conn_str += f"UID={conn_cfg['uid']};PWD={conn_cfg['pwd']};"
    else:
        conn_str += f"Trusted_Connection={conn_cfg.get('trusted_connection', 'yes')};"

    if conn_cfg.get("TrustServerCertificate"):
        conn_str += f"TrustServerCertificate={conn_cfg['TrustServerCertificate']};"

    _connection = pyodbc.connect(conn_str, readonly=True)
    _connection.autocommit = True
    return _connection


def _validate_readonly(query: str) -> None:
    """Reject queries that could modify data."""
    normalized = " ".join(query.upper().split())

    for keyword in FORBIDDEN_KEYWORDS:
        padded = f" {keyword} "
        if padded in f" {normalized} ":
            if keyword == "INTO" and "SELECT" in normalized:
                idx_into = normalized.index("INTO")
                idx_select = normalized.index("SELECT")
                if idx_into > idx_select:
                    raise ValueError(
                        f"SELECT INTO is not allowed. Read-only access only."
                    )
            else:
                raise ValueError(
                    f"Keyword '{keyword}' is not allowed. Read-only access only."
                )


@mcp.tool()
def execute_sql(query: str) -> str:
    """Execute a read-only T-SQL query against the CDW and return results as JSON.

    Args:
        query: A SELECT query to execute. INSERT/UPDATE/DELETE/DDL are rejected.

    Returns:
        JSON string with columns and rows, or an error message.
    """
    try:
        _validate_readonly(query)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute(query)

        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = []
        for row in cursor.fetchmany(10000):
            rows.append({col: _serialize(val) for col, val in zip(columns, row)})

        truncated = len(rows) == 10000
        result = {
            "columns": columns,
            "row_count": len(rows),
            "truncated": truncated,
            "rows": rows,
        }
        if truncated:
            result["note"] = "Results truncated at 10,000 rows. Add TOP or WHERE to narrow."

        return json.dumps(result, default=str)

    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {str(e)}"})


def _serialize(val):
    """Convert pyodbc values to JSON-serializable types."""
    if val is None:
        return None
    if isinstance(val, (int, float, str, bool)):
        return val
    if isinstance(val, bytes):
        return val.hex()
    return str(val)


if __name__ == "__main__":
    _load_config()
    mcp.run()
