// One D3 renderer per visualization type. Each reads only `encoding` and `data` from the spec,
// as documented in the README. `onPick(datum)` opens the citations panel.
"use strict";

const PALETTE = ["#2f6fdf", "#e07b39", "#2a9d8f", "#c44e8a", "#8a6fdf", "#6b8e23", "#b8860b", "#5f9ea0"];
const W = 900;

function svg(el, height) {
  return d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${height}`);
}

function axisTitle(g, text, x, y, rotate) {
  g.append("text").attr("x", x).attr("y", y).attr("text-anchor", "middle")
    .attr("transform", rotate ? `rotate(-90 ${x} ${y})` : null).text(text || "");
}

function tooltip(sel, fn) {
  sel.append("title").text(fn);
}

function legend(el, names, color) {
  const div = d3.select(el).append("div").attr("class", "legend");
  names.forEach((n) => div.append("span").style("--c", color(n)).text(n));
}

function bar(el, spec, onPick) {
  const { x, y } = spec.encoding;
  const horizontal = x.type === "quantitative";
  const cat = horizontal ? y : x, val = horizontal ? x : y;
  const data = spec.data;
  if (horizontal) {
    const h = Math.max(160, data.length * 26 + 50), m = { l: 230, r: 30, t: 10, b: 36 };
    const s = svg(el, h);
    const xs = d3.scaleLinear().domain([0, d3.max(data, (d) => d[val.field]) || 1]).nice().range([m.l, W - m.r]);
    const ys = d3.scaleBand().domain(data.map((d) => d[cat.field])).range([m.t, h - m.b]).padding(0.15);
    s.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(xs).ticks(6));
    s.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`)
      .call(d3.axisLeft(ys).tickFormat((t) => (t.length > 34 ? t.slice(0, 33) + "…" : t)));
    s.selectAll("rect").data(data).join("rect").attr("class", "clickable")
      .attr("x", m.l).attr("y", (d) => ys(d[cat.field])).attr("height", ys.bandwidth())
      .attr("width", (d) => xs(d[val.field]) - m.l).attr("fill", PALETTE[0]).on("click", (_, d) => onPick(d, d[cat.field]))
      .call(tooltip, (d) => `${d[cat.field]}: ${d[val.field]}`);
    axisTitle(s, val.title, (m.l + W - m.r) / 2, h - 4);
    return;
  }
  const h = 380, m = { l: 60, r: 20, t: 10, b: 70 };
  const s = svg(el, h);
  const xs = d3.scaleBand().domain(data.map((d) => d[cat.field])).range([m.l, W - m.r]).padding(0.2);
  const ys = d3.scaleLinear().domain([0, d3.max(data, (d) => d[val.field]) || 1]).nice().range([h - m.b, m.t]);
  s.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(xs))
    .selectAll("text").attr("transform", "rotate(-25)").style("text-anchor", "end");
  s.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(ys).ticks(6));
  s.selectAll("rect").data(data).join("rect").attr("class", "clickable")
    .attr("x", (d) => xs(d[cat.field])).attr("width", xs.bandwidth())
    .attr("y", (d) => ys(d[val.field])).attr("height", (d) => ys(0) - ys(d[val.field]))
    .attr("fill", PALETTE[0]).on("click", (_, d) => onPick(d, d[cat.field]))
    .call(tooltip, (d) => `${d[cat.field]}: ${d[val.field]}`);
  axisTitle(s, val.title, 16, (m.t + h - m.b) / 2, true);
}

function grouped(el, spec, onPick) {
  const { x, y, series } = spec.encoding;
  const data = spec.data;
  const cats = [...new Set(data.map((d) => d[x.field]))];
  const names = [...new Set(data.map((d) => d[series.field]))];
  const color = d3.scaleOrdinal(names, PALETTE);
  const h = 400, m = { l: 60, r: 20, t: 10, b: 90 };
  const s = svg(el, h);
  const x0 = d3.scaleBand().domain(cats).range([m.l, W - m.r]).padding(0.15);
  const x1 = d3.scaleBand().domain(names).range([0, x0.bandwidth()]).padding(0.05);
  const ys = d3.scaleLinear().domain([0, d3.max(data, (d) => d[y.field]) || 1]).nice().range([h - m.b, m.t]);
  s.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`)
    .call(d3.axisBottom(x0).tickFormat((t) => (t.length > 24 ? t.slice(0, 23) + "…" : t)))
    .selectAll("text").attr("transform", "rotate(-25)").style("text-anchor", "end");
  s.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(ys).ticks(6));
  s.selectAll("rect").data(data).join("rect").attr("class", "clickable")
    .attr("x", (d) => x0(d[x.field]) + x1(d[series.field])).attr("width", x1.bandwidth())
    .attr("y", (d) => ys(d[y.field])).attr("height", (d) => ys(0) - ys(d[y.field]))
    .attr("fill", (d) => color(d[series.field])).on("click", (_, d) => onPick(d, `${d[x.field]} · ${d[series.field]}`))
    .call(tooltip, (d) => `${d[series.field]} / ${d[x.field]}: ${d[y.field]}`);
  axisTitle(s, y.title, 16, (m.t + h - m.b) / 2, true);
  legend(el, names, color);
}

function timeSeries(el, spec, onPick) {
  const { x, y, series } = spec.encoding;
  const data = spec.data.map((d) => ({ ...d, _x: +d[x.field] }));
  const names = series ? [...new Set(data.map((d) => d[series.field]))] : ["Trials"];
  const color = d3.scaleOrdinal(names, PALETTE);
  const h = 360, m = { l: 60, r: 20, t: 10, b: 40 };
  const s = svg(el, h);
  const xs = d3.scaleLinear().domain(d3.extent(data, (d) => d._x)).range([m.l, W - m.r]);
  const ys = d3.scaleLinear().domain([0, d3.max(data, (d) => d[y.field]) || 1]).nice().range([h - m.b, m.t]);
  s.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(xs).tickFormat(d3.format("d")));
  s.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(ys).ticks(6));
  for (const name of names) {
    const pts = series ? data.filter((d) => d[series.field] === name) : data;
    s.append("path").datum(pts).attr("fill", "none").attr("stroke", color(name)).attr("stroke-width", 2)
      .attr("d", d3.line().x((d) => xs(d._x)).y((d) => ys(d[y.field])));
    s.append("path").datum(pts).attr("fill", "none").attr("stroke", color(name)).attr("stroke-dasharray", "4 3")
      .attr("opacity", 0.7).attr("d", d3.line().x((d) => xs(d._x)).y((d) => ys(d.estimated_count || 0)));
    s.selectAll(null).data(pts).join("circle").attr("class", "clickable").attr("r", 4)
      .attr("cx", (d) => xs(d._x)).attr("cy", (d) => ys(d[y.field])).attr("fill", color(name))
      .on("click", (_, d) => onPick(d, `${name} · ${d[x.field]}`))
      .call(tooltip, (d) => `${d[x.field]}: ${d[y.field]} (${d.estimated_count || 0} estimated)`);
  }
  axisTitle(s, y.title, 16, (m.t + h - m.b) / 2, true);
  legend(el, names, color);
  d3.select(el).append("div").attr("class", "hint").text("Dashed line: trials whose start date is still an estimate.");
}

function histogram(el, spec, onPick) {
  const { x, y } = spec.encoding;
  const data = spec.data.map((d) => ({ ...d, _label: d.bin_end == null ? `≥ ${d.bin_start}` : `${d.bin_start}-${d.bin_end}` }));
  bar(el, { encoding: { x: { field: "_label", type: "ordinal", title: x.title }, y }, data }, onPick);
}

function scatter(el, spec, onPick) {
  const { x, y, tooltip: tip } = spec.encoding;
  const data = spec.data;
  const h = 420, m = { l: 70, r: 20, t: 10, b: 46 };
  const s = svg(el, h);
  const xs = d3.scaleSymlog().domain([0, d3.max(data, (d) => d[x.field]) || 1]).range([m.l, W - m.r]).nice();
  const ys = d3.scaleSymlog().domain([0, d3.max(data, (d) => d[y.field]) || 1]).range([h - m.b, m.t]).nice();
  s.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(xs).ticks(8, "~s"));
  s.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(ys).ticks(8, "~s"));
  s.selectAll("circle").data(data).join("circle").attr("class", "clickable").attr("r", 3.5)
    .attr("cx", (d) => xs(d[x.field])).attr("cy", (d) => ys(d[y.field])).attr("fill", PALETTE[0]).attr("opacity", 0.6)
    .on("click", (_, d) => onPick(d, d.nct_id))
    .call(tooltip, (d) => `${d[tip.field]}\n${x.title}: ${d[x.field]}, ${y.title}: ${d[y.field]}`);
  axisTitle(s, x.title + " (log scale)", (m.l + W - m.r) / 2, h - 6);
  axisTitle(s, y.title + " (log scale)", 16, (m.t + h - m.b) / 2, true);
}

function network(el, spec, onPick) {
  const nodes = spec.data.nodes.map((n) => ({ ...n }));
  const edges = spec.data.edges.map((e) => ({ ...e }));
  const groups = [...new Set(nodes.map((n) => n.group))];
  const color = d3.scaleOrdinal(groups, PALETTE);
  const h = 620;
  const s = svg(el, h);
  const r = d3.scaleSqrt().domain([0, d3.max(nodes, (n) => n.weight) || 1]).range([4, 22]);
  const w = d3.scaleLinear().domain([0, d3.max(edges, (e) => e.weight) || 1]).range([0.5, 8]);
  const sim = d3.forceSimulation(nodes)
    .force("link", d3.forceLink(edges).id((n) => n.id).distance(150).strength(0.15))
    .force("charge", d3.forceManyBody().strength(-700))
    .force("x", d3.forceX(W / 2).strength(0.06))
    .force("y", d3.forceY(h / 2).strength(0.1))
    .force("collide", d3.forceCollide((n) => r(n.weight) + 14));
  const link = s.append("g").selectAll("line").data(edges).join("line").attr("class", "clickable")
    .attr("stroke", "#999").attr("stroke-opacity", 0.5).attr("stroke-width", (e) => w(e.weight))
    .on("click", (_, e) => onPick(e, `${e.source.label} ↔ ${e.target.label}`))
    .call(tooltip, (e) => `${e.source.label} ↔ ${e.target.label}: ${e.weight} trials`);
  const node = s.append("g").selectAll("g").data(nodes).join("g").attr("class", "clickable")
    .on("click", (_, n) => onPick(n, n.label))
    .call(d3.drag()
      .on("start", (ev, n) => { if (!ev.active) sim.alphaTarget(0.3).restart(); n.fx = n.x; n.fy = n.y; })
      .on("drag", (ev, n) => { n.fx = ev.x; n.fy = ev.y; })
      .on("end", (ev, n) => { if (!ev.active) sim.alphaTarget(0); n.fx = null; n.fy = null; }));
  node.append("circle").attr("r", (n) => r(n.weight)).attr("fill", (n) => color(n.group)).attr("stroke", "#fff");
  node.append("text").attr("x", (n) => r(n.weight) + 3).attr("y", 4)
    .text((n) => (n.label.length > 28 ? n.label.slice(0, 27) + "…" : n.label));
  node.call(tooltip, (n) => `${n.label} (${n.group}): ${n.weight} trials`);
  const draw = () => {
    nodes.forEach((n) => { n.x = Math.max(20, Math.min(W - 20, n.x)); n.y = Math.max(20, Math.min(h - 20, n.y)); });
    link.attr("x1", (e) => e.source.x).attr("y1", (e) => e.source.y).attr("x2", (e) => e.target.x).attr("y2", (e) => e.target.y);
    node.attr("transform", (n) => `translate(${n.x},${n.y})`);
  };
  // Settle the layout before the first paint, so the graph does not drift on screen.
  // The simulation runs again only while a node is dragged.
  sim.stop();
  for (let i = 0; i < 300; i++) sim.tick();
  draw();
  sim.on("tick", draw);
  legend(el, groups, color);
  d3.select(el).append("div").attr("class", "hint").text("Node size: trials with the entity. Line width: trials with both. Drag nodes; click for sources.");
}

function metric(el, spec, onPick) {
  const d = spec.data[0];
  const div = d3.select(el).append("div").attr("class", "metric clickable").text(d3.format(",")(d.trial_count));
  div.on("click", () => onPick(d, d.label));
  d3.select(el).append("div").attr("class", "hint").text(`${d.label}. Click for sample sources.`);
}

const RENDERERS = { bar_chart: bar, grouped_bar_chart: grouped, time_series: timeSeries,
                    histogram, scatter_plot: scatter, network_graph: network, metric };

// Only one mark on the page is marked as selected: the one whose sources are open.
function clearSelection() {
  document.querySelectorAll(".chart .selected").forEach((n) => n.classList.remove("selected"));
}

function renderViz(el, spec, onPick) {
  const fn = RENDERERS[spec.type];
  if (!fn) { el.textContent = `No renderer for ${spec.type}`; return; }
  fn(el, spec, onPick);
  el.addEventListener("click", (event) => {
    const mark = event.target.closest(".clickable");
    if (!mark || !el.contains(mark)) return;
    clearSelection();
    mark.classList.add("selected");
  });
}
