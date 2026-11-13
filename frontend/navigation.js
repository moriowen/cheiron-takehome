// Three-tab navigation (Ask, API response, How it works) driven by the URL hash.
// Panels are only hidden, never rebuilt, so charts and chat state survive a tab switch.
"use strict";

const Navigation = (() => {
  const TABS = ["ask", "api", "how"];
  const listeners = [];
  const tab = (id) => document.getElementById(`${id}-tab`);
  const panel = (id) => document.getElementById(`${id}-panel`);

  function current() {
    const id = location.hash.slice(1);
    return TABS.includes(id) ? id : "ask";
  }

  function sync() {
    const active = current();
    for (const id of TABS) {
      const on = id === active;
      panel(id).hidden = !on;
      tab(id).setAttribute("aria-selected", String(on));
      tab(id).tabIndex = on ? 0 : -1;
    }
    listeners.forEach((fn) => fn(active));
  }

  function show(id) {
    if (current() === id) sync();
    else location.hash = id;
  }

  function init() {
    TABS.forEach((id, i) => {
      tab(id).onclick = () => show(id);
      tab(id).onkeydown = (event) => {
        const moves = { ArrowLeft: i - 1, ArrowRight: i + 1, Home: 0, End: TABS.length - 1 };
        if (!(event.key in moves)) return;
        event.preventDefault();
        const next = TABS[(moves[event.key] + TABS.length) % TABS.length];
        tab(next).focus();
        show(next);
      };
    });
    window.addEventListener("hashchange", sync);
    sync();
  }

  return { init, show, current, onChange: (fn) => listeners.push(fn) };
})();
