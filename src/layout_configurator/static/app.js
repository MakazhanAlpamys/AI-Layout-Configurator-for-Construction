const state = { current: null, interaction: null, busy: false, issueActionBusy: false, selectedRoomId: null, selectedCoordinationTarget: null, selectedIssueId: null, issueFilter: "ALL" };

const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value)
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;").replaceAll("'", "&#039;");

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload.error || "Request failed");
    error.state = payload.state;
    throw error;
  }
  return payload;
}

function roomOptions(rooms, selected = "") {
  return rooms.map((room) => `<option value="${escapeHtml(room.id)}" ${room.id === selected ? "selected" : ""}>${escapeHtml(room.id)} — ${escapeHtml(room.type)}</option>`).join("");
}

function roomSelect(name, rooms, selected = "") {
  return `<label>${name}<select name="room_id">${roomOptions(rooms, selected)}</select></label>`;
}

function field(label, name, value = "", type = "number", extra = "") {
  return `<label>${label}<input name="${name}" type="${type}" value="${escapeHtml(value)}" ${extra}></label>`;
}

function renderFields() {
  const op = $("#operation").value;
  const rooms = state.current.rooms;
  const spec = state.current.spec;
  const entryRoom = spec.entry_room;
  if (op === "move_room") {
    $("#fields").innerHTML = `${roomSelect("Комната", rooms)}<div class="grid-2">${field("DX, мм", "dx_mm", "0")}${field("DY, мм", "dy_mm", "0")}</div>`;
  } else if (op === "resize_room") {
    $("#fields").innerHTML = `${roomSelect("Комната", rooms)}<div class="grid-2">${field("Ширина, мм", "width_mm")}${field("Глубина, мм", "height_mm")}</div><label>Якорь<select name="anchor"><option value="center">Центр</option><option value="bottom_left">Нижний левый угол</option></select></label>`;
  } else if (op === "add_door") {
    $("#fields").innerHTML = `<div class="grid-2"><label>Комната A<select name="room_a">${roomOptions(rooms)}</select></label><label>Комната B<select name="room_b">${roomOptions(rooms)}</select></label></div><div class="grid-2">${field("Центр, мм (необязательно)", "offset_mm", "", "number", "placeholder=\"по центру\"")}${field("Ширина, мм (необязательно)", "width_mm", "", "number", "placeholder=\"по умолчанию\"")}</div>${field("ID двери (необязательно)", "door_id", "", "text")}`;
  } else if (op === "remove_door") {
    const doors = spec.doors || [];
    $("#fields").innerHTML = `<label>Явная дверь<select name="door_id">${doors.length ? doors.map((door) => `<option value="${escapeHtml(door.id)}">${escapeHtml(door.id)} — ${escapeHtml(door.room_a)} / ${escapeHtml(door.room_b)}</option>`).join("") : "<option value=\"\">Нет явных дверей</option>"}</select></label>`;
  } else if (op === "add_window") {
    $("#fields").innerHTML = `${roomSelect("Комната", rooms)}<label>Сторона<select name="side"><option>left</option><option>right</option><option>bottom</option><option>top</option></select></label><div class="grid-2">${field("Центр, мм", "offset_mm")}${field("Ширина, мм", "width_mm")}</div>${field("ID окна (необязательно)", "window_id", "", "text")}`;
  } else if (op === "remove_window") {
    const windows = spec.windows || [];
    $("#fields").innerHTML = `<label>Явное окно<select name="window_id">${windows.length ? windows.map((window) => `<option value="${escapeHtml(window.id)}">${escapeHtml(window.id)} — ${escapeHtml(window.room_id)}</option>`).join("") : "<option value=\"\">Нет явных окон</option>"}</select></label>`;
  } else if (op === "set_external_entry") {
    $("#fields").innerHTML = `${roomSelect("Комната входа (entry_room: ${escapeHtml(entryRoom)})", rooms, entryRoom)}<label>Сторона<select name="side"><option>bottom</option><option>left</option><option>right</option><option>top</option></select></label><div class="grid-2">${field("Центр, мм", "offset_mm")}${field("Ширина, мм", "width_mm")}</div>${field("ID входа (необязательно)", "entry_id", "", "text")}`;
  } else {
    $("#fields").innerHTML = `<p class="history">Команда удалит внешний вход из LayoutIR.</p>`;
  }
}

function formPayload() {
  const form = new FormData($("#command-form"));
  const op = $("#operation").value;
  const payload = { type: op };
  for (const [key, value] of form.entries()) {
    if (key !== "operation" && value !== "") payload[key] = value;
  }
  return payload;
}

function modelPoint(event) {
  const svg = $("#plan");
  const bounds = svg.getBoundingClientRect();
  const width = state.current.boundary.width;
  const height = state.current.boundary.height;
  const scale = Math.min(bounds.width / width, bounds.height / height);
  const offsetX = (bounds.width - width * scale) / 2;
  const offsetY = (bounds.height - height * scale) / 2;
  return {
    x: (event.clientX - bounds.left - offsetX) / scale,
    y: height - (event.clientY - bounds.top - offsetY) / scale,
  };
}

function snap(value, grid) {
  return Math.round(value / grid) * grid;
}

function flowClass(type) {
  return String(type || "flow").toLowerCase().replace(/[^a-z0-9_-]+/g, "-");
}

function issueStatus(issue) {
  return ["RESOLVED", "CLOSED"].includes(String(issue?.status || "OPEN").toUpperCase()) ? "RESOLVED" : "OPEN";
}

function issueStatusClass(issue) {
  return issueStatus(issue) === "RESOLVED" ? "issue-resolved" : "issue-open";
}

function issueMatchesFilter(issue) {
  return state.issueFilter === "ALL" || issueStatus(issue) === state.issueFilter;
}

function issueTarget(issue) {
  if (issue?.flow_id) return { kind: "flow", id: issue.flow_id };
  if (issue?.equipment_id) return { kind: "equipment", id: issue.equipment_id };
  return null;
}

function issueLocation(issue) {
  const x = Number(issue?.location?.x);
  const y = Number(issue?.location?.y);
  return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
}

function selectedIssue(data) {
  return (data.issue_history || []).find((issue) => issue.issue_id === state.selectedIssueId) || null;
}

function renderIssueActions(data) {
  const container = $("#issue-actions");
  container.hidden = false;
  const issue = selectedIssue(data);
  if (!issue) {
    container.innerHTML = `<div class="issues-title">Issue actions</div><p class="issue-action-empty">Select an issue to resolve, reopen, assign or comment.</p>`;
    return;
  }
  const status = issueStatus(issue);
  const currentIssue = (data.facility?.issues || []).some((item) => item.issue_id === issue.issue_id);
  const comments = issue.comments || [];
  const audit = issue.management_history || [];
  const commentsHtml = comments.length
    ? `<div class="issue-comments"><strong>Comments</strong>${comments.slice().reverse().map((comment) => `<p><b>${escapeHtml(comment.author)}</b> · ${escapeHtml(comment.created_at)}<br>${escapeHtml(comment.text)}</p>`).join("")}</div>`
    : "";
  const auditHtml = audit.length
    ? `<details class="issue-audit"><summary>Audit trail (${audit.length})</summary>${audit.slice().reverse().map((entry) => `<p><b>${escapeHtml(entry.action)}</b> · ${escapeHtml(entry.author)} · ${escapeHtml(entry.timestamp)}${entry.comment ? `<br>${escapeHtml(entry.comment)}` : ""}</p>`).join("")}</details>`
    : "";
  container.innerHTML = `<div class="issues-title">Issue actions · ${escapeHtml(issue.issue_id || issue.code || "selected")}</div><div class="issue-selected"><span class="issue-status ${status === "RESOLVED" ? "status-resolved" : "status-open"}">${status}</span> · ${escapeHtml(issue.code || "BCF_TOPIC")} · ${escapeHtml(issue.title || "Issue")}${issue.assignee ? ` · owner: ${escapeHtml(issue.assignee)}` : ""}</div>${currentIssue ? "" : "<p class=\"issue-action-empty\">Historical BCF topic: status can only change after it reappears in current validation.</p>"}<div class="grid-2"><label>Author<input id="issue-author" value="reviewer" maxlength="120"></label><label>Assignee<input id="issue-assignee" value="${escapeHtml(issue.assignee || "")}" maxlength="120" placeholder="unassigned"></label></div><label>Comment / reason<textarea id="issue-comment" maxlength="4000" placeholder="Required for resolve, reopen and comment"></textarea></label><div class="grid-2"><button type="button" class="primary issue-action-button" data-issue-action="resolve" ${status === "RESOLVED" || !currentIssue ? "disabled" : ""}>Resolve</button><button type="button" class="secondary issue-action-button" data-issue-action="reopen" ${status === "OPEN" || !currentIssue ? "disabled" : ""}>Reopen</button></div><div class="grid-2"><button type="button" class="secondary issue-action-button" data-issue-action="comment">Add comment</button><button type="button" class="secondary issue-action-button" data-issue-action="assign">Save assignee</button></div>${commentsHtml}${auditHtml}<p id="issue-action-error" class="error" hidden></p>`;
}

async function submitIssueAction(action) {
  if (!state.selectedIssueId || state.issueActionBusy) return;
  const author = $("#issue-author")?.value || "";
  const comment = $("#issue-comment")?.value || "";
  const assignee = $("#issue-assignee")?.value || "";
  state.issueActionBusy = true;
  try {
    render(await request("/api/issue-action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, issue_id: state.selectedIssueId, author, comment, assignee }),
    }));
  } catch (err) {
    if (err.state) render(err.state);
    const error = $("#issue-action-error");
    if (error) {
      error.textContent = err.message;
      error.hidden = false;
    }
  } finally {
    state.issueActionBusy = false;
  }
}

function flowLabelAnchor(flow, index) {
  const points = flow.points || [];
  const middleIndex = Math.floor(points.length / 2);
  const anchor = points[middleIndex] || points[0] || { x: 0, y: 0 };
  const previous = points[Math.max(0, middleIndex - 1)] || anchor;
  const following = points[Math.min(points.length - 1, middleIndex + 1)] || anchor;
  const dx = Number(following.x) - Number(previous.x);
  const dy = Number(following.y) - Number(previous.y);
  const length = Math.hypot(dx, dy) || 1;
  const side = index % 2 === 0 ? 1 : -1;
  const offset = 260 + (index % 3) * 120;
  return {
    x: Number(anchor.x) - (dy / length) * offset * side,
    y: Number(anchor.y) + (dx / length) * offset * side,
  };
}

function drawFacilityOverlay(svg, data, height) {
  if (data.mode !== "facility-review") return;
  const currentIssues = data.facility?.issues || data.validation?.issues || [];
  const openIssues = currentIssues.filter((issue) => issueStatus(issue) === "OPEN");
  const structuralGrid = data.structural_grid;
  if (structuralGrid) {
    for (const [index, x] of (structuralGrid.axes_x_mm || []).entries()) {
      const label = (structuralGrid.labels_x || [])[index] || `X${index + 1}`;
      svg.insertAdjacentHTML("beforeend", `<line class="structural-axis" x1="${x}" y1="0" x2="${x}" y2="${height}"></line><text class="structural-label" x="${x + 70}" y="260">${escapeHtml(label)}</text>`);
    }
    for (const [index, y] of (structuralGrid.axes_y_mm || []).entries()) {
      const label = (structuralGrid.labels_y || [])[index] || `Y${index + 1}`;
      svg.insertAdjacentHTML("beforeend", `<line class="structural-axis" x1="0" y1="${height - y}" x2="${data.boundary.width}" y2="${height - y}"></line><text class="structural-label" x="180" y="${height - y - 70}">${escapeHtml(label)}</text>`);
    }
  }
  for (const [index, flow] of (data.flows || []).entries()) {
    const points = (flow.points || []).map((point) => `${point.x},${height - point.y}`).join(" ");
    if (!points) continue;
    const labelPoint = flow.points[Math.floor(flow.points.length / 2)] || flow.points[0];
    const labelAnchor = flowLabelAnchor(flow, index);
    const label = `${flow.flow_id} · ${flow.type || "flow"}`;
    const selected = state.selectedCoordinationTarget?.kind === "flow" && state.selectedCoordinationTarget.id === flow.flow_id;
    const conflicts = openIssues.filter((issue) => issue.flow_id === flow.flow_id);
    const conflictClass = conflicts.length ? " conflict" : "";
    svg.insertAdjacentHTML("beforeend", `<polyline class="flow-route flow-${escapeHtml(flowClass(flow.type))}${conflictClass}${selected ? " selected" : ""}" points="${points}" data-flow-id="${escapeHtml(flow.flow_id)}"></polyline><text class="flow-label" x="${labelAnchor.x}" y="${height - labelAnchor.y - 90}">${escapeHtml(label)}${conflicts.length ? ` · ${conflicts.length} conflict` : ""}</text>`);
    for (const issue of conflicts) {
      const location = issueLocation(issue) || labelPoint;
      svg.insertAdjacentHTML("beforeend", `<circle class="issue-marker issue-marker-open" cx="${location.x}" cy="${height - location.y}" r="280"><title>${escapeHtml(issue.code || "Open issue")}</title></circle>`);
    }
  }
  for (const item of data.equipment || []) {
    const clearance = item.clearance;
    const selected = state.selectedCoordinationTarget?.kind === "equipment" && state.selectedCoordinationTarget.id === item.equipment_id;
    const conflicts = openIssues.filter((issue) => issue.equipment_id === item.equipment_id);
    const conflictClass = conflicts.length ? " equipment-conflict" : "";
    if (clearance) {
      const clearanceY = height - clearance.y - clearance.depth;
      svg.insertAdjacentHTML("beforeend", `<rect class="equipment-clearance${conflictClass}${selected ? " selected" : ""}" data-equipment-id="${escapeHtml(item.equipment_id)}" x="${clearance.x}" y="${clearanceY}" width="${clearance.width}" height="${clearance.depth}" rx="24"></rect>`);
    }
    const y = height - item.y - item.depth;
    svg.insertAdjacentHTML("beforeend", `<rect class="equipment${conflictClass}${selected ? " selected" : ""}" data-equipment-id="${escapeHtml(item.equipment_id)}" x="${item.x}" y="${y}" width="${item.width}" height="${item.depth}" rx="18"></rect><text class="equipment-label" x="${item.x + item.width / 2}" y="${y + item.depth / 2 + 45}">${escapeHtml(item.equipment_id)}${conflicts.length ? ` · ${conflicts.length}` : ""}</text>`);
    for (const issue of conflicts) {
      const location = issueLocation(issue) || { x: item.x + item.width / 2, y: item.y + item.depth / 2 };
      svg.insertAdjacentHTML("beforeend", `<circle class="issue-marker issue-marker-open" cx="${location.x}" cy="${height - location.y}" r="280"><title>${escapeHtml(issue.code || "Open issue")}</title></circle>`);
    }
  }
  for (const issue of data.issue_history || []) {
    if (issueStatus(issue) !== "RESOLVED" || issue.record_type !== "bcf") continue;
    const location = issueLocation(issue);
    if (!location) continue;
    svg.insertAdjacentHTML("beforeend", `<circle class="issue-marker issue-marker-resolved" cx="${location.x}" cy="${height - location.y}" r="230"><title>${escapeHtml(issue.code || "Resolved issue")}</title></circle>`);
  }
}

function drawPlan(data, preview = null) {
  const svg = $("#plan");
  const width = data.boundary.width;
  const height = data.boundary.height;
  const grid = Number(data.spec.grid_mm) || 100;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.innerHTML = `<defs><pattern id="grid-pattern" width="${grid}" height="${grid}" patternUnits="userSpaceOnUse"><path class="preview-grid" d="M ${grid} 0 L 0 0 0 ${grid}"></path></pattern></defs><rect x="0" y="0" width="${width}" height="${height}" fill="url(#grid-pattern)"></rect><rect class="boundary" x="0" y="0" width="${width}" height="${height}"></rect>`;
  for (const room of data.rooms) {
    const r = preview && preview.roomId === room.id ? preview.rect : room.rect;
    const y = height - r.y - r.height;
    const selected = state.selectedRoomId === room.id;
    const cls = room.is_heated ? "room heated" : "room unheated";
    svg.insertAdjacentHTML("beforeend", `<g class="room-group" data-room-id="${escapeHtml(room.id)}"><rect class="${cls}${selected ? " selected" : ""}" data-room-id="${escapeHtml(room.id)}" x="${r.x}" y="${y}" width="${r.width}" height="${r.height}" rx="35"></rect>`);
    const area = (r.width * r.height / 1000000).toFixed(1);
    svg.insertAdjacentHTML("beforeend", `<text class="room-label" x="${r.x + r.width / 2}" y="${y + r.height / 2 - 30}">${escapeHtml(room.id)}</text><text class="room-area" x="${r.x + r.width / 2}" y="${y + r.height / 2 + 140}">${area} m²</text>`);
    if (selected) {
      const handle = Math.min(500, Math.max(180, Math.min(r.width, r.height) * 0.05));
      svg.insertAdjacentHTML("beforeend", `<rect class="resize-handle" data-resize-room="${escapeHtml(room.id)}" x="${r.x + r.width - handle / 2}" y="${height - r.y - handle / 2}" width="${handle}" height="${handle}" rx="35"></rect>`);
    }
    svg.insertAdjacentHTML("beforeend", "</g>");
  }
  drawFacilityOverlay(svg, data, height);
  if (preview) {
    const r = preview.rect;
    const y = height - r.y - r.height;
    const label = preview.kind === "move"
      ? `Δ ${Math.round(r.x - preview.initial.x)}, ${Math.round(r.y - preview.initial.y)} мм`
      : `${Math.round(r.width)} × ${Math.round(r.height)} мм`;
    const labelY = y > 220 ? y - 90 : y + r.height + 210;
    svg.insertAdjacentHTML("beforeend", `<text class="preview-dimension" x="${r.x + r.width / 2}" y="${labelY}">${escapeHtml(label)}</text>`);
  }
  for (const opening of data.openings) {
    const coords = opening.orientation === "horizontal"
      ? `${opening.start},${height - opening.fixed} ${opening.end},${height - opening.fixed}`
      : `${opening.fixed},${height - opening.start} ${opening.fixed},${height - opening.end}`;
    svg.insertAdjacentHTML("beforeend", `<line class="opening${opening.external ? " external" : ""}" x1="${coords.split(" ")[0].split(",")[0]}" y1="${coords.split(" ")[0].split(",")[1]}" x2="${coords.split(" ")[1].split(",")[0]}" y2="${coords.split(" ")[1].split(",")[1]}"></line>`);
  }
  for (const opening of data.windows) {
    const coords = opening.orientation === "horizontal"
      ? [opening.start, height - opening.fixed, opening.end, height - opening.fixed]
      : [opening.fixed, height - opening.start, opening.fixed, height - opening.end];
    svg.insertAdjacentHTML("beforeend", `<line class="window" x1="${coords[0]}" y1="${coords[1]}" x2="${coords[2]}" y2="${coords[3]}"></line>`);
  }
}

function canvasPointerDown(event) {
  if (!state.current || state.busy || state.current.mode === "facility-review") return;
  const handle = event.target.closest("[data-resize-room]");
  const roomNode = event.target.closest("[data-room-id]");
  if (!roomNode) return;
  const roomId = handle ? handle.dataset.resizeRoom : roomNode.dataset.roomId;
  const room = state.current.rooms.find((item) => item.id === roomId);
  if (!room) return;
  state.selectedRoomId = roomId;
  state.interaction = {
    kind: handle ? "resize" : "move",
    roomId,
    start: modelPoint(event),
    initial: { ...room.rect },
    preview: { ...room.rect },
  };
  $("#plan").setPointerCapture(event.pointerId);
  drawPlan(state.current);
  event.preventDefault();
}

function canvasPointerMove(event) {
  const interaction = state.interaction;
  if (!interaction) return;
  const point = modelPoint(event);
  const grid = Number(state.current.spec.grid_mm) || 100;
  if (interaction.kind === "move") {
    interaction.preview = {
      ...interaction.initial,
      x: interaction.initial.x + snap(point.x - interaction.start.x, grid),
      y: interaction.initial.y + snap(point.y - interaction.start.y, grid),
    };
  } else {
    interaction.preview = {
      ...interaction.initial,
      width: Math.max(grid, snap(point.x - interaction.initial.x, grid)),
      height: Math.max(grid, snap(point.y - interaction.initial.y, grid)),
    };
  }
  drawPlan(state.current, { roomId: interaction.roomId, rect: interaction.preview, initial: interaction.initial, kind: interaction.kind });
  const status = $("#status");
  status.textContent = interaction.kind === "move" ? "PREVIEW · отпустите" : "RESIZE · отпустите";
  status.classList.remove("bad");
  event.preventDefault();
}

async function canvasPointerUp(event) {
  const interaction = state.interaction;
  if (!interaction) return;
  state.interaction = null;
  try { $("#plan").releasePointerCapture(event.pointerId); } catch (_) { /* pointer capture may already be released */ }
  const initial = interaction.initial;
  const preview = interaction.preview;
  const changed = interaction.kind === "move"
    ? preview.x !== initial.x || preview.y !== initial.y
    : preview.width !== initial.width || preview.height !== initial.height;
  drawPlan(state.current);
  if (!changed) {
    render(state.current);
    return;
  }
  const payload = interaction.kind === "move"
    ? { type: "move_room", room_id: interaction.roomId, dx_mm: preview.x - initial.x, dy_mm: preview.y - initial.y }
    : { type: "resize_room", room_id: interaction.roomId, width_mm: preview.width, height_mm: preview.height, anchor: "bottom_left" };
  state.busy = true;
  $("#command-error").hidden = true;
  try {
    render(await request("/api/command", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }));
  } catch (err) {
    if (err.state) render(err.state);
    $("#command-error").textContent = err.message;
    $("#command-error").hidden = false;
  } finally {
    state.busy = false;
  }
}

function canvasPointerCancel(event) {
  if (!state.interaction) return;
  state.interaction = null;
  try { $("#plan").releasePointerCapture(event.pointerId); } catch (_) { /* no-op */ }
  render(state.current);
}

function issueRow(issue) {
  const target = issueTarget(issue);
  const interactive = Boolean(target && issue.issue_id);
  const status = issueStatus(issue);
  const targetLabel = target ? ` · focus ${escapeHtml(target.id)}` : "";
  const source = issue.source ? ` · ${escapeHtml(issue.source)}` : "";
  return `<div class="issue-row ${issueStatusClass(issue)}${interactive ? " interactive" : ""}" data-issue-id="${escapeHtml(issue.issue_id || "")}" data-target-kind="${target?.kind || ""}" data-target-id="${escapeHtml(target?.id || "")}" role="${interactive ? "button" : "status"}" tabindex="${interactive ? "0" : "-1"}"><strong><span class="issue-status ${status === "RESOLVED" ? "status-resolved" : "status-open"}">${status}</span> · ${escapeHtml(issue.severity || "WARNING")} · ${escapeHtml(issue.code || "BCF_TOPIC")}${targetLabel}</strong><span class="issue-title">${escapeHtml(issue.title || issue.code || "Issue")}</span><span>${escapeHtml(issue.message || "")}${source}</span></div>`;
}

function renderFacilityReview(data) {
  const facility = data.facility || {};
  const checks = facility.checks || [];
  const issues = facility.issues || data.validation.issues || [];
  const history = data.issue_history || issues;
  const visibleIssues = issues.filter(issueMatchesFilter);
  const visibleHistory = history.filter(issueMatchesFilter);
  const openCount = history.filter((issue) => issueStatus(issue) === "OPEN").length;
  const resolvedCount = history.filter((issue) => issueStatus(issue) === "RESOLVED").length;
  const equipmentCount = (data.equipment || []).length;
  const flowCount = (data.flow_declarations || []).length;
  const routedCount = (data.flows || []).length;
  const passCount = checks.filter((check) => check.status === "PASS").length;
  const profile = facility.profile || {};
  $("#summary").innerHTML = `<dt>Rooms</dt><dd>${data.rooms.length}</dd><dt>Equipment</dt><dd>${equipmentCount}</dd><dt>Flow declarations</dt><dd>${flowCount} / ${routedCount} routed</dd><dt>Checks</dt><dd>${passCount} / ${checks.length} pass</dd><dt>Issues</dt><dd><span class="status-open">${openCount} open</span> · <span class="status-resolved">${resolvedCount} resolved</span></dd><dt>Profile</dt><dd>${escapeHtml(profile.domain || "facility")}</dd><dt>Sheet</dt><dd>${escapeHtml((profile.drawing || {}).sheet_id || "A-101")}</dd>`;
  $("#issue-controls").hidden = false;
  for (const button of document.querySelectorAll("[data-issue-filter]")) {
    button.classList.toggle("active", button.dataset.issueFilter === state.issueFilter);
  }
  $("#issues").innerHTML = visibleIssues.length
    ? `<div class="issues"><div class="issues-title">Current conflicts (${visibleIssues.length})</div>${visibleIssues.map(issueRow).join("")}</div>`
    : issues.length && state.issueFilter !== "ALL"
      ? `<div class="issues issues-empty"><strong>No ${state.issueFilter.toLowerCase()} current conflicts</strong><span>Change the issue filter to inspect another status.</span></div>`
      : `<div class="issues issues-pass"><strong>Coordination clean</strong><span>All deterministic facility checks passed.</span></div>`;
  const bcfLabel = data.bcf ? `BCF ${escapeHtml(data.bcf.version)} · ${escapeHtml(data.bcf.path)}` : "No BCF package loaded";
  $("#issue-history").innerHTML = visibleHistory.length
    ? `<div class="issue-history"><div class="issues-title">Issue history · ${bcfLabel}</div><div class="issue-history-counts"><span class="status-open">${openCount} open</span><span class="status-resolved">${resolvedCount} resolved</span></div>${visibleHistory.map(issueRow).join("")}</div>`
    : `<div class="issue-history issue-history-empty"><div class="issues-title">Issue history</div><p>No issues match the selected filter.</p></div>`;
  renderIssueActions(data);
  $("#norms").innerHTML = checks.length
    ? `<div class="norms facility-checks"><div class="norms-head">Facility evidence · ${escapeHtml(profile.name || "selected profile")}</div>${checks.map((check) => { const statusClass = check.status === "PASS" ? "norm-pass" : check.status === "FAIL" ? "norm-fail" : "norm-na"; return `<details class="facility-check"><summary><span>${escapeHtml(check.id)}</span><strong class="${statusClass}">${escapeHtml(check.status)}</strong></summary><p>${escapeHtml((check.evidence || []).join("; "))}</p></details>`; }).join("")}</div>`
    : "";
  $("#journal").innerHTML = `<div class="journal-title">Review mode</div><p class="journal-empty">Read-only projection. Edit the BuildingIR program and regenerate the bundle to create a new revision.</p>`;
  $("#files").innerHTML = data.files.map((file) => `<a href="${file.url}" download>${escapeHtml(file.name)}</a>`).join("");
  $("#legend").innerHTML = `<span><i class="swatch flow-people"></i> people flow</span><span><i class="swatch flow-material"></i> material flow</span><span><i class="swatch equipment"></i> equipment</span><span><i class="swatch clearance"></i> service clearance</span><span><i class="swatch entry"></i> external entry</span>`;
}

function selectCoordinationIssue(issueId) {
  const issue = (state.current?.issue_history || state.current?.facility?.issues || []).find((item) => item.issue_id === issueId);
  if (!issue) return;
  state.selectedIssueId = issueId;
  state.selectedCoordinationTarget = issueTarget(issue);
  render(state.current);
}

function render(data) {
  state.current = data;
  const review = data.mode === "facility-review";
  document.body.classList.toggle("facility-review", review);
  $("#project-name").textContent = data.spec.project_name;
  const status = $("#status");
  status.textContent = data.validation.ok ? "VALID" : `${data.validation.issues.length} issue(s)`;
  status.classList.toggle("bad", !data.validation.ok);
  $("#undo").disabled = review || !data.can_undo;
  $("#redo").disabled = review || !data.can_redo;
  $("#reset").disabled = review;
  $(".command-card").hidden = review;
  $("#issue-controls").hidden = !review;
  $("#issue-actions").hidden = !review;
  $(".canvas-help").textContent = review
    ? "Read-only facility review: flows, equipment footprints, service clearances and deterministic checks are projected from the generated BuildingIR bundle."
    : "Перетащите комнату или resize-маркер; отпускание отправляет одну typed-команду.";
  drawPlan(data);
  if (review) {
    renderFacilityReview(data);
    return;
  }
  $("#issue-controls").hidden = true;
  $("#issue-actions").hidden = true;
  $("#issue-actions").innerHTML = "";
  $("#issue-history").innerHTML = "";
  $("#summary").innerHTML = `<dt>Комнат</dt><dd>${data.rooms.length}</dd><dt>Вариант</dt><dd>${data.layout.variant}</dd><dt>История</dt><dd>${data.history.length || "—"}</dd><dt>Внешний вход</dt><dd>${data.spec.external_entry ? escapeHtml(data.spec.external_entry.id) : "—"}</dd>`;
  $("#issues").innerHTML = data.validation.issues.length ? `<div class="issues">${data.validation.issues.map((issue) => `<div>${escapeHtml(issue.code)}: ${escapeHtml(issue.message)}</div>`).join("")}</div>` : "";
  if (data.norms) {
    $("#norms").innerHTML = `<div class="norms"><div class="norms-head">${escapeHtml(data.norms.ruleset.name)} · ${escapeHtml(data.norms.ruleset.jurisdiction)}</div>${data.norms.results.map((rule) => { const statusClass = rule.status === "PASS" ? "norm-pass" : rule.status === "FAIL" ? "norm-fail" : "norm-na"; return `<div class="norm"><span>${escapeHtml(rule.id)}</span><strong class="${statusClass}">${escapeHtml(rule.status)}</strong></div>`; }).join("")}</div>`;
  } else {
    $("#norms").innerHTML = "";
  }
  const journal = data.journal || [];
  $("#journal").innerHTML = journal.length
    ? `<div class="journal-title">Журнал операций</div><ol class="journal-list">${journal.slice().reverse().map((entry) => { const label = entry.action === "command" ? entry.type : entry.action === "undo" ? "Отмена" : "Повтор"; const detail = entry.payload ? JSON.stringify(entry.payload) : ""; return `<li class="journal-entry"><span>${escapeHtml(label)}</span><code>${escapeHtml(detail)}</code></li>`; }).join("")}</ol>`
    : `<div class="journal-title">Журнал операций</div><p class="journal-empty">Изменений пока нет.</p>`;
  $("#files").innerHTML = data.files.map((file) => `<a href="${file.url}" download>${escapeHtml(file.name)}</a>`).join("");
  $("#legend").innerHTML = `<span><i class="swatch heated"></i> отапливаемая</span><span><i class="swatch unheated"></i> неотапливаемая</span><span><i class="swatch entry"></i> внешний вход</span><span><i class="swatch window"></i> окно</span>`;
  renderFields();
}

$("#operation").addEventListener("change", renderFields);
$("#issues").addEventListener("click", (event) => {
  const row = event.target.closest("[data-issue-id]");
  if (row) selectCoordinationIssue(row.dataset.issueId);
});
$("#issues").addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const row = event.target.closest("[data-issue-id]");
  if (!row) return;
  event.preventDefault();
  selectCoordinationIssue(row.dataset.issueId);
});
$("#issue-history").addEventListener("click", (event) => {
  const row = event.target.closest("[data-issue-id]");
  if (row) selectCoordinationIssue(row.dataset.issueId);
});
$("#issue-history").addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const row = event.target.closest("[data-issue-id]");
  if (!row) return;
  event.preventDefault();
  selectCoordinationIssue(row.dataset.issueId);
});
$("#issue-controls").addEventListener("click", (event) => {
  const button = event.target.closest("[data-issue-filter]");
  if (!button) return;
  state.issueFilter = button.dataset.issueFilter || "ALL";
  if (state.current?.mode === "facility-review") renderFacilityReview(state.current);
});
$("#issue-actions").addEventListener("click", (event) => {
  const button = event.target.closest("[data-issue-action]");
  if (button) submitIssueAction(button.dataset.issueAction);
});
$("#plan").addEventListener("pointerdown", canvasPointerDown);
$("#plan").addEventListener("pointermove", canvasPointerMove);
$("#plan").addEventListener("pointerup", canvasPointerUp);
$("#plan").addEventListener("pointercancel", canvasPointerCancel);
$("#command-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const error = $("#command-error");
  error.hidden = true;
  try {
    render(await request("/api/command", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(formPayload()) }));
  } catch (err) {
    if (err.state) render(err.state);
    error.textContent = err.message;
    error.hidden = false;
  }
});

$("#reset").addEventListener("click", async () => {
  try { render(await request("/api/reset", { method: "POST" })); } catch (err) { $("#command-error").textContent = err.message; $("#command-error").hidden = false; }
});

$("#undo").addEventListener("click", async () => {
  try { render(await request("/api/undo", { method: "POST" })); } catch (err) { if (err.state) render(err.state); $("#command-error").textContent = err.message; $("#command-error").hidden = false; }
});

$("#redo").addEventListener("click", async () => {
  try { render(await request("/api/redo", { method: "POST" })); } catch (err) { if (err.state) render(err.state); $("#command-error").textContent = err.message; $("#command-error").hidden = false; }
});

request("/api/state").then(render).catch((err) => { $("#command-error").textContent = err.message; $("#command-error").hidden = false; });
