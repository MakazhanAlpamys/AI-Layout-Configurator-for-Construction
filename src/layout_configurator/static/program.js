"use strict";

// This producer collects semantic inputs only; the existing job pipeline solves geometry.
const byId = id => document.getElementById(id);
let configuration;
let validated;
let generating = false;
let token = "";
let rowSequence = 0;
const editors = {};
const title = key => key.replaceAll("_", " ").replace(/^./, value => value.toUpperCase());
const element = (tag, text) => { const result = document.createElement(tag); if (text !== undefined) result.textContent = text; return result; };

async function request(path, payload) {
  const headers = token ? {Authorization: `Bearer ${token}`} : {};
  if (payload !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {method: payload === undefined ? "GET" : "POST", headers,
    body: payload === undefined ? undefined : JSON.stringify(payload), cache: "no-store"});
  const result = await response.json();
  if (!response.ok) { const error = new Error(result.error || `HTTP ${response.status}`); error.issues = result.issues; throw error; }
  return result;
}

function invalidate() {
  validated = undefined;
  byId("generate").disabled = true;
  byId("download-program").disabled = true;
  byId("preview").textContent = "";
  byId("identity").textContent = "";
  byId("status").textContent = "Program changed. Validate before generation or download.";
}

function resolve(schema, root) {
  return schema.$ref ? root.$defs[schema.$ref.split("/").at(-1)] : schema;
}

function fieldEditor(schema, root, key, path, value, required, parent) {
  schema = resolve(schema, root);
  if (schema.type === "object") {
    const group = element("div");
    group.className = "object-editor";
    const heading = element("h3", title(key));
    group.append(heading);
    let enable;
    if (!required) {
      const label = element("label", `Include ${title(key).toLowerCase()}`);
      enable = element("input"); enable.type = "checkbox"; enable.checked = value !== undefined;
      enable.dataset.path = `${path}.__enabled`; label.append(enable); group.append(label);
    }
    const fields = element("div"); fields.className = "fields";
    group.append(fields); parent.append(group);
    const children = {};
    for (const [name, child] of Object.entries(schema.properties)) {
      children[name] = fieldEditor(child, root, name, `${path}.${name}`, value?.[name],
        (schema.required || []).includes(name), fields);
    }
    if (enable) {
      fields.hidden = !enable.checked;
      enable.addEventListener("change", () => { fields.hidden = !enable.checked; invalidate(); });
    }
    return () => {
      if (enable && !enable.checked) return undefined;
      const result = {};
      for (const [name, read] of Object.entries(children)) { const item = read(); if (item !== undefined) result[name] = item; }
      return result;
    };
  }
  if (schema.type === "array" && resolve(schema.items, root).type === "object") {
    const group = element("div");
    group.className = "array-editor";
    const heading = element("h3", title(key)); group.append(heading);
    const rows = element("div"); group.append(rows);
    const add = element("button", `Add ${title(key).toLowerCase()} row`); add.type = "button";
    add.dataset.add = path; group.append(add); parent.append(group);
    const reads = new Map();
    function appendRow(initial) {
      const card = element("div"); card.className = "card";
      const index = rowSequence++;
      card.dataset.row = path;
      const read = fieldEditor(schema.items, root, key.replace(/s$/, ""), `${path}[${index}]`, initial, true, card);
      const actions = element("div"); actions.className = "row-actions";
      for (const [caption, action] of [["Move up", () => { if (card.previousElementSibling) rows.insertBefore(card, card.previousElementSibling); }],
        ["Move down", () => { if (card.nextElementSibling) rows.insertBefore(card.nextElementSibling, card); }],
        ["Remove row", () => { reads.delete(card); card.remove(); }]]) {
        const button = element("button", caption); button.type = "button";
        button.addEventListener("click", () => { action(); invalidate(); }); actions.append(button);
      }
      card.append(actions); reads.set(card, read); rows.append(card);
    }
    for (const item of value || []) appendRow(item);
    add.addEventListener("click", () => { appendRow(undefined); invalidate(); });
    return () => [...rows.children].map(card => reads.get(card)());
  }
  const label = element("label", title(key) + (required ? "" : " (optional)"));
  let input;
  if (schema.type === "array") {
    input = element("textarea"); input.placeholder = "One value per line; leave empty for none";
    input.value = (value || []).join("\n");
  } else if (schema.enum) {
    input = element("select");
    if (!required) { const empty = element("option", "Not specified"); empty.value = ""; input.append(empty); }
    for (const item of schema.enum) { const option = element("option", item); option.value = item; input.append(option); }
    input.value = value ?? (required ? schema.enum[0] : "");
  } else {
    input = element("input");
    if (schema.type === "boolean") {
      input.type = "checkbox";
      input.checked = value ?? ["is_heated", "rotation_allowed", "required"].includes(key);
    } else if (["number", "integer"].includes(schema.type)) {
      input.type = "number"; input.step = schema.type === "integer" ? "1" : "any";
      if (schema.minimum !== undefined) input.min = schema.minimum;
      const fallback = key.startsWith("clearance_") || key === "anchor_offset_mm" || key === "offset_mm" || key === "x" || key === "y" ? 0 : undefined;
      input.value = value ?? fallback ?? "";
    } else { input.type = "text"; input.value = value ?? ""; }
  }
  input.dataset.path = path;
  input.addEventListener("input", invalidate);
  input.addEventListener("change", invalidate);
  label.append(input); parent.append(label);
  return () => {
    if (schema.type === "boolean") return input.checked;
    if (schema.type === "array") {
      const itemSchema = resolve(schema.items, root);
      const lines = input.value === "" ? [] : input.value.split(/\r?\n/);
      if (lines.some(line => !line.length)) throw new Error(`${path}: empty list entry`);
      return lines.map(line => {
        if (["number", "integer"].includes(itemSchema.type)) {
          const number = Number(line); if (!Number.isFinite(number) || !line.trim()) throw new Error(`${path}: finite numbers required`);
          return number;
        }
        return line;
      });
    }
    if (input.value === "" && !required) return undefined;
    if (["number", "integer"].includes(schema.type)) {
      if (!input.value || !Number.isFinite(Number(input.value))) throw new Error(`${path}: finite number required`);
      return Number(input.value);
    }
    return input.value;
  };
}

function buildControls(config) {
  const layoutRoot = config.schemas.layout;
  editors.layout = fieldEditor(layoutRoot, layoutRoot, "layout", "layout", config.defaults, true, byId("layout-fields"));
  const buildingRoot = config.schemas.building;
  for (const [name, target] of [["zones", "zone-fields"], ["equipment", "equipment-fields"], ["flows", "flow-fields"], ["structural_grid", "grid-fields"]]) {
    editors[name] = fieldEditor(buildingRoot.properties[name], buildingRoot, name, name, undefined, name !== "structural_grid", byId(target));
  }
  for (const item of config.profiles) {
    const option = element("option", `${item.profile.name} (${item.profile.version})`); option.value = item.id;
    byId("profile").append(option);
  }
  for (const strategy of config.room_solvers) { const option = element("option", strategy); option.value = strategy; byId("room-solver").append(option); }
  showProfile();
}

function selectedProfile() { return configuration.profiles.find(item => item.id === byId("profile").value).profile; }
function showProfile() {
  const profile = selectedProfile();
  byId("profile-source").textContent = `Domain: ${profile.domain}. Jurisdiction: ${profile.jurisdiction}. Source: ${profile.provenance.source || "Not declared"}. ${profile.provenance.note || ""} Engineering acceptance pending.`;
}
function collect() {
  const layout = editors.layout();
  const program = {version: "0.2", project_name: layout.project_name, layout,
    zones: editors.zones(), equipment: editors.equipment(), flows: editors.flows()};
  const grid = editors.structural_grid(); if (grid !== undefined) program.structural_grid = grid;
  return {program, profile: selectedProfile()};
}

function errors(error) {
  byId("validation").hidden = false;
  byId("errors").replaceChildren();
  for (const issue of error.issues || [{path: "Input", message: error.message}]) byId("errors").append(element("li", `${issue.path}: ${issue.message}`));
  byId("status").textContent = "Input validation failed. Correct the fields before generation.";
}
function download(body, type, filename) {
  const url = URL.createObjectURL(new Blob([body], {type}));
  const link = element("a"); link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

byId("connect").addEventListener("click", async () => {
  token = byId("token").value;
  try {
    const config = await request("/program/config");
    configuration = config; buildControls(config);
    byId("program-form").hidden = false;
    byId("connect").disabled = true;
    byId("token").disabled = true;
    byId("status").textContent = "Blank program ready. Add rooms and define the entry room.";
  } catch (error) { byId("status").textContent = `Connection failed: ${error.message}`; }
});
byId("profile").addEventListener("change", () => { showProfile(); invalidate(); });
byId("program-form").addEventListener("input", invalidate);
byId("program-form").addEventListener("submit", async event => {
  event.preventDefault(); invalidate(); byId("errors").replaceChildren();
  try {
    const result = await request("/program/validate", collect());
    validated = result;
    byId("validation").hidden = false;
    byId("preview").textContent = JSON.stringify(result.program, null, 2);
    byId("identity").textContent = `Canonical program SHA256: ${result.program_sha256}. Engineering acceptance: ${result.engineering_acceptance}.`;
    byId("status").textContent = "VALID INPUT. Ready for independent generation checks.";
    byId("generate").disabled = generating; byId("download-program").disabled = false;
  } catch (error) { errors(error); }
});
byId("download-program").addEventListener("click", () => {
  if (validated) download(JSON.stringify(validated.program, null, 2), "application/json", "building-program.json");
});
byId("generate").addEventListener("click", async () => {
  if (!validated || generating) return;
  generating = true;
  byId("generate").disabled = true; byId("job").hidden = false;
  byId("artifacts").replaceChildren(); byId("job-evidence").textContent = "";
  byId("job-checks").replaceChildren();
  byId("job-identity").textContent = `Submitted canonical input SHA256: ${validated.program_sha256}.`;
  const payload = {program: validated.program, profile: validated.profile,
    variants: Number(byId("variants").value), max_attempts: Number(byId("max-attempts").value),
    seed: Number(byId("seed").value), time_limit_seconds: Number(byId("time-limit").value),
    equipment_retries: Number(byId("equipment-retries").value), room_solver: byId("room-solver").value};
  if (byId("deterministic-units").value) payload.deterministic_units = Number(byId("deterministic-units").value);
  try {
    let job = await request("/jobs", payload);
    byId("job-runtime").textContent = `Runtime fingerprint: ${job.runtime || job.runtime_fingerprint || "See the job evidence below"}.`;
    for (;;) {
      byId("job-status").textContent = `Job ${job.id}: ${job.status}. Engineering acceptance pending.`;
      byId("job-evidence").textContent = JSON.stringify(job, null, 2);
      if (["SUCCEEDED", "FAILED", "CANCELLED", "INTERRUPTED"].includes(job.status)) break;
      await new Promise(done => setTimeout(done, 500)); job = await request(`/jobs/${job.id}`);
    }
    if (job.qa) {
      for (const [index, variant] of job.qa.variants.entries()) {
        for (const check of variant.checks) byId("job-checks").append(element("li", `Variant ${index+1} — ${check.id}: ${check.status}. ${check.message}`));
      }
      if (job.qa.consistency) byId("job-checks").append(element("li", `${job.qa.consistency.id}: ${job.qa.consistency.status}. ${job.qa.consistency.message}`));
    } else if (job.error) {
      byId("job-checks").append(element("li", `Generation failed: ${job.error.message}. No accepted artifacts are available.`));
    }
    if (job.status === "SUCCEEDED") {
      const inventory = await request(`/jobs/${job.id}/artifacts`);
      for (const name of inventory.files) {
        const button = element("button", name); button.type = "button";
        button.addEventListener("click", async () => {
          try {
            const response = await fetch(`/jobs/${job.id}/artifacts/${name.split("/").map(encodeURIComponent).join("/")}`,
              {headers: token ? {Authorization: `Bearer ${token}`} : {}, cache: "no-store"});
            if (!response.ok) throw new Error(`Artifact HTTP ${response.status}`);
            download(await response.arrayBuffer(), response.headers.get("Content-Type"), name.split("/").at(-1));
          } catch (error) { byId("job-status").textContent = `Download failed: ${error.message}`; }
        }); byId("artifacts").append(button);
      }
    }
  } catch (error) { byId("job-status").textContent = `Generation request failed: ${error.message}`; }
  finally { generating = false; byId("generate").disabled = !validated; }
});
