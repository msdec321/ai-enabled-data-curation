// Minimal single-page chat console for the AutoDQA agent.
// Server-rendered string — no build step, no framework.

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
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; background: #16161d; color: #e8e6e3; font: 15px/1.5 -apple-system, "Segoe UI", sans-serif; }
  header { display: flex; align-items: center; gap: 12px; padding: 14px 20px; border-bottom: 1px solid #2c2c38; }
  header h1 { font-size: 16px; margin: 0; font-weight: 600; }
  header .model { color: #8a8794; font-size: 12px; margin-left: auto; }
  .badge { font-size: 12px; padding: 3px 10px; border-radius: 999px; }
  .badge.ok { background: #0d3b2e; color: #7fd9b3; }
  .badge.warn { background: #4a3208; color: #f0c060; }
  main { max-width: 880px; margin: 0 auto; padding: 20px; padding-bottom: 130px; }
  .chips { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; }
  .chip { background: #23232e; border: 1px solid #34343f; border-radius: 999px; padding: 6px 12px; font-size: 12.5px; cursor: pointer; color: #b9b6c0; }
  .chip:hover { border-color: #5a5a6a; color: #e8e6e3; }
  .msg { margin: 14px 0; }
  .msg.user .bubble { background: #2b3a55; border-radius: 12px 12px 4px 12px; margin-left: 18%; }
  .msg.agent .bubble { background: #20202a; border-radius: 12px 12px 12px 4px; margin-right: 8%; }
  .bubble { padding: 10px 14px; white-space: pre-wrap; word-break: break-word; }
  details.tool { background: #1c1f2b; border: 1px solid #2e3344; border-radius: 8px; margin: 8px 8% 8px 0; font-size: 13px; }
  details.tool summary { cursor: pointer; padding: 8px 12px; color: #8fb3e8; }
  details.tool pre { margin: 0; padding: 10px 12px; overflow-x: auto; font-size: 12px; border-top: 1px solid #2e3344; }
  details.tool pre.result { color: #9ad0a9; max-height: 240px; overflow-y: auto; }
  .status { color: #8a8794; font-size: 12.5px; margin: 8px 0; }
  .error { background: #3b1518; border: 1px solid #6e2a30; color: #f0a0a0; padding: 10px 14px; border-radius: 8px; margin: 8px 0; white-space: pre-wrap; }
  form { position: fixed; bottom: 0; left: 0; right: 0; background: #16161dee; backdrop-filter: blur(4px); border-top: 1px solid #2c2c38; padding: 14px 20px; }
  .formrow { max-width: 880px; margin: 0 auto; display: flex; gap: 10px; }
  textarea { flex: 1; resize: none; background: #20202a; color: #e8e6e3; border: 1px solid #34343f; border-radius: 10px; padding: 10px 12px; font: inherit; height: 64px; }
  button { background: #3d6bb3; color: white; border: 0; border-radius: 10px; padding: 0 22px; font: inherit; cursor: pointer; }
  button:disabled { background: #2c3a52; color: #777; cursor: wait; }
  .spinner { display: inline-block; width: 12px; height: 12px; border: 2px solid #555; border-top-color: #9ad; border-radius: 50%; animation: spin 0.8s linear infinite; vertical-align: -2px; margin-right: 6px; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<header>
  <h1>AutoDQA &mdash; agent console</h1>
  ${badge}
  <span class="model">${escapeHtml(opts.model)} via AI Gateway &middot; synthetic Tier-1 CDM</span>
</header>
<main id="log">
  <div class="chips" id="chips">
    <button class="chip">Profile the DEMOGRAPHIC table: row count, null rate per column, and the SEX value distribution vs the PCORnet valueset.</button>
    <button class="chip">Find invalid SEX values in DEMOGRAPHIC, figure out which source system they come from, and trace the root cause in the ETL code.</button>
    <button class="chip">How many DIAGNOSIS rows reference an ENCOUNTERID that does not exist in ENCOUNTER? What in the ETL allows that?</button>
    <button class="chip">Are there encounters with DISCHARGE_DATE before ADMIT_DATE? Which ETL view causes it?</button>
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

const toolCards = {};
function addTool(ev) {
  const d = el("details", "tool");
  const input = ev.input && (ev.input.sql || ev.input.code || ev.input.pattern || ev.input.path);
  d.appendChild(el("summary", "", "\\u{1F527} " + ev.name + (ev.input && ev.input.pattern ? " — " + ev.input.pattern : "")));
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

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const task = taskEl.value.trim();
  if (!task || sendBtn.disabled) return;
  taskEl.value = "";
  sendBtn.disabled = true;
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
        if (ev.type === "text") addBubble("agent", ev.text);
        else if (ev.type === "tool_use") addTool(ev);
        else if (ev.type === "tool_result") addToolResult(ev);
        else if (ev.type === "error") log.appendChild(el("div", "error", ev.message));
        else if (ev.type === "done") status.textContent = "done — " + ev.turns + " model turns";
        status.scrollIntoView({ block: "end" });
      }
    }
  } catch (err) {
    log.appendChild(el("div", "error", String(err)));
  } finally {
    if (status.querySelector(".spinner")) status.remove();
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
