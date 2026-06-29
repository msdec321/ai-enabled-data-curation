"""Dataset registry: the broker's authorization-to-credential mapping for DATA
access. A dataset is the unit of access control — it owns how to reach it
(connection) and a *reference* to its credential in the vault (never the secret).

`authorize(tool, dataset)` is deny-by-default: a tool can only touch a dataset
the GRANTS set explicitly permits. Start as these dicts; graduate to DynamoDB
when it needs lifecycle, audit, or edits without redeploying the Lambda.

Note: the sandbox bearer secret is NOT here — that's transport infrastructure
for reaching the sandbox backend (see sandbox_client.py), not dataset access.
"""
import os

DATASETS = {
    "CDW": {
        "engine": "mssql",
        "tier": 1,  # synthetic for now; the real institutional CDW is Tier 2+
        # `server`/`port` are the TUNNEL endpoint the sandbox dials, NOT the LAN
        # IP — set CDW_TUNNEL_ENDPOINT/PORT on the Lambda once the tunnel is up.
        "connection": {
            "server": os.environ.get("CDW_TUNNEL_ENDPOINT", "<TUNNEL_HOST_NOT_SET>"),
            "port": int(os.environ.get("CDW_TUNNEL_PORT", "1433")),
            "database": os.environ.get("CDW_DATABASE", "CDW"),
        },
        # The login (uid/pwd) lives in the vault, fetched per call.
        "credential": {"vault": "aws_sm", "id": os.environ.get("CDW_SECRET_ARN", "autodqa/cdw-readonly-login")},
        "egress": [],  # informational until the sandbox enforces per-session egress
    },
    "ETL": {
        "engine": "fileserver",  # read-only HTTP bridge over the ETL repo (etl-bridge/)
        "tier": 1,  # synthetic/low-sensitivity ETL for now; secrets are denied at the bridge
        # `base_url` is the TUNNEL endpoint the Lambda dials (ngrok -> local bridge),
        # NOT the LAN host — set ETL_BRIDGE_URL on the Lambda once the bridge is up.
        "connection": {
            "base_url": os.environ.get("ETL_BRIDGE_URL", "<ETL_BRIDGE_URL_NOT_SET>"),
        },
        # The bridge bearer token lives in the vault, fetched per call.
        "credential": {"vault": "aws_sm", "id": os.environ.get("ETL_SECRET_ARN", "autodqa/etl-bridge-token"), "key": "token"},
        "egress": [],
    },
}

# Which tools may operate on which datasets (deny by default).
GRANTS = {
    ("query_cdw", "CDW"),
    ("list_etl", "ETL"),
    ("read_etl", "ETL"),
    ("grep_etl", "ETL"),
}


class NotAuthorized(Exception):
    """No grant permits this (tool, dataset) to access data."""


def authorize(tool: str, dataset: str) -> dict:
    """Return {connection, credential, tier} for a permitted (tool, dataset),
    or raise NotAuthorized. `credential` is a vault REFERENCE; the caller fetches
    the actual secret from the vault."""
    if dataset not in DATASETS:
        raise NotAuthorized(f"unknown dataset {dataset!r}")
    if (tool, dataset) not in GRANTS:
        raise NotAuthorized(f"tool {tool!r} not granted on dataset {dataset!r}")
    ds = DATASETS[dataset]
    return {"connection": ds["connection"], "credential": ds["credential"], "tier": ds["tier"]}
