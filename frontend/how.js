// Tab navigation and a trace of the saved comparison. Chat state stays in app.js.
(() => {
  "use strict";

  const askTab = document.getElementById("ask-tab");
  const howTab = document.getElementById("how-tab");
  const askPanel = document.getElementById("ask-panel");
  const howPanel = document.getElementById("how-panel");
  const example = document.getElementById("worked-example");
  let exampleLoaded = false;

  function traceStep(title, value, note) {
    const step = document.createElement("article");
    step.className = "trace-step";
    const heading = document.createElement("h4");
    heading.textContent = title;
    step.appendChild(heading);
    if (note) {
      const text = document.createElement("p");
      text.className = "hint";
      text.textContent = note;
      step.appendChild(text);
    }
    const pre = document.createElement("pre");
    pre.textContent = JSON.stringify(value, null, 2);
    step.appendChild(pre);
    return step;
  }

  async function loadExample() {
    if (exampleLoaded) return;
    exampleLoaded = true;
    try {
      const res = await fetch("../examples/02-compare-phases.json");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const { request, response } = await res.json();
      const { meta, visualization } = response;
      const row = visualization.data[0];
      const { citations, ...values } = row;
      const fragment = document.createDocumentFragment();
      fragment.appendChild(traceStep("Request", request));
      fragment.appendChild(traceStep("Planner → meta.plan", meta.plan));
      fragment.appendChild(traceStep("Compiler → meta.api_query", meta.api_query,
        "Each comparison value has its own registry search."));
      fragment.appendChild(traceStep("Fetch → snapshot and limits", {
        data_timestamp: meta.data_timestamp,
        total_matching: meta.total_matching,
        trials_counted: meta.trials_counted,
        count_completeness: meta.count_completeness,
        count_method: meta.count_method,
        records_fetched: meta.records_fetched,
        coverage: meta.coverage,
        notes: meta.notes,
      }));
      const datum = traceStep("Aggregate → one data row", values,
        "The first row in visualization.data. The citation count is the full support count; the saved citations are a sample.");
      const list = document.createElement("ol");
      list.className = "example-citations";
      for (const citation of citations) {
        const item = document.createElement("li");
        const link = document.createElement("a");
        link.href = `https://clinicaltrials.gov/study/${encodeURIComponent(citation.nct_id)}`;
        link.target = "_blank";
        link.rel = "noopener";
        link.textContent = citation.nct_id;
        const title = document.createElement("div");
        title.textContent = citation.title;
        const evidence = document.createElement("code");
        evidence.textContent = `${citation.field} = ${citation.excerpt}`;
        item.append(link, title, evidence);
        list.appendChild(item);
      }
      datum.appendChild(list);
      fragment.appendChild(datum);
      fragment.appendChild(traceStep("Chart selector + spec → visualization", {
        type: visualization.type,
        title: visualization.title,
        encoding: visualization.encoding,
      }));
      example.replaceChildren(fragment);
    } catch (error) {
      example.replaceChildren();
      const message = document.createElement("p");
      message.textContent = `Could not load the saved example (${error.message}).`;
      const retry = document.createElement("button");
      retry.type = "button";
      retry.textContent = "Try again";
      retry.onclick = () => { exampleLoaded = false; loadExample(); };
      example.append(message, retry);
    }
  }

  function syncTab() {
    const how = location.hash === "#how";
    askPanel.hidden = how;
    howPanel.hidden = !how;
    askTab.setAttribute("aria-selected", String(!how));
    howTab.setAttribute("aria-selected", String(how));
    askTab.tabIndex = how ? -1 : 0;
    howTab.tabIndex = how ? 0 : -1;
    if (how) loadExample();
  }

  for (const tab of [askTab, howTab]) {
    tab.onclick = () => { location.hash = tab === howTab ? "how" : "ask"; };
    tab.onkeydown = (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === "Home" ? askTab : event.key === "End" ? howTab :
        tab === askTab ? howTab : askTab;
      next.focus();
      next.click();
    };
  }
  window.addEventListener("hashchange", syncTab);
  syncTab();
})();
