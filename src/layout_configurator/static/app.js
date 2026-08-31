const state = { current: null };

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

function drawPlan(data) {
  const svg = $("#plan");
  const width = data.boundary.width;
  const height = data.boundary.height;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.innerHTML = `<rect class="boundary" x="0" y="0" width="${width}" height="${height}"></rect>`;
  for (const room of data.rooms) {
    const r = room.rect;
    const y = height - r.y - r.height;
    const cls = room.is_heated ? "room heated" : "room unheated";
    svg.insertAdjacentHTML("beforeend", `<rect class="${cls}" x="${r.x}" y="${y}" width="${r.width}" height="${r.height}" rx="35"></rect>`);
    svg.insertAdjacentHTML("beforeend", `<text class="room-label" x="${r.x + r.width / 2}" y="${y + r.height / 2 - 30}">${escapeHtml(room.id)}</text><text class="room-area" x="${r.x + r.width / 2}" y="${y + r.height / 2 + 140}">${r.area_m2.toFixed(1)} m²</text>`);
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

function render(data) {
  state.current = data;
  $("#project-name").textContent = data.spec.project_name;
  const status = $("#status");
  status.textContent = data.validation.ok ? "VALID" : `${data.validation.issues.length} issue(s)`;
  status.classList.toggle("bad", !data.validation.ok);
  drawPlan(data);
  $("#summary").innerHTML = `<dt>Комнат</dt><dd>${data.rooms.length}</dd><dt>Вариант</dt><dd>${data.layout.variant}</dd><dt>История</dt><dd>${data.history.length || "—"}</dd><dt>Внешний вход</dt><dd>${data.spec.external_entry ? escapeHtml(data.spec.external_entry.id) : "—"}</dd>`;
  $("#issues").innerHTML = data.validation.issues.length ? `<div class="issues">${data.validation.issues.map((issue) => `<div>${escapeHtml(issue.code)}: ${escapeHtml(issue.message)}</div>`).join("")}</div>` : "";
  $("#files").innerHTML = data.files.map((file) => `<a href="${file.url}" download>${escapeHtml(file.name)}</a>`).join("");
  $("#legend").innerHTML = `<span><i class="swatch heated"></i> отапливаемая</span><span><i class="swatch unheated"></i> неотапливаемая</span><span><i class="swatch entry"></i> внешний вход</span><span><i class="swatch window"></i> окно</span>`;
  renderFields();
}

$("#operation").addEventListener("change", renderFields);
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

request("/api/state").then(render).catch((err) => { $("#command-error").textContent = err.message; $("#command-error").hidden = false; });
