# dqa-sandbox-runner

A minimal Cloudflare Worker that runs untrusted/LLM-generated code in an
isolated [Cloudflare Sandbox](https://developers.cloudflare.com/sandbox/)
container and returns the output over HTTP. Used by the `run_python` tool in
`../agent.ipynb`.

## Endpoint

`POST /` with header `Authorization: Bearer <SANDBOX_SHARED_SECRET>` and body:

```json
{ "code": "print(2 + 2)", "language": "python", "session": "notebook" }
```

Response:

```json
{ "stdout": "4", "stderr": "", "results": [], "error": null }
```

- `language` — `python` (default), `javascript`, or `typescript`.
- `session` — the isolation boundary. The same name routes to the same
  container; use a per-run id (e.g. a LangGraph `thread_id`) to isolate runs.

## Deploy (one time)

Requires the Workers Paid plan (Containers).

```bash
cd sandbox-worker
npm install
npx wrangler secret put SANDBOX_SHARED_SECRET   # paste the same value used in ../.env
npm run deploy
```

Copy the printed `*.workers.dev` URL into `../.env` as `SANDBOX_WORKER_URL`.

### No local Docker required

`wrangler.jsonc` references Cloudflare's pre-built image
(`docker.io/cloudflare/sandbox:0.11.0-python` — the `-python` variant bundles the
Python interpreter that `runCode` needs) directly, so deploy does **not** build a
container locally and needs no Docker engine. The `Dockerfile` here is kept for
later: once you have Docker and want to bake extra packages into the image
(e.g. database drivers for the DQA pipeline), point `containers[].image` back at
`"./Dockerfile"` and redeploy.

## Local dev

```bash
echo 'SANDBOX_SHARED_SECRET=dev-secret' > .dev.vars
npm run dev
```

## Notes

- `instance_type` is `lite` (cheapest). Bump to `standard` in `wrangler.jsonc`
  for heavier workloads.
- Auth is a shared bearer secret — fine for local/experimental use. Put
  Cloudflare Access in front of the Worker before exposing anything real.
