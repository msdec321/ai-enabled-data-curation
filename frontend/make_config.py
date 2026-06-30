#!/usr/bin/env python3
"""Generate frontend/config.js from .frontend_config.json (Cognito region +
client id) and the runtime ARN in agentcore-runtime/.bedrock_agentcore.yaml.
The SPA loads config.js to know where to log in and which runtime to invoke.

    ../.venv/bin/python make_config.py
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
cfg = json.loads((HERE / ".frontend_config.json").read_text())
yaml_text = (HERE.parent / "agentcore-runtime" / ".bedrock_agentcore.yaml").read_text()
arn = re.search(r"agent_arn:\s*(arn:aws:bedrock-agentcore:\S+)", yaml_text).group(1)

out = {"region": cfg["region"], "clientId": cfg["client_id"], "runtimeArn": arn}
(HERE / "config.js").write_text("window.AUTODQA_CONFIG = " + json.dumps(out) + ";\n")
print("wrote config.js:", json.dumps(out))
