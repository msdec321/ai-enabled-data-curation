"""AutoDQA — Spec Query MCP Server

Query PCORnet data model specification tables (pcornet_fields, pcornet_valuesets,
pcornet_constraints) from the CDW to build expectations for data quality checks.
"""

import json
import os
import pyodbc
import yaml
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("spec_query")

_connection = None
_db_config = None


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
        f"Trusted_Connection={conn_cfg.get('trusted_connection', 'yes')};"
    )

    if conn_cfg.get("uid"):
        conn_str += f"UID={conn_cfg['uid']};PWD={conn_cfg['pwd']};"

    _connection = pyodbc.connect(conn_str, readonly=True)
    _connection.autocommit = True
    return _connection


def _serialize(val):
    if val is None:
        return None
    if isinstance(val, (int, float, str, bool)):
        return val
    return str(val)


@mcp.tool()
def get_table_spec(table_name: str) -> str:
    """Get the PCORnet field specification for a table.

    Queries pcornet_fields to return column names, data types, nullable flags,
    and descriptions for all columns in the specified table.

    Args:
        table_name: PCORnet table name (e.g., DEMOGRAPHIC, ENCOUNTER, DIAGNOSIS).

    Returns:
        JSON with column specifications.
    """
    conn = _get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            FIELD_NAME,
            DATA_TYPE,
            MAX_LENGTH,
            IS_NULLABLE,
            FIELD_DESCRIPTION
        FROM dbo.pcornet_fields
        WHERE TABLE_NAME = ?
        ORDER BY ORDINAL_POSITION
    """, table_name.upper())

    columns = []
    for row in cursor.fetchall():
        columns.append({
            "column": row[0],
            "data_type": row[1],
            "max_length": _serialize(row[2]),
            "nullable": bool(row[3]) if row[3] is not None else True,
            "description": row[4],
        })

    return json.dumps({
        "table": table_name.upper(),
        "column_count": len(columns),
        "columns": columns,
    })


@mcp.tool()
def get_valuesets(table_name: str, column_name: str = None) -> str:
    """Get valid value sets for a table or specific column.

    Queries pcornet_valuesets to return the allowed values for enumerated
    columns. If column_name is provided, returns values for just that column;
    otherwise returns all valuesets for the table.

    Args:
        table_name: PCORnet table name.
        column_name: Optional specific column name.

    Returns:
        JSON with valid values per column.
    """
    conn = _get_connection()
    cursor = conn.cursor()

    if column_name:
        cursor.execute("""
            SELECT FIELD_NAME, VALUESET_ITEM, VALUESET_ITEM_DESCRIPTOR
            FROM dbo.pcornet_valuesets
            WHERE TABLE_NAME = ? AND FIELD_NAME = ?
            ORDER BY FIELD_NAME, VALUESET_ITEM
        """, table_name.upper(), column_name.upper())
    else:
        cursor.execute("""
            SELECT FIELD_NAME, VALUESET_ITEM, VALUESET_ITEM_DESCRIPTOR
            FROM dbo.pcornet_valuesets
            WHERE TABLE_NAME = ?
            ORDER BY FIELD_NAME, VALUESET_ITEM
        """, table_name.upper())

    valuesets = {}
    for row in cursor.fetchall():
        field = row[0]
        if field not in valuesets:
            valuesets[field] = []
        valuesets[field].append({
            "value": row[1],
            "description": row[2],
        })

    return json.dumps({
        "table": table_name.upper(),
        "column": column_name.upper() if column_name else None,
        "valuesets": valuesets,
    })


@mcp.tool()
def get_constraints(table_name: str) -> str:
    """Get primary key, unique, and foreign key constraints for a table.

    Queries pcornet_constraints and pcornet_relational to return all
    constraint definitions for the specified table.

    Args:
        table_name: PCORnet table name.

    Returns:
        JSON with constraint definitions.
    """
    conn = _get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT CONSTRAINT_NAME, CONSTRAINT_TYPE, COLUMN_NAME
        FROM dbo.pcornet_constraints
        WHERE TABLE_NAME = ?
        ORDER BY CONSTRAINT_NAME, COLUMN_NAME
    """, table_name.upper())

    constraints = {}
    for row in cursor.fetchall():
        name = row[0]
        if name not in constraints:
            constraints[name] = {
                "type": row[1],
                "columns": [],
            }
        constraints[name]["columns"].append(row[2])

    cursor.execute("""
        SELECT
            CONSTRAINT_NAME,
            COLUMN_NAME,
            REFERENCED_TABLE_NAME,
            REFERENCED_COLUMN_NAME
        FROM dbo.pcornet_relational
        WHERE TABLE_NAME = ?
        ORDER BY CONSTRAINT_NAME
    """, table_name.upper())

    foreign_keys = []
    for row in cursor.fetchall():
        foreign_keys.append({
            "constraint_name": row[0],
            "column": row[1],
            "references_table": row[2],
            "references_column": row[3],
        })

    return json.dumps({
        "table": table_name.upper(),
        "constraints": constraints,
        "foreign_keys": foreign_keys,
    })


if __name__ == "__main__":
    _load_config()
    mcp.run()
