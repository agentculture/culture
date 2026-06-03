"use strict";

// Mission Control SPA — vanilla JS, no build step.
const state = {
  selected: null,
  // v8.19.22: explicit room/channel override. When a user clicks a card
  // body (a "room" inside the Channel), the Chat tab should show THAT
  // room's history — not the worker's home #task-<worker> channel.
  // selectedChannel carries the room name; null means "use the selected
  // agent's home channel" (the legacy behaviour for chip clicks).
  selectedChannel: null,
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

// v8.19.21: compact token formatter for the per-agent + per-task badges.
// 999 → "999t", 12_345 → "12.3k", 4_567_890 → "4.6M". Stays inside the
// chip width budget while preserving order-of-magnitude readability.
function formatTokens(n) {
  if (n == null) return "";
  const x = Number(n);
  if (!isFinite(x) || x <= 0) return "";
  if (x < 1000) return x.toFixed(0) + "t";
  if (x < 1_000_000) return (x / 1000).toFixed(x < 10_000 ? 1 : 0) + "k";
  return (x / 1_000_000).toFixed(x < 10_000_000 ? 1 : 0) + "M";
}

// v8.19.14 round 2: the channel/agent/pending lists previously called
// replaceChildren() on every poll (every 2.5–3s), nuking the DOM even
// when the data was identical. The user saw the entire left panel
// flicker every few seconds. Fix:
//   1. JSON-snapshot the incoming data and skip the re-render entirely
//      when it matches the previous snapshot — the common case for an
//      idle mesh, so most polls become DOM-free.
//   2. When the data DID change, snapshot the parent scroll container's
//      scrollTop BEFORE replaceChildren and restore it AFTER, so the
//      user doesn't get yanked to the top of a long list.
// state.listSnapshots holds the previous JSON-serialized payload per
// list id; same-payload = same DOM = skip.
state.listSnapshots = {};

function scrollContainerOf(el) {
  // Walk up from `el` to find the nearest ancestor with overflow-y auto/scroll.
  // For the three left/middle/right columns this is the wrapping <section>.
  for (let n = el?.parentElement; n; n = n.parentElement) {
    const ov = getComputedStyle(n).overflowY;
    if (ov === "auto" || ov === "scroll") return n;
  }
  return null;
}

function withListSnapshot(listId, data, render) {
  // Returns true if a re-render happened; false if the data was identical
  // and we skipped. Pass `data` as the raw payload; we stringify here.
  const next = JSON.stringify(data);
  if (state.listSnapshots[listId] === next) return false;
  state.listSnapshots[listId] = next;
  const el = document.getElementById(listId);
  const sc = el ? scrollContainerOf(el) : null;
  const savedTop = sc ? sc.scrollTop : 0;
  render();
  if (sc) sc.scrollTop = savedTop;
  return true;
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
  const treeView = document.getElementById("view-tree");
  if (treeView) treeView.classList.toggle("hidden", view !== "tree");
  if (view === "agents") refreshAgents();
  else if (view === "channels") refreshChannels();
  else if (view === "archived") refreshArchived();
  else if (view === "tree") refreshTree();
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
  withListSnapshot("agent-list", data, () => {
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
  });
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

// ---- Tree view (Phase 7.5 / AD-5) -----------------------------------------
// Collapsible project → boss → workers view backed by /api/agents/tree.
// Collapsed-state is per project_nick, persisted in memory only (the user
// expects collapse choices to reset across refreshes — the workflow is
// "expand to see, collapse to focus", not a long-lived preference).
state.collapsedProjects = new Set();

async function refreshTree() {
  let data;
  try { data = await api("/api/agents/tree"); } catch (_) { return; }
  withListSnapshot("tree-list", data, () => {
    const list = document.getElementById("tree-list");
    list.replaceChildren();
    if ((!data.projects || !data.projects.length)
        && (!data.peer_bosses || !data.peer_bosses.length)) {
      list.appendChild(el("div", "empty", "No projects registered."));
      return;
    }
    for (const p of (data.projects || [])) {
      list.appendChild(renderProjectGroup(p, false));
    }
    if (data.peer_bosses && data.peer_bosses.length) {
      list.appendChild(el("div", "tree-section-header", "Peer bosses (observed)"));
      for (const peer of data.peer_bosses) {
        list.appendChild(renderProjectGroup(peer, true));
      }
    }
  });
}

function renderProjectGroup(p, isPeer) {
  // For a local project: p = {project_nick, boss, workers, pending_perm_count}.
  // For a peer boss: p = {nick, state, is_boss, workers, pending_perm_count}.
  const group = el("div", "project-group" + (isPeer ? " peer-boss" : ""));
  const key = isPeer ? `peer:${p.nick}` : `proj:${p.project_nick}`;
  const collapsed = state.collapsedProjects.has(key);
  const header = el("div", "project-header");
  const caret = el("span", "project-caret", collapsed ? "▸" : "▾");
  header.appendChild(caret);
  const bossNick = isPeer ? p.nick : p.boss.nick;
  const bossState = isPeer ? p.state : p.boss.state;
  const dot = el("span", `dot ${bossState || "unknown"}`);
  header.appendChild(dot);
  const projLabel = el(
    "span",
    "project-nick",
    isPeer ? p.nick : p.project_nick,
  );
  header.appendChild(projLabel);
  // Boss nick under the project label so the operator can confirm
  // which boss IS this project (AD-2 says they're the same identity).
  if (!isPeer) {
    header.appendChild(el("span", "project-boss", bossNick));
  }
  const workers = p.workers || [];
  header.appendChild(el(
    "span",
    "project-workers",
    `${workers.length} worker${workers.length === 1 ? "" : "s"}`,
  ));
  if (p.pending_perm_count && p.pending_perm_count > 0) {
    const badge = el(
      "span",
      "project-pending-badge",
      `${p.pending_perm_count} pending`,
    );
    badge.title = `${p.pending_perm_count} permission request(s) awaiting approval in this project`;
    header.appendChild(badge);
  }
  header.style.cursor = "pointer";
  header.onclick = () => {
    if (state.collapsedProjects.has(key)) state.collapsedProjects.delete(key);
    else state.collapsedProjects.add(key);
    refreshTree();
  };
  group.appendChild(header);

  if (collapsed) return group;

  const body = el("div", "project-body");
  if (!isPeer) {
    // Render the boss agent itself first (it's a member of the project).
    body.appendChild(renderAgentItem(p.boss, false));
  }
  for (const w of workers) {
    body.appendChild(renderAgentItem(w, true));
  }
  group.appendChild(body);
  return group;
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
  withListSnapshot("channel-list", data, () => {
    const container = document.getElementById("channel-list");
    container.replaceChildren();
    if (!data.tasks || !data.tasks.length) {
      container.appendChild(el("div", "empty", "No tasks active."));
      return;
    }
    for (const t of data.tasks) {
      container.appendChild(renderTaskGroup(t));
    }
  });
}

function renderTaskGroup(task) {
  // v8.19.22: a TASK GROUP renders one Channel (per the user's data
  // model: Channel === Task scope). Inside the Channel are rooms — the
  // boss board, group chat, joint channels, and per-worker dialogs.
  const block = el("div", "task-group");
  const header = el("div", "task-header");
  const dot = el("span", `task-state-dot member-dot-${task.state || "unknown"}`);
  header.appendChild(dot);
  // Explicit "Channel:" label so the unit of scope is obvious — the
  // <task title> is the channel's purpose (from seed → mission → boss
  // nick) and the per-room cards below are the rooms within it.
  const channelLabel = el("span", "channel-scope-label", "Channel");
  header.appendChild(channelLabel);
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
  // Channel-level token total — sum over UNIQUE members so the boss
  // (who appears in every room) is counted exactly once. This is the
  // true task total, distinct from per-room sub-totals on each card.
  if (task.tokens_total && task.tokens_total > 0) {
    const tt = el(
      "span",
      "task-tokens-total",
      formatTokens(task.tokens_total),
    );
    tt.title = `Total tokens used in this Channel/Task (${task.tokens_total.toLocaleString()} across all unique members)`;
    header.appendChild(tt);
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
  // v8.19.21: task-total tokens at the top of the card (sum of every
  // member's tokens_used). Hidden when 0 to avoid noise on channels with
  // backends that don't expose token counts (codex/copilot today).
  if (ch.tokens_total && ch.tokens_total > 0) {
    const tt = el("span", "channel-tokens-total", formatTokens(ch.tokens_total));
    tt.title = `Total tokens used in this task (${formatTokens(ch.tokens_total)} across ${ch.members.length} member(s))`;
    header.appendChild(tt);
  }
  card.appendChild(header);

  // v8.19.18: seed preview line shown right under the channel title.
  // The full seed text is lazy-fetched into a collapsible panel when
  // the user clicks "Seed brief". seed_preview is "" when the channel
  // has no persisted seed — in which case we don't render the panel.
  if (ch.seed_preview) {
    const seedRow = el("div", "channel-seed");
    const toggle = el("span", "seed-toggle", "▸ Seed brief");
    const previewLine = el("span", "seed-preview-line", ch.seed_preview);
    const body = el("div", "seed-body hidden");
    seedRow.appendChild(toggle);
    seedRow.appendChild(previewLine);
    seedRow.appendChild(body);
    seedRow.onclick = async (ev) => {
      ev.stopPropagation();
      if (body.classList.contains("hidden")) {
        if (!body.textContent) {
          // Lazy-fetch the full seed on first expand. The endpoint is
          // 404-on-no-seed; we render an empty body if it lands.
          const name = ch.channel.replace(/^#/, "");
          try {
            const data = await api(`/api/channels/${encodeURIComponent(name)}/seed`);
            body.textContent = data.text || ch.seed_preview;
          } catch (_) {
            body.textContent = ch.seed_preview;
          }
        }
        body.classList.remove("hidden");
        toggle.textContent = "▾ Seed brief";
      } else {
        body.classList.add("hidden");
        toggle.textContent = "▸ Seed brief";
      }
    };
    card.appendChild(seedRow);
  }

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
      // v8.19.21: per-agent token badge. Hidden at 0 so the chip stays
      // clean for backends that don't expose usage yet.
      if (memberObj.tokens_used && memberObj.tokens_used > 0) {
        const tb = el("span", "token-badge", formatTokens(memberObj.tokens_used));
        const ti = memberObj.tokens_in || 0;
        const to = memberObj.tokens_out || 0;
        tb.title = `${ti.toLocaleString()} in + ${to.toLocaleString()} out`;
        chip.appendChild(tb);
      }
      chip.onclick = (ev) => {
        ev.stopPropagation();
        // v8.19.22: chip click follows the AGENT (not the room) — clear
        // selectedChannel so the Chat tab reverts to the agent's home
        // channel via /api/channel/<nick>.
        state.selectedChannel = null;
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

  // v8.19.21: card click follows THE WORKER, not the boss. Previously
  // every #task-* card picked the boss member (it appears in every task
  // channel) and the Activity tab stayed glued to local-boss no matter
  // which task the user clicked — confusing and broken. Now we pick the
  // first non-boss member of the channel (the worker doing the actual
  // task) and PRESERVE the user's currently-active stream tab so the
  // Activity tab actually changes content when they click around.
  card.onclick = () => {
    if (!ch.members || !ch.members.length) return;
    const workers = ch.members.filter(
      (m) => m && typeof m === "object" && !m.is_boss
    );
    const pick = workers[0] || ch.members[0];
    const nick = typeof pick === "string" ? pick : pick.nick;
    // v8.19.22: card body click follows THE ROOM. Set selectedChannel
    // so the Chat tab loads THIS channel's history (was loading the
    // worker's #task-<worker> instead, which is empty when the convo
    // happened in #team or another room). Audit + daemon-log still
    // follow the worker — those are per-agent streams.
    state.selectedChannel = ch.channel;
    selectAgent(nick);
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
  withListSnapshot("archived-list", data, () => { renderArchivedList(data); });
}

function renderArchivedList(data) {
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
  // v8.19.22: when the user has clicked a ROOM (card body), show the
  // room name in the Chat tab title. For audit/daemon-log we still
  // show the agent because those streams are per-agent regardless of
  // which room the user opened them from.
  const label = KIND_LABEL[state.kind] || state.kind;
  const subject =
    state.kind === "chat" && state.selectedChannel
      ? state.selectedChannel
      : state.selected || "\u2014";
  $("#stream-title").textContent = `${subject} \u00B7 ${label}`;
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
  // v8.19.22: prefer the room the user clicked (state.selectedChannel)
  // over the agent's home channel. Clicking #team should show #team's
  // history — not the empty #task-<first-worker> the legacy path
  // resolved to.
  let data;
  try {
    if (state.selectedChannel) {
      const roomName = state.selectedChannel.replace(/^#/, "");
      data = await api(`/api/channels/${encodeURIComponent(roomName)}/messages`);
    } else {
      data = await api(`/api/channel/${encodeURIComponent(state.selected)}`);
    }
  } catch (_) {
    return;
  }
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
  withListSnapshot("pending-list", data, () => {
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
  });
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

// ---- Human chat panel (Phase 7.5) -----------------------------------------
// DM any agent as the operator's human nick. Persist the human nick in
// localStorage so the operator only types it once. Validates against the
// same nick regex the backend enforces (^[A-Za-z0-9][A-Za-z0-9_-]*$).
const HUMAN_NICK_KEY = "culture.dashboard.humanNick";
const HUMAN_NICK_RE = /^[A-Za-z0-9][A-Za-z0-9_-]*$/;

function loadHumanNick() {
  try {
    const v = localStorage.getItem(HUMAN_NICK_KEY);
    if (v) $("#human-chat-nick").value = v;
  } catch (_) { /* private-mode: no-op */ }
}

async function sendHumanDM() {
  const human = $("#human-chat-nick").value.trim();
  const target = $("#human-chat-target").value.trim();
  const text = $("#human-chat-text").value.trim();
  if (!human || !HUMAN_NICK_RE.test(human)) { toast("Enter a valid human nick", true); return; }
  if (!target || !HUMAN_NICK_RE.test(target)) { toast("Enter a valid target nick", true); return; }
  if (!text) { toast("Type a message", true); return; }
  try {
    await post("/api/mesh/dm", { human_nick: human, target_nick: target, text });
    $("#human-chat-text").value = "";
    try { localStorage.setItem(HUMAN_NICK_KEY, human); } catch (_) {}
    toast(`Sent DM to ${target}`);
  } catch (e) { toast(e.message, true); }
}

if ($("#human-chat-send")) {
  loadHumanNick();
  $("#human-chat-send").onclick = sendHumanDM;
  $("#human-chat-text").addEventListener("keydown", (e) => { if (e.key === "Enter") sendHumanDM(); });
}

// ---- State streams (Phase 7.5) --------------------------------------------
// Push-everywhere (Rule 9): swap the three polling intervals for SSE
// subscriptions. Each stream emits a JSON snapshot whenever the
// underlying state changes; on each message we feed the snapshot
// directly to the same render path the refresh-X functions used —
// withListSnapshot still skips DOM work on identical payloads (so
// the snapshot-diff de-duplication that keeps scroll position intact
// is preserved across the polling → SSE switch).
//
// We keep the ORIGINAL refresh-X functions available so a tab switch
// can synchronously paint the latest state (the EventSource just keeps
// feeding diffs). A short fetch-based call is still used at boot so
// the first paint doesn't have to wait on a stream connect.

const _stateStreams = { agents: null, pending: null, channels: null };

function subscribeStateStream(name, url, handler) {
  // Close any prior subscription (e.g. on a hot-reload).
  if (_stateStreams[name]) { try { _stateStreams[name].close(); } catch (_) {} }
  const es = new EventSource(url);
  _stateStreams[name] = es;
  es.onmessage = (ev) => {
    if (!ev.data) return;
    let payload;
    try { payload = JSON.parse(ev.data); } catch (_) { return; }
    if (payload === null) return;
    try { handler(payload); } catch (_) { /* render errors don't kill the stream */ }
  };
  // EventSource auto-reconnects on error; no onerror handler needed.
}

// Render hooks for each stream. Each must (a) reuse the existing
// withListSnapshot path so scroll preservation + DOM-diff skipping
// still apply, and (b) update the same top-bar badges the polling
// versions did.

function renderAgentsPayload(data) {
  withListSnapshot("agent-list", data, () => {
    const list = $("#agent-list");
    list.replaceChildren();
    if (!data.agents.length) {
      list.appendChild(el("div", "empty", "No agents registered."));
      return;
    }
    const { teams, unassigned } = groupTeams(data.agents);
    for (const [bossNick, t] of teams) {
      const label = t.boss ? `${bossNick} · team` : `${bossNick} · team (boss offline)`;
      list.appendChild(teamHeader(label, t.workers.length, "worker"));
      if (t.boss) list.appendChild(renderAgentItem(t.boss, false));
      for (const w of t.workers) list.appendChild(renderAgentItem(w, true));
    }
    if (unassigned.length) {
      list.appendChild(teamHeader("unassigned", unassigned.length, "agent"));
      for (const a of unassigned) list.appendChild(renderAgentItem(a, false));
    }
  });
  // Tree view derives its hierarchy from the same agent state; when the
  // operator is on the Tree tab, fetch the tree shape so any change to
  // boss/worker membership repaints the collapsible groups too.
  if (state.view === "tree") refreshTree();
}

function renderPendingItem(p) {
  // Extracted from refreshPending (Phase 7.5) so both the polling
  // refresh and the SSE handler render identical DOM.
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
  return item;
}

function renderPendingPayload(data) {
  withListSnapshot("pending-list", data, () => {
    const list = $("#pending-list");
    list.replaceChildren();
    const badge = $("#pending-badge");
    if (!data.pending.length) {
      list.appendChild(el("div", "empty", "Nothing waiting."));
      badge.classList.add("hidden");
      return;
    }
    badge.textContent = `${data.pending.length} pending`;
    badge.classList.remove("hidden");
    for (const p of data.pending) {
      list.appendChild(renderPendingItem(p));
    }
  });
}

function renderChannelsPayload(data) {
  // The state stream returns {channels: [...]} (list_channels shape).
  // The richer task-grouped view (/api/tasks) stays poll-driven for
  // now — its shape is a heavier aggregation and the channel stream
  // is enough to invalidate the cached snapshot when membership
  // changes; the tab switch path still calls refreshChannels() to
  // pull the up-to-date task aggregation.
  refreshChannels();
}

// ---- Boot ------------------------------------------------------------------
// Channels-first (v8.19.7): Channels is the default tab, so prime it.
// Agents still refreshes in background so the tab switch is instant.

refreshChannels();
refreshAgents();
refreshPending();

// Push-everywhere replacement for the three 2.0–3.0 s setInterval polls
// (see RC-8 + Rule 9). EventSource auto-reconnects; if the stream isn't
// available (e.g. an older server) the existing initial fetch still
// painted the UI, just without live updates.
subscribeStateStream("agents", "/api/agents/stream", renderAgentsPayload);
subscribeStateStream("pending", "/api/pending/stream", renderPendingPayload);
subscribeStateStream("channels", "/api/channels/stream", renderChannelsPayload);
