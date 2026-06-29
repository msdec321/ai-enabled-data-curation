# ETL bridge — local read-only file server for the ETL repo

A small server that runs **on your machine** (the trusted side, on the campus
VPN) and exposes a narrow read API over the ETL source tree, so the cloud agent
can read ETL code to put data-quality findings in context. It mirrors the DB
tunnel pattern (see `agentcore-gateway/README.md`): the GitLab repo at
`gitpapl1.uth.tmc.edu` is VPN-only and unreachable from the Cloudflare sandbox,
so instead of the agent reaching *in*, we publish a read view *out* through a
second ngrok tunnel.

All the safety lives here, on the trusted side, so the cloud tools can stay
dumb:

- **jail** — every path resolves and must stay inside the ETL root (no `..`)
- **denylist** — secret/data files are never served (`datavant/`, `.git`,
  `etl.load_hash_token`, `*.env`, `*secret*`, keys, …)
- **allowlist** — only text/code suffixes (`.sql .txt .md .json .yaml .yml`)
- **auth** — every request needs the bearer token (the ngrok URL is public)

## Start it (two terminals)

```bash
# terminal 1 — the bridge (binds 127.0.0.1:8000; leave it running)
./.venv/bin/python etl-bridge/etl_server.py

# terminal 2 — the public tunnel (your DB tunnel stays on its own `ngrok tcp 1433`)
ngrok http 8000
```

The server prints the bearer token at startup; ngrok prints the public URL:

```
Forwarding   https://<subdomain>.ngrok-free.dev -> http://localhost:8000
```

Sanity-check the full path before relying on it:

```bash
curl https://<subdomain>.ngrok-free.dev/health     # -> {"service":"autodqa-etl-bridge","ok":true}
```

> Free-plan ngrok: if a second concurrent tunnel is refused, add an `etl` entry
> to your `ngrok.yml` and run `ngrok start db etl` so one agent serves both.

## The bearer token is persisted

On first run the server writes a random token to `etl-bridge/.etl_server_token`
(gitignored) and reuses it on every restart. So **restarting the server does not
change the token** — the copy stored in Secrets Manager stays valid. Override
with `ETL_SERVER_TOKEN=…` if you ever want to. Deleting `.etl_server_token`
forces a new token (and then you must re-vault it).

## Restart checklist — what changes, and what to update

| You restart… | Token | URL | Cloud action needed |
|---|---|---|---|
| **the server only** | unchanged (persisted) | unchanged | none |
| **ngrok** (new session) | unchanged | **new URL** | **update the Lambda's `ETL_BRIDGE_URL`** |
| after deleting `.etl_server_token` | **new** | unchanged | re-vault the token, redeploy |

The common case is the middle row: ngrok hands out a new
`https://<subdomain>.ngrok-free.dev` each session, so after restarting it, point
the cloud side at the new URL. This is the exact analog of updating
`CDW_TUNNEL_ENDPOINT` for the DB tunnel — a surgical env-var merge on the Lambda,
no full redeploy:

```bash
AWS_PROFILE=autodqa-admin AWS_DEFAULT_REGION=us-east-1 ./.venv/bin/python - <<'PY'
import boto3
lam = boto3.client("lambda"); NAME = "autodqa-run-python"
env = lam.get_function_configuration(FunctionName=NAME)["Environment"]["Variables"]
env["ETL_BRIDGE_URL"] = "https://<new-subdomain>.ngrok-free.dev"   # <-- paste new URL
lam.update_function_configuration(FunctionName=NAME, Environment={"Variables": env})
lam.get_waiter("function_updated_v2").wait(FunctionName=NAME)
print("ETL_BRIDGE_URL ->", env["ETL_BRIDGE_URL"])
PY
```

## API

All endpoints require `Authorization: Bearer <token>` except `/health`.
Requests should also send `ngrok-skip-browser-warning: true` to bypass ngrok's
free-tier interstitial (the ETL tools do this automatically).

The cloud side reaches these endpoints from **inside the sandbox container** (not
the Lambda): the broker authorizes the call, fetches the bearer token from the
vault, and injects the URL + token as env vars into a stdlib fetch script run in
the run's ephemeral container — the same execution model as `query_cdw`. So every
tool runs in the sandbox; the first ETL call in a run cold-starts the container.

| Endpoint | Returns |
|---|---|
| `GET /health` | liveness (no auth) |
| `GET /list` | `{root, count, files:[{path, size}]}` |
| `GET /read?path=REL` | raw file text, capped at 1 MB |
| `GET /grep?q=PAT` | `{query, count, truncated, matches:[{path, line, text}]}`; opts `&regex=1 &max=N &ignorecase=0` |

## Scope

PoC bridge for **synthetic / low-sensitivity ETL code** over a public ngrok URL.
The denylist keeps the de-identification token and tokenized data off the wire,
but before pointing this at anything more sensitive, move to the longer-term
path (a commit-pinned mirror/snapshot of the GitLab repo) discussed in the
project notes.
