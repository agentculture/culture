"use strict";

// Mission Control SPA — vanilla JS, no build step.
const state = {
  selected: null,
  kind: "audit",
  es: null,
  chatTimer: null,
  view: "agents",
  // v8.19.14: append-only chat refresh. chatLastMessages is the previously
  // rendered message list; chatLastChannel resets the baseline on switch.
  // Without this, refreshChat replaceChildren'd every 2.5s and any scroll-up
  // got reset to bottom — making history unreachable.
  chatLastMessages: [],
  chatLastChannel: null,
};

// v8.19.14: pixel margin within which we consider the user "at the bottom"
// of a scrollable feed. If they're within this margin we auto-scroll on
// append; otherwise we leave scroll alone so they can read history.
const SCROLL_BOTTOM_THRESHOLD_PX = 40;

function isAtBottom(box) {
  return box.scrollHeight - box.scrollTop - box.clientHeight < SCROLL_BOTTOM_THRESHOLD_PX;
}

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

// ---- Main tab navigation --------------------------------------------------

function switchView(view) {
  state.view = view;
  document.querySelectorAll(".main-tab").forEach((t) => {
    t.classList.toggle("active", t.dataset.view === view);
  });
  document.getElementById("view-agents").classList.toggle("hidden", view !== "agents");
  document.getElementById("view-channels").classList.toggle("hidden", view !== "channels");
  document.getElementById("view-archived").classList.toggle("hidden", view !== "archived");
  if (view === "agents") refreshAgents();
  else if (view === "channels") refreshChannels();
  else if (view === "archived") refreshArchived();
}

document.querySelectorAll(".main-tab").forEach((tab) => {
  tab.onclick = () => switchView(tab.dataset.view);
});

// ---- Agents grid -----------------------------------------------------------

function groupTeams(agents) {
  const teams = new Map();
  const unassigned = [];
  const team = (k) => {
    if (!teams.has(k)) teams.set(k, { boss: null, workers: [] });
    return teams.get(k);
  };
  for (const a of agents) {
    if (a.is_boss) team(a.nick).boss = a;
    else if (a.boss) team(a.boss).workers.push(a);
    else unassigned.push(a);
  }
  return { teams, unassigned };
}

function teamHeader(text, count, noun) {
  const li = el("li", "team-header");
  li.appendChild(el("span", "team-name", text));
  li.appendChild(el("span", "team-count", `${count} ${noun}${count === 1 ? "" : "s"}`));
  return li;
}

function renderAgentItem(a, isWorker) {
  const item = el("li", "agent-item" + (isWorker ? " team-worker" : ""));
  if (a.nick === state.selected) item.classList.add("selected");
  item.onclick = () => selectAgent(a.nick);

  const row = el("div", "agent-row");
  const nick = el("span", "agent-nick");
  nick.appendChild(el("span", "dot " + a.state));
  nick.appendChild(document.createTextNode(a.nick));
  if (a.is_boss) nick.appendChild(el("span", "boss-tag", "BOSS"));
  if (a.idle) nick.appendChild(el("span", "idle-tag", "IDLE"));
  row.appendChild(nick);
  if (a.pending > 0) row.appendChild(el("span", "agent-pending", a.pending + " \u23F3"));
  item.appendChild(row);

  // Channels row
  if (a.channels && a.channels.length) {
    const chRow = el("div", "agent-channels");
    chRow.textContent = a.channels.join(", ");
    item.appendChild(chRow);
  }

  // Brief preview
  if (a.last_brief) {
    const brief = el("div", "agent-brief");
    brief.appendChild(el("span", "brief-label", "Brief: "));
    brief.appendChild(document.createTextNode(a.last_brief));
    item.appendChild(brief);
  }

  // Last assistant text
  if (a.last_assistant) {
    const asst = el("div", "agent-assistant");
    asst.appendChild(el("span", "asst-label", "Last: "));
    asst.appendChild(document.createTextNode(a.last_assistant));
    item.appendChild(asst);
  }

  const meta = el("div", "agent-meta");
  meta.appendChild(el("span", null, a.state));
  meta.appendChild(el("span", null, a.last_action || ""));
  item.appendChild(meta);

  const actions = el("div", "agent-actions");
  actions.appendChild(ctlBtn("pause", "Pause", a.nick));
  actions.appendChild(ctlBtn("resume", "Resume", a.nick));
  const archive = el("button", "btn btn-sm btn-archive", "Archive");
  archive.onclick = (e) => { e.stopPropagation(); confirmArchive(a.nick); };
  actions.appendChild(archive);
  const close = el("button", "btn btn-sm btn-danger", "Close");
  close.onclick = (e) => { e.stopPropagation(); confirmClose(a.nick); };
  actions.appendChild(close);
  item.appendChild(actions);
  return item;
}

async function refreshAgents() {
  let data;
  try { data = await api("/api/agents"); } catch (e) { return; }
  const list = $("#agent-list");
  list.replaceChildren();
  if (!data.agents.length) {
    list.appendChild(el("div", "empty", "No agents registered."));
    return;
  }
  const { teams, unassigned } = groupTeams(data.agents);
  for (const [bossNick, t] of teams) {
    const label = t.boss ? `${bossNick} \u00B7 team` : `${bossNick} \u00B7 team (boss offline)`;
    list.appendChild(teamHeader(label, t.workers.length, "worker"));
    if (t.boss) list.appendChild(renderAgentItem(t.boss, false));
    for (const w of t.workers) list.appendChild(renderAgentItem(w, true));
  }
  if (unassigned.length) {
    list.appendChild(teamHeader("unassigned", unassigned.length, "agent"));
    for (const a of unassigned) list.appendChild(renderAgentItem(a, false));
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

function confirmArchive(nick) {
  if (!confirm(`Archive agent ${nick}? It will be stopped and moved to the Archived tab.`)) return;
  post("/api/archive", { nick })
    .then((r) => { toast(r.ok ? `Archived ${nick}` : `Archive failed`, !r.ok); refreshAgents(); })
    .catch((e) => toast(e.message, true));
}

// ---- Channels tab ----------------------------------------------------------

async function refreshChannels() {
  // Channels-as-tasks (v8.19.11). Render the /api/tasks endpoint
  // which groups every boss's #boss-channel + #joint-* + #task-<worker>
  // children under ONE TASK heading. The task title is the boss's
  // mission.md headline; the boss state dot lets the orchestrator see
  // at a glance whether the boss is running.
  let data;
  try { data = await api("/api/tasks"); } catch (e) { return; }
  const container = document.getElementById("channel-list");
  container.replaceChildren();
  if (!data.tasks || !data.tasks.length) {
    container.appendChild(el("div", "empty", "No tasks active."));
    return;
  }
  for (const t of data.tasks) {
    container.appendChild(renderTaskGroup(t));
  }
}

function renderTaskGroup(task) {
  // ONE task = boss + title + all the channels that boss participates in.
  const block = el("div", "task-group");
  const header = el("div", "task-header");
  const dot = el("span", `task-state-dot member-dot-${task.state || "unknown"}`);
  header.appendChild(dot);
  const title = el("div", "task-title", task.title || (task.boss + "'s work"));
  header.appendChild(title);
  if (task.boss) {
    const bossLabel = el("span", "task-boss", task.boss);
    header.appendChild(bossLabel);
  }
  if (task.worker_count != null) {
    const count = el(
      "span", "task-workers",
      task.worker_count + " worker" + (task.worker_count === 1 ? "" : "s"),
    );
    header.appendChild(count);
  }
  block.appendChild(header);

  for (const ch of (task.channels || [])) {
    block.appendChild(renderChannelCard(ch));
  }
  return block;
}

function sectionHeader(text) {
  const d = el("div", "channel-section-header", text);
  return d;
}

function renderChannelCard(ch) {
  // Channels-first card (v8.19.7). Renders the channel as a discrete
  // task envelope: title + category tag + member chips with role badges
  // (boss first). Clicking a member chip opens that member's stream;
  // clicking elsewhere opens the channel's chat stream via the boss
  // (or the first member if no boss).
  const card = el("div", "channel-card");
  card.dataset.channel = ch.channel;

  const header = el("div", "channel-card-header");
  const title = el("div", "channel-title", ch.channel);
  header.appendChild(title);
  if (ch.category) {
    const tag = el("span", `channel-cat channel-cat-${ch.category}`, ch.category);
    header.appendChild(tag);
  }
  card.appendChild(header);

  // Member chips — each shows nick + role badge + state dot.
  if (ch.members && ch.members.length) {
    // members is now a list of objects {nick, role, is_boss, state};
    // tolerate the legacy flat-string shape for back-compat.
    const memberList = el("div", "channel-members");
    for (const m of ch.members) {
      const memberObj = (typeof m === "string") ? { nick: m, role: "", is_boss: false, state: "" } : m;
      const chip = el("span", "member-chip");
      const dot = el("span", `member-dot member-dot-${memberObj.state || "unknown"}`);
      chip.appendChild(dot);
      const nickEl = el("span", "member-nick", memberObj.nick);
      if (memberObj.is_boss) nickEl.classList.add("is-boss");
      chip.appendChild(nickEl);
      if (memberObj.role) {
        const rb = el("span", "role-badge", memberObj.role);
        chip.appendChild(rb);
      }
      chip.onclick = (ev) => {
        ev.stopPropagation();
        selectAgent(memberObj.nick);
        state.kind = "audit";
        document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
        document.querySelector('.tab[data-kind="audit"]').classList.add("active");
        openStream();
        updateStreamTitle();
      };
      memberList.appendChild(chip);
    }
    card.appendChild(memberList);
  } else {
    card.appendChild(el("div", "channel-members empty", "no members"));
  }

  // Click anywhere else: open the channel chat through the boss
  // (or first non-boss member when there's no boss listed).
  card.onclick = () => {
    if (!ch.members || !ch.members.length) return;
    const pick = ch.members.find((m) => m && typeof m === "object" && m.is_boss)
      || ch.members[0];
    const nick = typeof pick === "string" ? pick : pick.nick;
    selectAgent(nick);
    state.kind = "chat";
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelector('.tab[data-kind="chat"]').classList.add("active");
    openStream();
    updateStreamTitle();
  };
  card.style.cursor = "pointer";

  return card;
}

// ---- Archived tab ----------------------------------------------------------

async function refreshArchived() {
  let data;
  try { data = await api("/api/archived"); } catch (e) { return; }
  const container = document.getElementById("archived-list");
  container.replaceChildren();
  if (!data.agents || !data.agents.length) {
    container.appendChild(el("div", "empty", "No archived agents."));
    return;
  }
  container.appendChild(sectionHeader("Archived Agents"));
  for (const a of data.agents) {
    const card = el("div", "archived-card");
    const nick = el("div", "archived-nick", a.nick);
    if (a.is_boss) nick.appendChild(el("span", "boss-tag", "BOSS"));
    card.appendChild(nick);

    if (a.archived_at) {
      card.appendChild(el("div", "archived-date", "Archived: " + localTs(a.archived_at)));
    }
    if (a.archived_reason) {
      card.appendChild(el("div", "archived-reason", a.archived_reason));
    }
    if (a.channels && a.channels.length) {
      card.appendChild(el("div", "archived-channels", "Channels: " + a.channels.join(", ")));
    }

    // Actions row
    const actions = el("div", "agent-actions");
    const restore = el("button", "btn btn-sm btn-ok", "Restore");
    restore.onclick = (e) => {
      e.stopPropagation();
      if (!confirm(`Restore agent ${a.nick} from archive?`)) return;
      post("/api/unarchive", { nick: a.nick })
        .then((r) => { toast(r.ok ? `Restored ${a.nick}` : `Restore failed`, !r.ok); refreshArchived(); })
        .catch((err) => toast(err.message, true));
    };
    actions.appendChild(restore);
    card.appendChild(actions);

    // Click to view daemon log
    card.onclick = () => {
      selectAgent(a.nick);
      state.kind = "daemon-log";
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      document.querySelector('.tab[data-kind="daemon-log"]').classList.add("active");
      openStream();
      updateStreamTitle();
    };
    card.style.cursor = "pointer";

    container.appendChild(card);
  }
}

// ---- Stream (per-agent session / daemon-log) -------------------------------

const KIND_LABEL = { audit: "Activity", "daemon-log": "Daemon actions", chat: "Chat" };

function updateStreamTitle() {
  const nick = state.selected || "\u2014";
  const label = KIND_LABEL[state.kind] || state.kind;
  $("#stream-title").textContent = `${nick} \u00B7 ${label}`;
}

function selectAgent(nick) {
  state.selected = nick;
  refreshAgents();
  openStream();
  updateStreamTitle();
}

function openStream() {
  if (state.es) { state.es.close(); state.es = null; }
  if (state.chatTimer) { clearInterval(state.chatTimer); state.chatTimer = null; }
  // v8.19.14: reset the chat diff baseline when switching streams. Without
  // this, refreshChat would see a stale chatLastMessages from the prior
  // channel and never re-paint, leaving an empty box.
  state.chatLastMessages = [];
  state.chatLastChannel = null;
  const box = $("#stream");
  box.replaceChildren();
  const chatInput = $("#chat-input");
  if (state.kind === "chat") {
    chatInput.classList.remove("hidden");
    if (!state.selected) return;
    refreshChat();
    state.chatTimer = setInterval(refreshChat, 2500);
    return;
  }
  chatInput.classList.add("hidden");
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

// ---- Chat (talk to an agent in its channel) --------------------------------

async function refreshChat() {
  // v8.19.14: append-only refresh. Previously this called replaceChildren()
  // every 2.5s, nuking the user's scroll position and any history they had
  // scrolled to. Now we compare against the previously rendered messages
  // and only append the new tail.
  if (!state.selected || state.kind !== "chat") return;
  let data;
  try { data = await api(`/api/channel/${encodeURIComponent(state.selected)}`); }
  catch (_) { return; }
  const box = $("#stream");
  const messages = data.messages || [];

  // Channel switch resets the baseline. selectAgent/openStream already
  // clears the box; we just reset the diff state here.
  if (data.channel !== state.chatLastChannel) {
    state.chatLastChannel = data.channel;
    state.chatLastMessages = [];
  }

  const wasAtBottom = isAtBottom(box);

  if (!messages.length) {
    if (!state.chatLastMessages.length && !box.firstChild) {
      box.appendChild(el("div", "empty", `No messages in ${data.channel} yet.`));
    }
    return;
  }

  // First render after channel switch: paint everything fresh.
  if (!state.chatLastMessages.length) {
    box.replaceChildren();
    for (const m of messages) box.appendChild(el("div", "stream-line", m));
    state.chatLastMessages = messages.slice();
    box.scrollTop = box.scrollHeight;
    return;
  }

  // Diff: find where the previously rendered tail lands in the new list.
  // If it lands somewhere, append from there. If it's gone (server-side
  // rotation / truncation), fall back to a full re-render.
  const prevTail = state.chatLastMessages[state.chatLastMessages.length - 1];
  let appendStart = -1;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i] === prevTail) { appendStart = i + 1; break; }
  }
  if (appendStart === -1) {
    box.replaceChildren();
    for (const m of messages) box.appendChild(el("div", "stream-line", m));
  } else {
    for (let i = appendStart; i < messages.length; i++) {
      box.appendChild(el("div", "stream-line", messages[i]));
    }
  }
  state.chatLastMessages = messages.slice();

  if (wasAtBottom) box.scrollTop = box.scrollHeight;
}

function sendChat() {
  const input = $("#chat-text");
  const text = input.value.trim();
  if (!text || !state.selected) return;
  post("/api/message", { nick: state.selected, text })
    .then((r) => { input.value = ""; toast(`Sent to ${r.channel}`); refreshChat(); })
    .catch((e) => toast(e.message, true));
}

function localTs(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleTimeString();
  } catch (_) {
    return iso;
  }
}

function renderActivityTurn(box, rec) {
  // v8.19.14: snapshot scroll position BEFORE appending so we don't yank
  // the user back to the bottom every time a turn lands. They can read
  // history while new activity streams in.
  const wasAtBottom = isAtBottom(box);
  const card = el("div", "turn");
  card.appendChild(el("div", "ts", localTs(rec.ts)));
  if (rec.thinking) {
    const t = el("div", "thinking");
    t.textContent = rec.thinking;
    card.appendChild(t);
  }
  if (rec.text) {
    const t = el("div", "assistant-text");
    t.textContent = rec.text;
    card.appendChild(t);
  }
  for (const tu of rec.tool_uses || []) {
    const block = el("div", "tool-use");
    block.appendChild(el("div", "tool-head", "\u2192 " + (tu.name || "(tool)")));
    if (tu.input) {
      const pre = el("pre", "tool-input");
      pre.textContent = tu.input;
      block.appendChild(pre);
    }
    card.appendChild(block);
  }
  for (const tr of rec.tool_results || []) {
    const block = el("div", "tool-result");
    block.appendChild(el("div", "tool-head", "\u2190 " + (tr.name || "(result)")));
    if (tr.content || tr.preview) {
      const pre = el("pre", "tool-output");
      pre.textContent = tr.content || tr.preview;
      block.appendChild(pre);
    }
    card.appendChild(block);
  }
  box.appendChild(card);
  if (wasAtBottom) box.scrollTop = box.scrollHeight;
}

function appendStreamLine(box, raw) {
  // v8.19.14: same scroll-preservation contract as renderActivityTurn.
  const wasAtBottom = isAtBottom(box);
  let rec;
  try { rec = JSON.parse(raw); } catch (_) { rec = null; }
  if (rec && state.kind === "audit") {
    renderActivityTurn(box, rec);
    return;
  }
  const line = el("div", "stream-line");
  if (rec && state.kind === "daemon-log") {
    line.appendChild(el("span", "ts", localTs(rec.ts) + "  "));
    line.appendChild(el("span", "action", rec.action || "?"));
    const detail = rec.detail ? " " + Object.entries(rec.detail).map(([k, v]) => `${k}=${v}`).join(" ") : "";
    if (detail) line.appendChild(document.createTextNode(detail));
  } else {
    line.textContent = raw;
  }
  box.appendChild(line);
  if (wasAtBottom) box.scrollTop = box.scrollHeight;
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
  if (!confirm("EMERGENCY STOP \u2014 kill every agent (including the boss)?")) return;
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
    updateStreamTitle();
  };
});

// ---- Chat input ------------------------------------------------------------

$("#chat-send").onclick = sendChat;
$("#chat-text").addEventListener("keydown", (e) => { if (e.key === "Enter") sendChat(); });

// ---- Boot ------------------------------------------------------------------
// Channels-first (v8.19.7): Channels is the default tab, so prime it.
// Agents still refreshes in background so the tab switch is instant.

refreshChannels();
refreshAgents();
refreshPending();
setInterval(refreshChannels, 3000);
setInterval(refreshAgents, 2500);
setInterval(refreshPending, 2000);
