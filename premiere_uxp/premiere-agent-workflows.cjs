"use strict";
(function () {
  const DEFAULT_CEP_URL = "http://127.0.0.1:48791/jsonrpc";
  const REVIEW_OUTPUT_DIR = "/private/tmp/premiere-agent-review-frames";
  let requestCounter = 1;

  function el(tag, attrs, text) {
    const node = document.createElement(tag);
    Object.keys(attrs || {}).forEach((key) => {
      if (key === "className") node.className = attrs[key];
      else if (key === "for") node.setAttribute("for", attrs[key]);
      else node.setAttribute(key, attrs[key]);
    });
    if (text != null) node.textContent = text;
    return node;
  }

  function resultBox() { return document.getElementById("premiere-agent-result"); }
  function cepUrl() {
    const input = document.getElementById("premiere-agent-cep-url");
    return input && input.value.trim() ? input.value.trim() : DEFAULT_CEP_URL;
  }
  function show(value) {
    const box = resultBox();
    if (box) box.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  }
  async function cepRpc(method, params) {
    const payload = { jsonrpc: "2.0", id: "premiere-agent-uxp-" + requestCounter++, method, params: params || {} };
    const response = await fetch(cepUrl(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const text = await response.text();
    let parsed = null;
    try { parsed = text ? JSON.parse(text) : null; } catch (_) { throw new Error("CEP bridge returned non-JSON: " + text); }
    if (!response.ok) throw new Error("CEP bridge HTTP " + response.status + ": " + text);
    if (parsed && parsed.error) throw new Error(parsed.error.message || JSON.stringify(parsed.error));
    show(parsed);
    return parsed;
  }
  function bind(id, fn) {
    const node = document.getElementById(id);
    if (!node) return;
    node.addEventListener("click", function () {
      Promise.resolve(fn()).catch(function (error) { show({ ok: false, error: error && error.message ? error.message : String(error) }); });
    });
  }
  function numberValue(id, fallback) {
    const raw = document.getElementById(id);
    const value = raw && raw.value !== "" ? Number(raw.value) : fallback;
    if (!isFinite(value)) throw new Error(id + " must be a number");
    return value;
  }
  function render() {
    const section = el("section", { className: "premiere-agent-card" });
    section.appendChild(el("h4", {}, "Premiere Agent CEP fallback"));
    section.appendChild(el("p", { className: "help" }, "Reference UXP bridge first; these buttons call the existing local CEP/ExtendScript bridge for Econ Films workflows that are not UXP-native yet."));
    section.appendChild(el("label", { for: "premiere-agent-cep-url" }, "CEP JSON-RPC URL"));
    section.appendChild(el("input", { id: "premiere-agent-cep-url", value: DEFAULT_CEP_URL, spellcheck: "false", autocomplete: "off" }));

    const row = el("div", { className: "premiere-agent-actions" });
    row.appendChild(el("button", { id: "premiere-agent-verify" }, "Verify CEP"));
    row.appendChild(el("button", { id: "premiere-agent-structure" }, "Read sequence"));
    row.appendChild(el("button", { id: "premiere-agent-markers" }, "List markers"));
    section.appendChild(row);

    section.appendChild(el("label", { for: "premiere-agent-frame-count" }, "Review frames"));
    const frameRow = el("div", { className: "premiere-agent-grid" });
    frameRow.appendChild(el("input", { id: "premiere-agent-frame-count", value: "4", inputmode: "numeric", title: "Frame count" }));
    frameRow.appendChild(el("input", { id: "premiere-agent-range-start", value: "0", inputmode: "decimal", title: "Range start seconds" }));
    frameRow.appendChild(el("input", { id: "premiere-agent-range-end", value: "12", inputmode: "decimal", title: "Range end seconds" }));
    section.appendChild(frameRow);
    section.appendChild(el("p", { className: "help" }, "Outputs: " + REVIEW_OUTPUT_DIR + " — backup token is only for non-destructive review-frame export."));
    const exportRow = el("div", { className: "premiere-agent-actions" });
    exportRow.appendChild(el("button", { id: "premiere-agent-review-frames" }, "Export review frames"));
    section.appendChild(exportRow);
    section.appendChild(el("pre", { id: "premiere-agent-result", role: "status", "aria-live": "polite", "aria-atomic": "true", tabindex: "0" }, "Premiere Agent workflow results appear here."));
    document.body.insertBefore(section, document.getElementById("status"));
  }

  document.addEventListener("DOMContentLoaded", function () {
    render();
    bind("premiere-agent-verify", function () { return cepRpc("verify_premiere_connection", {}); });
    bind("premiere-agent-structure", function () { return cepRpc("get_sequence_structure", { sequence_id: "active_sequence" }); });
    bind("premiere-agent-markers", function () { return cepRpc("list_markers", { sequence_id: "active_sequence" }); });
    bind("premiere-agent-review-frames", function () {
      return cepRpc("export_sequence_review_frames", {
        sequence_id: "active_sequence",
        backup_sequence_id: "manual_backup_confirmed_for_review_frame_export",
        output_dir: REVIEW_OUTPUT_DIR,
        frame_count: Math.max(2, Math.min(24, Math.floor(numberValue("premiere-agent-frame-count", 4)))),
        range_start_s: Math.max(0, numberValue("premiere-agent-range-start", 0)),
        range_end_s: Math.max(0, numberValue("premiere-agent-range-end", 12))
      });
    });
  });
}());
