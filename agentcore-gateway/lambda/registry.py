"""Tool / dataset registry: maps (tool, dataset) -> the secret a tool needs to
act on that dataset. Stores REFERENCES (vault + id + key), never secrets.

This is the broker's authorization-to-credential mapping. A missing entry means
"deny": a tool can only obtain a credential the registry explicitly grants it.
Start as this dict; graduate to DynamoDB when it needs lifecycle and audit.
"""
import os

# Dataset "*" = the tool uses the same credential regardless of any dataset
# argument. run_python only needs the sandbox bearer secret, so it maps to "*".
# When query_cdw lands, it gets per-dataset entries, e.g.
#   ("query_cdw", "synthetic-cdw"): {"vault": "aws_sm", "id": ..., "key": ...}
REGISTRY = {
    ("run_python", "*"): {
        "vault": "aws_sm",
        "id": os.environ.get("SANDBOX_SECRET_ARN", "autodqa/sandbox-shared-secret"),
        "key": "shared_secret",
    },
}


class NotAuthorized(Exception):
    """No registry entry grants this (tool, dataset) a credential."""


def resolve(tool: str, dataset=None) -> dict:
    """Return the secret reference for (tool, dataset), or raise NotAuthorized.

    Falls back to the tool's "*" entry when no dataset-specific entry exists.
    """
    ds = dataset or "*"
    ref = REGISTRY.get((tool, ds)) or REGISTRY.get((tool, "*"))
    if ref is None:
        raise NotAuthorized(f"no credential grant for tool={tool!r} dataset={ds!r}")
    return ref
