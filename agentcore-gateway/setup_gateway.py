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
    AWS_PROFILE=bigarc-autodqa AWS_DEFAULT_REGION=us-east-1 ../.venv/bin/python setup_gateway.py
"""
import base64
import io
import json
import os
import subprocess
import sys
import tempfile
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
DB_SECRET_NAME = "autodqa/cdw-readonly-login"
GITLAB_SECRET_NAME = "autodqa/gitlab-ssh-key"
CONFIG_OUT = HERE / ".gateway_config.json"

# The institutional (BigARC) account. Every AutoDQA component must live here: the
# gateway, the broker Lambda, the sandbox microVM image, and the Runtime.
EXPECTED_ACCOUNT = os.environ.get("AUTODQA_EXPECTED_ACCOUNT", "202102860812")


def require_expected_account() -> str:
    """Abort unless the active credentials are the institutional account.

    Guards against the failure this actually caused: a wrong AWS_PROFILE silently
    creates a SECOND, parallel stack in another account. That happened -- a gateway,
    Cognito pool and broker Lambda were built in a since-retired personal account, and
    because the Lambda's MICROVM_IMAGE_ARN still referenced the institutional sandbox
    image, run-microvm failed with a cross-account AccessDeniedException that looked
    like a permissions bug rather than a wrong-account bug. Nothing stopped it,
    because these scripts are idempotent by design and will happily build a fresh
    stack wherever they are pointed.

    Override with AUTODQA_EXPECTED_ACCOUNT if the stack is ever moved deliberately.
    """
    ident = boto3.client("sts").get_caller_identity()
    account = ident["Account"]
    if account != EXPECTED_ACCOUNT:
        sys.exit(
            f"\nREFUSING TO RUN: wrong AWS account.\n"
            f"  active   : {account}  ({ident.get('Arn','')})\n"
            f"  expected : {EXPECTED_ACCOUNT}  (institutional / BigARC)\n"
            f"  profile  : AWS_PROFILE={os.environ.get('AWS_PROFILE','<unset>')}\n\n"
            f"Re-run with:  AWS_PROFILE=bigarc-autodqa AWS_DEFAULT_REGION={REGION} ...\n"
            f"Running against another account would create a parallel stack there and\n"
            f"leave the two silently diverging. Set AUTODQA_EXPECTED_ACCOUNT to move\n"
            f"the deployment deliberately.\n"
        )
    print(f"account check OK: {account} (profile {os.environ.get('AWS_PROFILE','<unset>')})")
    return account


# NOTE: ETL_SECRET_NAME ("autodqa/etl-bridge-token") was removed. The ETL bridge is
# retired -- list_etl/read_etl/grep_etl now clone the synthetic ETL repo from GitLab
# into the sandbox, so there is no bridge bearer token to vault. The credential that
# path DOES need is the GitLab SSH key, provisioned as GITLAB_SECRET_NAME below. The
# existing etl-bridge-token entry is orphaned and can be deleted.

TOOL_SCHEMA = [
    {
        "name": "run_python",
        "description": (
            "Execute Python in an isolated sandbox microVM (remote compute, "
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


def ensure_db_secret(sm) -> tuple[str, str, str, str]:
    """The CDW read-only login (vaulted) plus its non-secret connection coords,
    from ../config.yaml's connection block. Returns
    (secret ARN, server, port, database)."""
    cfg = yaml.safe_load((HERE.parent / "config.yaml").read_text())
    conn = cfg["connection"]
    uid, pwd = conn.get("uid", ""), conn.get("pwd", "")
    if not uid or not pwd:
        print("WARNING: config.yaml connection has no uid/pwd — the cloud path "
              "requires SQL auth, not Windows auth.")
    arn = _put_secret(sm, DB_SECRET_NAME, json.dumps({"uid": uid, "pwd": pwd}),
                      "AutoDQA CDW read-only DB login (broker-fetched, injected into the sandbox)")
    return (arn, str(conn.get("server", "")), str(conn.get("port", 1433)),
            conn.get("database", "CDW"))


def _is_encrypted_private_key(pem: str) -> bool:
    """True if the SSH private key is passphrase-protected.

    Two formats to handle, and the naive check only covers one:
      * legacy PEM  — carries a `Proc-Type: 4,ENCRYPTED` / `DEK-Info` header.
      * OPENSSH v1  — no header at all. The base64 body decodes to
        b"openssh-key-v1\\0" followed by a length-prefixed cipher name, which is
        "none" for an unencrypted key. Grepping the text for "ENCRYPTED" always
        misses this, which is the format ssh-keygen has emitted by default for years.
    """
    if "ENCRYPTED" in pem.split("-----")[0] or "DEK-Info:" in pem:
        return True
    body = "".join(l.strip() for l in pem.splitlines() if "-----" not in l)
    try:
        raw = base64.b64decode(body)
    except Exception:
        return False                       # unparseable: let ssh report it later
    magic = b"openssh-key-v1\x00"
    if not raw.startswith(magic):
        return False                       # not OPENSSH v1; PEM check above applies
    off = len(magic)
    cipher_len = int.from_bytes(raw[off:off + 4], "big")
    cipher = raw[off + 4:off + 4 + cipher_len].decode("ascii", "replace")
    return cipher != "none"


def ensure_gitlab_secret(sm) -> str | None:
    """The GitLab SSH private key the agent clones the ETL repo with (vaulted).

    Sourced from $AUTODQA_GITLAB_KEY, else ~/.ssh/autodqa_gitlab_ed25519. Stored as
    JSON so the public key travels with it -- handy for confirming which key GitLab
    should have authorised without digging the private half out of the vault.

    Returns None (and provisions nothing) when no key file is present, so a deploy on a
    machine without the key is not blocked; the tooling simply has no GitLab access.

    Same handling as the CDW login: only the ARN reaches the Lambda env, the value is
    fetched per call, and it is injected into the ephemeral sandbox as an env var --
    never embedded in a code body, never written to the image.
    """
    path = Path(os.environ.get("AUTODQA_GITLAB_KEY")
                or (Path.home() / ".ssh" / "autodqa_gitlab_ed25519"))
    if not path.is_file():
        print(f"no GitLab key at {path} — skipping {GITLAB_SECRET_NAME} "
              f"(set AUTODQA_GITLAB_KEY to provision it)")
        return None
    private = path.read_text()
    if "PRIVATE KEY" not in private:
        sys.exit(f"{path} does not look like an SSH private key")
    if _is_encrypted_private_key(private):
        sys.exit(f"{path} is passphrase-protected; the agent runs unattended and cannot "
                 f"supply one. Provision a key with no passphrase.")
    pub_path = Path(str(path) + ".pub")
    public = pub_path.read_text().strip() if pub_path.is_file() else ""
    arn = _put_secret(sm, GITLAB_SECRET_NAME,
                      json.dumps({"private_key": private, "public_key": public}),
                      "AutoDQA GitLab SSH key (broker-fetched, injected into the sandbox)")
    fp = public.split()[1][:16] + "..." if public else "(no .pub alongside)"
    print(f"vaulted GitLab SSH key from {path.name} [{fp}]")
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


def grant_microvm_access(iam, role_name: str) -> None:
    """Let the broker Lambda manage per-session sandbox microVMs (the AWS-native
    sandbox on Lambda MicroVMs — see ../sandbox-microvm/ and lambda/sandbox_client.py)."""
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="autodqa-manage-sandbox-microvms",
        PolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": ["lambda:RunMicrovm", "lambda:GetMicrovm",
                           "lambda:CreateMicrovmAuthToken", "lambda:TerminateMicrovm",
                           # required to attach the ingress/egress network connectors
                           "lambda:PassNetworkConnector"],
                "Resource": "*",
            }],
        }),
    )
    print(f"granted sandbox-microvm manage perms to {role_name}")


def _microvm_image_arn() -> str:
    """The sandbox microVM image ARN, written by sandbox-microvm/build_image.py."""
    cfg = HERE.parent / "sandbox-microvm" / ".microvm_config.json"
    return json.loads(cfg.read_text()).get("image_arn", "") if cfg.exists() else ""


def _add_boto3(z) -> None:
    """Bundle a new-enough boto3 into the Lambda package so sandbox_client can call
    the lambda-microvms service (the managed runtime's boto3 may predate it). Task-root
    packages take precedence over the runtime's on sys.path."""
    tmp = tempfile.mkdtemp(prefix="autodqa-boto3-")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "boto3>=1.43.42", "-t", tmp],
                   check=True)
    root = Path(tmp)
    for f in root.rglob("*"):
        if f.is_file() and "__pycache__" not in f.parts:
            z.writestr(f.relative_to(root).as_posix(), f.read_bytes())
    print("bundled boto3 into the Lambda package")


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
        # The pinned GitLab host key travels with the code (extensionless, so the *.py
        # glob above misses it). Not a secret — a public host key — but gitlab_clone.py
        # refuses to run without it, since the alternative is disabling host-key
        # checking on a path we hand a private key to.
        kh = HERE / "lambda" / "gitlab_known_hosts"
        if kh.is_file():
            z.writestr(kh.name, kh.read_text())
        catalog = _catalog_json()
        if catalog is not None:
            z.writestr("valuesets.json", catalog)
        # NOTE: etl_corpus/ is deliberately NOT bundled any more. All five ETL tools
        # (list_etl, read_etl, grep_etl, search_etl, read_etl_file) now read the repo
        # the sandbox clones from GitLab, via etl_sandbox.py.
        # The old 13-file Lambda-bundled corpus used a different path layout
        # (etl/tables/etl.DEMOGRAPHIC.View.sql vs CDW/views/etl.DEMOGRAPHIC.sql), so
        # while both were live the tools disagreed about what the ETL contained and a
        # path from list_etl could not be read by read_etl_file. Leaving it out of the
        # package means a stale copy cannot silently win.
        _add_boto3(z)  # new-enough boto3 for lambda-microvms (sandbox_client)
    code = buf.getvalue()
    env = {"Variables": env_vars}
    try:
        fn = lam.create_function(
            FunctionName=LAMBDA_NAME,
            Runtime="python3.12",
            Role=role_arn,
            Handler="tool_router.handler",
            Code={"ZipFile": code},
            Timeout=180,
            Environment=env,
        )
        print(f"created Lambda {LAMBDA_NAME}")
        return fn["FunctionArn"]
    except lam.exceptions.ResourceConflictException:
        lam.update_function_code(FunctionName=LAMBDA_NAME, ZipFile=code)
        lam.get_waiter("function_updated_v2").wait(FunctionName=LAMBDA_NAME)
        lam.update_function_configuration(
            FunctionName=LAMBDA_NAME, Handler="tool_router.handler",
            Environment=env, Timeout=180,
        )
        print(f"updated Lambda {LAMBDA_NAME}")
        return lam.get_function(FunctionName=LAMBDA_NAME)["Configuration"]["FunctionArn"]


def _current_lambda_env(lam) -> dict:
    """The Lambda's current env vars, or {} if it doesn't exist yet. Lets a
    redeploy PRESERVE values set out-of-band and absent from the local env — the CDW
    endpoint (now from config.yaml), the microVM image ARN and the egress connectors —
    so rerunning setup doesn't wipe a live value.

    Note this preserves only the keys provision_lambda re-declares. The retired
    ETL_BRIDGE_URL / ETL_SECRET_ARN are no longer among them, so a redeploy drops
    them from the live function — which is the intent: the ETL snapshot now ships in
    the sandbox image."""
    try:
        return lam.get_function_configuration(FunctionName=LAMBDA_NAME).get(
            "Environment", {}).get("Variables", {})
    except lam.exceptions.ResourceNotFoundException:
        return {}


def provision_lambda(iam, lam, sm) -> str:
    """Vault the secrets, allow the Lambda role to read them, deploy the Lambda
    with only references + non-secret connection config. Shared with resume_setup.
    The CDW endpoint comes from config.yaml (env override wins, then the live
    Lambda value), so a redeploy without config.yaml handy is non-destructive."""
    cur = _current_lambda_env(lam)
    db_arn, db_server, db_port, db_database = ensure_db_secret(sm)
    gitlab_arn = ensure_gitlab_secret(sm)
    role_arn = ensure_lambda_role(iam)
    grant_secret_read(iam, LAMBDA_ROLE, [a for a in (db_arn, gitlab_arn) if a])
    grant_microvm_access(iam, LAMBDA_ROLE)
    env_vars = {
        # Sandbox is an AWS Lambda MicroVM (see lambda/sandbox_client.py).
        "MICROVM_IMAGE_ARN": _microvm_image_arn() or cur.get("MICROVM_IMAGE_ARN", ""),
        # Sandbox egress connectors (setup_vpc_egress.py); empty -> INTERNET_EGRESS.
        "MICROVM_EGRESS_CONNECTORS": os.environ.get("MICROVM_EGRESS_CONNECTORS", cur.get("MICROVM_EGRESS_CONNECTORS", "")),
        "CDW_SECRET_ARN": db_arn,
        "CDW_DATABASE": db_database,
        # CDW endpoint the sandbox dials — the institutional DB, over the VPC
        # egress connector. From config.yaml; env override > config.yaml > live.
        "CDW_SERVER": os.environ.get("CDW_SERVER", db_server or cur.get("CDW_SERVER", "")),
        "CDW_PORT": os.environ.get("CDW_PORT", db_port or cur.get("CDW_PORT", "1433")),
        # The ETL source is cloned from GitLab into the sandbox per run, so
        # list_etl/read_etl/grep_etl need no bridge URL and no bridge token.
        # ETL_BRIDGE_URL / ETL_SECRET_ARN are deliberately NOT set -- stale values on
        # the live function are dropped by this redeploy.
        #
        # GitLab SSH key: ARN only, exactly like the CDW login. Empty when no key was
        # found at provision time, which simply means no GitLab access.
        "GITLAB_SECRET_ARN": gitlab_arn or "",
        # Which repo the agent clones. Deliberately env-driven so re-pointing needs no
        # code change. This must be the SYNTHETIC ETL repo, never
        # big-arc/clinical-data-warehouse/cdw: production ETL differs from what the
        # synthetic warehouse actually runs (localized object references, plus the
        # deliberately injected defects), so pointing at production would have the agent
        # reading code that does not match the database it is profiling.
        "GITLAB_ETL_REPO": os.environ.get("GITLAB_ETL_REPO", cur.get("GITLAB_ETL_REPO", "")),
        "GITLAB_ETL_REF": os.environ.get("GITLAB_ETL_REF", cur.get("GITLAB_ETL_REF", "main")),
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
    require_expected_account()          # refuse to build a stack in the wrong account
    load_dotenv(HERE.parent / ".env")
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
