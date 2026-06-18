# autodqa-orchestrator

The AutoDQA agent re-hosted as a Cloudflare Worker with a streaming web console:
submit a data-quality task and watch the agent reason, call tools, and answer in
real time. It is a thin orchestrator — reasoning on Bedrock, **all tools driven
through the AgentCore Gateway** (see `../agentcore-gateway/`).

```
browser ── Cloudflare Access (SSO/OTP) ──▶ autodqa-orchestrator (this Worker)
                                               │
              ┌────────────────────────────────┼─────────────────────────────┐
              ▼                                ▼                              
       AI Gateway ▶ Bedrock            AgentCore Gateway (MCP + Cognito OAuth)
       (BYOK, gateway signs)                   │
       reasoning                               ▼
                                       Lambda broker (registry + vault)
                                          ├─ query_cdw → sandbox → tunnel → SQL Server
                                          └─ run_python → sandbox
```

Live at: `https://autodqa-orchestrator.<account>.workers.dev`

## How it works

The agent loop (`src/index.ts`) runs entirely in the Worker:

1. Per task it fetches a Cognito OAuth token and opens an MCP session to the
   AgentCore Gateway, pulling the tool catalog from `tools/list` (so the tools
   the model sees always match what the gateway exposes — today `query_cdw` and
   `run_python`).
2. It calls Bedrock (Claude) via the Cloudflare AI Gateway for reasoning.
3. When the model requests a tool, the Worker invokes it through the gateway
   (`tools/call`); the gateway's Lambda authorizes it, injects the credential
   from the vault, and runs it. The Worker holds **no DB or sandbox secret** —
   only the gateway's OAuth client secret.
4. Every step — reasoning text, tool call, tool result, final answer — streams
   to the browser as SSE and renders in `src/ui.ts`.

The tool names are de-prefixed for display (`query_cdw`, not
`sandbox___query_cdw`) and re-prefixed for the gateway call.

## Dependencies

This Worker is the front of the stack — the rest must be up:

- **AgentCore Gateway deployed** (`../agentcore-gateway/` — `setup_gateway.py`).
  The gateway URL, Cognito token endpoint, client id, and scope are baked into
  `wrangler.jsonc` from `agentcore-gateway/.gateway_config.json`.
- **The ngrok TCP tunnel up** for `query_cdw` (the gateway's Lambda reaches the
  LAN SQL Server through it). `run_python` works without the tunnel.

## Setup

```bash
cd orchestrator-worker
npm install
npx wrangler deploy
npx wrangler secret put CF_AIG_TOKEN            # AI Gateway token (same as ../.env)
npx wrangler secret put COGNITO_CLIENT_SECRET   # from agentcore-gateway/.gateway_config.json -> cognito.client_secret
```

If the AgentCore gateway is ever recreated, refresh the four `*_GATEWAY_*` /
`COGNITO_*` vars in `wrangler.jsonc` from the new `.gateway_config.json` and
re-put `COGNITO_CLIENT_SECRET`, then redeploy.

## Enabling Cloudflare Access (the Zero Trust front door)

1. Dashboard → **Workers & Pages → autodqa-orchestrator → Settings → Domains &
   Routes → workers.dev → Enable Cloudflare Access**.
2. Optionally edit the generated Access application (One-time PIN for a demo;
   SSO/MFA/groups for anything real).
3. From the Access application's **Overview** tab copy the **Application
   Audience (AUD) tag** and note your team domain.
4. Set both in `wrangler.jsonc` vars and redeploy:
   ```jsonc
   "ACCESS_TEAM_DOMAIN": "myteam",
   "ACCESS_AUD": "<aud-tag>"
   ```
   With both set, the Worker validates the Access JWT (signature via the team
   JWKS + aud/exp/iss) on every request, so direct-to-origin calls can't bypass
   the edge. Until then the UI shows a "demo mode" banner.

## Security posture (PoC honesty)

- Tier-0/1 only: synthetic data. The DB it reaches via the tunnel is a synthetic
  SQL Server on the LAN; the login is read-only. Do NOT point this at real CDW
  data — that requires the Mermaid Tier 2+ controls.
- The AgentCore gateway logs tool I/O (CloudWatch) and the AI Gateway logs
  request/response bodies by default — fine at this tier, revisit before real data.
- With Access not yet enforced, the `*.workers.dev` URL is publicly reachable;
  the AgentCore gateway behind it still requires the OAuth client secret (a Worker
  secret), so the tools aren't open to the world. Enable Access before sharing.

## Vestigial files

`seed/` and `etl-files/` are from the earlier design where this Worker queried a
synthetic PCORnet CDM in D1 and a synthetic ETL codebase in KV. Those bindings
were removed when tools moved behind the AgentCore Gateway; the directories are
kept for reference and are no longer used by the Worker.
