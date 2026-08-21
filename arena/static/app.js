"use strict";

const state = {
  lanes: [],          // lane config from /api/lanes
  raceId: null,
  prompt: "",
  laneRuntime: {},    // name -> { status, cost_usd, step_count, elapsed_ms, sources: [], lastRaw }
  sources: {},         // name -> EventSource
};

const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

// ---------------- boot ----------------

async function boot() {
  const res = await fetch("/api/lanes");
  const body = await res.json();
  state.lanes = body.lanes;
  renderLaneConfig();
  wireStaticControls();
}

function renderLaneConfig() {
  const el = $("#lanesConfig");
  el.innerHTML = "";
  for (const lane of state.lanes) {
    const row = document.createElement("div");
    row.className = "lane-row";
    row.innerHTML = `<span class="lock">&#128274;</span>
      <span class="lane-label">${escapeHtml(lane.label)}</span>
      <span>pinned to <b>${escapeHtml(lane.provider)}</b></span>
      <span class="lane-path">${escapeHtml(lane.workspace)}</span>`;
    el.appendChild(row);
  }
}

function wireStaticControls() {
  $("#startBtn").addEventListener("click", startRace);
  $("#newRaceBtn").addEventListener("click", resetToStart);
  $("#resetBtn").addEventListener("click", resetWorkspaces);
  $$(".tab-btn").forEach((btn) => btn.addEventListener("click", () => switchView(btn.dataset.view)));
  $$("[data-close]").forEach((btn) => btn.addEventListener("click", () => {
    $("#" + btn.dataset.close).classList.add("hidden");
  }));
}

// ---------------- start / race lifecycle ----------------

async function startRace() {
  const prompt = $("#promptInput").value.trim();
  if (!prompt) return;
  $("#startBtn").disabled = true;
  $("#startStatus").textContent = "Starting all lanes…";
  try {
    const res = await fetch("/api/race/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt }),
    });
    if (!res.ok) throw new Error(`start failed: ${res.status}`);
    const body = await res.json();
    state.raceId = body.race_id;
    state.prompt = body.prompt;
    state.laneRuntime = {};
    for (const lane of body.lanes) {
      state.laneRuntime[lane.name] = {
        status: "starting", cost_usd: 0, step_count: 0, elapsed_ms: 0, log: [],
      };
    }
    enterRaceView(body.lanes);
  } catch (err) {
    $("#startStatus").textContent = String(err);
  } finally {
    $("#startBtn").disabled = false;
  }
}

function resetToStart() {
  Object.values(state.sources).forEach((es) => es.close());
  state.sources = {};
  state.raceId = null;
  $("#startStatus").textContent = "";
  $("#viewSwitchWrap").classList.add("hidden");
  showScreen("startScreen");
}

async function resetWorkspaces() {
  $("#resetBtn").textContent = "Resetting…";
  try {
    const res = await fetch("/api/reset", { method: "POST" });
    const body = await res.json();
    const failed = body.results.filter((r) => !r.ok);
    $("#resetBtn").textContent = failed.length ? `${failed.length} failed` : "Reset done";
  } catch {
    $("#resetBtn").textContent = "Reset failed";
  } finally {
    setTimeout(() => { $("#resetBtn").textContent = "Reset workspaces"; }, 2500);
  }
}

function switchView(view) {
  $$(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  showScreen(view === "diff" ? "diffScreen" : "raceScreen");
  if (view === "diff") loadDiffs();
}

function showScreen(id) {
  $$(".screen").forEach((s) => s.classList.add("hidden"));
  $("#" + id).classList.remove("hidden");
}

// ---------------- race view ----------------

function enterRaceView(lanes) {
  $("#racePromptText").textContent = state.prompt;
  const grid = $("#raceLanes");
  grid.innerHTML = "";
  for (const lane of lanes) {
    grid.appendChild(buildLaneCard(lane));
  }
  $("#viewSwitchWrap").classList.remove("hidden");
  switchView("race");
  showScreen("raceScreen");
  for (const lane of lanes) openLaneStream(lane);
}

function buildLaneCard(lane) {
  const card = document.createElement("div");
  card.className = "lane-card";
  card.id = `lane-${lane.name}`;
  card.innerHTML = `
    <div class="lane-card-head">
      <div class="lane-title">
        <div>
          <div class="lane-provider">${escapeHtml(lane.label)}</div>
          <div class="lane-model">${escapeHtml(lane.provider)}</div>
        </div>
      </div>
      <div style="display:flex; align-items:center; gap:8px;">
        <button class="lane-graph-btn" data-lane="${lane.name}">graph</button>
        <span class="badge badge-starting" data-role="badge">starting</span>
      </div>
    </div>
    <div class="lane-stats">
      <span>elapsed <b data-role="elapsed">0s</b></span>
      <span>steps <b data-role="steps">0</b></span>
      <span>cost <b data-role="cost">$0.000000</b></span>
    </div>
    <div class="lane-log" data-role="log"></div>`;
  card.querySelector(".lane-graph-btn").addEventListener("click", () => openGraphModal(lane.name, lane.label));
  return card;
}

function openLaneStream(lane) {
  const es = new EventSource(`/api/race/${state.raceId}/lanes/${lane.name}/events`);
  state.sources[lane.name] = es;
  es.onmessage = (ev) => handleLaneEvent(lane.name, JSON.parse(ev.data));
  es.onerror = () => {
    // The backend's own watchdog/reconnect logic marks the lane errored and
    // sends a terminal ARENA_ERROR event before closing; the browser-side
    // EventSource retrying past that point would just spin on a closed race.
  };
}

function handleLaneEvent(laneName, msg) {
  const rt = state.laneRuntime[laneName];
  if (!rt) return;
  rt.status = msg.status;
  rt.cost_usd = msg.cost_usd;
  rt.step_count = msg.step_count;
  rt.elapsed_ms = msg.elapsed_ms;
  rt.log.push(msg.raw);
  updateLaneCard(laneName, rt, msg.raw);
}

function updateLaneCard(laneName, rt, raw) {
  const card = $(`#lane-${laneName}`);
  if (!card) return;
  const badge = card.querySelector('[data-role="badge"]');
  badge.textContent = rt.status;
  badge.className = `badge badge-${rt.status}`;
  card.querySelector('[data-role="elapsed"]').textContent = formatElapsed(rt.elapsed_ms);
  card.querySelector('[data-role="steps"]').textContent = String(rt.step_count);
  card.querySelector('[data-role="cost"]').textContent = `$${rt.cost_usd.toFixed(6)}`;

  const line = renderLogLine(raw);
  if (line) {
    const log = card.querySelector('[data-role="log"]');
    log.appendChild(line);
    log.scrollTop = log.scrollHeight;
  }
}

function renderLogLine(raw) {
  const div = document.createElement("div");
  const type = raw.type;
  let text = "";
  let kind = "custom";
  let clickable = false;

  if (type === "RUN_STARTED") {
    text = "race started"; kind = "run";
  } else if (type === "STEP_STARTED") {
    text = `▸ ${raw.stepName || "step"} started`; kind = "step";
  } else if (type === "STEP_FINISHED") {
    if (raw.error) {
      const refused = String(raw.error).startsWith("GuardError:");
      text = `${refused ? "✖ refused" : "✖ failed"} — ${raw.stepName || ""}: ${raw.error}`;
      kind = "error";
    } else {
      const value = (raw.delta && raw.delta.value) || {};
      const isValidator = /^validate_work/.test(raw.stepName || "");
      const summary = summarizeResult(value);
      text = `✓ ${raw.stepName || "step"} finished${summary ? " — " + summary : ""}`;
      kind = isValidator ? "validator" : "step";
      clickable = true;
    }
  } else if (type === "STATE_DELTA" && raw.delta && raw.delta.op === "graph_patched") {
    text = `● ${raw.delta.reason || "graph updated"}`;
    kind = "graph";
  } else if (type === "RUN_FINISHED") {
    text = "race finished"; kind = "run";
  } else if (type === "ARENA_ERROR") {
    text = `⚠ ${raw.reason || "lane error"}`; kind = "error";
  } else if (type === "CUSTOM") {
    text = raw.custom || "event"; kind = "custom";
  } else {
    return null;
  }

  div.className = `log-line kind-${kind}${clickable ? " clickable" : ""}`;
  div.innerHTML = `<span class="lt">#${raw.seq ?? ""}</span>${escapeHtml(text)}`;
  if (clickable) div.addEventListener("click", () => openThinkModal(raw));
  return div;
}

function summarizeResult(value) {
  if (value.path) return value.path;
  if (typeof value.exit_code === "number") return `exit ${value.exit_code}`;
  if (value.answer) return value.answer.slice(0, 80);
  if (value.passed !== undefined) return `passed=${value.passed}`;
  return "";
}

function formatElapsed(ms) {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

// ---------------- thinking panel ----------------

function openThinkModal(raw) {
  const value = (raw.delta && raw.delta.value) || {};
  $("#thinkModalTitle").textContent = "Step detail";
  $("#thinkStepName").textContent = raw.stepName || "(unnamed step)";
  const reasoning = value.reasoning_text;
  $("#thinkReasoning").textContent = reasoning
    ? reasoning
    : "No reasoning returned for this step. Not every provider separates a reasoning channel from its answer, and this one didn't for this call.";
  $("#thinkRaw").textContent = JSON.stringify(raw, null, 2);
  $("#thinkModal").classList.remove("hidden");
}

// ---------------- graph modal ----------------

async function openGraphModal(laneName, laneLabel) {
  $("#graphModalTitle").textContent = `${laneLabel} — run graph`;
  $("#graphModal").classList.remove("hidden");
  const svg = $("#graphSvg");
  svg.innerHTML = "<text x='12' y='24' fill='#8b90a3' font-size='12'>Loading…</text>";
  try {
    const res = await fetch(`/api/race/${state.raceId}/lanes/${laneName}/graph`);
    const body = await res.json();
    renderGraph(body.nodes || {}, body.edges || []);
  } catch {
    svg.innerHTML = "<text x='12' y='24' fill='#f16d6d' font-size='12'>Could not load graph</text>";
  }
}

function renderGraph(nodes, edges) {
  const svg = $("#graphSvg");
  const ids = Object.keys(nodes);
  if (ids.length === 0) {
    svg.innerHTML = "<text x='12' y='24' fill='#8b90a3' font-size='12'>No nodes yet</text>";
    return;
  }
  // Topological levels via BFS from nodes with no incoming edge.
  const incoming = new Map(ids.map((id) => [id, 0]));
  const children = new Map(ids.map((id) => [id, []]));
  for (const [parent, child] of edges) {
    if (incoming.has(child)) incoming.set(child, incoming.get(child) + 1);
    if (children.has(parent)) children.get(parent).push(child);
  }
  const level = new Map();
  let frontier = ids.filter((id) => incoming.get(id) === 0);
  if (frontier.length === 0) frontier = [ids[0]];
  frontier.forEach((id) => level.set(id, 0));
  let depth = 0;
  while (frontier.length) {
    const next = [];
    for (const id of frontier) {
      for (const child of children.get(id) || []) {
        if (!level.has(child)) {
          level.set(child, depth + 1);
          next.push(child);
        }
      }
    }
    frontier = next;
    depth += 1;
  }
  ids.forEach((id) => { if (!level.has(id)) level.set(id, 0); });

  const byLevel = {};
  ids.forEach((id) => {
    const l = level.get(id);
    (byLevel[l] = byLevel[l] || []).push(id);
  });

  const colW = 220, rowH = 64, nodeW = 190, nodeH = 40;
  const maxLevel = Math.max(...Object.keys(byLevel).map(Number));
  const maxRows = Math.max(...Object.values(byLevel).map((a) => a.length));
  const width = (maxLevel + 1) * colW + 40;
  const height = maxRows * rowH + 40;
  svg.setAttribute("viewBox", `0 0 ${Math.max(width, 400)} ${Math.max(height, 200)}`);

  const pos = {};
  Object.keys(byLevel).forEach((l) => {
    byLevel[l].forEach((id, i) => {
      pos[id] = { x: Number(l) * colW + 20, y: i * rowH + 20 };
    });
  });

  let svgContent = "";
  for (const [parent, child] of edges) {
    const a = pos[parent], b = pos[child];
    if (!a || !b) continue;
    const x1 = a.x + nodeW, y1 = a.y + nodeH / 2, x2 = b.x, y2 = b.y + nodeH / 2;
    const midX = (x1 + x2) / 2;
    svgContent += `<path class="graph-edge" d="M${x1},${y1} C${midX},${y1} ${midX},${y2} ${x2},${y2}" />`;
  }
  for (const id of ids) {
    const node = nodes[id];
    const p = pos[id];
    if (!p) continue;
    const stateName = node.state || "pending";
    const isValidator = /^validate_work/.test(node.skill || "");
    svgContent += `<g class="graph-node state-${stateName}${isValidator ? " is-validator" : ""}"
        transform="translate(${p.x},${p.y})">
      <rect width="${nodeW}" height="${nodeH}" rx="8"></rect>
      <text x="10" y="16">${escapeHtml(node.skill || id)}</text>
      <text x="10" y="30" fill="#8b90a3" font-size="9">${escapeHtml(stateName)}</text>
    </g>`;
  }
  svg.innerHTML = svgContent;
}

// ---------------- compare edits ----------------

async function loadDiffs() {
  const grid = $("#diffLanes");
  grid.innerHTML = "";
  for (const [name, rt] of Object.entries(state.laneRuntime)) {
    const lane = state.lanes.find((l) => l.name === name);
    const card = document.createElement("div");
    card.className = "lane-card";
    card.innerHTML = `<div class="lane-card-head">
        <div class="lane-title"><div class="lane-provider">${escapeHtml(lane ? lane.label : name)}</div></div>
        <span class="badge badge-${rt.status}">${rt.status}</span>
      </div>
      <div data-role="body"></div>`;
    grid.appendChild(card);
    const body = card.querySelector('[data-role="body"]');
    body.innerHTML = "<div class='lane-empty'>Loading…</div>";
    try {
      const res = await fetch(`/api/race/${state.raceId}/lanes/${name}/diff`);
      const data = await res.json();
      renderDiffBody(body, data.edits || [], rt.status);
    } catch {
      body.innerHTML = "<div class='lane-empty'>Could not load edits.</div>";
    }
  }
}

function renderDiffBody(container, edits, status) {
  if (edits.length === 0) {
    const reason = status === "refused" ? "This lane was refused before it could make a successful edit."
      : status === "thrashing" ? "This lane thrashed and never converged on a successful edit."
      : status === "errored" ? "This lane errored before producing an edit."
      : "No edits yet.";
    container.innerHTML = `<div class="lane-empty">${escapeHtml(reason)}</div>`;
    return;
  }
  container.innerHTML = "";
  for (const edit of edits) {
    const block = document.createElement("div");
    block.className = "diff-block";
    const oldPart = edit.old_string ? `<div class="diff-old">- ${escapeHtml(edit.old_string)}</div>` : "";
    block.innerHTML = `<div class="diff-path">${escapeHtml(edit.path || edit.node_id)}</div>
      ${oldPart}
      <div class="diff-new">+ ${escapeHtml(edit.new_string || "")}</div>`;
    container.appendChild(block);
  }
}

// ---------------- utils ----------------

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

boot();
