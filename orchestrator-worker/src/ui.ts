// Minimal single-page chat console for the AutoDQA agent.
// Server-rendered string — no build step, no framework.
//
// Left pane: the project's architecture diagram (docs/autodqa_architecture.svg),
// re-themed dark to match the UI, whose nodes glow as the worker emits
// {type:"trace", active:[...]} events; inactive nodes dim so the path stands out.

const ARCH_SVG = `<svg class="arch-svg" viewBox="150 30 550 670" role="img" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker>
    <mask id="arch-gaps" maskUnits="userSpaceOnUse"><rect x="0" y="0" width="700" height="880" fill="white"/><rect x="296.9" y="51.2" width="158.7" height="21.6" fill="black" rx="2"/><rect x="286.8" y="72.4" width="177.9" height="19.2" fill="black" rx="2"/><rect x="360.4" y="90.4" width="31.8" height="19.2" fill="black" rx="2"/><rect x="331.7" y="413.2" width="89.0" height="21.6" fill="black" rx="2"/><rect x="304.5" y="438.4" width="143.1" height="19.2" fill="black" rx="2"/><rect x="306.5" y="569.2" width="139.3" height="21.6" fill="black" rx="2"/><rect x="277.1" y="592.4" width="198.0" height="19.2" fill="black" rx="2"/><rect x="297.4" y="610.4" width="157.1" height="19.2" fill="black" rx="2"/><rect x="325.5" y="628.4" width="101.9" height="19.2" fill="black" rx="2"/></mask>
  </defs>

  <g class="anode" data-id="aigw" style="font-family:'Anthropic Sans',-apple-system,'Segoe UI',sans-serif">
    <rect x="252" y="40" width="248" height="78" rx="10" style="fill:rgb(42,36,24);stroke:rgb(196,154,78);stroke-width:1.2px"/>
    <text x="376" y="62" text-anchor="middle" dominant-baseline="central" style="fill:rgb(236,214,164);font-size:14px;font-weight:500">Cloudflare AI Gateway</text>
    <text x="376" y="82" text-anchor="middle" dominant-baseline="central" style="fill:rgb(205,174,116);font-size:12px">Access policies · Audit logging</text>
    <text x="376" y="100" text-anchor="middle" dominant-baseline="central" style="fill:rgb(205,174,116);font-size:12px">DLP</text>
  </g>
  <g class="anode" data-id="bedrock" style="font-family:'Anthropic Sans',-apple-system,'Segoe UI',sans-serif">
    <rect x="540" y="40" width="148" height="78" rx="10" style="fill:rgb(42,32,26);stroke:rgb(204,122,84);stroke-width:1.2px"/>
    <text x="614" y="62" text-anchor="middle" dominant-baseline="central" style="fill:rgb(238,191,166);font-size:14px;font-weight:500">Amazon Bedrock</text>
    <text x="614" y="82" text-anchor="middle" dominant-baseline="central" style="fill:rgb(210,150,111);font-size:12px">Model inference</text>
    <text x="614" y="100" text-anchor="middle" dominant-baseline="central" style="fill:rgb(210,150,111);font-size:12px">HIPAA eligible</text>
  </g>
  <line x1="500" y1="72" x2="538" y2="72" marker-end="url(#arrow)" style="fill:none;stroke:rgb(116,115,126);stroke-width:1.5px"/>
  <line x1="538" y1="88" x2="500" y2="88" marker-end="url(#arrow)" style="fill:none;stroke:rgb(116,115,126);stroke-width:1.5px"/>

  <g class="anode" data-id="orchestrator" style="font-family:'Anthropic Sans',-apple-system,'Segoe UI',sans-serif">
    <rect x="252" y="228" width="248" height="80" rx="10" style="fill:rgb(22,48,42);stroke:rgb(62,160,136);stroke-width:1.2px"/>
    <text x="376" y="262" text-anchor="middle" dominant-baseline="central" style="fill:rgb(155,224,203);font-size:14px;font-weight:500">LangGraph Orchestration Loop</text>
    <text x="376" y="284" text-anchor="middle" dominant-baseline="central" style="fill:rgb(105,194,170);font-size:12px">Trusted agent loop</text>
  </g>

  <text x="312" y="174" text-anchor="middle" style="fill:rgb(138,135,148);font-size:12px;font-family:'Anthropic Sans',sans-serif">1 · context input</text>
  <line x1="342" y1="228" x2="342" y2="120" marker-end="url(#arrow)" mask="url(#arch-gaps)" style="fill:none;stroke:rgb(116,115,126);stroke-width:1.5px"/>
  <text x="432" y="174" text-anchor="middle" style="fill:rgb(138,135,148);font-size:12px;font-family:'Anthropic Sans',sans-serif">2 · model response</text>
  <line x1="417" y1="120" x2="417" y2="228" marker-end="url(#arrow)" mask="url(#arch-gaps)" style="fill:none;stroke:rgb(116,115,126);stroke-width:1.5px"/>

  <g class="anode" data-id="broker" style="font-family:'Anthropic Sans',-apple-system,'Segoe UI',sans-serif">
    <rect x="252" y="396" width="248" height="80" rx="10" style="fill:rgb(23,38,56);stroke:rgb(79,147,207);stroke-width:1.2px"/>
    <text x="376" y="424" text-anchor="middle" dominant-baseline="central" style="fill:rgb(166,206,240);font-size:14px;font-weight:500">MCP Broker</text>
    <text x="376" y="448" text-anchor="middle" dominant-baseline="central" style="fill:rgb(118,174,222);font-size:12px">Tool + dataset allowlists</text>
  </g>

  <text x="304" y="338" text-anchor="end" style="fill:rgb(138,135,148);font-size:12px;font-family:'Anthropic Sans',sans-serif">3 · execution request</text>
  <line x1="342" y1="308" x2="342" y2="394" marker-end="url(#arrow)" style="fill:none;stroke:rgb(116,115,126);stroke-width:1.5px"/>
  <text x="414" y="338" text-anchor="start" style="fill:rgb(138,135,148);font-size:12px;font-family:'Anthropic Sans',sans-serif">8 · result</text>
  <line x1="410" y1="394" x2="410" y2="308" marker-end="url(#arrow)" mask="url(#arch-gaps)" style="fill:none;stroke:rgb(116,115,126);stroke-width:1.5px"/>

  <g class="anode" data-id="registry" style="font-family:'Anthropic Sans',-apple-system,'Segoe UI',sans-serif">
    <rect x="540" y="392" width="148" height="50" rx="10" style="fill:rgb(32,32,42);stroke:rgb(74,74,86);stroke-width:1.2px;stroke-dasharray:5px,4px"/>
    <text x="614" y="417" text-anchor="middle" dominant-baseline="central" style="fill:rgb(212,210,218);font-size:14px;font-weight:500">Dataset Registry</text>
  </g>
  <text x="520" y="406" text-anchor="middle" style="fill:rgb(138,135,148);font-size:12px;font-family:'Anthropic Sans',sans-serif">consults</text>
  <line x1="500" y1="417" x2="538" y2="417" marker-end="url(#arrow)" style="fill:none;stroke:rgb(116,115,126);stroke-width:1.5px;stroke-dasharray:5px,4px"/>

  <g class="anode" data-id="vault" style="font-family:'Anthropic Sans',-apple-system,'Segoe UI',sans-serif">
    <rect x="540" y="458" width="148" height="50" rx="10" style="fill:rgb(32,32,42);stroke:rgb(74,74,86);stroke-width:1.2px;stroke-dasharray:5px,4px"/>
    <text x="614" y="478" text-anchor="middle" dominant-baseline="central" style="fill:rgb(212,210,218);font-size:14px;font-weight:500">Secrets Manager</text>
    <text x="614" y="494" text-anchor="middle" dominant-baseline="central" style="fill:rgb(138,135,148);font-size:12px">AWS</text>
  </g>
  <text x="520" y="472" text-anchor="middle" style="fill:rgb(138,135,148);font-size:12px;font-family:'Anthropic Sans',sans-serif">retrieves</text>
  <line x1="500" y1="483" x2="538" y2="483" marker-end="url(#arrow)" style="fill:none;stroke:rgb(116,115,126);stroke-width:1.5px;stroke-dasharray:5px,4px"/>

  <g class="anode" data-id="sandbox" style="font-family:'Anthropic Sans',-apple-system,'Segoe UI',sans-serif">
    <rect x="252" y="556" width="248" height="100" rx="10" style="fill:rgb(32,32,58);stroke:rgb(130,120,216);stroke-width:1.2px"/>
    <text x="376" y="580" text-anchor="middle" dominant-baseline="central" style="fill:rgb(196,188,242);font-size:14px;font-weight:500">Cloudflare sandbox</text>
    <text x="376" y="602" text-anchor="middle" dominant-baseline="central" style="fill:rgb(162,150,214);font-size:12px">Graceful container create / delete</text>
    <text x="376" y="620" text-anchor="middle" dominant-baseline="central" style="fill:rgb(162,150,214);font-size:12px">Controlled code execution</text>
    <text x="376" y="638" text-anchor="middle" dominant-baseline="central" style="fill:rgb(162,150,214);font-size:12px">Managed egress</text>
  </g>

  <text x="304" y="508" text-anchor="end" style="fill:rgb(138,135,148);font-size:12px;font-family:'Anthropic Sans',sans-serif">4 · dispatch + inject creds</text>
  <line x1="342" y1="476" x2="342" y2="554" marker-end="url(#arrow)" style="fill:none;stroke:rgb(116,115,126);stroke-width:1.5px"/>
  <text x="414" y="508" text-anchor="start" style="fill:rgb(138,135,148);font-size:12px;font-family:'Anthropic Sans',sans-serif">7 · stdout / results</text>
  <line x1="410" y1="554" x2="410" y2="476" marker-end="url(#arrow)" mask="url(#arch-gaps)" style="fill:none;stroke:rgb(116,115,126);stroke-width:1.5px"/>

  <g class="anode" data-id="cdw" style="font-family:'Anthropic Sans',-apple-system,'Segoe UI',sans-serif">
    <rect x="540" y="554" width="148" height="56" rx="10" style="fill:rgb(31,42,20);stroke:rgb(118,164,62);stroke-width:1.2px"/>
    <text x="614" y="574" text-anchor="middle" dominant-baseline="central" style="fill:rgb(191,224,146);font-size:14px;font-weight:500">CDW · SQL Server</text>
    <text x="614" y="592" text-anchor="middle" dominant-baseline="central" style="fill:rgb(147,187,99);font-size:12px">Read-only</text>
  </g>
  <g class="anode" data-id="docstore" style="font-family:'Anthropic Sans',-apple-system,'Segoe UI',sans-serif">
    <rect x="540" y="634" width="148" height="56" rx="10" style="fill:rgb(32,32,42);stroke:rgb(74,74,86);stroke-width:1.2px"/>
    <text x="614" y="654" text-anchor="middle" dominant-baseline="central" style="fill:rgb(212,210,218);font-size:14px;font-weight:500">Documentation Store</text>
    <text x="614" y="672" text-anchor="middle" dominant-baseline="central" style="fill:rgb(138,135,148);font-size:12px">ETL code · docs</text>
  </g>
  <text x="520" y="554" text-anchor="middle" style="fill:rgb(138,135,148);font-size:12px;font-family:'Anthropic Sans',sans-serif">5 · read-only T-SQL</text>
  <line x1="500" y1="590" x2="538" y2="578" marker-end="url(#arrow)" style="fill:none;stroke:rgb(116,115,126);stroke-width:1.5px"/>
  <text x="512" y="624" text-anchor="middle" style="fill:rgb(138,135,148);font-size:12px;font-family:'Anthropic Sans',sans-serif">6 · rows</text>
  <line x1="538" y1="594" x2="500" y2="606" marker-end="url(#arrow)" style="fill:none;stroke:rgb(116,115,126);stroke-width:1.5px"/>
  <text x="520" y="662" text-anchor="middle" style="fill:rgb(138,135,148);font-size:12px;font-family:'Anthropic Sans',sans-serif">reads</text>
  <line x1="500" y1="634" x2="538" y2="658" marker-end="url(#arrow)" mask="url(#arch-gaps)" style="fill:none;stroke:rgb(116,115,126);stroke-width:1.5px"/>
</svg>`;

export function renderPage(opts: { enforced: boolean; email?: string; model: string }): string {
  const badge = opts.enforced
    ? `<span class="badge ok">&#128274; Access: ${escapeHtml(opts.email ?? "authenticated")}</span>`
    : `<span class="badge warn">&#9888; Access not enforced — demo mode</span>`;

  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AutoDQA — agent console</title>
<style>
  :root { color-scheme: dark; --pane: clamp(400px, 33vw, 720px); --hdr: 52px; }
  * { box-sizing: border-box; }
  body { margin: 0; background: #16161d; color: #e8e6e3; font: 15px/1.5 -apple-system, "Segoe UI", sans-serif; }
  header { display: flex; align-items: center; gap: 12px; height: var(--hdr); padding: 0 20px; border-bottom: 1px solid #2c2c38; }
  header h1 { font-size: 16px; margin: 0; font-weight: 600; }
  header .model { color: #8a8794; font-size: 12px; margin-left: auto; }
  .badge { font-size: 12px; padding: 3px 10px; border-radius: 999px; }
  .badge.ok { background: #0d3b2e; color: #7fd9b3; }
  .badge.warn { background: #4a3208; color: #f0c060; }

  /* Left architecture pane — the diagram re-themed to the dark UI palette */
  .diagram-pane { position: fixed; left: 0; top: var(--hdr); bottom: 0; width: var(--pane); padding: 16px 14px; border-right: 1px solid #2c2c38; background: #13131a; overflow: auto; }
  .diagram-pane h2 { font-size: 11px; color: #8a8794; margin: 0 0 4px; text-transform: uppercase; letter-spacing: .6px; }
  .diagram-pane p.cap { font-size: 11px; color: #6f6c78; margin: 0 0 12px; min-height: 14px; transition: color .2s; }
  .diagram-pane p.cap.live { color: #8fb3e8; font-weight: 500; }
  .arch-card { background: #17171f; border: 1px solid #2a2a36; border-radius: 10px; padding: 10px; }
  .arch-svg { width: 100%; display: block; }
  .anode { transition: opacity .25s ease, filter .25s ease; }
  .arch-svg.running .anode { opacity: .3; }
  .anode.active { opacity: 1 !important; filter: drop-shadow(0 0 6px rgba(130,175,235,.85)) brightness(1.45) saturate(1.1); }

  /* Live container registry under the diagram */
  .containers { margin-top: 18px; }
  .containers h2 { font-size: 11px; color: #8a8794; margin: 0 0 8px; text-transform: uppercase; letter-spacing: .6px; display: flex; align-items: center; gap: 8px; }
  .cactive { font-weight: 400; color: #7fd9b3; text-transform: none; letter-spacing: 0; }
  .clist { display: flex; flex-direction: column; gap: 6px; }
  .cempty { font-size: 11px; color: #565463; margin: 0; }
  .crow { display: flex; align-items: center; gap: 8px; font-size: 12px; padding: 5px 9px; border: 1px solid #2a2a36; border-radius: 7px; background: #17171f; transition: opacity .3s; }
  .cdot { width: 8px; height: 8px; border-radius: 50%; flex: 0 0 auto; background: #565463; }
  .crow.active .cdot { background: #4fcf8f; box-shadow: 0 0 6px #4fcf8f; animation: cpulse 1.3s ease-in-out infinite; }
  .crow.gone { opacity: .5; }
  .cid { font-family: ui-monospace, "SF Mono", monospace; color: #c8c6cc; }
  .cstate { margin-left: auto; font-size: 11px; color: #8a8794; }
  @keyframes cpulse { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }

  main { margin-left: var(--pane); max-width: 820px; padding: 20px; padding-bottom: 130px; }
  .chips { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; }
  .chip { background: #23232e; border: 1px solid #34343f; border-radius: 999px; padding: 6px 12px; font-size: 12.5px; cursor: pointer; color: #b9b6c0; }
  .chip:hover { border-color: #5a5a6a; color: #e8e6e3; }
  .msg { margin: 14px 0; }
  .msg.user .bubble { background: #2b3a55; border-radius: 12px 12px 4px 12px; margin-left: 18%; }
  .msg.agent .bubble { background: #20202a; border-radius: 12px 12px 12px 4px; margin-right: 8%; }
  .bubble { padding: 10px 14px; white-space: pre-wrap; word-break: break-word; }
  .reasoning { margin: 8px 8% 8px 0; padding: 8px 14px; border-left: 2px solid #4a4458; color: #9a93b0; font-style: italic; font-size: 13.5px; white-space: pre-wrap; word-break: break-word; }
  .reasoning .rlabel { display: block; font-style: normal; font-size: 11px; letter-spacing: .4px; text-transform: uppercase; color: #6f6c84; margin-bottom: 3px; }
  details.tool { background: #1c1f2b; border: 1px solid #2e3344; border-radius: 8px; margin: 8px 8% 8px 0; font-size: 13px; }
  details.tool summary { cursor: pointer; padding: 8px 12px; color: #8fb3e8; }
  details.tool pre { margin: 0; padding: 10px 12px; overflow-x: auto; font-size: 12px; border-top: 1px solid #2e3344; }
  details.tool pre.result { color: #9ad0a9; max-height: 240px; overflow-y: auto; }
  .step { margin: 12px 0 4px; padding-left: 12px; border-left: 2px solid #2e3344; }
  .step .lbl { color: #8fb3e8; font-size: 12.5px; font-weight: 500; }
  .step .route { color: #6f6c78; font-size: 11px; margin-top: 1px; }
  .status { color: #8a8794; font-size: 12.5px; margin: 8px 0; }
  .error { background: #3b1518; border: 1px solid #6e2a30; color: #f0a0a0; padding: 10px 14px; border-radius: 8px; margin: 8px 0; white-space: pre-wrap; }
  .report-card { margin: 14px 8% 14px 0; padding: 12px 14px; background: #16271f; border: 1px solid #2f6f57; border-radius: 10px; }
  .report-card .rhead { color: #7fd9b3; font-weight: 600; font-size: 13.5px; margin-bottom: 8px; }
  .report-card a { display: inline-block; margin-right: 16px; color: #8fb3e8; text-decoration: none; font-size: 13px; }
  .report-card a:hover { text-decoration: underline; }
  .report-card .bytes { color: #6f6c78; font-size: 12px; }
  .report-card .rname { color: #8a8794; font-size: 12px; font-family: ui-monospace, "SF Mono", monospace; margin-bottom: 8px; }
  form { position: fixed; bottom: 0; left: var(--pane); right: 0; background: #16161dee; backdrop-filter: blur(4px); border-top: 1px solid #2c2c38; padding: 14px 20px; }
  .formrow { max-width: 820px; margin: 0 auto; display: flex; gap: 10px; }
  textarea { flex: 1; resize: none; background: #20202a; color: #e8e6e3; border: 1px solid #34343f; border-radius: 10px; padding: 10px 12px; font: inherit; height: 64px; }
  button { background: #3d6bb3; color: white; border: 0; border-radius: 10px; padding: 0 22px; font: inherit; cursor: pointer; }
  button:disabled { background: #2c3a52; color: #777; cursor: wait; }
  .spinner { display: inline-block; width: 12px; height: 12px; border: 2px solid #555; border-top-color: #9ad; border-radius: 50%; animation: spin 0.8s linear infinite; vertical-align: -2px; margin-right: 6px; }
  @keyframes spin { to { transform: rotate(360deg); } }
  @media (max-width: 940px) {
    .diagram-pane { display: none; }
    main { margin-left: 0; }
    form { left: 0; }
  }
</style>
</head>
<body>
<header>
  <h1>AutoDQA &mdash; agent console</h1>
  ${badge}
  <span class="model">${escapeHtml(opts.model)} via AI Gateway &middot; tools via AgentCore Gateway</span>
</header>

<aside class="diagram-pane">
  <h2>Architecture</h2>
  <p class="cap" id="archStep">The active path lights up as the agent runs.</p>
  <div class="arch-card">${ARCH_SVG}</div>
  <div class="containers">
    <h2>Sandbox containers <span class="cactive" id="cActive">0 active</span></h2>
    <div class="clist" id="cList"><p class="cempty">none yet</p></div>
  </div>
</aside>

<main id="log">
  <div class="chips" id="chips">
    <button class="chip">How many rows are in DEMOGRAPHIC, ENCOUNTER, and DIAGNOSIS?</button>
    <button class="chip">Profile DEMOGRAPHIC: row count, null rate per column, and the SEX value distribution vs the PCORnet valueset (F, M, A, NI, UN, OT).</button>
    <button class="chip">Find SEX values in DEMOGRAPHIC that are not in the PCORnet valueset, and count rows for each invalid value.</button>
    <button class="chip">How many DIAGNOSIS rows reference an ENCOUNTERID that does not exist in ENCOUNTER?</button>
  </div>
</main>
<form id="form">
  <div class="formrow">
    <textarea id="task" placeholder="Give the agent a data-quality task&hellip; (Enter to send, Shift+Enter for newline)"></textarea>
    <button id="send" type="submit">Run</button>
  </div>
</form>
<script>
const log = document.getElementById("log");
const form = document.getElementById("form");
const taskEl = document.getElementById("task");
const sendBtn = document.getElementById("send");
const session = crypto.randomUUID().slice(0, 18);
const archSvg = document.querySelector(".arch-svg");
const nodeEls = document.querySelectorAll(".anode");
const archStep = document.getElementById("archStep");
const DEFAULT_CAP = "The active path lights up as the agent runs.";

// ---- live container registry ----
const cList = document.getElementById("cList");
const cActive = document.getElementById("cActive");
const containers = {};
let activeCount = 0;
const shortId = (id) => String(id).replace(/^run-/, "").slice(0, 8);
function renderActive() { cActive.textContent = activeCount + " active"; }
function containerUp(id) {
  const empty = cList.querySelector(".cempty"); if (empty) empty.remove();
  const row = el("div", "crow active");
  row.appendChild(el("span", "cdot"));
  row.appendChild(el("span", "cid", shortId(id)));
  const st = el("span", "cstate", "active");
  row.appendChild(st);
  cList.appendChild(row);
  containers[id] = { row, st };
  activeCount++; renderActive();
}
function containerDown(id) {
  const c = containers[id]; if (!c) return;
  c.row.classList.remove("active"); c.row.classList.add("gone");
  c.st.textContent = "destroyed";
  activeCount = Math.max(0, activeCount - 1); renderActive();
}
function addContainerLog(id, up) {
  const d = el("div", "step");
  d.appendChild(el("div", "lbl",
    (up ? "\\u{1F7E2} " : "\\u{26AB} ") + "Sandbox container " + shortId(id) + (up ? " created" : " destroyed")));
  log.appendChild(d);
  d.scrollIntoView({ block: "end" });
}

function setTrace(active, label) {
  const set = new Set(active || []);
  archSvg.classList.toggle("running", set.size > 0);
  nodeEls.forEach((n) => n.classList.toggle("active", set.has(n.dataset.id)));
  if (label) { archStep.textContent = "▸ " + label; archStep.classList.add("live"); }
  else { archStep.textContent = DEFAULT_CAP; archStep.classList.remove("live"); }
}

document.getElementById("chips").addEventListener("click", (e) => {
  if (e.target.classList.contains("chip")) { taskEl.value = e.target.textContent.trim(); taskEl.focus(); }
});
taskEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
});

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function addBubble(role, text) {
  const m = el("div", "msg " + role);
  const b = el("div", "bubble", text);
  m.appendChild(b); log.appendChild(m);
  b.scrollIntoView({ block: "end" });
  return b;
}

function addReasoning(text) {
  const d = el("div", "reasoning");
  d.appendChild(el("span", "rlabel", "\\u{1F4AD} reasoning"));
  d.appendChild(document.createTextNode(text));
  log.appendChild(d);
  d.scrollIntoView({ block: "end" });
}

const toolCards = {};
function addTool(ev) {
  const d = el("details", "tool");
  const input = ev.input && (ev.input.sql || ev.input.code || ev.input.query || ev.input.path);
  d.appendChild(el("summary", "", "\\u{1F527} " + ev.name));
  d.appendChild(el("pre", "", typeof input === "string" ? input : JSON.stringify(ev.input, null, 2)));
  log.appendChild(d); toolCards[ev.id] = d;
  d.scrollIntoView({ block: "end" });
}
function addToolResult(ev) {
  const d = toolCards[ev.id]; if (!d) return;
  let pretty = ev.preview;
  try { pretty = JSON.stringify(JSON.parse(ev.preview), null, 2); } catch {}
  d.appendChild(el("pre", "result", pretty + (ev.truncated ? "\\n… (truncated)" : "")));
}

function addReport(ev) {
  const d = el("div", "report-card");
  d.appendChild(el("div", "rhead", "\\u{1F4C4} Final report saved"));
  if (ev.filename) d.appendChild(el("div", "rname", ev.filename));
  const view = document.createElement("a");
  view.href = ev.url; view.target = "_blank"; view.rel = "noopener";
  view.textContent = "View report";
  d.appendChild(view);
  const dl = document.createElement("a");
  dl.href = ev.url + "?download=1";
  dl.textContent = "Download .md";
  d.appendChild(dl);
  if (ev.bytes) d.appendChild(el("span", "bytes", Math.max(1, Math.round(ev.bytes / 1024)) + " KB"));
  log.appendChild(d);
  d.scrollIntoView({ block: "end" });
}

const NODE_NAMES = { orchestrator: "Orchestrator", aigw: "AI Gateway", bedrock: "Bedrock", broker: "MCP Broker", registry: "Dataset Registry", vault: "Secrets Manager", sandbox: "Sandbox", cdw: "CDW", docstore: "Documentation Store" };
function addStep(ev) {
  const s = el("div", "step");
  s.appendChild(el("div", "lbl", "▸ " + (ev.label || "Step")));
  // Drop the orchestrator (always the hub) from the displayed route.
  const route = (ev.active || []).filter((id) => id !== "orchestrator").map((id) => NODE_NAMES[id] || id);
  if (route.length) s.appendChild(el("div", "route", route.join(" → ")));
  log.appendChild(s);
  s.scrollIntoView({ block: "end" });
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const task = taskEl.value.trim();
  if (!task || sendBtn.disabled) return;
  taskEl.value = "";
  sendBtn.disabled = true;
  setTrace([]);
  addBubble("user", task);
  const status = el("div", "status");
  status.innerHTML = '<span class="spinner"></span>agent running…';
  log.appendChild(status);

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ task, session }),
    });
    if (!resp.ok || !resp.body) {
      log.appendChild(el("div", "error", "HTTP " + resp.status + ": " + (await resp.text())));
      return;
    }
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\\n\\n")) >= 0) {
        const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
        if (!chunk.startsWith("data: ")) continue;
        const ev = JSON.parse(chunk.slice(6));
        if (ev.type === "container") {
          if (ev.state === "up") { containerUp(ev.id); addContainerLog(ev.id, true); }
          else { containerDown(ev.id); addContainerLog(ev.id, false); }
        }
        else if (ev.type === "reasoning") addReasoning(ev.text);
        else if (ev.type === "text") addBubble("agent", ev.text);
        else if (ev.type === "tool_use") addTool(ev);
        else if (ev.type === "tool_result") addToolResult(ev);
        else if (ev.type === "trace") { setTrace(ev.active, ev.label); addStep(ev); }
        else if (ev.type === "report") addReport(ev);
        else if (ev.type === "error") log.appendChild(el("div", "error", ev.message));
        else if (ev.type === "done") status.textContent = "done — " + ev.turns + " model turns";
        status.scrollIntoView({ block: "end" });
      }
    }
  } catch (err) {
    log.appendChild(el("div", "error", String(err)));
  } finally {
    if (status.querySelector(".spinner")) status.remove();
    setTrace([]);
    sendBtn.disabled = false;
    taskEl.focus();
  }
});
</script>
</body>
</html>`;
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
