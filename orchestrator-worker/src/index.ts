// AutoDQA orchestrator — Cloudflare Worker
//
// The agent loop, re-hosted on Cloudflare behind Zero Trust/Access, driving the
// AgentCore Gateway as its tool broker:
//   browser ── Access (SSO/OTP) ──▶ this Worker
//     ├── reasoning: Bedrock Claude via Cloudflare AI Gateway (BYOK)
//     └── tools:     AgentCore Gateway (MCP + Cognito OAuth) ──▶ Lambda broker
//                       ├── query_cdw: vault-injected DB login → sandbox → tunnel → SQL Server
//                       └── run_python: sandbox
//
// Every reasoning step, tool call, and result streams to the browser as SSE.

import { renderPage } from "./ui";

export interface Env {
  // Bedrock via Cloudflare AI Gateway
  CF_ACCOUNT_ID: string;
  CF_AIG_GATEWAY: string;
  AWS_REGION: string;
  MODEL_ID: string;
  CF_AIG_TOKEN: string; // secret

  // AgentCore Gateway (MCP) + Cognito OAuth
  AGENTCORE_GATEWAY_URL: string;
  COGNITO_TOKEN_ENDPOINT: string;
  COGNITO_CLIENT_ID: string;
  COGNITO_SCOPE: string;
  COGNITO_CLIENT_SECRET: string; // secret

  // Cloudflare Access (empty until enabled)
  ACCESS_TEAM_DOMAIN: string;
  ACCESS_AUD: string;

  // R2 bucket where each run's final report is persisted (see wrangler.jsonc).
  REPORTS_BUCKET: R2Bucket;
}

const MAX_TURNS = 24;

const SYSTEM_PROMPT = `You are AutoDQA, an autonomous data-quality analyst for a PCORnet CDM \
clinical data warehouse on Microsoft SQL Server. The data is SYNTHETIC (no PHI). Your tools reach \
the warehouse through a governed gateway that injects credentials for you.

- The query tool runs read-only T-SQL (SQL Server dialect — use TOP, not LIMIT). Tables: \
DEMOGRAPHIC, ENCOUNTER, DIAGNOSIS.
- The python tool runs code in an isolated sandbox with NO database access — inline any data your \
code needs.

Work step by step: gather evidence with the query tool, compute with python where helpful, then \
answer concisely with concrete numbers. The PCORnet SEX valueset is F, M, A, NI, UN, OT. \
Never attempt to modify data.`;

// Instruction for the dedicated report turn: after the evidence-gathering loop
// ends, the orchestrator asks the model once more to render a standalone report
// document, which is then persisted to R2. Kept separate from the conversational
// final answer so the deliverable has consistent structure.
const REPORT_INSTRUCTION = `Now write the final data-quality report as a standalone Markdown \
document for a reviewer who did NOT watch you work. The system's job is to surface data-quality \
ISSUES and present each one so a reviewer can validate it. Follow this structure exactly.

# AutoDQA Report

**Task:** restate the data-quality task you were given.

**Plan:** the approach you took — which checks you ran, and why.

**Number of potential issues found:** an integer (0 if none); it must equal the number of issue entries below.

Then ONE entry per potential issue, each separated by a horizontal rule (\`---\`) and laid out as:

### Issue 1
**Summary:** what the issue is, in one or two sentences.
**Query:** the read-only SQL (one or more queries) that surfaced it, VERBATIM in a \`\`\`sql code block — \
copied exactly as executed, with no reformatting and no queries you did not actually run.
**Query results:** the actual values the query returned — the concrete numbers you observed.
**Investigation findings:** your analysis — severity, how many rows are affected, and, when you traced \
it through the ETL, the root cause with file path and line numbers.

Repeat as "### Issue 2", "### Issue 3", … for every issue.

If you ran queries that did NOT surface an issue (checks that came back clean), list them after the \
issues so the clean result stays reproducible:

## Checks performed (no issues found)
For each: a one-line note of what it checked plus its result, then the query VERBATIM in a \`\`\`sql block.

Base the report ONLY on evidence gathered in this session — never invent numbers or queries. If you found \
no issues, set the count to 0, write no issue entries, and use "Checks performed" to show what you \
validated. Output only the Markdown document — no preamble, no closing remarks.`;

// ---------- Cognito OAuth (client-credentials) ----------

let tokenCache: { token: string; exp: number } | null = null;

async function getOAuthToken(env: Env): Promise<string> {
  if (tokenCache && tokenCache.exp > Date.now() + 60_000) return tokenCache.token;
  const body = new URLSearchParams({
    grant_type: "client_credentials",
    client_id: env.COGNITO_CLIENT_ID,
    client_secret: env.COGNITO_CLIENT_SECRET,
  });
  if (env.COGNITO_SCOPE) body.set("scope", env.COGNITO_SCOPE);
  const resp = await fetch(env.COGNITO_TOKEN_ENDPOINT, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!resp.ok) {
    throw new Error(`OAuth token failed: HTTP ${resp.status} — ${(await resp.text()).slice(0, 300)}`);
  }
  const j = (await resp.json()) as any;
  tokenCache = { token: j.access_token, exp: Date.now() + (j.expires_in ?? 3600) * 1000 };
  return tokenCache.token;
}

// ---------- Minimal MCP client (streamable HTTP) ----------

function parseSse(text: string): any {
  const datas = text
    .split("\n")
    .filter((l) => l.startsWith("data:"))
    .map((l) => l.slice(5).trim());
  for (let i = datas.length - 1; i >= 0; i--) {
    try {
      return JSON.parse(datas[i]);
    } catch {
      /* keep scanning */
    }
  }
  throw new Error(`no JSON-RPC in SSE response: ${text.slice(0, 300)}`);
}

class McpClient {
  private nextId = 1;
  private sessionId: string | null = null;
  constructor(private url: string, private token: string) {}

  private async rpc(method: string, params?: unknown, notification = false): Promise<any> {
    const headers: Record<string, string> = {
      "content-type": "application/json",
      accept: "application/json, text/event-stream",
      authorization: `Bearer ${this.token}`,
    };
    if (this.sessionId) headers["mcp-session-id"] = this.sessionId;
    const payload: any = { jsonrpc: "2.0", method };
    if (!notification) payload.id = this.nextId++;
    if (params !== undefined) payload.params = params;

    const resp = await fetch(this.url, { method: "POST", headers, body: JSON.stringify(payload) });
    const sid = resp.headers.get("mcp-session-id");
    if (sid) this.sessionId = sid;
    if (notification) return null;
    if (!resp.ok) {
      throw new Error(`MCP ${method}: HTTP ${resp.status} — ${(await resp.text()).slice(0, 300)}`);
    }
    const ct = resp.headers.get("content-type") || "";
    const data = ct.includes("text/event-stream") ? parseSse(await resp.text()) : await resp.json();
    if (data.error) throw new Error(`MCP ${method} error: ${JSON.stringify(data.error).slice(0, 300)}`);
    return data.result;
  }

  async initialize(): Promise<void> {
    await this.rpc("initialize", {
      protocolVersion: "2025-03-26",
      capabilities: {},
      clientInfo: { name: "autodqa-orchestrator", version: "1.0" },
    });
    await this.rpc("notifications/initialized", undefined, true);
  }

  async listTools(): Promise<any[]> {
    return (await this.rpc("tools/list", {}))?.tools ?? [];
  }

  async callTool(name: string, args: unknown): Promise<any> {
    return this.rpc("tools/call", { name, arguments: args });
  }
}

// Map MCP tools -> Bedrock toolConfig, stripping the gateway's "<target>___"
// prefix for cleaner names the model (and UI) see; remember the full name for
// the actual tools/call.
// Internal lifecycle tools the orchestrator drives directly — never exposed to
// the model in the Bedrock toolConfig.
const INTERNAL_TOOLS = new Set(["destroy_sandbox"]);
// Args the orchestrator injects per call — stripped from the model's view.
const ORCHESTRATOR_ARGS = new Set(["session"]);

function toBedrockTools(mcpTools: any[]): { tools: any[]; nameMap: Record<string, string> } {
  const nameMap: Record<string, string> = {};
  const tools: any[] = [];
  for (const t of mcpTools) {
    const short = t.name.includes("___") ? t.name.split("___").pop() : t.name;
    nameMap[short] = t.name; // keep the full name so the orchestrator can call it
    if (INTERNAL_TOOLS.has(short)) continue;
    const schema = t.inputSchema ?? { type: "object", properties: {} };
    const props = { ...(schema.properties ?? {}) };
    for (const a of ORCHESTRATOR_ARGS) delete props[a];
    tools.push({
      toolSpec: {
        name: short,
        description: t.description ?? "",
        inputSchema: { json: { ...schema, properties: props } },
      },
    });
  }
  return { tools, nameMap };
}

// ---------- Bedrock via Cloudflare AI Gateway (BYOK) ----------

async function converse(env: Env, messages: unknown[], tools: unknown[]): Promise<any> {
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
      toolConfig: { tools },
      // Extended thinking requires temperature 1; the reasoning budget is part
      // of maxTokens, so give headroom above the budget for the actual answer.
      inferenceConfig: { maxTokens: 8000, temperature: 1 },
      additionalModelRequestFields: {
        reasoning_config: { type: "enabled", budget_tokens: 2000 },
      },
    }),
  });
  if (!resp.ok) {
    throw new Error(`Bedrock via AI Gateway failed: HTTP ${resp.status} — ${(await resp.text()).slice(0, 500)}`);
  }
  return resp.json();
}

// ---------- Final report ----------

type ReportRef = { runId: string; reportId: string; url: string; filename: string; bytes: number };

// A readable, sortable object name: UTC timestamp + a short slice of the run id
// for collision safety (two runs in the same second still differ).
// e.g. 2026-06-18_153045Z-3fa85f64. The full runId is preserved in metadata.
function makeReportId(isoNow: string, runId: string): string {
  const ts = isoNow.slice(0, 19).replace(/:/g, "").replace("T", "_") + "Z"; // 2026-06-18_153045Z
  const short = runId.replace(/^run-/, "").slice(0, 8);
  return `${ts}-${short}`;
}

// One more model turn that renders the standalone report, then persists it to
// R2 under reports/<reportId>.md and serves a retrieval URL. Best-effort: a
// failure here is surfaced but does not fail the run (the answer already
// streamed). `tools` is passed through (not an empty list) because the message
// history contains toolUse/toolResult blocks, which Bedrock Converse requires a
// toolConfig to accompany; the instruction steers the model to just write.
async function persistReport(
  task: string,
  env: Env,
  messages: unknown[],
  tools: unknown[],
  runId: string,
  emit: Emit,
): Promise<ReportRef | null> {
  try {
    const reportMessages = [...messages, { role: "user", content: [{ text: REPORT_INSTRUCTION }] }];
    const out = await converse(env, reportMessages, tools);
    const md = (out?.output?.message?.content ?? [])
      .map((b: any) => (typeof b?.text === "string" ? b.text : ""))
      .filter(Boolean)
      .join("\n")
      .trim();
    if (!md) throw new Error("model produced no report text");

    const now = new Date().toISOString();
    const reportId = makeReportId(now, runId);
    await env.REPORTS_BUCKET.put(`reports/${reportId}.md`, md, {
      httpMetadata: { contentType: "text/markdown; charset=utf-8" },
      customMetadata: { task: task.slice(0, 1024), runId, createdAt: now },
    });

    const ref: ReportRef = {
      runId,
      reportId,
      url: `/api/report/${reportId}`,
      filename: `autodqa-${reportId}.md`,
      bytes: md.length,
    };
    await emit({ type: "report", ...ref });
    return ref;
  } catch (e: any) {
    await emit({ type: "error", message: `Report generation failed: ${String(e?.message ?? e)}` });
    return null;
  }
}

// ---------- Agent loop ----------

type Emit = (event: Record<string, unknown>) => Promise<void>;

async function runAgent(task: string, env: Env, emit: Emit): Promise<void> {
  // Establish the gateway session once per run (OAuth + MCP handshake + catalog).
  await emit({ type: "trace", active: ["orchestrator"], label: "Receiving task" });
  const token = await getOAuthToken(env);
  const mcp = new McpClient(env.AGENTCORE_GATEWAY_URL, token);
  // The broker is contacted here to LIST tools (catalog discovery), not to run
  // one — the model needs the catalog before it can choose a tool.
  await emit({ type: "trace", active: ["orchestrator", "broker"], label: "Discovering available tools" });
  await mcp.initialize();
  const { tools, nameMap } = toBedrockTools(await mcp.listTools());

  // One ephemeral sandbox container per agent run: a unique session id, threaded
  // into every tool call, and destroyed when the run ends. (Containers that
  // escape teardown — e.g. on an exception — still auto-sleep, per the worker's
  // sleepAfter setting.)
  const runId = `run-${crypto.randomUUID()}`;
  let containerStarted = false;
  let toreDown = false;
  const teardown = async () => {
    if (toreDown || !containerStarted) return; // no container was created
    toreDown = true;
    const destroyFull = nameMap["destroy_sandbox"];
    if (!destroyFull) return; // can't destroy — leave it "active"; sleepAfter reclaims it
    await emit({ type: "trace", active: ["orchestrator", "broker", "sandbox"], label: "Tearing down the sandbox" });
    try {
      await mcp.callTool(destroyFull, { session: runId });
    } catch {
      /* best effort — sleepAfter is the fallback */
    }
    await emit({ type: "container", id: runId, state: "down" });
    await emit({ type: "trace", active: [] });
  };

  const messages: any[] = [{ role: "user", content: [{ text: task }] }];

  for (let turn = 0; turn < MAX_TURNS; turn++) {
    await emit({ type: "trace", active: ["orchestrator", "aigw", "bedrock"], label: "Reasoning with the model" });
    const out = await converse(env, messages, tools);
    const msg = out?.output?.message;
    if (!msg) throw new Error(`No message in model response: ${JSON.stringify(out).slice(0, 300)}`);
    messages.push(msg);

    const toolUses: any[] = [];
    for (const block of msg.content ?? []) {
      // Reasoning blocks come back as reasoningContent on the Converse response;
      // emit before text so the thought precedes the answer. The full block
      // (with its signature) is preserved in `messages` above for tool-use turns.
      if (block.reasoningContent?.reasoningText?.text) {
        await emit({ type: "reasoning", text: block.reasoningContent.reasoningText.text });
      }
      if (block.text) await emit({ type: "text", text: block.text });
      if (block.toolUse) toolUses.push(block.toolUse);
    }

    if (out.stopReason !== "tool_use" || toolUses.length === 0) {
      // Final answer reached. Render the standalone report and persist it before
      // we finish — the sandbox can go; the report lives in R2, written here.
      await emit({ type: "trace", active: ["orchestrator", "aigw", "bedrock"], label: "Writing the final report" });
      const report = await persistReport(task, env, messages, tools, runId, emit);
      await emit({ type: "trace", active: ["orchestrator"], label: "Returning the answer" });
      await emit({ type: "done", stopReason: out.stopReason, turns: turn + 1, usage: out.usage, report });
      await teardown();
      return;
    }

    const resultBlocks: any[] = [];
    for (const tu of toolUses) {
      // First sandbox-backed tool call of the run → the per-run container spins up.
      if (!containerStarted) {
        containerStarted = true;
        await emit({ type: "container", id: runId, state: "up" });
      }
      const full = nameMap[tu.name] ?? tu.name;
      const cdwPath = full.includes("query_cdw");
      const dataset = (tu.input?.dataset as string) || "CDW";

      // The broker's tools/call is a single round trip — we can't observe the
      // internal authorize→execute boundary from here. But the two phases are
      // real (and auth is sub-second while execution is slow), so we show the
      // authorization phase, dispatch the call, then switch the diagram to the
      // execution phase after a brief beat while the call is still running.
      const call = (async () => {
        try {
          // Inject the per-run session id so the broker routes to this run's
          // container (overrides anything the model might have set).
          return { res: await mcp.callTool(full, { ...(tu.input ?? {}), session: runId }) };
        } catch (e: any) {
          return { err: e };
        }
      })();

      // Phase 1 — broker checks the registry and fetches credentials.
      await emit({
        type: "trace",
        active: cdwPath
          ? ["orchestrator", "broker", "registry", "vault"]
          : ["orchestrator", "broker", "vault"],
        label: cdwPath
          ? `Authorizing ${tu.name} on ${dataset} + fetching credentials`
          : `Fetching credentials for ${tu.name}`,
      });
      await emit({ type: "tool_use", id: tu.toolUseId, name: tu.name, input: tu.input });

      // Phase 2 — broker dispatches to the sandbox, which runs the work.
      let settled = false;
      void call.then(() => { settled = true; });
      await new Promise((r) => setTimeout(r, 700));
      if (!settled) {
        await emit({
          type: "trace",
          active: cdwPath
            ? ["orchestrator", "broker", "sandbox", "cdw"]
            : ["orchestrator", "broker", "sandbox"],
          label: cdwPath ? "Running the query in the sandbox" : "Executing in the sandbox",
        });
      }

      const { res, err } = await call;
      let resultText: string;
      let isError = false;
      if (err) {
        isError = true;
        resultText = JSON.stringify({ error: String(err?.message ?? err) });
      } else {
        isError = Boolean(res?.isError);
        resultText = (res?.content ?? [])
          .map((c: any) => (typeof c?.text === "string" ? c.text : JSON.stringify(c)))
          .join("\n");
        if (!resultText) resultText = JSON.stringify(res);
      }
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
          status: isError ? "error" : "success",
        },
      });
    }
    messages.push({ role: "user", content: resultBlocks });
  }
  await emit({ type: "error", message: `Stopped after ${MAX_TURNS} turns without a final answer.` });
  await teardown();
}

// ---------- Cloudflare Access JWT validation ----------
//
// Access (enabled on the workers.dev domain via the dashboard) authenticates
// users at the edge and forwards a signed JWT in cf-access-jwt-assertion. The
// origin must validate it (signature via the team JWKS, plus aud/exp/iss) so
// direct-to-origin requests can't bypass the edge. Enforced only when
// ACCESS_TEAM_DOMAIN + ACCESS_AUD are set.

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

    // Serve a persisted report. Behind the same Access gate as everything else.
    // `?download=1` forces a file download; otherwise it renders inline.
    if (request.method === "GET" && url.pathname.startsWith("/api/report/")) {
      const id = decodeURIComponent(url.pathname.slice("/api/report/".length));
      if (!/^[A-Za-z0-9_-]+$/.test(id)) {
        return new Response("invalid report id", { status: 400 });
      }
      const obj = await env.REPORTS_BUCKET.get(`reports/${id}.md`);
      if (!obj) return new Response("report not found", { status: 404 });
      const disp = url.searchParams.get("download") ? "attachment" : "inline";
      return new Response(obj.body, {
        headers: {
          "content-type": "text/markdown; charset=utf-8",
          "content-disposition": `${disp}; filename="autodqa-${id}.md"`,
          "cache-control": "no-store",
        },
      });
    }

    if (request.method === "POST" && url.pathname === "/api/chat") {
      let body: { task?: string };
      try {
        body = await request.json();
      } catch {
        return Response.json({ error: "invalid JSON body" }, { status: 400 });
      }
      const task = (body.task ?? "").trim();
      if (!task) return Response.json({ error: "missing 'task'" }, { status: 400 });

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
            await runAgent(task, env, emit);
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
