"""The AutoDQA LangGraph agent — reasoning on Bedrock, tools via the AgentCore Gateway.

Ported from autodqa_agent.ipynb cell 6. The ONE substantive change from the
notebook: tools are no longer local (pyodbc / subprocess) — they come from the
AgentCore Gateway MCP catalog (gateway_tools.py), because a Runtime instance runs
in AWS and can't reach the LAN. Bedrock reasoning still routes through the
Cloudflare AI Gateway (BYOK) for v1, exactly as the notebook does.
"""
import os

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from langchain.agents import create_agent
from langchain_aws import ChatBedrockConverse

SYSTEM = (
    "You are AutoDQA, an autonomous data-quality analyst for a PCORnet CDM clinical data "
    "warehouse on Microsoft SQL Server. The data is SYNTHETIC (no PHI). Your tools reach the "
    "warehouse and ETL through a governed gateway that injects credentials for you.\n"
    "- query_cdw runs read-only T-SQL (SQL Server dialect — use TOP, not LIMIT). "
    "Tables: DEMOGRAPHIC, ENCOUNTER, DIAGNOSIS.\n"
    "- list_etl, grep_etl, read_etl browse, search, and read the ETL SQL codebase.\n"
    "- get_valuesets returns the PCORnet CDM permissible values for a column (for conformance checks).\n"
    "- run_python runs code in an isolated sandbox with NO database access — inline any data it needs.\n"
    "Work step by step: gather evidence with the tools, then answer concisely with concrete numbers, "
    "citing ETL file paths and line numbers when you reference code. Never attempt to modify data."
)


def build_llm():
    """Bedrock client routed through the Cloudflare AI Gateway (BYOK).

    v1 keeps the notebook's proven path: the gateway holds the AWS IAM SigV4 keys
    and signs the upstream request, so the container needs NO AWS Bedrock creds —
    only the cf-aig-authorization token (CF_AIG_TOKEN). When we tackle migration
    point 3 (drop the AI Gateway), this collapses to a plain region-only client
    that signs with the Runtime's IAM execution role, and the CF_* vars go away.
    """
    region = os.environ.get("AWS_REGION", "us-east-1")
    model_id = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    acct = os.environ["CF_ACCOUNT_ID"]
    gateway = os.environ["CF_AIG_GATEWAY"]
    token = os.environ["CF_AIG_TOKEN"]

    endpoint = f"https://gateway.ai.cloudflare.com/v1/{acct}/{gateway}/aws-bedrock/bedrock-runtime/{region}"
    client = boto3.client(
        "bedrock-runtime",
        region_name=region,
        endpoint_url=endpoint,
        config=Config(signature_version=UNSIGNED),  # the gateway does the SigV4 signing
    )

    def _add_aig_auth(request, **kwargs):
        request.headers["cf-aig-authorization"] = f"Bearer {token}"

    client.meta.events.register("before-send.bedrock-runtime", _add_aig_auth)
    return ChatBedrockConverse(model=model_id, client=client, region_name=region, temperature=0)


def build_agent(llm, tools):
    """LangGraph ReAct agent (langchain create_agent), unchanged from the notebook."""
    return create_agent(model=llm, tools=tools, system_prompt=SYSTEM)
