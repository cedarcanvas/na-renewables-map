// The separate grids: North America runs as a few AC grids (Eastern, Western, Texas, Québec,
// plus Alaska, Hawaii and Newfoundland) that are tied together only by DC links. This view
// colours regions, outlines and transmission lines by grid, lets you pick grids and the
// operators inside them (the rest fade), and marks the ties between grids with the power
// crossing them. Grid and operator come from pipeline/grids.py; the ties from EIA-930
// interchange. Loaded after transmission.js; uses app.js's globals at run time.

const GRID_COLORS = {
  east: "#6b5bd2", west: "#d6336c", texas: "#7c8a1e", quebec: "#0891b2",
  alaska: "#64748b", hawaii: "#b45309", newfoundland: "#b06ad4",
};
const GRID_OTHER = "#8a93a3";
const GRID_NOTES = {
  east: "Everything from the Rockies' eastern edge to the Atlantic, plus Ontario, the Prairies and the Maritimes. One synchronous grid: every plant swings at the same 60 Hz.",
  west: "The Rockies to the Pacific, from Alberta and B.C. to Baja. SPP's western expansion joined it in 2026.",
  texas: "ERCOT keeps clear of federal rules on purpose. Only a few DC ties join it to its neighbours.",
  quebec: "Hydro-Québec runs asynchronously from its neighbours, linked by DC lines and back-to-back converters.",
  alaska: "The Railbelt is an island: no line joins it to Canada or the rest of the US.",
  hawaii: "Each island has its own grid; shown here as one.",
  newfoundland: "The island grid, joined to Nova Scotia since 2017 by a DC cable (the Maritime Link).",
};

const gridSel = { grids: new Set(), ops: new Set() };
let gridInfo = { grids: [], regionGrid: {}, regionName: {}, points: {} };

const gridModeOn = () => Boolean($("#show-grids")?.checked);
const gridColorMatch = (fallback = GRID_OTHER) => ["match", ["get", "grid"],
  ...Object.entries(GRID_COLORS).flat(), fallback];

// Grids that stand out: a picked grid, or the grid of a picked operator. None picked: all do.
function gridsInFocus() {
  const g = new Set(gridSel.grids);
  for (const grid of gridInfo.grids) {
    if (grid.operators.some((o) => gridSel.ops.has(o.key))) g.add(grid.key);
  }
  return g;
}
function regionsInFocus() {
  const out = new Set();
  for (const grid of gridInfo.grids) {
    for (const o of grid.operators) {
      if (gridSel.grids.has(grid.key) || gridSel.ops.has(o.key)) o.regions.forEach((r) => out.add(r));
    }
  }
  return out;
}
const anyFocus = () => gridSel.grids.size + gridSel.ops.size > 0;

async function addGridLayers() {
  const gen = state.gen;
  if (!gen?.grids?.length) {
    $("#grid-panel").hidden = true; // an older generation.json
    return;
  }
  let outlines, regions;
  try {
    [outlines, regions] = await Promise.all([loadData("grids.geojson"), loadData("regions.geojson")]);
  } catch {
    $("#grid-panel").hidden = true;
    return;
  }
  gridInfo.grids = gen.grids;
  for (const g of gen.grids) for (const o of g.operators) for (const r of o.regions) gridInfo.regionGrid[r] = g.key;
  for (const f of regions.features) {
    const p = f.properties;
    gridInfo.regionName[p.code] = p.short || p.name;
    if (f.geometry.type === "Point") gridInfo.points[p.code] = f.geometry.coordinates;
  }

  map.addSource("grids", { type: "geojson", data: outlines });
  const above = map.getLayer("lines") ? "lines" : map.getLayer("region-lines") ? "region-lines" : undefined;
  // Tint of each grid, and a firm outline: the edge is the point.
  map.addLayer({ id: "grid-fill", type: "fill", source: "grids", layout: { visibility: "none" },
    paint: { "fill-color": gridColorMatch(), "fill-opacity": 0.1 } }, above);
  map.addLayer({ id: "grid-outline", type: "line", source: "grids", layout: { visibility: "none", "line-join": "round" },
    paint: { "line-color": gridColorMatch(), "line-width": 2, "line-opacity": 0.85 } }, above);
  // Picked operators' regions, a stronger tint.
  map.addLayer({ id: "operator-fill", type: "fill", source: "regions", layout: { visibility: "none" },
    filter: ["all", ["!=", ["geometry-type"], "Point"], ["in", ["get", "code"], ["literal", []]]],
    paint: { "fill-color": gridColorMatch(), "fill-opacity": 0.22 } }, above);

  buildGridPanel();
  $("#show-grids").addEventListener("change", () => { applyGridMode(); updateFlows(); });
  applyGridMode();
}

// --- The panel -------------------------------------------------------------------

function buildGridPanel() {
  const el = $("#grid-groups");
  el.innerHTML = gridInfo.grids.map((g) => `
    <div class="grid-group">
      <button type="button" class="grid-chip" data-grid="${g.key}" aria-pressed="false" title="${escapeHtml(GRID_NOTES[g.key] ?? "")}">
        <span class="swatch" style="background:${GRID_COLORS[g.key]}"></span>${escapeHtml(g.name)}
      </button>
      ${g.operators.length > 1 ? `<div class="grid-ops">${g.operators.map((o) => `
        <button type="button" class="grid-chip op" data-op="${o.key}" data-grid="${g.key}" aria-pressed="false">${escapeHtml(o.name)}</button>`).join("")}</div>` : ""}
    </div>`).join("");
  el.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    const toggle = (set, key) => (set.has(key) ? set.delete(key) : set.add(key));
    if (b.dataset.op) toggle(gridSel.ops, b.dataset.op);
    else {
      toggle(gridSel.grids, b.dataset.grid);
      // Picking a whole grid replaces picks of single operators inside it.
      const grid = gridInfo.grids.find((g) => g.key === b.dataset.grid);
      grid.operators.forEach((o) => gridSel.ops.delete(o.key));
    }
    applyGridMode();
    updateFlows();
  });
  $("#grid-clear").addEventListener("click", () => {
    gridSel.grids.clear();
    gridSel.ops.clear();
    applyGridMode();
    updateFlows();
  });
}

function refreshGridPanel() {
  const focus = gridsInFocus();
  for (const b of $("#grid-groups").querySelectorAll("button")) {
    const on = b.dataset.op ? gridSel.ops.has(b.dataset.op) : gridSel.grids.has(b.dataset.grid);
    b.setAttribute("aria-pressed", String(on));
    b.closest(".grid-group").classList.toggle("dim", anyFocus() && !focus.has(b.dataset.grid));
  }
  $("#grid-clear").hidden = !anyFocus();
  const note = $("#grid-note");
  const picked = gridInfo.grids.filter((g) => gridSel.grids.has(g.key));
  note.textContent = picked.length ? picked.map((g) => `${g.name}: ${GRID_NOTES[g.key] ?? ""}`).join(" ")
    : "Each colour is a grid. Inside one, power flows freely; between two, only through the marked ties. Pick grids or operators to follow one.";
}

// --- The map ---------------------------------------------------------------------

function applyGridMode() {
  const on = gridModeOn();
  $("#grid-body").hidden = !on;
  const vis = on ? "visible" : "none";
  const focus = [...gridsInFocus()];
  const dimmed = ["!", ["in", ["get", "grid"], ["literal", focus]]];
  const set = (id, prop, v) => map.getLayer(id) && map.setPaintProperty(id, prop, v);
  for (const id of ["grid-fill", "grid-outline", "operator-fill"]) map.getLayer(id) && map.setLayoutProperty(id, "visibility", vis);
  if (on) {
    refreshGridPanel();
    const hasFocus = anyFocus();
    const faded = (full, low) => (hasFocus ? ["case", dimmed, low, full] : full);
    set("grid-fill", "fill-opacity", faded(0.1, 0.015));
    set("grid-outline", "line-opacity", faded(0.95, 0.25));
    set("grid-outline", "line-width", hasFocus ? ["case", dimmed, 1.5, 3.2] : 2.4);
    map.setFilter("operator-fill", ["all", ["!=", ["geometry-type"], "Point"],
      ["in", ["get", "code"], ["literal", [...regionsInFocus()]]]]);
    set("operator-fill", "fill-color", gridColorMatch());
    set("operator-fill", "fill-opacity", hasFocus ? 0.24 : 0);
  }
  styleGridLines(on);
  dimPlants(on);
}

// The transmission lines in their grid's colour; the unpicked fade to a whisper.
function styleGridLines(on) {
  if (!map.getLayer("lines")) return;
  if (!on) {
    map.setPaintProperty("lines", "line-color", lineColor());
    map.setPaintProperty("lines", "line-opacity", ["interpolate", ["linear"], ["zoom"],
      3, ["case", ["<", ["get", "cls"], 0], 0.25, 0.4], 7, ["case", ["<", ["get", "cls"], 0], 0.35, 0.6],
      10, ["case", ["<", ["get", "cls"], 0], 0.45, 0.75]]);
    return;
  }
  const focus = [...gridsInFocus()];
  const dimmed = anyFocus() ? ["!", ["in", ["get", "grid"], ["literal", focus]]] : false;
  const unknown = ["<", ["get", "cls"], 0];
  const o = (hi, lo, faint) => (dimmed ? ["case", dimmed, faint, unknown, lo, hi] : ["case", unknown, lo, hi]);
  map.setPaintProperty("lines", "line-color", gridColorMatch());
  map.setPaintProperty("lines", "line-opacity", ["interpolate", ["linear"], ["zoom"],
    3, o(0.7, 0.4, 0.07), 7, o(0.85, 0.55, 0.1), 10, o(0.95, 0.7, 0.12)]);
}
function gridLineColor() { return gridColorMatch(); }

// Plants outside the picked grids fade, so the ones you follow stand out.
function dimPlants(on) {
  const regions = [...regionsInFocus()];
  const outside = ["!", ["in", ["get", "ba"], ["literal", regions]]];
  const fade = on && anyFocus();
  const set = (id, prop, v) => map.getLayer(id) && map.setPaintProperty(id, prop, v);
  set("capacity", "circle-opacity", fade ? ["case", outside, 0.02, 0.08] : 0.08);
  set("capacity", "circle-stroke-opacity", fade ? ["case", outside, 0.12, 0.7] : 0.55);
  set("output", "circle-opacity", fade ? ["case", outside, 0.15, 0.85] : 0.8);
}

// --- Ties between grids (deck.gl, drawn with the flow arcs) ------------------------------

function gridTies() {
  if (!gridModeOn() || !state.gen?.ties?.length) return [];
  const focus = gridsInFocus();
  return state.gen.ties.map((t) => {
    const mw = t.mw[state.hour];
    const [from, to] = mw != null && mw < 0 ? [t.b, t.a] : [t.a, t.b];
    const ga = gridInfo.regionGrid[t.a], gb = gridInfo.regionGrid[t.b];
    return { ...t, tie: true, mw, from, to, ga, gb,
      dim: anyFocus() && !focus.has(ga) && !focus.has(gb) };
  });
}

function tieLayers() {
  const ties = gridTies();
  if (!ties.length) return [];
  const hasMw = (t) => t.mw != null && Math.abs(t.mw) >= 1;
  const rgb = (hex, a) => [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16), a];
  const live = ties.filter((t) => hasMw(t) && gridInfo.points[t.from] && gridInfo.points[t.to]);
  const dark = resolvedTheme() === "dark";
  return [
    // The power crossing: sender's colour at its end, receiver's at the other.
    new deck.ArcLayer({
      id: "tie-arcs", data: live,
      getSourcePosition: (t) => gridInfo.points[t.from], getTargetPosition: (t) => gridInfo.points[t.to],
      getSourceColor: (t) => rgb(GRID_COLORS[gridInfo.regionGrid[t.from]] ?? GRID_OTHER, t.dim ? 40 : 230),
      getTargetColor: (t) => rgb(GRID_COLORS[gridInfo.regionGrid[t.to]] ?? GRID_OTHER, t.dim ? 20 : 120),
      getWidth: (t) => Math.max(1.5, Math.abs(t.mw) / 120), widthUnits: "pixels", getHeight: 0.3,
      updateTriggers: { getSourcePosition: state.hour, getTargetPosition: state.hour, getWidth: state.hour, getSourceColor: [state.hour, gridSel.grids.size, gridSel.ops.size] },
    }),
    // The converter station: a ring at the border, filled in when something is flowing.
    new deck.ScatterplotLayer({
      id: "tie-sites", data: ties, pickable: true,
      getPosition: (t) => t.at,
      getRadius: (t) => (hasMw(t) ? 6 + Math.sqrt(Math.abs(t.mw)) / 2.2 : 5), radiusUnits: "pixels",
      getFillColor: (t) => (hasMw(t) ? [...(dark ? [255, 255, 255] : [30, 41, 59]), t.dim ? 60 : 235] : [0, 0, 0, 0]),
      getLineColor: (t) => [...(dark ? [255, 255, 255] : [30, 41, 59]), t.dim ? 70 : 255],
      stroked: true, lineWidthUnits: "pixels", getLineWidth: 2,
      updateTriggers: { getRadius: state.hour, getFillColor: [state.hour, gridSel.grids.size, gridSel.ops.size, resolvedTheme()], getLineColor: [gridSel.grids.size, gridSel.ops.size, resolvedTheme()] },
    }),
  ];
}

function tieTooltip(t) {
  const name = (c) => gridInfo.regionName[c] ?? c;
  const grid = (c) => gridInfo.grids.find((g) => g.key === gridInfo.regionGrid[c])?.name ?? "";
  if (t.mw == null || Math.abs(t.mw) < 1) {
    return { text: `${name(t.a)} (${grid(t.a)}) and ${name(t.b)} (${grid(t.b)})\nTie between two separate grids. ` +
      "No reading for this hour (EIA-930 trails by about a day)." };
  }
  return { text: `${name(t.from)} → ${name(t.to)}: ${fmt(Math.abs(t.mw))} MW\n` +
    `A tie between two separate grids (${grid(t.from)} to ${grid(t.to)}): power crosses only as scheduled.\n` +
    "EIA-930 net interchange; the marker sits mid-border, not at the converter." };
}
