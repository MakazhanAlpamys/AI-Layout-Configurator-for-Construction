const state = { current: null, interaction: null, busy: false, issueActionBusy: false, selectedRoomId: null, selectedCoordinationTarget: null, selectedIssueId: null, issueFilter: "ALL", view: null, pan: null };

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
    $("#fields").innerHTML = `${roomSelect("Room", rooms)}<div class="grid-2">${field("DX, mm", "dx_mm", "0")}${field("DY, mm", "dy_mm", "0")}</div>`;
  } else if (op === "resize_room") {
    $("#fields").innerHTML = `${roomSelect("Room", rooms)}<div class="grid-2">${field("Width, mm", "width_mm")}${field("Depth, mm", "height_mm")}</div><label>Anchor<select name="anchor"><option value="center">Center</option><option value="bottom_left">Bottom-left corner</option></select></label>`;
  } else if (op === "add_door") {
    $("#fields").innerHTML = `<div class="grid-2"><label>Room A<select name="room_a">${roomOptions(rooms)}</select></label><label>Room B<select name="room_b">${roomOptions(rooms)}</select></label></div><div class="grid-2">${field("Center, mm (optional)", "offset_mm", "", "number", "placeholder=\"centered\"")}${field("Width, mm (optional)", "width_mm", "", "number", "placeholder=\"default\"")}</div>${field("Door ID (optional)", "door_id", "", "text")}`;
  } else if (op === "remove_door") {
    const doors = spec.doors || [];
    $("#fields").innerHTML = `<label>Explicit door<select name="door_id">${doors.length ? doors.map((door) => `<option value="${escapeHtml(door.id)}">${escapeHtml(door.id)} — ${escapeHtml(door.room_a)} / ${escapeHtml(door.room_b)}</option>`).join("") : "<option value=\"\">No explicit doors</option>"}</select></label>`;
  } else if (op === "add_window") {
    $("#fields").innerHTML = `${roomSelect("Room", rooms)}<label>Side<select name="side"><option>left</option><option>right</option><option>bottom</option><option>top</option></select></label><div class="grid-2">${field("Center, mm", "offset_mm")}${field("Width, mm", "width_mm")}</div>${field("Window ID (optional)", "window_id", "", "text")}`;
  } else if (op === "remove_window") {
    const windows = spec.windows || [];
    $("#fields").innerHTML = `<label>Explicit window<select name="window_id">${windows.length ? windows.map((window) => `<option value="${escapeHtml(window.id)}">${escapeHtml(window.id)} — ${escapeHtml(window.room_id)}</option>`).join("") : "<option value=\"\">No explicit windows</option>"}</select></label>`;
  } else if (op === "set_external_entry") {
    $("#fields").innerHTML = `${roomSelect("Entry room (entry_room: ${escapeHtml(entryRoom)})", rooms, entryRoom)}<label>Side<select name="side"><option>bottom</option><option>left</option><option>right</option><option>top</option></select></label><div class="grid-2">${field("Center, mm", "offset_mm")}${field("Width, mm", "width_mm")}</div>${field("Entry ID (optional)", "entry_id", "", "text")}`;
  } else {
    $("#fields").innerHTML = `<p class="history">The command removes the external entry from LayoutIR.</p>`;
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

function projectionUnit(svg, width, height) {
  // The viewBox is fitted with the default xMidYMid meet, so the axis that runs
  // out of room first sets the scale. Returning user units per rendered CSS
  // pixel lets the plan size text, markers and label offsets in screen terms
  // while the geometry stays in millimetres.
  const bounds = svg.getBoundingClientRect();
  if (!bounds.width || !bounds.height) return 40;
  return Math.max(width / bounds.width, height / bounds.height);
}

function fullView(data) {
  return { x: 0, y: 0, width: data.boundary.width, height: data.boundary.height };
}

function currentView(data) {
  // The view is kept in SVG user units (millimetres, y down). A null view means
  // "fit the whole boundary", which is also what a fresh load and Fit restore.
  return state.view || fullView(data);
}

function svgPoint(event) {
  // Inverse of the default xMidYMid meet fit of the current viewBox.
  const svg = $("#plan");
  const bounds = svg.getBoundingClientRect();
  const view = currentView(state.current);
  const scale = Math.min(bounds.width / view.width, bounds.height / view.height);
  const offsetX = (bounds.width - view.width * scale) / 2;
  const offsetY = (bounds.height - view.height * scale) / 2;
  return {
    x: view.x + (event.clientX - bounds.left - offsetX) / scale,
    y: view.y + (event.clientY - bounds.top - offsetY) / scale,
  };
}

function modelPoint(event) {
  const point = svgPoint(event);
  return { x: point.x, y: state.current.boundary.height - point.y };
}

function clampView(view, data) {
  // Never zoom out past the whole boundary, and never so far in that a single
  // millimetre grid step fills the screen.
  const full = fullView(data);
  const minimum = Math.max(500, Math.min(full.width, full.height) / 40);
  const ratio = view.width / view.height;
  let width = Math.min(full.width, Math.max(minimum, view.width));
  let height = width / ratio;
  if (height > full.height) {
    height = full.height;
    width = height * ratio;
  }
  if (width >= full.width - 1 && height >= full.height - 1) return null;
  return {
    x: Math.min(Math.max(view.x, 0), full.width - width),
    y: Math.min(Math.max(view.y, 0), full.height - height),
    width,
    height,
  };
}

function zoomAt(factor, center = null) {
  if (!state.current) return;
  const view = currentView(state.current);
  const focus = center || { x: view.x + view.width / 2, y: view.y + view.height / 2 };
  state.view = clampView({
    x: focus.x - (focus.x - view.x) / factor,
    y: focus.y - (focus.y - view.y) / factor,
    width: view.width / factor,
    height: view.height / factor,
  }, state.current);
  drawPlan(state.current);
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

function flowLabelAnchor(flow, fraction, side, offset) {
  // A point at `fraction` of the route's length, pushed `offset` user units to
  // one side of the segment it lies on.
  const points = (flow.points || []).map((point) => ({ x: Number(point.x), y: Number(point.y) }));
  if (!points.length) return { x: 0, y: 0, side };
  const lengths = [];
  let total = 0;
  for (let i = 1; i < points.length; i += 1) {
    const length = Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y);
    lengths.push(length);
    total += length;
  }
  if (!total) return { x: points[0].x, y: points[0].y + offset * side, side };
  let remaining = total * fraction;
  for (let i = 1; i < points.length; i += 1) {
    const length = lengths[i - 1];
    if (remaining > length && i < points.length - 1) {
      remaining -= length;
      continue;
    }
    const t = length ? Math.min(1, remaining / length) : 0;
    const dx = (points[i].x - points[i - 1].x) / (length || 1);
    const dy = (points[i].y - points[i - 1].y) / (length || 1);
    return {
      x: points[i - 1].x + dx * length * t - dy * offset * side,
      y: points[i - 1].y + dy * length * t + dx * offset * side,
      side,
    };
  }
  return { x: points[0].x, y: points[0].y, side };
}

function boxesOverlap(a, b) {
  return a.x1 < b.x2 && b.x1 < a.x2 && a.y1 < b.y2 && b.y1 < a.y2;
}

function overlapArea(box, others) {
  let area = 0;
  for (const other of others) {
    if (!boxesOverlap(box, other)) continue;
    area += (Math.min(box.x2, other.x2) - Math.max(box.x1, other.x1))
      * (Math.min(box.y2, other.y2) - Math.max(box.y1, other.y1));
  }
  return area;
}

function placeFlowLabel(flow, label, index, context) {
  // Greedy collision avoidance: walk candidate positions along the route, on
  // both sides and at two distances, and keep the first whose estimated text
  // box stays on the sheet and clear of labels that are already placed. When
  // nothing is clear the candidate with the least overlap wins, so a dense plan
  // degrades gracefully instead of stacking every label on the midpoint.
  const { px, height, width, placed, obstacles } = context;
  const textWidth = label.length * px(11) * 0.58;
  const textHeight = px(13);
  const pad = px(3);
  const fractions = [0.5, 0.35, 0.65, 0.2, 0.8, 0.1, 0.9];
  const preferred = index % 2 === 0 ? 1 : -1;
  let best = null;
  for (const fraction of fractions) {
    for (const side of [preferred, -preferred]) {
      for (const offset of [px(14), px(30)]) {
        const anchor = flowLabelAnchor(flow, fraction, side, offset);
        const baseline = height - anchor.y + px(4);
        for (const textAnchor of side > 0 ? ["start", "end"] : ["end", "start"]) {
          const x1 = textAnchor === "start" ? anchor.x : anchor.x - textWidth;
          const box = { x1: x1 - pad, x2: x1 + textWidth + pad, y1: baseline - textHeight - pad, y2: baseline + pad };
          if (box.x1 < 0 || box.x2 > width || box.y1 < 0 || box.y2 > height) continue;
          const labelOverlap = overlapArea(box, placed);
          const score = labelOverlap * 10 + overlapArea(box, obstacles);
          if (!best || score < best.score) best = { x: anchor.x, y: baseline, textAnchor, box, score };
          if (score === 0) return best;
        }
      }
    }
  }
  if (best) return best;
  // A route hugging a tiny sheet: keep the old midpoint behaviour.
  const anchor = flowLabelAnchor(flow, 0.5, preferred, px(14));
  const baseline = height - anchor.y + px(4);
  return { x: anchor.x, y: baseline, textAnchor: "start", box: { x1: anchor.x, x2: anchor.x + textWidth, y1: baseline - textHeight, y2: baseline }, score: 0 };
}

function issueRouteMatch(issue, flow) {
  // Route findings name their own endpoints ("flow (from → to)"), so a finding
  // belongs to exactly one route of a multi-route flow when that text matches.
  const message = `${issue.title || ""} ${issue.message || ""}`;
  return message.includes(`${flow.from_id} → ${flow.to_id}`);
}

function routeConflicts(flow, flows, openIssues) {
  const flowIssues = openIssues.filter((issue) => issue.flow_id === flow.flow_id);
  const siblings = flows.filter((entry) => entry.flow_id === flow.flow_id);
  if (siblings.length < 2) return flowIssues;
  return flowIssues.filter((issue) => {
    if (issueRouteMatch(issue, flow)) return true;
    // A flow-level finding names no route; it is drawn once, on the first one.
    return !siblings.some((sibling) => issueRouteMatch(issue, sibling)) && siblings[0] === flow;
  });
}

function flowTypesInUse(data) {
  const types = new Set();
  for (const flow of data.flow_declarations || []) types.add(flowClass(flow.type));
  for (const flow of data.flows || []) types.add(flowClass(flow.type));
  return [...types].sort();
}

function resolvedFallbackLocation(issue, data) {
  if (issue.equipment_id) {
    const item = (data.equipment || []).find((entry) => entry.equipment_id === issue.equipment_id);
    if (item) return { x: item.x + item.width / 2, y: item.y + item.depth / 2 };
  }
  if (issue.flow_id) {
    const flow = (data.flows || []).find((entry) => entry.flow_id === issue.flow_id);
    const points = flow?.points || [];
    if (points.length) return points[Math.floor(points.length / 2)];
  }
  return null;
}

function drawFacilityOverlay(svg, data, height, unitsPerPixel) {
  if (data.mode !== "facility-review") return;
  const px = (value) => value * unitsPerPixel;
  const currentIssues = data.facility?.issues || data.validation?.issues || [];
  const openIssues = currentIssues.filter((issue) => issueStatus(issue) === "OPEN");
  const resolvedIssues = currentIssues.filter((issue) => issueStatus(issue) === "RESOLVED");
  const structuralGrid = data.structural_grid;
  if (structuralGrid) {
    for (const [index, x] of (structuralGrid.axes_x_mm || []).entries()) {
      const label = (structuralGrid.labels_x || [])[index] || `X${index + 1}`;
      svg.insertAdjacentHTML("beforeend", `<line class="structural-axis" x1="${x}" y1="0" x2="${x}" y2="${height}"></line><text class="structural-label" x="${x + px(4)}" y="${px(14)}">${escapeHtml(label)}</text>`);
    }
    for (const [index, y] of (structuralGrid.axes_y_mm || []).entries()) {
      const label = (structuralGrid.labels_y || [])[index] || `Y${index + 1}`;
      svg.insertAdjacentHTML("beforeend", `<line class="structural-axis" x1="0" y1="${height - y}" x2="${data.boundary.width}" y2="${height - y}"></line><text class="structural-label" x="${px(10)}" y="${height - y - px(4)}">${escapeHtml(label)}</text>`);
    }
  }
  const flows = data.flows || [];
  const labels = [];
  const drawnMarkers = new Set();
  for (const flow of flows) {
    const points = (flow.points || []).map((point) => `${point.x},${height - point.y}`).join(" ");
    if (!points) continue;
    const labelPoint = flow.points[Math.floor(flow.points.length / 2)] || flow.points[0];
    const multiRoute = flows.filter((entry) => entry.flow_id === flow.flow_id).length > 1;
    const routeName = multiRoute ? ` (${flow.from_id} → ${flow.to_id})` : "";
    const selected = state.selectedCoordinationTarget?.kind === "flow" && state.selectedCoordinationTarget.id === flow.flow_id;
    const conflicts = routeConflicts(flow, flows, openIssues);
    const label = `${flow.flow_id}${routeName} · ${flow.type || "flow"}${conflicts.length ? ` · ${conflicts.length} conflict` : ""}`;
    labels.push({ flow, label });
    const conflictClass = conflicts.length ? " conflict" : "";
    // The corridor keeps the declared clear width in millimetres so a reviewer
    // sees the real envelope; the centreline is a hairline drawn over it.
    const clearWidth = Number(flow.minimum_clear_width_mm) || 0;
    const typeClass = escapeHtml(flowClass(flow.type));
    if (clearWidth > 0) {
      svg.insertAdjacentHTML("beforeend", `<polyline class="flow-corridor flow-${typeClass}${conflictClass}${selected ? " selected" : ""}" style="stroke-width:${clearWidth}" points="${points}"></polyline>`);
    }
    svg.insertAdjacentHTML("beforeend", `<polyline class="flow-route flow-${typeClass}${conflictClass}${selected ? " selected" : ""}" points="${points}" data-flow-id="${escapeHtml(flow.flow_id)}"></polyline>`);
    for (const issue of conflicts) {
      // One finding, one marker, even when several routes share its flow.
      const key = issue.issue_id || `${issue.code}:${issue.flow_id}:${issue.message}`;
      if (drawnMarkers.has(key)) continue;
      drawnMarkers.add(key);
      const location = issueLocation(issue) || labelPoint;
      svg.insertAdjacentHTML("beforeend", `<circle class="issue-marker issue-marker-open" data-issue-marker="${escapeHtml(key)}" cx="${location.x}" cy="${height - location.y}" r="${px(7)}"><title>${escapeHtml(issue.code || "Open issue")}</title></circle>`);
    }
  }
  // Labels go on top of every route and are placed after all of them, so each
  // one can avoid the labels already on the sheet and the room names.
  const obstacles = [...svg.querySelectorAll("text[data-label-room]")].map((text) => {
    try {
      const box = text.getBBox();
      return { x1: box.x, x2: box.x + box.width, y1: box.y, y2: box.y + box.height };
    } catch (_) {
      return null;
    }
  }).filter((box) => box && box.x2 > box.x1);
  const placed = [];
  const context = { px, height, width: data.boundary.width, placed, obstacles };
  for (const [index, { flow, label }] of labels.entries()) {
    const position = placeFlowLabel(flow, label, index, context);
    placed.push(position.box);
    svg.insertAdjacentHTML("beforeend", `<text class="flow-label" data-flow-label="${escapeHtml(flow.flow_id)}" style="text-anchor:${position.textAnchor}" x="${position.x}" y="${position.y}">${escapeHtml(label)}</text>`);
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
    svg.insertAdjacentHTML("beforeend", `<rect class="equipment${conflictClass}${selected ? " selected" : ""}" data-equipment-id="${escapeHtml(item.equipment_id)}" x="${item.x}" y="${y}" width="${item.width}" height="${item.depth}" rx="18"></rect><text class="equipment-label" x="${item.x + item.width / 2}" y="${y + item.depth / 2 + px(3)}">${escapeHtml(item.equipment_id)}${conflicts.length ? ` · ${conflicts.length}` : ""}</text>`);
    for (const issue of conflicts) {
      const location = issueLocation(issue) || { x: item.x + item.width / 2, y: item.y + item.depth / 2 };
      svg.insertAdjacentHTML("beforeend", `<circle class="issue-marker issue-marker-open" cx="${location.x}" cy="${height - location.y}" r="${px(7)}"><title>${escapeHtml(issue.code || "Open issue")}</title></circle>`);
    }
  }
  const historyResolved = (data.issue_history || []).filter(
    (issue) => issueStatus(issue) === "RESOLVED" && issue.record_type === "bcf",
  );
  for (const issue of [...resolvedIssues, ...historyResolved]) {
    const location = issueLocation(issue) || resolvedFallbackLocation(issue, data);
    if (!location) continue;
    svg.insertAdjacentHTML("beforeend", `<circle class="issue-marker issue-marker-resolved" cx="${location.x}" cy="${height - location.y}" r="${px(6)}"><title>${escapeHtml(issue.code || "Resolved issue")}</title></circle>`);
  }
}

function fitRoomLabels(svg, rects, unitsPerPixel) {
  const px = (value) => value * unitsPerPixel;
  const minimumLegibleRatio = 0.62;
  const dropped = new Set();
  // Document order puts each room name immediately before its own area, so a
  // dropped name is already known when that area is examined.
  for (const text of svg.querySelectorAll("text[data-label-room]")) {
    const rect = rects.get(text.dataset.labelRoom);
    if (!rect) continue;
    const isArea = text.classList.contains("room-area");
    if (isArea && (rect.height < px(26) || dropped.has(text.dataset.labelRoom))) {
      text.remove();
      continue;
    }
    const available = rect.width - px(6);
    const length = text.getComputedTextLength();
    if (!length || length <= available) continue;
    const ratio = available / length;
    if (ratio < minimumLegibleRatio) {
      if (!isArea) dropped.add(text.dataset.labelRoom);
      text.remove();
      continue;
    }
    text.style.fontSize = `${parseFloat(getComputedStyle(text).fontSize) * ratio}px`;
  }
}

function drawPlan(data, preview = null) {
  const svg = $("#plan");
  const width = data.boundary.width;
  const height = data.boundary.height;
  const grid = Number(data.spec.grid_mm) || 100;
  if (state.view) state.view = clampView(state.view, data);
  const view = currentView(data);
  const unitsPerPixel = projectionUnit(svg, view.width, view.height);
  const px = (value) => value * unitsPerPixel;
  state.unitsPerPixel = unitsPerPixel;
  // CSS sizes text and label strokes as calc(var(--u) * Npx), so a millimetre
  // model still renders type at a fixed screen size.
  svg.style.setProperty("--u", String(unitsPerPixel));
  // Keep the reference grid at least nine pixels apart; the raw 100 mm step
  // collapses into a solid field on a facility-sized boundary.
  const gridStep = Math.max(grid, Math.ceil(px(9) / grid) * grid);
  svg.setAttribute("viewBox", `${view.x} ${view.y} ${view.width} ${view.height}`);
  $("#zoom-fit").disabled = !state.view;
  svg.innerHTML = `<defs><pattern id="grid-pattern" width="${gridStep}" height="${gridStep}" patternUnits="userSpaceOnUse"><path class="preview-grid" d="M ${gridStep} 0 L 0 0 0 ${gridStep}"></path></pattern></defs><rect x="0" y="0" width="${width}" height="${height}" fill="url(#grid-pattern)"></rect><rect class="boundary" x="0" y="0" width="${width}" height="${height}"></rect>`;
  for (const room of data.rooms) {
    const r = preview && preview.roomId === room.id ? preview.rect : room.rect;
    const y = height - r.y - r.height;
    const selected = state.selectedRoomId === room.id;
    const cls = room.is_heated ? "room heated" : "room unheated";
    svg.insertAdjacentHTML("beforeend", `<g class="room-group" data-room-id="${escapeHtml(room.id)}"><rect class="${cls}${selected ? " selected" : ""}" data-room-id="${escapeHtml(room.id)}" x="${r.x}" y="${y}" width="${r.width}" height="${r.height}" rx="35"></rect>`);
    const area = (r.width * r.height / 1000000).toFixed(1);
    svg.insertAdjacentHTML("beforeend", `<text class="room-label" data-label-room="${escapeHtml(room.id)}" x="${r.x + r.width / 2}" y="${y + r.height / 2 - px(2)}">${escapeHtml(room.id)}</text><text class="room-area" data-label-room="${escapeHtml(room.id)}" x="${r.x + r.width / 2}" y="${y + r.height / 2 + px(11)}">${area} m²</text>`);
    if (selected) {
      const handle = Math.max(px(10), Math.min(r.width, r.height) * 0.05);
      svg.insertAdjacentHTML("beforeend", `<rect class="resize-handle" data-resize-room="${escapeHtml(room.id)}" x="${r.x + r.width - handle / 2}" y="${height - r.y - handle / 2}" width="${handle}" height="${handle}" rx="35"></rect>`);
    }
    svg.insertAdjacentHTML("beforeend", "</g>");
  }
  fitRoomLabels(
    svg,
    new Map(data.rooms.map((room) => [
      room.id,
      preview && preview.roomId === room.id ? preview.rect : room.rect,
    ])),
    unitsPerPixel,
  );
  drawFacilityOverlay(svg, data, height, unitsPerPixel);
  if (preview) {
    const r = preview.rect;
    const y = height - r.y - r.height;
    const label = preview.kind === "move"
      ? `Δ ${Math.round(r.x - preview.initial.x)}, ${Math.round(r.y - preview.initial.y)} mm`
      : `${Math.round(r.width)} × ${Math.round(r.height)} mm`;
    const labelY = y > px(14) ? y - px(6) : y + r.height + px(14);
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

function startPan(event) {
  state.pan = { start: svgPoint(event), view: { ...currentView(state.current) }, moved: false };
  $("#plan").setPointerCapture(event.pointerId);
  $("#plan").classList.add("panning");
  event.preventDefault();
}

function canvasPointerDown(event) {
  if (!state.current || state.busy) return;
  const review = state.current.mode === "facility-review";
  const handle = event.target.closest("[data-resize-room]");
  const roomNode = event.target.closest("[data-room-id]");
  // Rooms are draggable only in the editor; everywhere else a drag pans.
  if (review || !roomNode || event.button === 1) {
    if (state.view) startPan(event);
    return;
  }
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
  if (state.pan) {
    // The view moves with the pointer, so measure against the view the pan
    // started from rather than the one being dragged.
    const saved = state.view;
    state.view = state.pan.view;
    const point = svgPoint(event);
    state.view = saved;
    const dx = point.x - state.pan.start.x;
    const dy = point.y - state.pan.start.y;
    state.pan.moved = state.pan.moved || Math.abs(dx) > 0 || Math.abs(dy) > 0;
    state.view = clampView({ ...state.pan.view, x: state.pan.view.x - dx, y: state.pan.view.y - dy }, state.current);
    drawPlan(state.current);
    event.preventDefault();
    return;
  }
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
  status.textContent = interaction.kind === "move" ? "PREVIEW · release" : "RESIZE · release";
  status.classList.remove("bad");
  event.preventDefault();
}

function endPan(event) {
  if (!state.pan) return false;
  state.pan = null;
  $("#plan").classList.remove("panning");
  try { $("#plan").releasePointerCapture(event.pointerId); } catch (_) { /* already released */ }
  return true;
}

async function canvasPointerUp(event) {
  if (endPan(event)) return;
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
  if (endPan(event)) return;
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
  $("#summary").innerHTML = `<dt>Rooms</dt><dd>${data.rooms.length}</dd><dt>Equipment</dt><dd>${equipmentCount}</dd><dt>Flows declared</dt><dd>${flowCount}</dd><dt>Derived routes</dt><dd>${routedCount}</dd><dt>Checks</dt><dd>${passCount} / ${checks.length} pass</dd><dt>Issues</dt><dd><span class="status-open">${openCount} open</span> · <span class="status-resolved">${resolvedCount} resolved</span></dd><dt>Profile</dt><dd>${escapeHtml(profile.domain || "facility")}</dd><dt>Sheet</dt><dd>${escapeHtml((profile.drawing || {}).sheet_id || "A-101")}</dd>`;
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
  const flowLegend = flowTypesInUse(data).map((type) => `<span><i class="swatch flow-${escapeHtml(type)}"></i> ${escapeHtml(type.replaceAll("_", " "))} flow</span>`).join("");
  $("#legend").innerHTML = `${flowLegend}<span><i class="swatch equipment"></i> equipment</span><span><i class="swatch clearance"></i> service clearance</span><span><i class="swatch entry"></i> external entry</span><span><i class="swatch issue-open"></i> open issue</span><span><i class="swatch issue-resolved"></i> resolved issue</span>`;
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
  const counts = data.issue_counts || {
    open: data.validation.issues.length,
    resolved: 0,
    total: data.validation.issues.length,
  };
  status.textContent = data.validation.ok
    ? "VALID"
    : counts.resolved
      ? `${counts.open} open · ${counts.resolved} resolved`
      : `${counts.open} issue(s)`;
  // Red only while something is still open; a fully triaged result stays marked
  // as failing the deterministic check without shouting about it.
  status.classList.toggle("bad", !data.validation.ok && counts.open > 0);
  // A reviewer gets a review surface, not a disabled editor (VQ-07).
  $("#mode-eyebrow").textContent = review ? "FACILITY REVIEW · READ-ONLY" : "SOLVER-FIRST EDITOR";
  $("#mode-title").textContent = review ? "Facility review" : "Layout editor";
  $("#mode-subtitle").textContent = review
    ? "Deterministic checks, derived flow routes and coordination issues of a generated BuildingIR bundle. Geometry cannot be edited here."
    : "The browser sends typed commands; CP-SAT recalculates the geometry.";
  document.title = review ? `Facility review — ${data.spec.project_name}` : "Layout Configurator — editor";
  $("#edit-toolbar").hidden = review;
  $("#undo").disabled = review || !data.can_undo;
  $("#redo").disabled = review || !data.can_redo;
  $("#reset").disabled = review;
  $(".command-card").hidden = review;
  $("#issue-controls").hidden = !review;
  $("#issue-actions").hidden = !review;
  $(".canvas-help").textContent = review
    ? "Read-only facility review: flows, equipment footprints, service clearances and deterministic checks are projected from the generated BuildingIR bundle. Wheel zooms, drag pans, double-click fits."
    : "Drag a room or its resize handle; releasing sends one typed command. Wheel zooms, dragging empty space pans, double-click fits the whole plan.";
  drawPlan(data);
  if (review) {
    renderFacilityReview(data);
    return;
  }
  $("#issue-controls").hidden = true;
  $("#issue-actions").hidden = true;
  $("#issue-actions").innerHTML = "";
  $("#issue-history").innerHTML = "";
  $("#summary").innerHTML = `<dt>Rooms</dt><dd>${data.rooms.length}</dd><dt>Variant</dt><dd>${data.layout.variant}</dd><dt>History</dt><dd>${data.history.length || "—"}</dd><dt>External entry</dt><dd>${data.spec.external_entry ? escapeHtml(data.spec.external_entry.id) : "—"}</dd>`;
  $("#issues").innerHTML = data.validation.issues.length ? `<div class="issues">${data.validation.issues.map((issue) => `<div>${escapeHtml(issue.code)}: ${escapeHtml(issue.message)}</div>`).join("")}</div>` : "";
  if (data.norms) {
    $("#norms").innerHTML = `<div class="norms"><div class="norms-head">${escapeHtml(data.norms.ruleset.name)} · ${escapeHtml(data.norms.ruleset.jurisdiction)}</div>${data.norms.results.map((rule) => { const statusClass = rule.status === "PASS" ? "norm-pass" : rule.status === "FAIL" ? "norm-fail" : "norm-na"; return `<div class="norm"><span>${escapeHtml(rule.id)}</span><strong class="${statusClass}">${escapeHtml(rule.status)}</strong></div>`; }).join("")}</div>`;
  } else {
    $("#norms").innerHTML = "";
  }
  const journal = data.journal || [];
  $("#journal").innerHTML = journal.length
    ? `<div class="journal-title">Operation log</div><ol class="journal-list">${journal.slice().reverse().map((entry) => { const label = entry.action === "command" ? entry.type : entry.action === "undo" ? "Undo" : "Redo"; const detail = entry.payload ? JSON.stringify(entry.payload) : ""; return `<li class="journal-entry"><span>${escapeHtml(label)}</span><code>${escapeHtml(detail)}</code></li>`; }).join("")}</ol>`
    : `<div class="journal-title">Operation log</div><p class="journal-empty">No changes yet.</p>`;
  $("#files").innerHTML = data.files.map((file) => `<a href="${file.url}" download>${escapeHtml(file.name)}</a>`).join("");
  $("#legend").innerHTML = `<span><i class="swatch heated"></i> heated</span><span><i class="swatch unheated"></i> unheated</span><span><i class="swatch entry"></i> external entry</span><span><i class="swatch window"></i> window</span>`;
  renderFields();
}

let resizeFrame = 0;
window.addEventListener("resize", () => {
  if (!state.current || resizeFrame) return;
  resizeFrame = requestAnimationFrame(() => {
    resizeFrame = 0;
    if (state.current) drawPlan(state.current);
  });
});

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
$("#plan").addEventListener("wheel", (event) => {
  if (!state.current || state.interaction) return;
  event.preventDefault();
  const factor = Math.exp(-Math.max(-300, Math.min(300, event.deltaY)) / 600);
  zoomAt(factor, svgPoint(event));
}, { passive: false });
$("#plan").addEventListener("dblclick", () => {
  if (!state.current || state.interaction) return;
  state.view = null;
  drawPlan(state.current);
});
$("#zoom-in").addEventListener("click", () => zoomAt(1.5));
$("#zoom-out").addEventListener("click", () => zoomAt(1 / 1.5));
$("#zoom-fit").addEventListener("click", () => {
  state.view = null;
  if (state.current) drawPlan(state.current);
});
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
