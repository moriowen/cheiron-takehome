// API response inspector. app.js publishes one result per operation; this module keeps the
// most recent ones in memory and shows the exact request and response for the selected one.
// JSON is always written with textContent, never as HTML.
"use strict";

const Inspector = (() => {
  const MAX_RESULTS = 25;
  const ENDPOINT = "/query";
  const results = [];  // newest first
  let selectedId = null;
  let nextId = 1;
  const $ = (id) => document.getElementById(id);

  // result: {mode: "live"|"saved", request, raw, response?, status?, elapsedMs?, error?}
  function publish(result) {
    const entry = { ...result, id: nextId++, at: new Date() };
    results.unshift(entry);
    results.splice(MAX_RESULTS);
    selectedId = entry.id;
    render();
    return entry.id;
  }

  function select(id) {
    if (!results.some((r) => r.id === id)) return;
    selectedId = id;
    render();
  }

  function selected() {
    return results.find((r) => r.id === selectedId) || null;
  }

  function outcome(r) {
    if (r.error && r.response == null) return "error";
    if (r.status != null && (r.status < 200 || r.status >= 300)) return `HTTP ${r.status}`;
    return r.response?.status ?? "unknown";
  }

  function render() {
    const r = selected();
    $("inspect-empty").hidden = !!r;
    $("inspect-body").hidden = !r;
    const pick = $("inspect-select");
    pick.replaceChildren(...results.map((x) => {
      const opt = document.createElement("option");
      opt.value = String(x.id);
      const q = x.request?.query ?? "";
      opt.textContent = `#${x.id} · ${x.mode} · ${outcome(x)} · ${q.length > 70 ? q.slice(0, 68) + "…" : q}`;
      opt.selected = x.id === selectedId;
      return opt;
    }));
    if (!r) return;
    $("inspect-mode").textContent = r.mode === "live" ? "Live call" : "Saved playback";
    $("inspect-mode").dataset.mode = r.mode;
    $("inspect-endpoint").textContent = `POST ${ENDPOINT}`;
    if (r.mode === "live") {
      $("inspect-status").textContent = r.status == null ? "No HTTP response" : `HTTP ${r.status}`;
      $("inspect-latency").textContent = r.elapsedMs == null ? "" :
        `${Math.round(r.elapsedMs).toLocaleString()} ms client-observed latency`;
    } else {
      $("inspect-status").textContent = "Saved run: no live HTTP status";
      $("inspect-latency").textContent = r.source ? `From ${r.source}` : "";
    }
    $("inspect-request").textContent = JSON.stringify(r.request, null, 2);
    const notes = [];
    if (r.error) notes.push(r.error);
    if (r.response === undefined && r.raw) notes.push("The response body is not JSON. It is shown as received.");
    if (r.mode === "live" && r.response !== undefined) notes.push("Pretty-printed for reading. Copy and download use the exact body.");
    $("inspect-note").textContent = notes.join(" ");
    $("inspect-response").textContent = r.response !== undefined
      ? JSON.stringify(r.response, null, 2) : (r.raw || "(empty body)");
    const hasBody = !!r.raw;
    $("copy-response").disabled = !hasBody;
    $("download-response").disabled = !hasBody;
  }

  function queryUrl() {
    const live = document.body.dataset.mode === "live";
    return live ? new URL(`..${ENDPOINT}`, location.href).href : `http://localhost:8000${ENDPOINT}`;
  }

  function curl(request) {
    const body = JSON.stringify(request).replace(/'/g, "'\\''");
    return `curl -s -X POST '${queryUrl()}' \\\n  -H 'content-type: application/json' \\\n  -d '${body}'`;
  }

  function feedback(text) {
    $("inspect-feedback").textContent = text;
  }

  async function copy(text, label) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      const ok = document.execCommand("copy");
      area.remove();
      if (!ok) { feedback(`Could not copy the ${label}. Select the text and copy it by hand.`); return; }
    }
    feedback(`Copied the ${label}.`);
  }

  function download(r) {
    const blob = new Blob([r.raw], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `trialviz-response-${r.id}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 0);
    feedback(`Downloaded ${link.download}.`);
  }

  function init() {
    $("inspect-select").onchange = (event) => select(Number(event.target.value));
    $("copy-request").onclick = () => { const r = selected(); if (r) copy(JSON.stringify(r.request, null, 2), "request"); };
    $("copy-response").onclick = () => { const r = selected(); if (r?.raw) copy(r.raw, "response"); };
    $("download-response").onclick = () => { const r = selected(); if (r?.raw) download(r); };
    $("copy-curl").onclick = () => { const r = selected(); if (r) copy(curl(r.request), "curl command"); };
    render();
  }

  return { init, publish, select, selected };
})();
