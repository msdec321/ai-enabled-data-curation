# autodqa-orchestrator

Cloudflare-hosted PoC of the AutoDQA agent: the LangGraph-notebook loop from
`../autodqa_agent.ipynb`, re-hosted as a Worker behind Cloudflare
Access/Zero Trust. Tier-0/1 by design — all data is synthetic, matching the
"synthetic-first" pilot phase of the IT Security proposal (Project Mermaid).

```
browser ── Cloudflare Access (SSO / OTP) ──▶ autodqa-orchestrator (this Worker)
                                                 │
              ┌──────────────────────────────────┼────────────────────┐
              ▼                                  ▼                    ▼
       AI Gateway ▶ Bedrock               D1: synthetic         KV: synthetic
       (BYOK, gateway signs)              PCORnet CDM           ETL codebase
              │
              ▼
       dqa-sandbox-runner (private service binding — run_python)
```

Live at: `https://autodqa-orchestrator.<account>.workers.dev`

## What's in the synthetic data

`seed/generate.py` (deterministic) plants four data-quality issues, each with
a root cause discoverable in the synthetic ETL files:

| Issue | Symptom | Root cause (in ETL files) |
|---|---|---|
| 1 | `DEMOGRAPHIC.SEX` has invalid `X`/`U` values (ALLSCRIPTS only) | `etl.DEMOGRAPHIC_ALLSCRIPTS.View.sql` passes `gender_code` raw instead of calling `etl.fnMapSex` |
| 2 | `BIRTH_DATE` NULL for most GECBI patients | `etl.DEMOGRAPHIC_GECBI.View.sql` selects `NULL AS BIRTH_DATE` (HL7 feed lacks PID-7) |
| 3 | 12 `DIAGNOSIS` rows reference nonexistent encounters | `etl.load_DIAGNOSIS.StoredProcedure.sql` has no existence check (hard-deleted encounters) |
| 4 | ED encounters with `DISCHARGE_DATE < ADMIT_DATE` | `etl.ENCOUNTER_EPIC.View.sql` reads the legacy `ed_visit_xfer` columns reversed for `ENC_TYPE='ED'` |

The chat UI's suggestion chips walk through these.

## Setup from scratch

```bash
npm install
npx wrangler d1 create autodqa-synthetic-cdw        # put id in wrangler.jsonc
npx wrangler kv namespace create ETL                # put id in wrangler.jsonc
../.venv/bin/python3 seed/generate.py               # writes seed/data.sql + etl-files/
npx wrangler d1 execute autodqa-synthetic-cdw --remote --file seed/schema.sql
npx wrangler d1 execute autodqa-synthetic-cdw --remote --file seed/data.sql
# upload etl-files/** to KV (bulk JSON; see git history) with --remote
npx wrangler deploy
npx wrangler secret put CF_AIG_TOKEN                # AI Gateway token
npx wrangler secret put SANDBOX_SHARED_SECRET       # same value the sandbox worker uses
```

KV is used for the ETL store because R2 isn't activated on this account
(API error 10042). To switch: enable R2 in the dashboard, replace the
`kv_namespaces` binding with an `r2_buckets` binding, and update the two
`env.ETL` call sites in `src/index.ts`.

## Enabling Cloudflare Access (the Zero Trust front door)

1. Dashboard → **Workers & Pages → autodqa-orchestrator → Settings →
   Domains & Routes → workers.dev → Enable Cloudflare Access**.
2. Optionally edit the generated Access application in the Zero Trust
   dashboard (One-time PIN for a demo; SSO/MFA/groups for anything real).
3. From the Access application's **Overview** tab copy the **Application
   Audience (AUD) tag**, and note your team domain
   (Zero Trust → Settings → Custom Pages, e.g. `myteam`).
4. Set both in `wrangler.jsonc` vars and redeploy:
   ```jsonc
   "ACCESS_TEAM_DOMAIN": "myteam",
   "ACCESS_AUD": "<aud-tag>"
   ```
   With both set, the Worker validates the Access JWT (signature via the
   team JWKS + aud/exp/iss) on every request, so direct-to-origin calls
   can't bypass the edge. Until then the UI shows a "demo mode" banner.

## Security posture (PoC honesty)

- Tier-0/1 only: synthetic data, full logging acceptable. Do NOT point this
  Worker at real CDW data — that requires the Mermaid Tier 2+ controls
  (DLP, redacted logs, BAA-covered services, dataset registry).
- The `query_cdw` read-only check is a regex guard; on a real database,
  enforce read-only at the DB login (`db_datareader`), not in code.
- The sandbox call now uses a private service binding (never public
  internet); the shared secret remains for defense in depth.
- AI Gateway logs request/response bodies by default — fine at this tier,
  revisit before any real data.
