// AutoDQA orchestrator — Cloudflare Worker PoC
//
// The agent loop from autodqa_agent.ipynb, re-hosted on Cloudflare behind
// Zero Trust/Access:
//   browser ── Access (SSO/OTP) ──▶ this Worker
//     ├── reasoning:  Bedrock Claude via Cloudflare AI Gateway (BYOK)
//     ├── query_cdw:  D1 (synthetic PCORnet CDM — Tier-1 demo data)
//     ├── search_etl / read_etl_file: R2 (synthetic ETL codebase)
//     └── run_python: dqa-sandbox-runner via private service binding

import { renderPage } from "./ui";

export interface Env {
  CDW: D1Database;
  ETL: KVNamespace; // synthetic ETL codebase (KV; R2 not activated on this account)
  SANDBOX: Fetcher;
  CF_ACCOUNT_ID: string;
  CF_AIG_GATEWAY: string;
  AWS_REGION: string;
  MODEL_ID: string;
  ACCESS_TEAM_DOMAIN: string; // e.g. "myteam" — empty until Access is enabled
  ACCESS_AUD: string; // Access application AUD tag — empty until enabled
  CF_AIG_TOKEN: string; // secret
  SANDBOX_SHARED_SECRET: string; // secret
}

const MAX_TURNS = 24;
const MAX_ROWS = 100;

const SYSTEM_PROMPT = `You are AutoDQA, an autonomous data-quality analyst for a PCORnet CDM \
clinical data warehouse. This is a Tier-1 DEMO environment: the warehouse is a SYNTHETIC \
SQLite database (no PHI), but the ETL codebase you can search reflects the real warehouse's \
T-SQL views, stored procedures, and mapping functions — root causes of data issues live there.

Tools:
- query_cdw: read-only SQL against the warehouse. SQLite dialect (use LIMIT not TOP). \
Tables: DEMOGRAPHIC, ENCOUNTER, DIAGNOSIS.
- search_etl / read_etl_file: search and read the ETL codebase (views, procs, functions, docs).
- run_python: isolated sandbox for computation. It has NO database access — inline any data \
your code needs.

Work step by step: gather evidence with tools first, then answer concisely with concrete \
numbers. When you reference ETL code, cite file paths and line numbers. The PCORnet SEX \
valueset is F, M, A, NI, UN, OT. Never attempt to modify data.`;

const TOOLS = [
  {
    toolSpec: {
      name: "query_cdw",
      description:
        "Run a single read-only SQL statement against the synthetic PCORnet CDM warehouse " +
        "(SQLite dialect — use LIMIT, not TOP). Tables: DEMOGRAPHIC, ENCOUNTER, DIAGNOSIS.",
      inputSchema: {
        json: {
          type: "object",
          properties: { sql: { type: "string", description: "One read-only SQL statement" } },
          required: ["sql"],
        },
      },
    },
  },
  {
    toolSpec: {
      name: "search_etl",
      description:
        "Search every file in the ETL codebase (T-SQL views/procs/functions + markdown docs) " +
        "for a regex or substring. Returns matching lines as path:line: text.",
      inputSchema: {
        json: {
          type: "object",
          properties: { pattern: { type: "string", description: "Regex or plain substring" } },
          required: ["pattern"],
        },
      },
    },
  },
  {
    toolSpec: {
      name: "read_etl_file",
      description: "Read one file from the ETL codebase by its path (as returned by search_etl).",
      inputSchema: {
        json: {
          type: "object",
          properties: { path: { type: "string" } },
          required: ["path"],
        },
      },
    },
  },
  {
    toolSpec: {
      name: "run_python",
      description:
        "Execute Python in an isolated Cloudflare sandbox container. No database or network " +
        "access — embed any input data in the code. Returns stdout/stderr.",
      inputSchema: {
        json: {
          type: "object",
          properties: { code: { type: "string" } },
          required: ["code"],
        },
      },
    },
  },
];

// ---------- Tool execution ----------

async function execTool(name: string, input: any, env: Env, session: string): Promise<unknown> {
  switch (name) {
    case "query_cdw": {
      const sql = String(input?.sql ?? "");
      // Demo-tier guard. On the real CDW, read-only is enforced at the DB
      // login (db_datareader), not here.
      if (!/^\s*(select|with|pragma|explain)\b/i.test(sql)) {
        return { error: "Only read-only statements (SELECT/WITH/PRAGMA/EXPLAIN) are allowed." };
      }
      try {
        const res = await env.CDW.prepare(sql).all();
        const rows = res.results ?? [];
        return { row_count: rows.length, rows: rows.slice(0, MAX_ROWS), truncated: rows.length > MAX_ROWS };
      } catch (e: any) {
        return { error: String(e?.message ?? e) };
      }
    }
    case "search_etl": {
      const pattern = String(input?.pattern ?? "");
      let re: RegExp | null = null;
      try {
        re = new RegExp(pattern, "i");
      } catch {
        re = null; // fall back to substring search
      }
      const listing = await env.ETL.list({ limit: 1000 });
      const matches: string[] = [];
      for (const key of listing.keys) {
        const body = await env.ETL.get(key.name, "text");
        if (!body) continue;
        const lines = body.split("\n");
        for (let i = 0; i < lines.length && matches.length < 60; i++) {
          const hit = re ? re.test(lines[i]) : lines[i].toLowerCase().includes(pattern.toLowerCase());
          if (hit) matches.push(`${key.name}:${i + 1}: ${lines[i].trim()}`);
        }
      }
      return { match_count: matches.length, matches };
    }
    case "read_etl_file": {
      const path = String(input?.path ?? "");
      const content = await env.ETL.get(path, "text");
      if (content === null) {
        const listing = await env.ETL.list({ limit: 1000 });
        return { error: `Not found: ${path}`, available_files: listing.keys.map((k) => k.name) };
      }
      return { path, content };
    }
    case "run_python": {
      const code = String(input?.code ?? "");
      const resp = await env.SANDBOX.fetch("https://sandbox/", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          authorization: `Bearer ${env.SANDBOX_SHARED_SECRET}`,
        },
        body: JSON.stringify({ code, language: "python", session }),
      });
      const json = (await resp.json().catch(() => null)) as any;
      if (!resp.ok || !json) return { error: `sandbox HTTP ${resp.status}`, detail: json };
      return { stdout: json.stdout, stderr: json.stderr, error: json.error };
    }
    default:
      return { error: `Unknown tool: ${name}` };
  }
}

// ---------- Bedrock via Cloudflare AI Gateway (BYOK) ----------

async function converse(env: Env, messages: unknown[]): Promise<any> {
  // Same endpoint shape the notebook uses; the gateway holds the IAM keys and
  // does the SigV4 signing, so the Worker sends an unsigned request with only
  // the gateway token.
  const url =
    `https://gateway.ai.cloudflare.com/v1/${env.CF_ACCOUNT_ID}/${env.CF_AIG_GATEWAY}` +
    `/aws-bedrock/bedrock-runtime/${env.AWS_REGION}/model/${encodeURIComponent(env.MODEL_ID)}/converse`;
  const resp = await fetch(url, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "cf-aig-authorization": `Bearer ${env.CF_AIG_TOKEN}`,
    },
    body: JSON.stringify({
      system: [{ text: SYSTEM_PROMPT }],
      messages,
      toolConfig: { tools: TOOLS },
      inferenceConfig: { maxTokens: 4096, temperature: 0 },
    }),
  });
  if (!resp.ok) {
    throw new Error(`Bedrock via AI Gateway failed: HTTP ${resp.status} — ${(await resp.text()).slice(0, 500)}`);
  }
  return resp.json();
}

// ---------- Agent loop ----------

type Emit = (event: Record<string, unknown>) => Promise<void>;

async function runAgent(task: string, session: string, env: Env, emit: Emit): Promise<void> {
  const messages: any[] = [{ role: "user", content: [{ text: task }] }];

  for (let turn = 0; turn < MAX_TURNS; turn++) {
    const out = await converse(env, messages);
    const msg = out?.output?.message;
    if (!msg) throw new Error(`No message in model response: ${JSON.stringify(out).slice(0, 300)}`);
    messages.push(msg);

    const toolUses: any[] = [];
    for (const block of msg.content ?? []) {
      if (block.text) await emit({ type: "text", text: block.text });
      if (block.toolUse) toolUses.push(block.toolUse);
    }

    if (out.stopReason !== "tool_use" || toolUses.length === 0) {
      await emit({ type: "done", stopReason: out.stopReason, turns: turn + 1, usage: out.usage });
      return;
    }

    const resultBlocks: any[] = [];
    for (const tu of toolUses) {
      await emit({ type: "tool_use", id: tu.toolUseId, name: tu.name, input: tu.input });
      let result: unknown;
      try {
        result = await execTool(tu.name, tu.input, env, session);
      } catch (e: any) {
        result = { error: String(e?.message ?? e) };
      }
      const resultText = JSON.stringify(result);
      await emit({
        type: "tool_result",
        id: tu.toolUseId,
        name: tu.name,
        preview: resultText.slice(0, 2000),
        truncated: resultText.length > 2000,
      });
      resultBlocks.push({
        toolResult: {
          toolUseId: tu.toolUseId,
          content: [{ text: resultText.slice(0, 30000) }],
          status: (result as any)?.error ? "error" : "success",
        },
      });
    }
    messages.push({ role: "user", content: resultBlocks });
  }
  await emit({ type: "error", message: `Stopped after ${MAX_TURNS} turns without a final answer.` });
}

// ---------- Cloudflare Access JWT validation ----------
//
// Access (enabled on the workers.dev domain via the dashboard) authenticates
// users at the edge and forwards a signed JWT in cf-access-jwt-assertion.
// Per Cloudflare docs the origin must validate that JWT (signature via the
// team's JWKS, plus aud/exp/iss) so direct-to-origin requests can't bypass
// the edge check. Enforced only when ACCESS_TEAM_DOMAIN + ACCESS_AUD are set.

let certCache: { certsUrl: string; keys: any[]; fetchedAt: number } | null = null;

function b64urlToBytes(s: string): Uint8Array {
  s = s.replace(/-/g, "+").replace(/_/g, "/");
  const pad = s.length % 4 ? "=".repeat(4 - (s.length % 4)) : "";
  const bin = atob(s + pad);
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

function accessEnforced(env: Env): boolean {
  return Boolean(env.ACCESS_TEAM_DOMAIN && env.ACCESS_AUD);
}

async function verifyAccess(
  request: Request,
  env: Env,
): Promise<{ ok: boolean; reason?: string; email?: string }> {
  if (!accessEnforced(env)) return { ok: true };

  const token = request.headers.get("cf-access-jwt-assertion");
  if (!token) return { ok: false, reason: "missing Access JWT" };
  const [h, p, sig] = token.split(".");
  if (!h || !p || !sig) return { ok: false, reason: "malformed JWT" };

  const team = env.ACCESS_TEAM_DOMAIN.replace(/^https?:\/\//, "").replace(/\.cloudflareaccess\.com.*$/, "");
  const certsUrl = `https://${team}.cloudflareaccess.com/cdn-cgi/access/certs`;
  if (!certCache || certCache.certsUrl !== certsUrl || Date.now() - certCache.fetchedAt > 3600_000) {
    const resp = await fetch(certsUrl);
    if (!resp.ok) return { ok: false, reason: `failed to fetch Access certs (HTTP ${resp.status})` };
    certCache = { certsUrl, keys: ((await resp.json()) as any).keys ?? [], fetchedAt: Date.now() };
  }

  const header = JSON.parse(new TextDecoder().decode(b64urlToBytes(h)));
  const jwk = certCache.keys.find((k: any) => k.kid === header.kid);
  if (!jwk) return { ok: false, reason: "no matching Access signing key" };

  const key = await crypto.subtle.importKey(
    "jwk",
    jwk,
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
    false,
    ["verify"],
  );
  const valid = await crypto.subtle.verify(
    "RSASSA-PKCS1-v1_5",
    key,
    b64urlToBytes(sig),
    new TextEncoder().encode(`${h}.${p}`),
  );
  if (!valid) return { ok: false, reason: "invalid JWT signature" };

  const payload = JSON.parse(new TextDecoder().decode(b64urlToBytes(p)));
  const audOk = Array.isArray(payload.aud)
    ? payload.aud.includes(env.ACCESS_AUD)
    : payload.aud === env.ACCESS_AUD;
  if (!audOk) return { ok: false, reason: "JWT audience mismatch" };
  if (typeof payload.exp === "number" && payload.exp * 1000 < Date.now()) {
    return { ok: false, reason: "JWT expired" };
  }
  if (!String(payload.iss ?? "").includes(`${team}.cloudflareaccess.com`)) {
    return { ok: false, reason: "JWT issuer mismatch" };
  }
  return { ok: true, email: payload.email };
}

// ---------- HTTP handlers ----------

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);

    const access = await verifyAccess(request, env);
    if (!access.ok) {
      return new Response(`403 — Cloudflare Access required (${access.reason})`, { status: 403 });
    }

    if (request.method === "GET" && url.pathname === "/") {
      return new Response(
        renderPage({ enforced: accessEnforced(env), email: access.email, model: env.MODEL_ID }),
        { headers: { "content-type": "text/html;charset=utf-8" } },
      );
    }

    if (request.method === "POST" && url.pathname === "/api/chat") {
      let body: { task?: string; session?: string };
      try {
        body = await request.json();
      } catch {
        return Response.json({ error: "invalid JSON body" }, { status: 400 });
      }
      const task = (body.task ?? "").trim();
      if (!task) return Response.json({ error: "missing 'task'" }, { status: 400 });
      const session = (body.session ?? "demo").replace(/[^a-zA-Z0-9_-]/g, "").slice(0, 64) || "demo";

      // Stream agent events to the browser as SSE while the loop runs.
      const { readable, writable } = new TransformStream();
      const writer = writable.getWriter();
      const enc = new TextEncoder();
      const emit: Emit = async (e) => {
        await writer.write(enc.encode(`data: ${JSON.stringify(e)}\n\n`));
      };
      ctx.waitUntil(
        (async () => {
          try {
            await runAgent(task, session, env, emit);
          } catch (e: any) {
            await emit({ type: "error", message: String(e?.message ?? e) }).catch(() => {});
          } finally {
            await writer.close().catch(() => {});
          }
        })(),
      );
      return new Response(readable, {
        headers: { "content-type": "text/event-stream", "cache-control": "no-cache" },
      });
    }

    return new Response("Not found", { status: 404 });
  },
};
