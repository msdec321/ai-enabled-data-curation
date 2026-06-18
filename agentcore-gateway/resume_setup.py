#!/usr/bin/env python3
"""Resume setup_gateway.py after a partial run (e.g. the CreateGatewayTarget
"Gateway service is not authorized to perform AssumeRole" propagation error).

Discovers the existing gateway by name, recovers the Cognito client info from
the gateway's authorizer config, verifies the execution role's trust policy,
creates the target (with retry) if missing, grants the Lambda invoke, and
writes .gateway_config.json. Safe to rerun.

    AWS_PROFILE=<admin-profile> AWS_DEFAULT_REGION=us-east-1 ../.venv/bin/python resume_setup.py
"""
import json
import os
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ParamValidationError

from setup_gateway import (
    GATEWAY_NAME, LAMBDA_NAME, TARGET_NAME, TOOL_SCHEMA, CONFIG_OUT, HERE,
    allow_gateway_to_invoke, provision_lambda, load_dotenv,
)

REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


def find_gateway(ctrl) -> dict:
    token = None
    while True:
        kwargs = {"nextToken": token} if token else {}
        page = ctrl.list_gateways(**kwargs)
        for item in page.get("items", []):
            if item["name"] == GATEWAY_NAME:
                return ctrl.get_gateway(gatewayIdentifier=item["gatewayId"])
        token = page.get("nextToken")
        if not token:
            sys.exit(f"no gateway named {GATEWAY_NAME} found — run setup_gateway.py instead")


def ensure_trust_policy(iam, role_arn: str) -> None:
    """Make sure the gateway service can assume its execution role."""
    role_name = role_arn.split("/")[-1]
    doc = iam.get_role(RoleName=role_name)["Role"]["AssumeRolePolicyDocument"]
    services = []
    for stmt in doc.get("Statement", []):
        svc = stmt.get("Principal", {}).get("Service", [])
        services += [svc] if isinstance(svc, str) else list(svc)
    if "bedrock-agentcore.amazonaws.com" in services:
        print(f"trust policy on {role_name} OK")
        return
    doc.setdefault("Statement", []).append({
        "Effect": "Allow",
        "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
        "Action": "sts:AssumeRole",
    })
    iam.update_assume_role_policy(RoleName=role_name, PolicyDocument=json.dumps(doc))
    print(f"added bedrock-agentcore.amazonaws.com to {role_name} trust policy")
    time.sleep(12)


def recover_cognito_client_info(gateway: dict) -> dict:
    """Rebuild the client_info that setup would have saved, from the live pool."""
    discovery = gateway["authorizerConfiguration"]["customJWTAuthorizer"]["discoveryUrl"]
    pool_id = discovery.split("/")[3]  # https://cognito-idp.<r>.amazonaws.com/<poolId>/...
    idp = boto3.client("cognito-idp", region_name=REGION)
    domain = idp.describe_user_pool(UserPoolId=pool_id)["UserPool"].get("Domain")
    clients = idp.list_user_pool_clients(UserPoolId=pool_id, MaxResults=10)["UserPoolClients"]
    if len(clients) != 1:
        sys.exit(f"expected 1 app client on pool {pool_id}, found {len(clients)}")
    client = idp.describe_user_pool_client(
        UserPoolId=pool_id, ClientId=clients[0]["ClientId"]
    )["UserPoolClient"]
    return {
        "user_pool_id": pool_id,
        "client_id": client["ClientId"],
        "client_secret": client["ClientSecret"],
        "token_endpoint": f"https://{domain}.auth.{REGION}.amazoncognito.com/oauth2/token",
        "scope": " ".join(client.get("AllowedOAuthScopes", [])),
        "domain_prefix": domain,
    }


def ensure_target(ctrl, gateway: dict, lambda_arn: str) -> str:
    gid = gateway["gatewayId"]
    cfg = {"mcp": {"lambda": {"lambdaArn": lambda_arn, "toolSchema": {"inlinePayload": TOOL_SCHEMA}}}}
    creds = [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]
    tool_names = [t["name"] for t in TOOL_SCHEMA]

    # If the target exists, refresh its tool schema (it may predate query_cdw).
    for t in ctrl.list_gateway_targets(gatewayIdentifier=gid).get("items", []):
        if t["name"] == TARGET_NAME:
            tid = t["targetId"]
            try:
                ctrl.update_gateway_target(
                    gatewayIdentifier=gid, targetId=tid, name=TARGET_NAME,
                    targetConfiguration=cfg, credentialProviderConfigurations=creds,
                )
                print(f"updated target {TARGET_NAME} ({tid}); tools now: {tool_names}")
                return tid
            except ParamValidationError:
                # Client-side schema bug — deleting + recreating would fail the
                # same way and leave the gateway with no target. Keep it; re-raise.
                raise
            except Exception as e:
                print(f"update_gateway_target failed ({type(e).__name__}: {e}); recreating")
                ctrl.delete_gateway_target(gatewayIdentifier=gid, targetId=tid)
                time.sleep(6)
            break

    for attempt in range(8):
        try:
            target = ctrl.create_gateway_target(
                gatewayIdentifier=gid, name=TARGET_NAME,
                targetConfiguration=cfg, credentialProviderConfigurations=creds,
            )
            print(f"target {TARGET_NAME} ({target['targetId']}); tools: {tool_names}")
            return target["targetId"]
        except ctrl.exceptions.ValidationException as e:
            if "AssumeRole" not in str(e):
                raise
            wait = min(5 * (attempt + 1), 30)
            print(f"role trust still propagating; retrying in {wait}s...")
            time.sleep(wait)
    sys.exit("gateway role trust never propagated — inspect the role in the IAM console")


def main() -> None:
    iam = boto3.client("iam", region_name=REGION)
    lam = boto3.client("lambda", region_name=REGION)
    sm = boto3.client("secretsmanager", region_name=REGION)
    ctrl = boto3.client("bedrock-agentcore-control", region_name=REGION)

    gateway = find_gateway(ctrl)
    print(f"found gateway {gateway['gatewayId']} at {gateway['gatewayUrl']}")

    # Re-vault the secret + redeploy the Lambda so local changes always ship
    load_dotenv(HERE.parent / ".env")
    lambda_arn = provision_lambda(iam, lam, sm)

    ensure_trust_policy(iam, gateway["roleArn"])
    ensure_target(ctrl, gateway, lambda_arn)
    allow_gateway_to_invoke(iam, gateway["roleArn"], lambda_arn)

    CONFIG_OUT.write_text(json.dumps({
        "region": REGION,
        "gateway_id": gateway["gatewayId"],
        "gateway_url": gateway["gatewayUrl"],
        "target_name": TARGET_NAME,
        "cognito": recover_cognito_client_info(gateway),
    }, indent=2, default=str))
    print(f"\nwrote {CONFIG_OUT}")
    print("next: ../.venv/bin/python test_gateway.py")


if __name__ == "__main__":
    main()
