#!/usr/bin/env python3
"""De-risk gate for the JWT front-end path.

Mirrors exactly what the browser SPA will do: fetch a Cognito access token via
USER_PASSWORD_AUTH, then POST directly to the AgentCore Runtime endpoint with the
bearer token — no IAM/SigV4. Also sends a CORS preflight so we learn whether a
browser is actually allowed to call the runtime directly (the make-or-break for
the no-proxy architecture).

Requires the runtime to already be switched to JWT inbound auth (re-run
agentcore-runtime/deploy.sh after frontend/.frontend_config.json exists).

    AUTODQA_UI_USER=... AUTODQA_UI_PASSWORD=... AWS_PROFILE=bigarc-autodqa \
      AWS_DEFAULT_REGION=us-east-1 ../.venv/bin/python invoke_jwt.py "Use run_python to compute 6*7."
"""
import json
import os
import re
import sys
import urllib.parse
import uuid
from pathlib import Path

import boto3
import requests

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
cfg = json.loads((HERE / ".frontend_config.json").read_text())
region, client_id = cfg["region"], cfg["client_id"]
user, pw = os.environ["AUTODQA_UI_USER"], os.environ["AUTODQA_UI_PASSWORD"]
task = sys.argv[1] if len(sys.argv) > 1 else "Use run_python to compute 6*7 and report it."

# 1) Cognito access token via USER_PASSWORD_AUTH — what the browser does via fetch.
idp = boto3.client("cognito-idp", region_name=region)
auth = idp.initiate_auth(
    ClientId=client_id, AuthFlow="USER_PASSWORD_AUTH",
    AuthParameters={"USERNAME": user, "PASSWORD": pw})
token = auth["AuthenticationResult"]["AccessToken"]
print(f"[1] Cognito access token OK ({len(token)} chars)")

# 2) Runtime ARN from the deploy config -> the direct HTTPS invoke URL.
yaml_text = (REPO / "agentcore-runtime" / ".bedrock_agentcore.yaml").read_text()
arn = re.search(r"agent_arn:\s*(arn:aws:bedrock-agentcore:\S+)", yaml_text).group(1)
escaped = urllib.parse.quote(arn, safe="")
url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped}/invocations?qualifier=DEFAULT"
print(f"[2] runtime {arn.split('/')[-1]}")

# 3) CORS preflight probe — exactly what a browser sends before the POST.
pre = requests.options(url, headers={
    "Origin": "https://d-example.cloudfront.net",
    "Access-Control-Request-Method": "POST",
    "Access-Control-Request-Headers": "authorization,content-type,x-amzn-bedrock-agentcore-runtime-session-id",
}, timeout=15)
acao = pre.headers.get("access-control-allow-origin")
verdict = "browser-direct OK" if acao else "NO CORS headers -> browser-direct blocked; need a proxy"
print(f"[3] CORS preflight -> HTTP {pre.status_code}; Access-Control-Allow-Origin: {acao!r}  ({verdict})")

# 4) Direct bearer invoke (no IAM), streaming the SSE response.
session_id = f"jwt-test-{uuid.uuid4()}"  # >=33 chars
resp = requests.post(url, headers={
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
    "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
}, data=json.dumps({"task": task}), timeout=180, stream=True)
print(f"[4] invoke -> HTTP {resp.status_code} ({resp.headers.get('content-type')})")
if resp.status_code != 200:
    print(resp.text[:800])
    sys.exit(1)
for line in resp.iter_lines():
    if line:
        s = line.decode("utf-8")
        print("    " + (s[6:] if s.startswith("data: ") else s))
