"use strict";

// Mission Control SPA — vanilla JS, no build step.
const state = { selected: null, kind: "audit", es: null };

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

function toast(msg, isErr) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden", "err");
  if (isErr) t.classList.add("err");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), 3000);
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  let data = {};
  try { data = await res.json(); } catch (_) {}
  if (!res.ok || data.error) {
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return data;
}

async function post(path, body) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}

// ---- Agents grid -----------------------------------------------------------

async function refreshAgents() {
  let data;
  try { data = await api("/api/agents"); } catch (e) { return; }
  const list = $("#agent-list");
  list.replaceChildren();
  if (!data.agents.length) {
    list.appendChild(el("div", "empty", "No agents registered."));
    return;
  }
  for (const a of data.agents) {
    const item = el("li", "agent-item");
    if (a.nick === state.selected) item.classList.add("selected");
    item.onclick = () => selectAgent(a.nick);

    const row = el("div", "agent-row");
    const nick = el("span", "agent-nick");
    nick.appendChild(el("span", "dot " + a.state));
    nick.appendChild(document.createTextNode(a.nick));
    if (a.is_boss) nick.appendChild(el("span", "boss-tag", "BOSS"));
    row.appendChild(nick);
    if (a.pending > 0) row.appendChild(el("span", "agent-pending", a.pending + " ⏳"));
    item.appendChild(row);

    const meta = el("div", "agent-meta");
    meta.appendChild(el("span", null, a.state));
    meta.appendChild(el("span", null, a.last_action || ""));
    item.appendChild(meta);

    const actions = el("div", "agent-actions");
    actions.appendChild(ctlBtn("pause", "Pause", a.nick));
    actions.appendChild(ctlBtn("resume", "Resume", a.nick));
    const close = el("button", "btn btn-sm btn-danger", "Close");
    close.onclick = (e) => { e.stopPropagation(); confirmClose(a.nick); };
    actions.appendChild(close);
    item.appendChild(actions);
    list.appendChild(item);
  }
}

function ctlBtn(action, label, nick) {
  const b = el("button", "btn btn-sm", label);
  b.onclick = async (e) => {
    e.stopPropagation();
    try {
      const r = await post("/api/" + action, { nick });
      toast(r.ok ? `${label} ${nick}` : `${label} ${nick} failed`, !r.ok);
      refreshAgents();
    } catch (err) { toast(err.message, true); }
  };
  return b;
}

function confirmClose(nick) {
  if (!confirm(`Close agent ${nick}? Its daemon will be stopped.`)) return;
  post("/api/close", { nick })
    .then((r) => { toast(r.ok ? `Closed ${nick}` : `Close failed`, !r.ok); refreshAgents(); })
    .catch((e) => toast(e.message, true));
}

// ---- Stream (per-agent session / daemon-log) -------------------------------

function selectAgent(nick) {
  state.selected = nick;
  $("#stream-title").textContent = nick;
  refreshAgents();
  openStream();
}

function openStream() {
  if (state.es) { state.es.close(); state.es = null; }
  const box = $("#stream");
  box.replaceChildren();
  if (!state.selected) return;
  const url = `/api/stream/${state.kind}/${encodeURIComponent(state.selected)}`;
  const es = new EventSource(url);
  state.es = es;
  es.onmessage = (ev) => {
    if (!ev.data) return;
    appendStreamLine(box, ev.data);
  };
  es.onerror = () => { /* EventSource auto-reconnects */ };
}

function appendStreamLine(box, raw) {
  let rec;
  try { rec = JSON.parse(raw); } catch (_) { rec = null; }
  const line = el("div", "stream-line");
  if (rec && state.kind === "audit") {
    line.appendChild(el("span", "ts", (rec.ts || "") + "  "));
    if (rec.text) line.appendChild(document.createTextNode(rec.text));
    if (rec.tool_uses && rec.tool_uses.length) {
      const tools = rec.tool_uses.map((t) => t.name).join(", ");
      line.appendChild(el("div", "tools", "→ " + tools));
    }
  } else if (rec && state.kind === "daemon-log") {
    line.appendChild(el("span", "ts", (rec.ts || "") + "  "));
    line.appendChild(el("span", "action", rec.action || "?"));
    const detail = rec.detail ? " " + Object.entries(rec.detail).map(([k, v]) => `${k}=${v}`).join(" ") : "";
    if (detail) line.appendChild(document.createTextNode(detail));
  } else {
    line.textContent = raw;
  }
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

// ---- Pending approvals -----------------------------------------------------

async function refreshPending() {
  let data;
  try { data = await api("/api/pending"); } catch (_) { return; }
  const list = $("#pending-list");
  list.replaceChildren();
  const badge = $("#pending-badge");
  if (!data.pending.length) {
    list.appendChild(el("div", "empty", "Nothing waiting."));
    badge.classList.add("hidden");
    return;
  }
  badge.textContent = data.pending.length + " pending";
  badge.classList.remove("hidden");
  for (const p of data.pending) {
    const item = el("li", "pending-item");
    item.appendChild(el("div", "ptool", p.tool_name || "?"));
    item.appendChild(el("div", "pworker", p.helper_nick || ""));
    item.appendChild(el("div", "pinput", inputPreview(p)));
    const actions = el("div", "pending-actions");
    const ok = el("button", "btn btn-sm btn-ok", "Approve");
    ok.onclick = () => decide("approve", p.id, { id: p.id });
    const okAlways = el("button", "btn btn-sm btn-ok", "Always");
    okAlways.onclick = () => decide("approve", p.id, { id: p.id, always: true });
    const no = el("button", "btn btn-sm btn-danger", "Deny");
    no.onclick = () => {
      const reason = prompt("Deny reason (optional):") || "";
      decide("deny", p.id, { id: p.id, reason });
    };
    actions.appendChild(ok);
    actions.appendChild(okAlways);
    actions.appendChild(no);
    item.appendChild(actions);
    list.appendChild(item);
  }
}

function inputPreview(p) {
  const inp = p.input || {};
  if (p.tool_name === "Bash") return inp.command || "";
  if (p.tool_name === "Edit" || p.tool_name === "Write") return inp.file_path || "";
  try { return JSON.stringify(inp); } catch (_) { return ""; }
}

async function decide(kind, id, body) {
  try {
    await post("/api/" + kind, body);
    toast(`${kind} ${id}`);
    refreshPending();
    refreshAgents();
  } catch (e) { toast(e.message, true); }
}

// ---- Emergency controls ----------------------------------------------------

$("#btn-stop-pause").onclick = async () => {
  if (!confirm("Pause EVERY running agent?")) return;
  try { const r = await post("/api/stop-all", { mode: "pause" }); toast(`Paused ${(r.paused||[]).length} agent(s)`); refreshAgents(); }
  catch (e) { toast(e.message, true); }
};

$("#btn-stop-kill").onclick = async () => {
  if (!confirm("EMERGENCY STOP — kill every agent (including the boss)?")) return;
  try { await post("/api/stop-all", { mode: "kill" }); toast("Stopped all agents"); refreshAgents(); }
  catch (e) { toast(e.message, true); }
};

// ---- Stream tabs -----------------------------------------------------------

document.querySelectorAll(".tab").forEach((tab) => {
  tab.onclick = () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    state.kind = tab.dataset.kind;
    openStream();
  };
});

// ---- Boot ------------------------------------------------------------------

refreshAgents();
refreshPending();
setInterval(refreshAgents, 2500);
setInterval(refreshPending, 2000);
