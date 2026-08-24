"use strict";

let uxp = null;
let ppro = null;
try { uxp = require("uxp"); } catch (_) {}
try { ppro = require("premierepro"); } catch (_) {}

const DEFAULT_BRIDGE_URL = "http://127.0.0.1:48791/jsonrpc";
const REVIEW_OUTPUT_DIR = "/private/tmp/premiere-agent-review-frames";

const els = {};
let requestCounter = 1;

if (uxp && uxp.entrypoints && typeof uxp.entrypoints.setup === "function") {
  uxp.entrypoints.setup({
    panels: {
      premiereAgentPanel: {
        create() {},
        show() { refreshState(); },
        destroy() {}
      }
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  for (const id of [
    "uxp-pill", "cep-pill", "bridge-url", "refresh-state", "check-bridge",
    "list-markers", "review-frames", "copy-result", "status",
    "project-state", "sequence-state", "playhead-state", "host-state",
    "live-confirm", "backup-id", "marker-notes", "add-editorial-markers",
    "caption-path", "caption-start", "caption-format", "choose-caption", "import-captions"
  ]) els[id] = document.getElementById(id);

  if (els["bridge-url"] && !els["bridge-url"].value) els["bridge-url"].value = DEFAULT_BRIDGE_URL;
  bind("refresh-state", "click", refreshState);
  bind("check-bridge", "click", verifyCepBridge);
  bind("list-markers", "click", listMarkers);
  bind("review-frames", "click", exportReviewFrames);
  bind("add-editorial-markers", "click", addEditorialMarkers);
  bind("choose-caption", "click", chooseCaptionFile);
  bind("import-captions", "click", importCaptions);
  bind("copy-result", "click", copyResult);
  setPill("uxp-pill", ppro ? "UXP ready" : "UXP unavailable", ppro ? "ok" : "err");
  refreshState();
});

function bind(id, event, handler) {
  if (els[id]) els[id].addEventListener(event, () => { Promise.resolve(handler()).catch(showError); });
}

function setPill(id, text, kind) {
  const el = els[id];
  if (!el) return;
  el.textContent = text;
  el.className = "pill " + (kind || "warn");
}

function show(value) {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  if (els.status) els.status.textContent = text;
}

function showError(error) {
  setPill("uxp-pill", "error", "err");
  show({ ok: false, error: error && error.message ? error.message : String(error) });
}

function secondsLabel(value) {
  if (value == null || !isFinite(Number(value))) return "—";
  return Number(value).toFixed(3) + "s";
}

async function refreshState() {
  if (!ppro || !ppro.Project || typeof ppro.Project.getActiveProject !== "function") {
    setPill("uxp-pill", "UXP API unavailable", "err");
    show({ ok: false, backend: "uxp", error: "Premiere UXP API unavailable. Open this panel inside Premiere Pro 25.6+." });
    return;
  }
  const project = await ppro.Project.getActiveProject();
  const sequence = project && typeof project.getActiveSequence === "function" ? await project.getActiveSequence() : null;
  let position = null;
  try { if (sequence && typeof sequence.getPlayerPosition === "function") position = await sequence.getPlayerPosition(); } catch (_) {}
  const state = {
    ok: true,
    backend: "uxp",
    projectOpen: !!project,
    sequenceOpen: !!sequence,
    project: project ? { guid: String(project.guid || ""), name: String(project.name || "") } : null,
    sequence: sequence ? { guid: String(sequence.guid || ""), name: String(sequence.name || "") } : null,
    playheadSeconds: position && position.seconds != null ? Number(position.seconds) : null,
    cepFallback: { url: bridgeUrl(), purpose: "existing localhost JSON-RPC bridge for ExtendScript/QE compatibility" }
  };
  setPill("uxp-pill", state.sequenceOpen ? "sequence open" : state.projectOpen ? "project open" : "no project", state.projectOpen ? "ok" : "warn");
  if (els["project-state"]) els["project-state"].textContent = state.project ? state.project.name || state.project.guid : "No project";
  if (els["sequence-state"]) els["sequence-state"].textContent = state.sequence ? state.sequence.name || state.sequence.guid : "No sequence";
  if (els["playhead-state"]) els["playhead-state"].textContent = secondsLabel(state.playheadSeconds);
  if (els["host-state"]) els["host-state"].textContent = "Premiere UXP";
  show(state);
}

function bridgeUrl() {
  const raw = els["bridge-url"] && els["bridge-url"].value ? els["bridge-url"].value : DEFAULT_BRIDGE_URL;
  return raw.trim() || DEFAULT_BRIDGE_URL;
}

async function cepRpc(method, params) {
  const payload = { jsonrpc: "2.0", id: requestCounter++, method, params: params || {} };
  const response = await fetch(bridgeUrl(), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  const text = await response.text();
  let parsed = null;
  try { parsed = text ? JSON.parse(text) : null; } catch (error) { throw new Error("CEP bridge returned non-JSON: " + text); }
  if (!response.ok) throw new Error("CEP bridge HTTP " + response.status + ": " + text);
  if (parsed && parsed.error) throw new Error(parsed.error.message || JSON.stringify(parsed.error));
  setPill("cep-pill", "connected", "ok");
  show(parsed);
  return parsed;
}

async function verifyCepBridge() {
  return cepRpc("verify_premiere_connection", {});
}

async function listMarkers() {
  return cepRpc("list_markers", {});
}

async function exportReviewFrames() {
  return cepRpc("export_sequence_review_frames", {
    backup_sequence_id: "manual_backup_confirmed_for_review_frame_export",
    output_dir: REVIEW_OUTPUT_DIR,
    frame_count: 4,
    range_start_s: 0,
    range_end_s: 12
  });
}

function checkedLiveWritePayload() {
  const confirmed = !!(els["live-confirm"] && els["live-confirm"].checked);
  const backupId = els["backup-id"] && els["backup-id"].value ? els["backup-id"].value.trim() : "";
  if (!confirmed) throw new Error("Tick the live-write confirmation after backing up/duplicating the active sequence.");
  if (!backupId) throw new Error("Enter the backup sequence ID/name before live-write actions.");
  return { sequence_id: "active_sequence", backup_sequence_id: backupId };
}

function parseMarkerNotes(text) {
  const notes = [];
  const lines = String(text || "").split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.charAt(0) === "#") continue;
    const parts = trimmed.indexOf("|") !== -1 ? trimmed.split("|") : trimmed.split(",");
    const timeS = Number((parts[0] || "").trim());
    if (!isFinite(timeS) || timeS < 0) throw new Error("Bad marker time in line: " + line);
    const label = (parts[1] || "AI EDITORIAL NOTE").trim() || "AI EDITORIAL NOTE";
    const comment = (parts.slice(2).join("|") || "").trim();
    notes.push({ time_s: timeS, kind: "uxp_editorial_note", label, comment, color_index: 1 });
  }
  if (!notes.length) throw new Error("Add at least one marker note line: time | label | comment");
  return notes;
}

async function addEditorialMarkers() {
  const safe = checkedLiveWritePayload();
  return cepRpc("add_editorial_markers", Object.assign(safe, {
    default_color: "red",
    notes: parseMarkerNotes(els["marker-notes"] ? els["marker-notes"].value : "")
  }));
}

async function chooseCaptionFile() {
  if (!uxp || !uxp.storage || !uxp.storage.localFileSystem || typeof uxp.storage.localFileSystem.getFileForOpening !== "function") {
    throw new Error("UXP file picker unavailable; paste an absolute .srt/.vtt path instead.");
  }
  const file = await uxp.storage.localFileSystem.getFileForOpening({ types: ["srt", "vtt"] });
  if (!file) return null;
  const path = file.nativePath || file.fsName || file.path || "";
  if (!path) throw new Error("Selected file did not expose a native path; paste the absolute caption path instead.");
  if (els["caption-path"]) els["caption-path"].value = path;
  show({ ok: true, selected_caption_path: path });
  return path;
}

async function importCaptions() {
  const safe = checkedLiveWritePayload();
  const path = els["caption-path"] && els["caption-path"].value ? els["caption-path"].value.trim() : "";
  if (!path) throw new Error("Choose or paste an absolute .srt/.vtt caption path first.");
  const start = Number(els["caption-start"] && els["caption-start"].value ? els["caption-start"].value : 0);
  if (!isFinite(start) || start < 0) throw new Error("Caption start seconds must be a non-negative number.");
  return cepRpc("import_captions", Object.assign(safe, {
    caption_path: path,
    start_s: start,
    caption_format: els["caption-format"] && els["caption-format"].value ? els["caption-format"].value.trim() : "subtitle"
  }));
}

async function copyResult() {
  const text = els.status ? els.status.textContent : "";
  if (!text) return;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const area = document.createElement("textarea");
  area.value = text;
  document.body.appendChild(area);
  area.select();
  document.execCommand("copy");
  document.body.removeChild(area);
}
