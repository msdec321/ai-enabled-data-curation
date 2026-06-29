#!/usr/bin/env python3
"""One-time setup: AgentCore Gateway (MCP) fronting the sandbox-executed tools
(run_python, query_cdw).

Creates, in this order (all idempotent-ish — safe to rerun after a failure):
  1. two AWS Secrets Manager secrets — the sandbox bearer (from ../.env) and the
     CDW read-only login (from ../config.yaml) — an IAM execution role allowed to
     read both, and the autodqa-run-python Lambda. The Lambda holds NO secret in
     its env, only references (ARNs) it reads from the vault per call.
  2. a Cognito OAuth authorizer (user pool + client-credentials client)
  3. the AgentCore Gateway (MCP protocol, JWT inbound auth)
  4. a Lambda target named "sandbox" exposing run_python + query_cdw
  5. an inline policy letting the gateway's execution role invoke the Lambda

Writes .gateway_config.json (gitignored — contains the Cognito client secret).

Run with AWS credentials that can create IAM roles, Lambda functions, Secrets
Manager secrets, Cognito pools, and AgentCore gateways. The notebook never needs
these credentials — it only consumes the gateway with an OAuth token.

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
import yaml

HERE = Path(__file__).parent
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
GATEWAY_NAME = "autodqa-gateway"
LAMBDA_NAME = "autodqa-run-python"
LAMBDA_ROLE = "autodqa-gateway-lambda-role"
TARGET_NAME = "sandbox"
SANDBOX_SECRET_NAME = "autodqa/sandbox-shared-secret"
DB_SECRET_NAME = "autodqa/cdw-readonly-login"
ETL_SECRET_NAME = "autodqa/etl-bridge-token"
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
    },
    {
        "name": "query_cdw",
        "description": (
            "Run a read-only T-SQL SELECT against a registered clinical data "
            "warehouse dataset and return rows as JSON. T-SQL dialect (use TOP, "
            "not LIMIT). Writes/DDL are rejected; results are capped at 100 rows "
            "(aggregate with COUNT/GROUP BY for profiling)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "Read-only T-SQL SELECT to execute"},
                "dataset": {"type": "string", "description": "Dataset id to query (default: CDW if omitted)"},
                "session": {"type": "string", "description": "Per-run sandbox session id (set by the orchestrator)"},
            },
            "required": ["sql"],
        },
    },
    {
        "name": "search_etl",
        "description": (
            "Search the ETL codebase (synthetic, Tier-0/1) for a string, "
            "case-insensitive. Returns matching file paths, line numbers, and "
            "lines — use it to find which views/procedures/functions reference a "
            "table or column when tracing an issue to its ETL root cause."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Case-insensitive substring to search for"},
                "max_results": {"type": "integer", "description": "Max matching lines to return (default 40)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_etl_file",
        "description": (
            "Read a file from the ETL codebase (synthetic, Tier-0/1), scoped to "
            "the ETL corpus. `path` is relative to the corpus root (as returned by "
            "search_etl). Returns up to max_lines lines of content plus metadata."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Corpus-relative path (e.g. etl/tables/etl.DEMOGRAPHIC.View.sql)"},
                "start_line": {"type": "integer", "description": "0-based line to start from (default 0)"},
                "max_lines": {"type": "integer", "description": "Max lines to return (default 400)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "destroy_sandbox",
        "description": (
            "Tear down the ephemeral sandbox container for a run's session. "
            "Internal lifecycle tool called by the orchestrator at run end."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string", "description": "The run's sandbox session id to destroy"},
            },
            "required": ["session"],
        },
    },
    {
        "name": "list_etl",
        "description": (
            "List the ETL repository's files (paths + sizes). Use this first to "
            "understand the ETL codebase structure before reading or searching. "
            "Read-only; secret/data files are not exposed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string", "description": "Per-run sandbox session id (set by the orchestrator)"},
            },
        },
    },
    {
        "name": "read_etl",
        "description": (
            "Read one ETL source file by its repository-relative path (e.g. "
            "'views/EncounterMap.sql'). Returns the file text. Paths are jailed "
            "to the ETL repo and secret/data files are refused."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Repo-relative path, e.g. procedures/etl.load_DEMOGRAPHIC.StoredProcedure.sql"},
                "session": {"type": "string", "description": "Per-run sandbox session id (set by the orchestrator)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "grep_etl",
        "description": (
            "Search the ETL codebase for a pattern; returns matching file, line "
            "number, and text. The workhorse for tracing a data-quality finding "
            "to the ETL logic that produced it (grep a column or table name). "
            "Literal substring by default."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "Pattern to search for (e.g. a column or table name)"},
                "regex": {"type": "boolean", "description": "Treat q as a regular expression (default false)"},
                "max": {"type": "integer", "description": "Max matches to return (default 200)"},
                "session": {"type": "string", "description": "Per-run sandbox session id (set by the orchestrator)"},
            },
            "required": ["q"],
        },
    },
    {
        "name": "get_valuesets",
        "description": (
            "Return the predefined valuesets (permissible coded values) for a CDM "
            "table's columns, to check value conformance while profiling — e.g. a "
            "SEX value outside A/F/M/NI/UN/OT is a data-quality issue. Call with a "
            "table name (e.g. DEMOGRAPHIC) to get its constrained columns and their "
            "allowed codes; columns NOT returned are unconstrained (profile by "
            "range/format/nulls instead). Large valuesets are summarized — pass a "
            "column name to fetch that column's full code list. Call with no "
            "arguments to list which tables have valuesets."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "CDM table, e.g. DEMOGRAPHIC, ENCOUNTER, DIAGNOSIS"},
                "column": {"type": "string", "description": "Optional column name to fetch one column's full valueset"},
                "session": {"type": "string", "description": "Per-run sandbox session id (set by the orchestrator)"},
            },
        },
    },
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


def _put_secret(sm, name: str, payload: str, desc: str) -> str:
    try:
        arn = sm.create_secret(Name=name, SecretString=payload, Description=desc)["ARN"]
        print(f"created secret {name}")
    except sm.exceptions.ResourceExistsException:
        sm.put_secret_value(SecretId=name, SecretString=payload)
        arn = sm.describe_secret(SecretId=name)["ARN"]
        print(f"updated secret {name}")
    return arn


def ensure_sandbox_secret(sm, value: str) -> str:
    """The sandbox bearer (transport credential). ../.env stays the dev source;
    this copies it into the vault the broker Lambda reads."""
    return _put_secret(sm, SANDBOX_SECRET_NAME, json.dumps({"shared_secret": value}),
                       "AutoDQA sandbox worker bearer secret (broker-fetched)")


def ensure_db_secret(sm) -> tuple[str, str]:
    """The CDW read-only login (data credential), from ../config.yaml's
    connection block. Returns (secret ARN, database name)."""
    cfg = yaml.safe_load((HERE.parent / "config.yaml").read_text())
    conn = cfg["connection"]
    uid, pwd = conn.get("uid", ""), conn.get("pwd", "")
    if not uid or not pwd:
        print("WARNING: config.yaml connection has no uid/pwd — the tunneled "
              "cloud path requires SQL auth, not Windows auth.")
    arn = _put_secret(sm, DB_SECRET_NAME, json.dumps({"uid": uid, "pwd": pwd}),
                      "AutoDQA CDW read-only DB login (broker-fetched, injected into the sandbox)")
    return arn, conn.get("database", "CDW")


def ensure_etl_secret(sm) -> str:
    """The ETL bridge bearer token (transport credential for the local read-only
    ETL file server). Sourced from $ETL_SERVER_TOKEN, else the persisted token
    file the bridge writes (etl-bridge/.etl_server_token)."""
    token = os.environ.get("ETL_SERVER_TOKEN", "").strip()
    if not token:
        tok_file = HERE.parent / "etl-bridge" / ".etl_server_token"
        if tok_file.exists():
            token = tok_file.read_text().strip()
    if not token:
        print("WARNING: no ETL bridge token ($ETL_SERVER_TOKEN or etl-bridge/"
              ".etl_server_token) — read_etl/grep_etl will 401 until it's set.")
    return _put_secret(sm, ETL_SECRET_NAME, json.dumps({"token": token}),
                       "AutoDQA ETL bridge bearer token (broker-fetched)")


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


def grant_secret_read(iam, role_name: str, secret_arns) -> None:
    """Scope the Lambda role to read ONLY the broker's secrets. This IAM grant is
    the vault-level allowlist (the role KSM's 'share to app' would have played)."""
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="autodqa-read-tool-secrets",
        PolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": "secretsmanager:GetSecretValue",
                "Resource": list(secret_arns),
            }],
        }),
    )
    print(f"granted secretsmanager:GetSecretValue on {len(list(secret_arns))} secret(s) to {role_name}")


def _catalog_json() -> str | None:
    """The valueset catalog, converted YAML->JSON at build time so the Lambda can
    read it with stdlib json (no pyyaml in the runtime). Bundled as valuesets.json
    for the get_valuesets tool. Returns None if the catalog hasn't been generated."""
    cat = HERE.parent / "valuesets" / "pcornet_cdm.yaml"
    if not cat.exists():
        print("WARNING: valuesets/pcornet_cdm.yaml not found — get_valuesets will "
              "fail until you run valuesets/build_catalog.py")
        return None
    return json.dumps(yaml.safe_load(cat.read_text()))


def deploy_lambda(lam, role_arn: str, env_vars: dict) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for py in sorted((HERE / "lambda").glob("*.py")):
            z.writestr(py.name, py.read_text())
        catalog = _catalog_json()
        if catalog is not None:
            z.writestr("valuesets.json", catalog)
        # Bundle the synthetic ETL corpus so search_etl/read_etl_file can serve it
        # from inside the Lambda (Tier-0/1: static synthetic text travels in the
        # package; for the real ETL repo the executor moves in-network instead).
        etl_root = HERE / "etl_corpus"
        for f in sorted(etl_root.rglob("*")):
            if f.is_file():
                z.writestr(f"etl_corpus/{f.relative_to(etl_root).as_posix()}", f.read_bytes())
    code = buf.getvalue()
    env = {"Variables": env_vars}
    try:
        fn = lam.create_function(
            FunctionName=LAMBDA_NAME,
            Runtime="python3.12",
            Role=role_arn,
            Handler="tool_router.handler",
            Code={"ZipFile": code},
            Timeout=120,
            Environment=env,
        )
        print(f"created Lambda {LAMBDA_NAME}")
        return fn["FunctionArn"]
    except lam.exceptions.ResourceConflictException:
        lam.update_function_code(FunctionName=LAMBDA_NAME, ZipFile=code)
        lam.get_waiter("function_updated_v2").wait(FunctionName=LAMBDA_NAME)
        lam.update_function_configuration(
            FunctionName=LAMBDA_NAME, Handler="tool_router.handler",
            Environment=env, Timeout=120,
        )
        print(f"updated Lambda {LAMBDA_NAME}")
        return lam.get_function(FunctionName=LAMBDA_NAME)["Configuration"]["FunctionArn"]


def _current_lambda_env(lam) -> dict:
    """The Lambda's current env vars, or {} if it doesn't exist yet. Lets a
    redeploy PRESERVE tunnel endpoints (CDW_TUNNEL_*, ETL_BRIDGE_URL) that were
    set out-of-band (e.g. after restarting ngrok) and aren't in the local env —
    so rerunning setup doesn't silently wipe a live tunnel."""
    try:
        return lam.get_function_configuration(FunctionName=LAMBDA_NAME).get(
            "Environment", {}).get("Variables", {})
    except lam.exceptions.ResourceNotFoundException:
        return {}


def provision_lambda(iam, lam, sm) -> str:
    """Vault the secrets, allow the Lambda role to read them, deploy the Lambda
    with only references + non-secret connection config. Shared with resume_setup.
    Tunnel endpoints fall back to the live Lambda value, then a default, so a
    redeploy without them exported preserves what's already there."""
    cur = _current_lambda_env(lam)
    sandbox_arn = ensure_sandbox_secret(sm, os.environ["SANDBOX_SHARED_SECRET"])
    db_arn, db_database = ensure_db_secret(sm)
    etl_arn = ensure_etl_secret(sm)
    role_arn = ensure_lambda_role(iam)
    grant_secret_read(iam, LAMBDA_ROLE, [sandbox_arn, db_arn, etl_arn])
    env_vars = {
        "SANDBOX_WORKER_URL": os.environ["SANDBOX_WORKER_URL"],
        "SANDBOX_SECRET_ARN": sandbox_arn,
        "CDW_SECRET_ARN": db_arn,
        "CDW_DATABASE": db_database,
        # Tunnel endpoints the cloud dials (NOT LAN hosts). Export to override;
        # otherwise the live Lambda value is preserved (see _current_lambda_env).
        "CDW_TUNNEL_ENDPOINT": os.environ.get("CDW_TUNNEL_ENDPOINT", cur.get("CDW_TUNNEL_ENDPOINT", "")),
        "CDW_TUNNEL_PORT": os.environ.get("CDW_TUNNEL_PORT", cur.get("CDW_TUNNEL_PORT", "1433")),
        # ETL bridge (ngrok -> local read-only file server in etl-bridge/).
        "ETL_SECRET_ARN": etl_arn,
        "ETL_BRIDGE_URL": os.environ.get("ETL_BRIDGE_URL", cur.get("ETL_BRIDGE_URL", "")),
    }
    return deploy_lambda(lam, role_arn, env_vars)


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
    if not (HERE.parent / "config.yaml").exists():
        sys.exit("../config.yaml not found — needed for the CDW DB login")

    from bedrock_agentcore_starter_toolkit.operations.gateway.client import GatewayClient

    iam = boto3.client("iam", region_name=REGION)
    lam = boto3.client("lambda", region_name=REGION)
    sm = boto3.client("secretsmanager", region_name=REGION)
    ctrl = boto3.client("bedrock-agentcore-control", region_name=REGION)

    # 1. Secrets + role + Lambda
    lambda_arn = provision_lambda(iam, lam, sm)

    # 2 + 3. Cognito authorizer + gateway (toolkit handles the OAuth boilerplate
    # and auto-creates the gateway execution role)
    gc = GatewayClient(region_name=REGION)
    cognito = gc.create_oauth_authorizer_with_cognito(GATEWAY_NAME)
    gateway = gc.create_mcp_gateway(
        name=GATEWAY_NAME,
        role_arn=None,
        authorizer_config=cognito["authorizer_config"],
        enable_semantic_search=False,
    )
    gateway_id = gateway["gatewayId"]
    gateway_url = gateway["gatewayUrl"]
    print(f"gateway {gateway_id} at {gateway_url}")

    # 4. Lambda target. CreateGatewayTarget is the first call that assumes the
    # gateway's execution role, and IAM trust takes ~10-30s to propagate — retry.
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
        "cognito": cognito["client_info"],
    }, indent=2, default=str))
    print(f"\nwrote {CONFIG_OUT} (gitignored — contains the Cognito client secret)")
    print("next: ../.venv/bin/python test_gateway.py")


if __name__ == "__main__":
    main()
