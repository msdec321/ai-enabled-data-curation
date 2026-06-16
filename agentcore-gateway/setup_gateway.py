#!/usr/bin/env python3
"""One-time setup: AgentCore Gateway (MCP) fronting the run_python sandbox tool.

Creates, in this order (all idempotent-ish — safe to rerun after a failure):
  1. the sandbox bearer secret in AWS Secrets Manager (copied from ../.env), an
     IAM execution role allowed to read it, and the autodqa-run-python Lambda
     (which forwards to the Cloudflare sandbox worker, fetching the bearer
     secret from the vault per call — no secret in its own env)
  2. a Cognito OAuth authorizer (user pool + client-credentials client)
  3. the AgentCore Gateway (MCP protocol, JWT inbound auth)
  4. a Lambda target named "sandbox" exposing the run_python tool
  5. an inline policy letting the gateway's execution role invoke the Lambda

Writes .gateway_config.json (gitignored — contains the Cognito client secret)
with everything the notebook/test script needs to connect.

Run with AWS credentials that can create IAM roles, Lambda functions, Secrets
Manager secrets, Cognito pools, and AgentCore gateways. The notebook never
needs these credentials — it only consumes the gateway with an OAuth token.

    ../.venv/bin/pip install "bedrock-agentcore-starter-toolkit>=0.1.10" boto3
    AWS_PROFILE=<admin-profile> ../.venv/bin/python setup_gateway.py
"""
import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path

import boto3

HERE = Path(__file__).parent
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
GATEWAY_NAME = "autodqa-gateway"
LAMBDA_NAME = "autodqa-run-python"
LAMBDA_ROLE = "autodqa-gateway-lambda-role"
TARGET_NAME = "sandbox"
SECRET_NAME = "autodqa/sandbox-shared-secret"
CONFIG_OUT = HERE / ".gateway_config.json"

TOOL_SCHEMA = [
    {
        "name": "run_python",
        "description": (
            "Execute Python in an isolated Cloudflare sandbox (remote compute, "
            "no CDW or ETL repo access). Use for analysis on data already "
            "retrieved. Print anything you want returned."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python source to execute"},
                "session": {"type": "string", "description": "Sandbox isolation key (same key = same container)"},
            },
            "required": ["code"],
        },
    }
]


def load_dotenv(path: Path) -> None:
    """Same loader as the notebook: KEY=VALUE / 'export KEY=VALUE' lines."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip().removeprefix("export ").strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def ensure_secret(sm, value: str) -> str:
    """Create or update the sandbox bearer secret in Secrets Manager; return ARN.

    Stored as JSON {"shared_secret": <value>} so the registry's ref key matches.
    ../.env stays the human/dev source of truth (the notebook reads it directly);
    this copies it into the vault that the broker Lambda reads.
    """
    payload = json.dumps({"shared_secret": value})
    try:
        arn = sm.create_secret(
            Name=SECRET_NAME,
            SecretString=payload,
            Description="AutoDQA sandbox worker bearer secret (broker-fetched)",
        )["ARN"]
        print(f"created secret {SECRET_NAME}")
    except sm.exceptions.ResourceExistsException:
        sm.put_secret_value(SecretId=SECRET_NAME, SecretString=payload)
        arn = sm.describe_secret(SecretId=SECRET_NAME)["ARN"]
        print(f"updated secret {SECRET_NAME}")
    return arn


def ensure_lambda_role(iam) -> str:
    try:
        return iam.get_role(RoleName=LAMBDA_ROLE)["Role"]["Arn"]
    except iam.exceptions.NoSuchEntityException:
        pass
    role = iam.create_role(
        RoleName=LAMBDA_ROLE,
        AssumeRolePolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }],
        }),
        Description="Execution role for the AutoDQA gateway tool Lambda",
    )
    iam.attach_role_policy(
        RoleName=LAMBDA_ROLE,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )
    print(f"created IAM role {LAMBDA_ROLE}; waiting for propagation...")
    time.sleep(12)  # IAM eventual consistency before Lambda can assume it
    return role["Role"]["Arn"]


def grant_secret_read(iam, role_name: str, secret_arn: str) -> None:
    """Scope the Lambda role to read ONLY the broker's tool secrets. This IAM
    grant is the vault-level allowlist (the role KSM's 'share to app' played)."""
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="autodqa-read-tool-secrets",
        PolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": "secretsmanager:GetSecretValue",
                "Resource": secret_arn,
            }],
        }),
    )
    print(f"granted secretsmanager:GetSecretValue on {SECRET_NAME} to {role_name}")


def deploy_lambda(lam, role_arn: str, secret_arn: str) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for py in sorted((HERE / "lambda").glob("*.py")):
            z.writestr(py.name, py.read_text())
    code = buf.getvalue()
    # No secret in the Lambda env — only a reference (the ARN) the handler reads
    # from the vault at call time.
    env = {"Variables": {
        "SANDBOX_WORKER_URL": os.environ["SANDBOX_WORKER_URL"],
        "SANDBOX_SECRET_ARN": secret_arn,
    }}
    try:
        fn = lam.create_function(
            FunctionName=LAMBDA_NAME,
            Runtime="python3.12",
            Role=role_arn,
            Handler="run_python_tool.handler",
            Code={"ZipFile": code},
            Timeout=120,
            Environment=env,
        )
        print(f"created Lambda {LAMBDA_NAME}")
        return fn["FunctionArn"]
    except lam.exceptions.ResourceConflictException:
        lam.update_function_code(FunctionName=LAMBDA_NAME, ZipFile=code)
        waiter = lam.get_waiter("function_updated_v2")
        waiter.wait(FunctionName=LAMBDA_NAME)
        lam.update_function_configuration(FunctionName=LAMBDA_NAME, Environment=env, Timeout=120)
        print(f"updated Lambda {LAMBDA_NAME}")
        return lam.get_function(FunctionName=LAMBDA_NAME)["Configuration"]["FunctionArn"]


def provision_lambda(iam, lam, sm) -> str:
    """Vault the sandbox secret, allow the Lambda role to read it, deploy the
    Lambda with only a reference. Shared by setup_gateway and resume_setup."""
    secret_arn = ensure_secret(sm, os.environ["SANDBOX_SHARED_SECRET"])
    role_arn = ensure_lambda_role(iam)
    grant_secret_read(iam, LAMBDA_ROLE, secret_arn)
    return deploy_lambda(lam, role_arn, secret_arn)


def allow_gateway_to_invoke(iam, gateway_role_arn: str, lambda_arn: str) -> None:
    role_name = gateway_role_arn.split("/")[-1]
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="autodqa-invoke-tool-lambda",
        PolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": "lambda:InvokeFunction",
                "Resource": lambda_arn,
            }],
        }),
    )
    print(f"granted lambda:InvokeFunction on {LAMBDA_NAME} to {role_name}")


def main() -> None:
    load_dotenv(HERE.parent / ".env")
    for var in ("SANDBOX_WORKER_URL", "SANDBOX_SHARED_SECRET"):
        if var not in os.environ:
            sys.exit(f"{var} not set (expected in ../.env)")

    from bedrock_agentcore_starter_toolkit.operations.gateway.client import GatewayClient

    iam = boto3.client("iam", region_name=REGION)
    lam = boto3.client("lambda", region_name=REGION)
    sm = boto3.client("secretsmanager", region_name=REGION)
    ctrl = boto3.client("bedrock-agentcore-control", region_name=REGION)

    # 1. Secret + role + Lambda
    lambda_arn = provision_lambda(iam, lam, sm)

    # 2 + 3. Cognito authorizer + gateway (toolkit handles the OAuth boilerplate
    # and auto-creates the gateway execution role)
    gc = GatewayClient(region_name=REGION)
    cognito = gc.create_oauth_authorizer_with_cognito(GATEWAY_NAME)
    gateway = gc.create_mcp_gateway(
        name=GATEWAY_NAME,
        role_arn=None,  # let the toolkit create the execution role
        authorizer_config=cognito["authorizer_config"],
        enable_semantic_search=False,
    )
    gateway_id = gateway["gatewayId"]
    gateway_url = gateway["gatewayUrl"]
    print(f"gateway {gateway_id} at {gateway_url}")

    # 4. Lambda target via the raw control-plane API (precise control over the
    # credential provider config, which the toolkit wrapper has been fussy about).
    # CreateGatewayTarget is the first call that actually assumes the gateway's
    # execution role, and IAM trust policies take ~10-30s to propagate — retry.
    target = None
    for attempt in range(8):
        try:
            target = ctrl.create_gateway_target(
                gatewayIdentifier=gateway_id,
                name=TARGET_NAME,
                targetConfiguration={"mcp": {"lambda": {
                    "lambdaArn": lambda_arn,
                    "toolSchema": {"inlinePayload": TOOL_SCHEMA},
                }}},
                credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
            )
            break
        except ctrl.exceptions.ValidationException as e:
            if "AssumeRole" not in str(e):
                raise
            wait = min(5 * (attempt + 1), 30)
            print(f"gateway role trust still propagating; retrying in {wait}s...")
            time.sleep(wait)
    if target is None:
        sys.exit("gateway role trust never propagated — rerun resume_setup.py")
    print(f"target {TARGET_NAME} ({target['targetId']}) -> {LAMBDA_NAME}")

    # 5. Let the gateway's execution role invoke our Lambda
    allow_gateway_to_invoke(iam, gateway["roleArn"], lambda_arn)

    CONFIG_OUT.write_text(json.dumps({
        "region": REGION,
        "gateway_id": gateway_id,
        "gateway_url": gateway_url,
        "target_name": TARGET_NAME,
        "cognito": cognito["client_info"],  # client_id, client_secret, token_endpoint, scope
    }, indent=2, default=str))
    print(f"\nwrote {CONFIG_OUT} (gitignored — contains the Cognito client secret)")
    print("next: ../.venv/bin/python test_gateway.py")


if __name__ == "__main__":
    main()
