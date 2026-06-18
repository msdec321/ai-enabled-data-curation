"""Pluggable secret vault for the broker.

The broker resolves a tool call to a *secret reference* (via registry.py), then
asks a Vault to materialize the actual secret. AWS Secrets Manager is the
backend today; the interface is deliberately tiny so a different backend
(Cloudflare Secrets Store, Keeper KSM) is a single new class. See the
secrets-vault-ksm-unavailable note in project memory for why it isn't Keeper.

A secret reference is an opaque dict, e.g.:
    {"vault": "aws_sm", "id": "<secret name or ARN>", "key": "shared_secret"}
`key` is optional: present means the stored secret is JSON and we return that
field; absent means return the raw secret string (the caller may json.loads it).
"""
import json
import os

import boto3


class AwsSecretsManagerVault:
    """Fetches secrets from AWS Secrets Manager using the Lambda's IAM role.

    There is no bootstrap credential: the execution role's identity IS the auth,
    scoped by an IAM policy to only the secrets this broker may read. Values are
    not cached, so a rotated secret is picked up on the next call (fine at this
    volume; add a short TTL cache if call rate ever warrants it).
    """

    def __init__(self, region: str):
        self._sm = boto3.client("secretsmanager", region_name=region)

    def fetch(self, ref: dict) -> str:
        resp = self._sm.get_secret_value(SecretId=ref["id"])
        raw = resp.get("SecretString")
        if raw is None:
            raise ValueError(f"secret {ref['id']} has no SecretString")
        key = ref.get("key")
        return raw if key is None else json.loads(raw)[key]


_DEFAULT = None


def default() -> AwsSecretsManagerVault:
    """Process-wide vault, reused across warm Lambda invocations."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = AwsSecretsManagerVault(os.environ.get("AWS_REGION", "us-east-1"))
    return _DEFAULT
