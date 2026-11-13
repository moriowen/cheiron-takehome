// Chat shell. Live mode posts to the backend; static mode (no backend, e.g. GitHub Pages)
// replays the committed example runs in ../examples/.
"use strict";

const PRESETS = [
  "01-trend-with-hint", "02-compare-phases", "03-countries-recruiting", "04-sponsor-drug-network",
  "05-drug-cooccurrence-network", "06-enrollment-histogram", "07-out-of-scope",
  "08-exact-phase-counts",
];
const API = "../query";
let live = false;
const presetCache = {};

const $ = (id) => document.getElementById(id);
const chat = $("chat");

function addMsg(cls) {
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
    li.innerHTML = `<a target="_blank" rel="noopener" href="https://clinicaltrials.gov/study/${c.nct_id}">${c.nct_id}</a>
      ${c.title ? `<div>${escapeHtml(c.title)}</div>` : ""}
      <code>${escapeHtml(c.field)}</code> = <strong>${escapeHtml(c.excerpt)}</strong>`;
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

function renderAnswer(box, resp) {
  box.innerHTML = "";
  const m = resp.meta;
  if (resp.status === "unsupported") {
    box.innerHTML = `<h3>This question can't be answered from registry data</h3><p>${escapeHtml(m.notes.join(" "))}</p>`;
  } else {
    const v = resp.visualization;
    box.insertAdjacentHTML("beforeend", `<h3>${escapeHtml(v.title)}</h3>`);
    const chart = document.createElement("div");
    chart.className = "chart";
    box.appendChild(chart);
    renderViz(chart, v, showCitations);
    const summary = `${m.records_used.toLocaleString()} of ${m.total_matching.toLocaleString()} matching trials` +
      ` · data as of ${(m.data_timestamp || "").slice(0, 10)}` + (m.llm ? ` · planned by ${m.llm.model}` : "");
    box.insertAdjacentHTML("beforeend", `<div class="hint">${summary}. Click any mark to see its sources.</div>`);
  }
  if (m.notes.length && resp.status === "ok") {
    const ul = document.createElement("ul");
    ul.className = "notes";
    m.notes.forEach((n) => ul.insertAdjacentHTML("beforeend", `<li>${escapeHtml(n)}</li>`));
    box.appendChild(ul);
  }
  box.insertAdjacentHTML("beforeend",
    `<details><summary>How the question was interpreted</summary><pre>${escapeHtml(JSON.stringify({ plan: m.plan, api_query: m.api_query }, null, 2))}</pre></details>`);
  chat.scrollTop = chat.scrollHeight;
}

async function ask(body) {
  addMsg("user").textContent = body.query + (body.drug_name ? ` [drug: ${body.drug_name}]` : "");
  const box = addMsg("bot");
  box.textContent = "Thinking…";
  $("q").disabled = true;
  try {
    const res = await fetch(API, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    const json = await res.json();
    if (!res.ok) throw new Error(typeof json.detail === "string" ? json.detail : JSON.stringify(json.detail));
    renderAnswer(box, json);
  } catch (e) {
    box.innerHTML = `<p class="error">Request failed: ${escapeHtml(e.message)}</p>`;
  } finally {
    $("q").disabled = false;
    $("q").focus();
  }
}

async function loadPreset(name) {
  presetCache[name] ??= await (await fetch(`../examples/${name}.json`)).json();
  return presetCache[name];
}

async function playPreset(name, saved = false) {
  const ex = await loadPreset(name);
  if (live && !saved) return ask(ex.request);
  addMsg("user").textContent = ex.request.query + (ex.request.drug_name ? ` [drug: ${ex.request.drug_name}]` : "");
  renderAnswer(addMsg("bot"), ex.response);
}

async function init() {
  try {
    live = (await fetch("../health")).ok;
  } catch { live = false; }
  $("mode").textContent = live ? "Live: questions go to the backend" : "Demo mode: showing saved answers from real runs. Run the backend to ask your own questions.";
  for (const name of PRESETS) {
    const ex = await loadPreset(name).catch(() => null);
    if (!ex) continue;
    const b = document.createElement("button");
    b.textContent = ex.request.query.length > 60 ? ex.request.query.slice(0, 58) + "…" : ex.request.query;
    b.title = ex.request.query;
    b.onclick = () => playPreset(name);
    $("presets").appendChild(b);
  }
  $("ask").onsubmit = (ev) => {
    ev.preventDefault();
    const q = $("q").value.trim();
    if (!q) return;
    $("q").value = "";
    if (live) return ask({ query: q });
    addMsg("user").textContent = q;
    addMsg("bot").textContent = "Demo mode can only replay the saved questions above. Start the backend (see README) to ask new ones.";
  };
  $("cite-close").onclick = () => { $("citations").hidden = true; };
  // ?replay renders every saved example, without calling the backend.
  if (new URLSearchParams(location.search).has("replay")) {
    for (const name of PRESETS) await playPreset(name, true);
  }
}

init();
