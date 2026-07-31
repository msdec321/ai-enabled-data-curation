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

# The CDW endpoint the sandbox dials — direct to the institutional DB over the
# VPC egress connector (was an ngrok tunnel endpoint before the 2026-07 in-network
# migration). Set at deploy from config.yaml's connection block. CDW_SERVER may be
# "host" or "host:port"; a port in the host string wins over CDW_PORT.
_ep = os.environ.get("CDW_SERVER", "<CDW_SERVER_NOT_SET>")
if ":" in _ep:
    _CDW_HOST, _, _p = _ep.partition(":")
    _CDW_PORT = int(_p)
else:
    _CDW_HOST = _ep
    _CDW_PORT = int(os.environ.get("CDW_PORT", "1433"))

DATASETS = {
    "CDW": {
        "engine": "mssql",
        "tier": 1,  # synthetic for now; the real institutional CDW is Tier 2+
        # `server`/`port`: the CDW endpoint the sandbox dials (see CDW_SERVER/
        # CDW_PORT above) — the institutional DB, reached over the VPC egress
        # connector.
        "connection": {
            "server": _CDW_HOST,
            "port": _CDW_PORT,
            "database": os.environ.get("CDW_DATABASE", "CDW"),
        },
        # The login (uid/pwd) lives in the vault, fetched per call.
        "credential": {"vault": "aws_sm", "id": os.environ.get("CDW_SECRET_ARN", "autodqa/cdw-readonly-login")},
        "egress": [],  # informational until the sandbox enforces per-session egress
    },
    # NOTE: the ETL dataset no longer appears here. list_etl / read_etl / grep_etl read
    # the synthetic ETL repo that the sandbox clones from GitLab (see etl_sandbox.py
    # and gitlab_clone.py). It previously carried an ngrok `base_url` plus a vaulted
    # bearer token for the local ETL bridge (etl-bridge/), which is now unused.
    #
    # The GitLab SSH key IS vaulted (GITLAB_SECRET_ARN) and etl_sandbox.py fetches it
    # per call, so unlike query_cdw the fetch is not registry-mediated. Worth revisiting:
    # an ETL dataset entry here would put the GitLab credential under the same
    # deny-by-default grant as the CDW login instead of beside it.
}

# Which tools may operate on which datasets (deny by default).
# Only data access needs a grant; the ETL tools access no credentialed dataset while
# the snapshot is staged in the sandbox image.
GRANTS = {
    ("query_cdw", "CDW"),
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
