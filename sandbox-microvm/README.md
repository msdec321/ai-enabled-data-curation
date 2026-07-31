# autodqa-sandbox-microvm

The AutoDQA sandbox as an **AWS Lambda MicroVM** image — the AWS-native replacement
for the Cloudflare `sandbox-worker/` (migration point 2). MicroVMs give per-session
Firecracker isolation for untrusted/AI-generated code, and — critically — let us
**bake `python-tds` into the image** so `query_cdw` no longer pip-installs at runtime
(the cold-start that forced the timeout bump + serialization lock).

Decisions locked: **egress via connector** (originally public `INTERNET_EGRESS`
→ ngrok → SQL Server; now a customer VPC egress connector straight to the
institutional DB — see `agentcore-gateway/setup_vpc_egress.py`),
**name/tag-keyed sessions** (Phase 2).

## Contents (Phase 1 — the image)
| File | Role |
|---|---|
| `server.py` | Single-threaded HTTP code-exec server: `POST {code, env}` → run Python with `env` in os.environ → `{stdout, stderr, results, error}` (same contract `sandbox_client.py` expects). Answers the build `/ready` `/validate` hooks. |
| `Dockerfile` | `FROM public.ecr.aws/lambda/microvms:al2023-minimal`; bakes `python-tds` + `git`/`openssh-clients`; runs `server.py` on :8080. |
| `build_image.py` | Zip → S3 → `create/update-microvm-image` (pytds baked, snapshot). Writes `.microvm_config.json` (image ARN). |

**No ETL is baked in.** A snapshot briefly lived at `/opt/etl`, which meant every ETL
change needed a full cloud image rebuild before the served source matched what the
database runs — and if that step was missed, an ETL-origin defect became unattributable
because the agent read code that did not contain it. The sandbox now clones the
synthetic ETL repo from GitLab on first use in a run
(`agentcore-gateway/lambda/gitlab_clone.py` → `/tmp/etl-repo`), so updating the ETL is
a git push. `git` + `openssh-clients` in the image are what make that work; the private
key is never baked in, only injected per call from Secrets Manager.

## Build
```bash
cd sandbox-microvm
AWS_PROFILE=bigarc-autodqa AWS_DEFAULT_REGION=us-east-1 ../.venv/bin/python build_image.py
```
No local Docker — Lambda builds the image server-side from the Dockerfile on S3.

⚠️ `lambda-microvms` is a ~2-week-old service; `build_image.py` mirrors the documented
CLI but the boto3 client/param names are **verify-on-first-run** (see the note in the
script). The authoritative CLI, from the AWS docs:
```bash
aws lambda-microvms create-microvm-image \
  --name autodqa-sandbox \
  --code-artifact uri=s3://<bucket>/autodqa-sandbox/app.zip \
  --base-image-arn arn:aws:lambda:us-east-1:aws:microvm-image:al2023-1 \
  --build-role-arn arn:aws:iam::<acct>:role/autodqa-microvm-build-role
```

## Phase 2 (next) — repoint the broker
`sandbox_client.py` will drive the per-session VM lifecycle instead of POSTing to the
Cloudflare worker. The MicroVM ops (from the docs):
```bash
# launch one VM per run session (public egress connector)
aws lambda-microvms run-microvm --image-identifier autodqa-sandbox \
  --ingress-network-connectors "arn:aws:lambda:us-east-1:aws:network-connector:aws-network-connector:ALL_INGRESS" \
  --egress-network-connectors  "arn:aws:lambda:us-east-1:aws:network-connector:aws-network-connector:INTERNET_EGRESS" \
  --idle-policy '{"autoResumeEnabled":true,"maxIdleDurationSeconds":900,"suspendedDurationSeconds":300}'
# -> {microvmId, endpoint: "<id>.lambda-microvm.us-east-1.on.aws"}

aws lambda-microvms create-microvm-auth-token --microvm-identifier <id> \
  --expiration-in-minutes 30 --allowed-ports '[{"allPorts":{}}]'   # -> authToken

curl https://<endpoint>/ -H "X-aws-proxy-auth: <authToken>" -d '{"code":"...","env":{...}}'

aws lambda-microvms terminate-microvm --microvm-identifier <id>   # <- our destroy_sandbox
```
Session→microVM keyed by name/tag (looked up), since the broker Lambda is stateless.

## Then (Phase 3–5)
- Drop the `pip install` block from `tool_query_cdw._RUNNER`; remove `SANDBOX_WORKER_URL`/secret.
- Relax the `sandbox_client` timeout and drop the `gateway_tools.py` serialization lock
  (server is single-threaded per VM; no cold-install race).
- Decommission `sandbox-worker/` → compute is now fully AWS-native.
