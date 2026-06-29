"""get_valuesets tool: return the predefined valuesets (permissible coded values)
for a CDM table's columns, so the agent can check value conformance while
profiling (e.g. flag a SEX outside A/F/M/NI/UN/OT).

Like every other tool, the lookup EXECUTES IN THE SANDBOX (uniform trust model —
all tool execution runs in the run's ephemeral container). The catalog is static
reference data generated from the PCORnet CDM spec (valuesets/build_catalog.py)
and bundled with this Lambda as valuesets.json. The broker (this Lambda) is just
a courier: it loads the catalog, gzip+base64-compresses it (~290KB -> ~88KB), and
injects it as a sandbox env var alongside the requested table/column; the runner
below decompresses it and resolves the answer inside the container.

Usage:
  get_valuesets()                                  -> tables that have valuesets
  get_valuesets(table="DEMOGRAPHIC")               -> constrained columns + values
                                                      (large sets summarized)
  get_valuesets(table="ENCOUNTER", column="...")   -> one column's FULL value list
"""
import base64
import gzip
import json
import os

import sandbox_client

# Runs INSIDE the sandbox: decompress the catalog from env, resolve the request,
# print one JSON envelope. Reference sets larger than LARGE are summarized at the
# table level (size + how to fetch) so a table lookup doesn't dump hundreds of
# codes into context; a per-column request returns the full list.
_RUNNER = r"""
import os, json, gzip, base64
LARGE = 60
cat = json.loads(gzip.decompress(base64.b64decode(os.environ["VS_CATALOG"])))
cols, sets = cat["columns"], cat["valuesets"]
table = os.environ.get("VS_TABLE", "").strip().upper()
column = os.environ.get("VS_COLUMN", "").strip().upper()

def resolve(table, col, entry, full):
    out = {"kind": entry.get("kind", "enumerable"), "source": entry.get("source")}
    if "values" in entry:
        out["values"] = entry["values"]
        return out
    name = entry["valueset"]; s = sets[name]
    out["valueset"] = name; out["size"] = s["size"]
    if full or s["size"] <= LARGE:
        out["values"] = s["values"]
    else:
        out["note"] = ("%d codes; call get_valuesets(table='%s', column='%s') "
                       "for the full list" % (s["size"], table, col))
    return out

if not table:
    res = {"tables_with_valuesets": sorted({k.split(".")[0] for k in cols}),
           "hint": "call get_valuesets(table=NAME) for a table's column valuesets"}
else:
    prefix = table + "."
    entries = {k.split(".", 1)[1]: v for k, v in cols.items() if k.startswith(prefix)}
    if not entries:
        res = {"table": table, "constrained_columns": [],
               "note": ("No predefined valuesets for %s. Its columns are "
                        "unconstrained — profile by range/format/nulls, not "
                        "value membership." % table)}
    elif column:
        if column not in entries:
            res = {"table": table, "column": column,
                   "error": "%s.%s has no predefined valueset" % (table, column),
                   "constrained_columns": sorted(entries)}
        else:
            res = {"table": table, "column": column,
                   **resolve(table, column, entries[column], True)}
    else:
        res = {"table": table, "constrained_columns": sorted(entries),
               "valuesets": {c: resolve(table, c, entries[c], False) for c in sorted(entries)}}
print(json.dumps(res))
"""

_CATALOG_B64 = None


def _catalog_b64():
    """Compress the bundled catalog once per warm Lambda; reused across calls."""
    global _CATALOG_B64
    if _CATALOG_B64 is None:
        path = os.path.join(os.path.dirname(__file__), "valuesets.json")
        with open(path, "rb") as f:
            _CATALOG_B64 = base64.b64encode(gzip.compress(f.read(), 9)).decode()
    return _CATALOG_B64


def handle(event):
    try:
        env = {
            "VS_CATALOG": _catalog_b64(),
            "VS_TABLE": (event.get("table") or "").strip(),
            "VS_COLUMN": (event.get("column") or "").strip(),
        }
    except FileNotFoundError:
        return {"error": "valueset catalog not bundled — run valuesets/build_catalog.py then redeploy"}

    # Per-run session (set by the orchestrator) → reuse the run's container.
    session = event.get("session") or "valuesets"
    data = sandbox_client.run_in_sandbox(_RUNNER, session=session, env=env)
    if data.get("error"):
        e = data["error"]
        return {"error": f"sandbox: {e if not isinstance(e, dict) else e.get('message') or e}"}
    out = (data.get("stdout") or "").strip()
    if not out:
        return {"error": f"get_valuesets: no output (stderr: {(data.get('stderr') or '')[:200]})"}
    try:
        return json.loads(out)
    except ValueError:
        return out
