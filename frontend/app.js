// Chat shell. Live mode posts to the backend; static mode (no backend, e.g. GitHub Pages)
// replays the committed example runs in ../examples/. Every operation is published to the
// Inspector before any chart is drawn, so a rendering failure cannot hide the response.
"use strict";

const PRESETS = [
  "01-trend-with-hint", "02-compare-phases", "03-countries-recruiting", "04-sponsor-drug-network",
  "05-drug-cooccurrence-network", "06-enrollment-histogram", "07-out-of-scope",
  "08-exact-phase-counts", "09-misspelled-drug",
];
const API = "../query";
let live = false;
const presetCache = {};

const $ = (id) => document.getElementById(id);
const chat = $("chat");

function addMsg(cls) {
  chat.querySelector(".empty-state")?.remove();
  const div = document.createElement("div");
  div.className = `msg ${cls}`;
  chat.appendChild(div);
  return div;
}

function showCitations(datum, heading) {
  $("cite-title").textContent = `Sources: ${heading}`;
  const list = $("cite-list");
  list.innerHTML = "";
  for (const c of datum.citations || []) {
    const li = document.createElement("li");
    li.innerHTML = `<a target="_blank" rel="noopener" href="https://clinicaltrials.gov/study/${encodeURIComponent(c.nct_id)}">${escapeHtml(c.nct_id)}</a>
      ${c.title ? `<div>${escapeHtml(c.title)}</div>` : ""}
      <code>${escapeHtml(c.field)}</code> = <strong>${escapeHtml(c.excerpt)}</strong>
      ${c.match && c.match !== "exact" ? `<span class="hint"> (mapped as ${escapeHtml(c.match.replace("_", " "))})</span>` : ""}`;
    list.appendChild(li);
  }
  const shown = new Set((datum.citations || []).map((c) => c.nct_id)).size;
  if (datum.citation_count > shown) {
    const li = document.createElement("li");
    li.textContent = `…and ${datum.citation_count - shown} more trials.`;
    list.appendChild(li);
  }
  $("citations").hidden = false;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]);
}

function userLabel(body) {
  return body.query + (body.drug_name ? ` [drug: ${body.drug_name}]` : "");
}

// Footer on every answer: mode label and a link to the exact response in the inspector.
function addInspectLink(box, resultId, mode) {
  const bar = document.createElement("div");
  bar.className = "answer-actions";
  const tag = document.createElement("span");
  tag.className = `mode-tag ${mode}`;
  tag.textContent = mode === "live" ? "Live answer" : "Saved playback";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "secondary";
  btn.textContent = "View API response";
  btn.onclick = () => { Inspector.select(resultId); Navigation.show("api"); };
  bar.append(tag, btn);
  box.appendChild(bar);
}

function renderAnswer(box, resp) {
  box.innerHTML = "";
  const m = resp.meta;
  if (resp.status === "unsupported") {
    box.classList.add("unsupported");
    box.innerHTML = `<h3>This question can't be answered from registry data</h3><p>${escapeHtml(m.notes.join(" "))}</p>`;
  } else {
    const v = resp.visualization;
    box.insertAdjacentHTML("beforeend", `<h3>${escapeHtml(v.title)}</h3>`);
    if (v.subtitle) {
      const sampled = m.count_completeness === "sample";
      box.insertAdjacentHTML("beforeend", `<p class="subtitle${sampled ? " sampled" : ""}">${escapeHtml(v.subtitle)}</p>`);
    }
    if ((m.ignored_constraints || []).length) {
      box.insertAdjacentHTML("beforeend", `<p class="correction omitted"><strong>Not applied:</strong> ${escapeHtml(m.ignored_constraints.join("; "))}. The registry filters cannot express this, so the chart counts trials with or without it.</p>`);
    }
    (m.entities || []).filter((e) => e.note).forEach((e) =>
      box.insertAdjacentHTML("beforeend", `<p class="correction">${escapeHtml(e.note)}</p>`));
    const chart = document.createElement("div");
    chart.className = "chart";
    box.appendChild(chart);
    try {
      renderViz(chart, v, showCitations);
    } catch (e) {
      chart.innerHTML = `<p class="error">The chart could not be drawn (${escapeHtml(e.message)}). The full response is still in the API response tab.</p>`;
    }
    const counted = m.trials_counted ?? m.records_used;  // older saved examples use records_used
    const summary = `${(counted ?? 0).toLocaleString()} of ${(m.total_matching ?? 0).toLocaleString()} matching trials counted` +
      ` · data as of ${(m.data_timestamp || "").slice(0, 10)}` + (m.llm ? ` · planned by ${m.llm.model}` : "");
    box.insertAdjacentHTML("beforeend", `<div class="hint">${summary}. Click any mark to see its sources.</div>`);
  }
  const shownAbove = new Set((m.entities || []).map((e) => e.note));
  const notes = (m.notes || []).filter((n) => !shownAbove.has(n) && !n.startsWith("Not applied:"));
  if (notes.length && resp.status === "ok") {
    const ul = document.createElement("ul");
    ul.className = "notes";
    notes.forEach((n) => ul.insertAdjacentHTML("beforeend", `<li>${escapeHtml(n)}</li>`));
    box.appendChild(ul);
  }
  box.insertAdjacentHTML("beforeend",
    `<details><summary>How the question was interpreted</summary><pre>${escapeHtml(JSON.stringify({ plan: m.plan, api_query: m.api_query }, null, 2))}</pre></details>`);
}

function errorMessage(json, raw, status) {
  const detail = json?.detail;
  if (typeof detail === "string") return detail;
  if (detail !== undefined) return JSON.stringify(detail);
  return raw ? raw.slice(0, 300) : `HTTP ${status}`;
}

async function ask(body) {
  addMsg("user").textContent = userLabel(body);
  const box = addMsg("bot");
  box.innerHTML = `<p class="loading">Planning the query and counting registry records…</p>`;
  $("q").disabled = true;
  $("ask-btn").disabled = true;
  const started = performance.now();
  let res = null, raw = "", json;
  try {
    res = await fetch(API, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    raw = await res.text();
  } catch (e) {
    const id = Inspector.publish({ mode: "live", request: body, raw: "", status: null,
      elapsedMs: performance.now() - started, error: `Network error: ${e.message}` });
    box.innerHTML = `<p class="error">Request failed: ${escapeHtml(e.message)}</p>`;
    addInspectLink(box, id, "live");
    return finish();
  }
  const elapsedMs = performance.now() - started;
  try { json = JSON.parse(raw); } catch { json = undefined; }
  const failed = !res.ok || json === undefined;
  const id = Inspector.publish({ mode: "live", request: body, raw, response: json, status: res.status, elapsedMs,
    error: failed ? (res.ok ? "The response body is not valid JSON." : `HTTP ${res.status}: ${errorMessage(json, raw, res.status)}`) : null });
  if (failed) {
    box.innerHTML = `<p class="error">Request failed: ${escapeHtml(res.ok ? "the response is not valid JSON" : errorMessage(json, raw, res.status))}</p>`;
  } else {
    try { renderAnswer(box, json); } catch (e) {
      box.innerHTML = `<p class="error">The answer could not be displayed (${escapeHtml(e.message)}).</p>`;
    }
  }
  addInspectLink(box, id, "live");
  finish();
}

function finish() {
  chat.scrollTop = chat.scrollHeight;
  $("q").disabled = false;
  $("ask-btn").disabled = false;
  $("q").focus();
}

async function loadPreset(name) {
  if (!presetCache[name]) {
    const res = await fetch(`../examples/${name}.json`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    presetCache[name] = await res.json();
  }
  return presetCache[name];
}

async function playPreset(name, saved = false) {
  const ex = await loadPreset(name);
  if (live && !saved) return ask(ex.request);
  addMsg("user").textContent = userLabel(ex.request);
  const box = addMsg("bot");
  const id = Inspector.publish({ mode: "saved", request: ex.request, response: ex.response,
    raw: JSON.stringify(ex.response, null, 2), source: `examples/${name}.json` });
  try { renderAnswer(box, ex.response); } catch (e) {
    box.innerHTML = `<p class="error">The saved answer could not be displayed (${escapeHtml(e.message)}).</p>`;
  }
  addInspectLink(box, id, "saved");
  chat.scrollTop = chat.scrollHeight;
}

function setMode() {
  document.body.dataset.mode = live ? "live" : "saved";
  $("mode").textContent = live ? "Live" : "Saved demo";
  $("mode").title = live ? "Questions go to the backend." : "No backend: buttons replay saved runs.";
  $("mode-detail").textContent = live
    ? "Live: each question calls the backend, the planner, and ClinicalTrials.gov. Example buttons send their question again."
    : "Saved demo: no backend is running. Example buttons replay saved responses from real runs. Start the backend to ask new questions.";
  const docs = $("docs-link");
  // GitHub Pages renders no Markdown here, so point at the repository README instead.
  const readme = location.hostname.endsWith("github.io")
    ? "https://github.com/moriowen/cheiron-takehome#readme" : "../README.md";
  docs.href = live ? "../docs" : readme;
  docs.textContent = live ? "Interactive API docs (/docs)" : "README: request and response schemas";
  $("q").placeholder = live
    ? "Ask about clinical trials, e.g. Which countries have the most recruiting trials for asthma?"
    : "Saved demo: use an example button, or start the backend to ask your own question";
}

async function initExamples() {
  for (const btn of document.querySelectorAll("[data-preset]")) {
    btn.onclick = () => playPreset(btn.dataset.preset).catch((e) => {
      addMsg("bot").innerHTML = `<p class="error">Could not load the saved example (${escapeHtml(e.message)}).</p>`;
    });
    loadPreset(btn.dataset.preset).then((ex) => { btn.title = ex.request.query; })
      .catch(() => { btn.disabled = true; btn.title = "Saved example file not found"; });
  }
  for (const btn of document.querySelectorAll("[data-example]")) {
    btn.onclick = () => {
      $("q").value = btn.dataset.example;
      $("q").focus();
      if (!live) {
        addMsg("bot").textContent = "This example has no saved run. Start the backend to send it live.";
        chat.scrollTop = chat.scrollHeight;
      }
    };
  }
}

async function init() {
  Inspector.init();
  Navigation.init();
  try {
    live = (await fetch("../health")).ok;
  } catch { live = false; }
  setMode();
  initExamples();
  $("ask").onsubmit = (ev) => {
    ev.preventDefault();
    const q = $("q").value.trim();
    if (!q) return;
    $("q").value = "";
    if (live) return ask({ query: q });
    addMsg("user").textContent = q;
    addMsg("bot").textContent = "Saved demo mode can only replay the saved examples. Start the backend (see README) to ask new questions.";
  };
  $("cite-close").onclick = () => { $("citations").hidden = true; };
  // ?replay renders every saved example, without calling the backend.
  if (new URLSearchParams(location.search).has("replay")) {
    for (const name of PRESETS) await playPreset(name, true);
  }
}

init();
