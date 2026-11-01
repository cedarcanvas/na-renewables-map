const FUELS = {
  wind: { label: "Wind", short: "Wind", color: "#1f9e89" },
  // Validated (dataviz palette checks) for both themes; the brighter #e9a800 failed lightness and contrast.
  solar: { label: "Solar", short: "Solar", color: "#b88300" },
  hydro: { label: "Hydro", short: "Hydro", color: "#3a6fd8" },
  geothermal: { label: "Geothermal", short: "Geo", color: "#c2410c" },
};

const state = {
  plants: null,       // GeoJSON FeatureCollection from plants.geojson
  gen: null,          // generation.json, or null before the pipeline has run
  hour: 0,
  visible: new Set(Object.keys(FUELS)),
  playing: null,
  partialHours: [],   // per hour: true where the reporting fleet is under PARTIAL of capacity
  table: { sort: "mw", dir: "desc", country: "all", query: "" },
};

const $ = (sel) => document.querySelector(sel);
const fmt = (n, digits = 0) => n.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits });

// Plant circles. By default area ∝ MW (radius ∝ √MW), growing with zoom. "Exponential"
// raises MW to a higher power, so differences stand out (areas no longer match MW);
// "normalized" draws every capacity ring the size of a 100 MW plant's and fills it by the
// plant's share of capacity. Both match the default size at 100 MW.
const symbols = { normalized: false, exponential: false };
const RADIUS_PER_ROOT_MW = { 3: 0.22, 7: 0.9, 11: 4 };
const NORMAL_MW = 100;
const radius = (mw) => {
  const p = symbols.exponential ? 0.75 : 0.5;
  const scaled = ["*", NORMAL_MW ** (0.5 - p), ["^", ["max", mw, 0], p]];
  return ["interpolate", ["exponential", 1.6], ["zoom"],
    ...Object.entries(RADIUS_PER_ROOT_MW).flatMap(([z, k]) => [Number(z), ["*", k, scaled]])];
};
const ringRadius = () => radius(symbols.normalized ? NORMAL_MW : ["get", "cap"]);
const fillRadius = () => radius(symbols.normalized
  ? ["*", NORMAL_MW, ["min", ["/", ["get", "mw"], ["get", "cap"]], 1]]
  : ["get", "mw"]);
const fuelColor = ["match", ["get", "fuel"], ...Object.entries(FUELS).flatMap(([k, f]) => [k, f.color]), "#888"];
// Below this share of a fuel's capacity reporting, its total is marked partial.
const PARTIAL = 0.9;

// --- Theme ----------------------------------------------------------------------
// "auto" follows the system setting; a choice of light or dark is remembered per browser.
const BASEMAPS = {
  light: "https://tiles.openfreemap.org/styles/positron",
  dark: "https://tiles.openfreemap.org/styles/dark",
};
const THEME_COLORS = {
  light: {
    region: "#6d28d9", regionLabel: "#5b21b6", halo: "#ffffff", hover: "#7c3aed",
    noData: "#9aa3ad", particles: [30, 41, 59], cloudBrightness: [0, 0.75],
    flowLabel: [40, 60, 110, 255], flowHalo: [255, 255, 255, 230],
  },
  dark: {
    region: "#a78bfa", regionLabel: "#c4b5fd", halo: "#0c0c0c", hover: "#a78bfa",
    noData: "#5b6573", particles: [226, 232, 240], cloudBrightness: [0.3, 0.85],
    flowLabel: [191, 219, 254, 255], flowHalo: [12, 12, 12, 230],
  },
};
const THEME_CHOICES = ["auto", "light", "dark"];
let themeChoice = "auto";
try {
  themeChoice = THEME_CHOICES.includes(localStorage.getItem("theme")) ? localStorage.getItem("theme") : "auto";
} catch {}
const systemDark = window.matchMedia("(prefers-color-scheme: dark)");
const resolvedTheme = () => (themeChoice === "auto" ? (systemDark.matches ? "dark" : "light") : themeChoice);
const colors = () => THEME_COLORS[resolvedTheme()];
// Plants whose region has no data for the selected hour (mw = -1) are drawn grey.
const ringColor = () => ["case", ["<", ["get", "mw"], 0], colors().noData, fuelColor];

function markTheme() {
  if (themeChoice === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = themeChoice;
  $("#theme").textContent = `◐ ${themeChoice[0].toUpperCase()}${themeChoice.slice(1)}`;
}

const map = new maplibregl.Map({
  container: "map",
  style: BASEMAPS[resolvedTheme()],
  bounds: [[-125, 24.5], [-63, 50.5]], // lower 48 and southern Canada; a URL hash overrides this
  fitBoundsOptions: { padding: 20 },
  hash: true,
  attributionControl: { compact: true },
});
map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), "top-right");
// Captured now: with a cached style, "load" can fire before our data has downloaded.
const mapLoaded = new Promise((resolve) => map.once("load", resolve));

// Our sources; everything else in the style belongs to the basemap.
const OWN_SOURCES = new Set(["plants", "regions", "transmission", "clouds"]);

function setTheme(choice) {
  themeChoice = choice;
  try {
    localStorage.setItem("theme", choice);
  } catch {}
  markTheme();
  applyBasemap();
}

// Swap basemaps, carrying our own sources and layers across, then recolour them.
let currentBasemap = BASEMAPS[resolvedTheme()];

function applyBasemap() {
  const url = BASEMAPS[resolvedTheme()];
  if (url === currentBasemap) return applyThemeColors();
  currentBasemap = url;
  map.setStyle(url, {
    transformStyle: (prev, next) => ({
      ...next,
      sources: { ...next.sources, ...Object.fromEntries(Object.entries(prev?.sources ?? {}).filter(([id]) => OWN_SOURCES.has(id))) },
      layers: [...next.layers, ...(prev?.layers ?? []).filter((l) => OWN_SOURCES.has(l.source))],
    }),
  });
  map.once("style.load", applyThemeColors);
}

function applyThemeColors() {
  const c = colors();
  const paint = (layer, prop, value) => map.getLayer(layer) && map.setPaintProperty(layer, prop, value);
  paint("region-fill", "fill-color", c.hover);
  paint("region-lines", "line-color", c.region);
  paint("region-labels", "text-color", c.regionLabel);
  paint("region-labels", "text-halo-color", c.halo);
  paint("capacity", "circle-color", ringColor());
  paint("capacity", "circle-stroke-color", ringColor());
  paint("clouds", "raster-brightness-min", c.cloudBrightness[0]);
  paint("clouds", "raster-brightness-max", c.cloudBrightness[1]);
  updateFlows();
  updateHistoryTheme();
  applyLineTheme();
  weather.roses?.render();
  weather.sunArcs?.render();
  updateMapKey();
}

const DATA_URL = new URL(document.querySelector('meta[name="data-url"]')?.content ?? "data/", location.href);
const dataURL = (path) => new URL(path, DATA_URL).href;

async function loadJSON(url) {
  const r = await fetch(url, { cache: "no-cache" });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

async function init() {
  markTheme();
  $("#theme").addEventListener("click", () =>
    setTheme(THEME_CHOICES[(THEME_CHOICES.indexOf(themeChoice) + 1) % THEME_CHOICES.length]));
  systemDark.addEventListener("change", () => themeChoice === "auto" && applyBasemap());

  state.plants = await loadJSON(dataURL("plants.geojson"));
  try {
    state.gen = await loadJSON(dataURL("generation.json"));
  } catch {
    state.gen = null;
  }

  const slider = $("#slider");
  if (state.gen?.weighting === "weather") $("#weighting").textContent = ", weighted by local wind and sunshine";
  if (state.gen) {
    slider.max = state.gen.hours.length - 1;
    state.hour = state.gen.hours.length - 1;
    slider.value = state.hour;
  } else {
    slider.disabled = true;
    $("#play").disabled = true;
    $("#latest").disabled = true;
    const notice = $("#notice");
    notice.hidden = false;
    notice.innerHTML = "No generation estimates yet, so only capacity is shown. Run <code>python -m pipeline.run</code>.";
  }

  slider.addEventListener("input", () => setHour(+slider.value));
  $("#latest").addEventListener("click", () => {
    stop();
    setHour(state.gen.hours.length - 1);
  });
  $("#play").addEventListener("click", () => (state.playing ? stop() : play()));

  buildKpis();
  setupTable();
  buildHistory();
  renderPanel();
  renderSources();

  await mapLoaded;
  await addRegionLayers(); // first, so the lines and plant circles draw on top
  await addTransmissionLayers();
  addPlantLayers();
  updateMapKey();
  addFlowOverlay();
  addSunArcs();
  await addWeatherLayers();
}

// --- Cross-border flows (deck.gl arcs) ---------------------------------------------

// Representative points, not the actual converter stations or line routes.
const PLACES = {
  "Québec": [-73.57, 45.5],           // Montréal
  "New England": [-71.6, 42.6],
  "New York": [-75.0, 42.9],
  Ontario: [-75.7, 45.42],            // Ottawa
  "New Brunswick": [-66.64, 45.96],   // Fredericton
  "Nova Scotia": [-63.58, 44.65],     // Halifax
  PEI: [-63.13, 46.24],               // Charlottetown
  "Northern Maine": [-68.02, 46.68],  // Presque Isle
  Newfoundland: [-57.95, 48.95],      // Corner Brook, near the Maritime Link's landing
};
// What each source's numbers are, for the tooltip.
const FLOW_NOTES = {
  "Hydro-Québec": "As reported by Hydro-Québec, hourly (includes wheel-through).",
  "NB Power": "NB Power's net scheduled interchange (scheduled, not metered).",
  "NS Power": "NS Power's 2-minute reading, taken just after the hour.",
};
let flowOverlay = null;

function addFlowOverlay() {
  if (typeof deck === "undefined" || !state.gen?.flows?.length) {
    $("#show-flows").closest("label").hidden = true;
    return;
  }
  flowOverlay = new deck.MapboxOverlay({
    // deck.gl 9.4's interleaved mode (drawing inside MapLibre's WebGL context) blanks
    // MapLibre 5.24's map; its own canvas on top works and the arcs sit above everything.
    interleaved: false,
    layers: flowLayers(),
    getTooltip: ({ object }) => object && {
      text: `${object.start} → ${object.end}: ${fmt(Math.abs(object.mw))} MW\n` +
        `${FLOW_NOTES[object.source] ?? `From ${object.source}.`}\nEndpoints are approximate.`,
    },
  });
  map.addControl(flowOverlay);
  $("#show-flows").addEventListener("change", updateFlows);
}

function flowLayers() {
  if (!$("#show-flows").checked) return [];
  // Each arc runs the way the power flows, whichever way the source reports the link.
  const flows = (state.gen.flows ?? [])
    .map((f) => ({ ...f, mw: f.mw[state.hour] }))
    .filter((f) => f.mw != null && Math.abs(f.mw) >= 1 && PLACES[f.from] && PLACES[f.to])
    .map((f) => ({ ...f, start: f.mw >= 0 ? f.from : f.to, end: f.mw >= 0 ? f.to : f.from }));
  const midpoint = (f) => PLACES[f.start].map((v, i) => (v + PLACES[f.end][i]) / 2);
  return [
    new deck.ArcLayer({
      id: "flows",
      data: flows,
      getSourcePosition: (f) => PLACES[f.start],
      getTargetPosition: (f) => PLACES[f.end],
      getSourceColor: [58, 111, 216, 230], // strongest at the sending end
      getTargetColor: [58, 111, 216, 90],
      getWidth: (f) => Math.max(1.5, Math.abs(f.mw) / 150),
      widthUnits: "pixels",
      getHeight: 0.35,
      pickable: true,
      updateTriggers: { getSourcePosition: state.hour, getTargetPosition: state.hour, getWidth: state.hour },
    }),
    new deck.TextLayer({
      id: "flow-labels",
      // Several arcs end in New England, so each label sits at its arc's midpoint.
      data: flows,
      getPosition: midpoint,
      getText: (f) => `${fmt(Math.abs(f.mw))} MW`,
      getSize: 12,
      getColor: colors().flowLabel,
      getPixelOffset: [0, -10],
      fontFamily: "system-ui, -apple-system, sans-serif",
      fontWeight: 600,
      outlineWidth: 3,
      outlineColor: colors().flowHalo,
      fontSettings: { sdf: true },
      characterSet: "auto", // the default is ASCII only, which drops accented letters
      updateTriggers: { getText: state.hour, getPosition: state.hour },
    }),
  ];
}

function updateFlows() {
  flowOverlay?.setProps({ layers: flowLayers() });
}

// --- Grid regions -------------------------------------------------------------

const REGION_LAYERS = ["region-fill", "region-lines", "region-labels"];
let hoveredRegion = null;

async function addRegionLayers() {
  let regions;
  try {
    regions = await loadJSON(dataURL("regions.geojson"));
  } catch {
    $("#show-regions").closest("label").hidden = true; // not built yet: python -m pipeline.regions
    return;
  }
  const areas = ["in", ["geometry-type"], ["literal", ["Polygon", "MultiPolygon"]]];
  const c = colors();
  // promoteId lets a region (outline and label point share a code) carry hover state.
  map.addSource("regions", { type: "geojson", data: regions, promoteId: "code" });
  // A faint tint under the cursor; otherwise invisible, but still there for clicks.
  map.addLayer({ id: "region-fill", type: "fill", source: "regions", filter: areas,
    paint: {
      "fill-color": c.hover,
      "fill-opacity": ["case", ["boolean", ["feature-state", "hover"], false], 0.1, 0],
    } });
  // A hairline, faint until hovered, so the outlines sit behind the plants and weather.
  const hovered = ["boolean", ["feature-state", "hover"], false];
  map.addLayer({
    id: "region-lines", type: "line", source: "regions", filter: areas,
    layout: { "line-join": "round" },
    paint: {
      "line-color": c.region,
      "line-opacity": ["case", hovered, 0.9, 0.35],
      "line-width": ["interpolate", ["linear"], ["zoom"], 3, ["case", hovered, 1.6, 0.6], 8, ["case", hovered, 2.4, 1]],
    },
  });
  map.addLayer({
    id: "region-labels", type: "symbol", source: "regions", filter: ["==", ["geometry-type"], "Point"],
    layout: {
      "text-field": ["get", "short"],
      "text-font": ["Noto Sans Bold"],
      "text-size": ["interpolate", ["linear"], ["zoom"], 3, 10, 7, 14],
      "text-transform": "uppercase",
      "text-letter-spacing": 0.08,
      "text-max-width": 8,
    },
    paint: { "text-color": c.regionLabel, "text-halo-color": c.halo, "text-halo-width": 2 },
  });

  $("#show-regions").addEventListener("change", (e) => {
    for (const id of REGION_LAYERS) map.setLayoutProperty(id, "visibility", e.target.checked ? "visible" : "none");
  });
  map.on("mousemove", "region-fill", (e) => setHoveredRegion(e.features[0]?.properties.code));
  map.on("mouseleave", "region-fill", () => setHoveredRegion(null));
  map.on("click", "region-fill", (e) => {
    // A click on a plant or a line opens that popup instead.
    if (map.queryRenderedFeatures(e.point, { layers: ["capacity"] }).length || lineAt(e.point)) return;
    const seen = new Set();
    const hits = e.features.map((f) => f.properties).filter((p) => !seen.has(p.code) && seen.add(p.code));
    new maplibregl.Popup({ maxWidth: "280px" })
      .setLngLat(e.lngLat)
      .setHTML(`<div class="popup">${hits.map(regionSummary).join("")}</div>`)
      .addTo(map);
  });
}

function setHoveredRegion(code) {
  if (code === hoveredRegion) return;
  if (hoveredRegion) map.setFeatureState({ source: "regions", id: hoveredRegion }, { hover: false });
  hoveredRegion = code ?? null;
  if (hoveredRegion) map.setFeatureState({ source: "regions", id: hoveredRegion }, { hover: true });
}

function regionSummary(r) {
  const g = state.gen;
  const totals = g?.ba_totals?.[r.code] ?? {};
  const caps = {};
  for (const f of state.plants.features) {
    if (f.properties.ba === r.code) caps[f.properties.fuel] = (caps[f.properties.fuel] ?? 0) + f.properties.cap;
  }
  const rows = Object.entries(FUELS)
    .filter(([fuel]) => caps[fuel] || totals[fuel])
    .map(([fuel, f]) => {
      const mw = totals[fuel]?.[state.hour];
      const pct = mw != null && caps[fuel] ? ` (${fmt((100 * Math.max(mw, 0)) / caps[fuel])}%)` : "";
      return `<dt><span class="swatch" style="background:${f.color}"></span>${f.label}</dt>
        <dd>${mw == null ? "no data" : `${fmt(Math.max(mw, 0))} MW${pct}`}</dd>`;
    })
    .join("");
  const live = g?.live?.[r.code];
  const hour = g?.hours[state.hour];
  const source = !g ? ""
    : live && hour >= live.from && hour <= live.until ? `${live.feed} live feed`
    : Object.values(totals).some((s) => s[state.hour] != null) ? "EIA-930"
    : "no data for this hour";
  return `<div class="region">
    <h3>${escapeHtml(r.name)}</h3>
    <div class="muted small">${r.code}${source ? ` · ${source}` : ""}</div>
    ${rows ? `<dl>${rows}</dl>` : `<div class="note">No wind, solar, hydro or geothermal plants mapped here yet.</div>`}
  </div>`;
}

function addPlantLayers() {
  map.addSource("plants", { type: "geojson", data: featuresForHour() });
  map.addLayer({
    id: "capacity",
    type: "circle",
    source: "plants",
    layout: { "circle-sort-key": ["-", ["get", "cap"]] },
    paint: {
      "circle-radius": ringRadius(),
      "circle-color": ringColor(),
      "circle-opacity": 0.08,
      "circle-stroke-color": ringColor(),
      "circle-stroke-opacity": 0.55,
      "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 3, 0.5, 9, 1.2],
    },
  });
  map.addLayer({
    id: "output",
    type: "circle",
    source: "plants",
    filter: [">", ["get", "mw"], 0],
    layout: { "circle-sort-key": ["-", ["get", "mw"]] },
    paint: {
      "circle-radius": fillRadius(),
      "circle-color": fuelColor,
      "circle-opacity": 0.8,
    },
  });
  applyFuelFilter();
  setUpSymbolOptions();

  for (const layer of ["capacity", "output"]) {
    map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
  }
  map.on("click", "capacity", (e) => showPopup(e.features[0].properties.id));
}

const SYMBOL_NOTES = {
  "normalized": "Every plant's ring is the same size; the fill shows its share of capacity.",
  "exponential": "Sizes grow faster than MW (radius ∝ MW^0.75), so differences stand out; areas no longer match MW.",
  "normalized exponential": "Same-size rings; fills grow faster than share of capacity, so differences stand out.",
};

// The Normalized and Exponential checkboxes; the choice is remembered in this browser.
function setUpSymbolOptions() {
  try {
    Object.assign(symbols, JSON.parse(localStorage.getItem("symbols") ?? "{}"));
  } catch { /* storage blocked or malformed: defaults */ }
  for (const key of Object.keys(symbols)) {
    const box = $(`#sym-${key}`);
    box.checked = Boolean(symbols[key]);
    box.addEventListener("change", () => {
      symbols[key] = box.checked;
      try { localStorage.setItem("symbols", JSON.stringify(symbols)); } catch { /* not remembered */ }
      applySymbols();
    });
  }
  applySymbols();
}

function applySymbols() {
  map.setPaintProperty("capacity", "circle-radius", ringRadius());
  map.setPaintProperty("output", "circle-radius", fillRadius());
  const on = Object.keys(symbols).filter((k) => symbols[k]).join(" ");
  $("#sym-note").textContent = SYMBOL_NOTES[on] ?? "";
}

// --- Data for the selected hour -------------------------------------------

function mwFor(id) {
  const s = state.gen?.series[id];
  return s ? s[state.hour] : null;
}

// Only what the plant layers read: each hour's data is copied to MapLibre's worker, and
// copying every property (names, regions, years) blocked the page for ~50 ms an hour.
// Popups look plants up in state.plants by id.
function featuresForHour() {
  return {
    type: "FeatureCollection",
    features: state.plants.features.map(({ geometry, properties: { id, fuel, cap } }) => ({
      type: "Feature",
      geometry,
      properties: { id, fuel, cap, mw: mwFor(id) ?? -1 },
    })),
  };
}

function setHour(i) {
  state.hour = i;
  $("#slider").value = i;
  map.getSource("plants")?.setData(featuresForHour());
  updateFlows();
  updateWeather();
  renderPanel();
}

// Each played hour lasts this long; the wind particles blend between hours over it.
const PLAY_STEP_MS = 400;

function play() {
  $("#play").textContent = "❚❚";
  if (state.hour >= state.gen.hours.length - 1) setHour(0);
  state.playing = setInterval(() => {
    if (state.hour >= state.gen.hours.length - 1) return stop();
    setHour(state.hour + 1);
  }, PLAY_STEP_MS);
  updateWeather(); // start blending toward the next hour now, not after the first step
}

function stop() {
  clearInterval(state.playing);
  state.playing = null;
  $("#play").textContent = "▶";
  updateWeather();
}

function applyFuelFilter() {
  const filter = ["in", ["get", "fuel"], ["literal", [...state.visible]]];
  map.setFilter("capacity", filter);
  map.setFilter("output", ["all", filter, [">", ["get", "mw"], 0]]);
}

// --- Side panel -------------------------------------------------------------

function capacityByFuel() {
  const caps = {};
  for (const f of state.plants.features) caps[f.properties.fuel] = (caps[f.properties.fuel] ?? 0) + f.properties.cap;
  return caps;
}

// Capacity (MW) of plants whose region reported an hour; all of it for older data files.
function coveredCap(fuel, caps, hour = state.hour) {
  return state.gen?.covered_cap?.[fuel]?.[hour] ?? caps[fuel];
}

// Share (%) of reporting capacity generating. Solar reads slightly negative at night
// (inverters draw power), so it's floored at zero.
function shareAt(fuel, caps, hour) {
  const mw = state.gen?.totals[fuel]?.[hour];
  const covered = coveredCap(fuel, caps, hour);
  return mw == null || !covered ? null : (100 * Math.max(mw, 0)) / covered;
}

const listFormat = (items) => new Intl.ListFormat("en", { type: "conjunction" }).format(items);
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const hourLabel = (iso, opts) => new Date(iso).toLocaleString(undefined, opts);

// Stat tiles: one per fuel; each is also that fuel's on/off switch for the map.
function buildKpis() {
  const grid = $("#kpis");
  grid.replaceChildren();
  for (const [key, fuel] of Object.entries(FUELS)) {
    const tile = document.createElement("button");
    tile.type = "button";
    tile.className = "kpi";
    tile.dataset.fuel = key;
    tile.style.setProperty("--fuel", fuel.color);
    tile.setAttribute("aria-pressed", "true");
    tile.innerHTML = `<div class="kpi-label"><span class="swatch" style="background:${fuel.color}"></span>
      <span class="name"></span><span class="partial-tag" hidden>partial</span></div>
      <div class="kpi-value"></div><div class="meter"><span></span></div><div class="kpi-sub"></div>`;
    tile.querySelector(".name").textContent = fuel.label;
    tile.addEventListener("click", () => {
      const on = !state.visible.has(key);
      on ? state.visible.add(key) : state.visible.delete(key);
      tile.setAttribute("aria-pressed", String(on));
      if (map.getLayer("capacity")) applyFuelFilter();
      renderTop();
    });
    grid.append(tile);
  }
}

function renderKpis(caps) {
  for (const tile of document.querySelectorAll(".kpi")) {
    const fuel = tile.dataset.fuel;
    const now = state.gen?.totals[fuel]?.[state.hour];
    const mw = now == null ? null : Math.max(now, 0);
    const covered = coveredCap(fuel, caps);
    const share = shareAt(fuel, caps, state.hour);
    const partial = covered < PARTIAL * caps[fuel];
    tile.querySelector(".kpi-value").innerHTML = mw == null ? "–" : `${fmt(mw / 1000, 1)} <small>GW</small>`;
    tile.querySelector(".meter span").style.width = `${Math.min(share ?? 0, 100)}%`;
    tile.querySelector(".kpi-sub").textContent = share == null
      ? `${fmt(caps[fuel] / 1000, 1)} GW of capacity`
      : `${fmt(share)}% of ${fmt(covered / 1000, covered < 10000 ? 1 : 0)} GW reporting`;
    tile.querySelector(".partial-tag").hidden = !partial;
    tile.title = `${FUELS[fuel].label}: ${fmt(caps[fuel] / 1000, 1)} GW of capacity in all.` +
      (partial ? ` Regions reporting this hour hold ${fmt((100 * covered) / caps[fuel])}% of it.` : "");
  }
}

// Live feeds with data at the selected hour, and live feeds that haven't reached it yet.
function liveFeedsNow() {
  const hour = state.gen.hours[state.hour];
  const started = Object.values(state.gen.live ?? {}).filter((l) => l.from <= hour);
  const now = new Set(started.filter((l) => hour <= l.until).map((l) => l.feed));
  const behind = new Set(started.filter((l) => hour > l.until && !now.has(l.feed)).map((l) => l.feed));
  return { now: [...now].sort(), behind: [...behind].sort() };
}

// Status chips: an icon and a label for each source, so state never rests on colour.
function renderStatus(caps) {
  const ul = $("#feeds");
  ul.replaceChildren();
  if (!state.gen) return ($("#coverage").textContent = "");
  const chip = (ok, text, title) => {
    const li = document.createElement("li");
    li.className = ok ? "ok" : "behind";
    li.title = title;
    const icon = document.createElement("span");
    icon.className = "icon";
    icon.textContent = ok ? "●" : "◐";
    li.append(icon, document.createTextNode(text));
    ul.append(li);
  };
  const hour = state.gen.hours[state.hour];
  const eiaHas = !state.gen.eia_until || hour <= state.gen.eia_until;
  chip(eiaHas, eiaHas ? "EIA-930" : "EIA-930 · pending",
    eiaHas ? "EIA-930's hourly data covers this hour" : "EIA-930 hasn't published this hour; its regions show grey");
  const { now, behind } = liveFeedsNow();
  for (const f of now) chip(true, f, `${f}: live data for this hour`);
  for (const f of behind) chip(false, `${f} · behind`, `${f} hasn't reached this hour yet`);

  const partial = Object.entries(FUELS)
    .filter(([fuel]) => coveredCap(fuel, caps) < PARTIAL * caps[fuel])
    .map(([fuel, f]) => `${fmt((100 * coveredCap(fuel, caps)) / caps[fuel])}% of ${f.label.toLowerCase()}`);
  $("#coverage").textContent = partial.length ? `Regions reporting this hour hold ${listFormat(partial)} capacity.` : "";
}

function renderPanel() {
  const caps = capacityByFuel();
  renderKpis(caps);
  renderStatus(caps);
  if (state.gen) {
    const iso = state.gen.hours[state.hour];
    $("#time-local").textContent = hourLabel(iso, {
      weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short",
    });
    $("#time-utc").textContent = `Hour ending ${iso.replace("T", " ").replace(":00Z", " UTC")}`;
  }
  historyChart?.draw(); // moves the selected-hour marker
  renderTop();
}

// --- 48-hour chart: share of reporting capacity, one line per fuel ------------------

let historyChart = null;

// Under the lines: shading for hours with only part of the fleet reporting, and
// a hairline at the selected hour.
const historyMarks = {
  id: "historyMarks",
  beforeDatasetsDraw(chart) {
    const { ctx, chartArea: area, scales: { x } } = chart;
    const step = x.getPixelForValue(1) - x.getPixelForValue(0);
    ctx.save();
    ctx.fillStyle = cssVar("--hover");
    state.partialHours.forEach((partial, i) => {
      if (partial) ctx.fillRect(x.getPixelForValue(i) - step / 2, area.top, step, area.bottom - area.top);
    });
    const sx = x.getPixelForValue(state.hour);
    ctx.strokeStyle = cssVar("--text");
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(sx, area.top);
    ctx.lineTo(sx, area.bottom);
    ctx.stroke();
    ctx.restore();
  },
};

function historyTheme() {
  const text = cssVar("--muted"), line = cssVar("--line");
  return {
    ticks: text, grid: line,
    tooltip: { backgroundColor: cssVar("--panel"), titleColor: cssVar("--text"), bodyColor: cssVar("--text"), borderColor: line },
    ring: cssVar("--panel"),
  };
}

function buildHistory() {
  const card = $("#history").closest(".card");
  if (!state.gen || typeof Chart === "undefined") return (card.hidden = true);
  const caps = capacityByFuel();
  const fuels = Object.keys(FUELS);
  const total = fuels.reduce((s, f) => s + caps[f], 0);
  state.partialHours = state.gen.hours.map((_, i) =>
    fuels.reduce((s, f) => s + coveredCap(f, caps, i), 0) < PARTIAL * total);

  $("#chart-legend").innerHTML = Object.values(FUELS)
    .map((f) => `<li style="--fuel:${f.color}"><span class="key"></span>${f.label}</li>`).join("");

  const t = historyTheme();
  historyChart = new Chart($("#history"), {
    type: "line",
    data: {
      labels: state.gen.hours,
      datasets: fuels.map((fuel) => ({
        label: FUELS[fuel].label,
        fuel,
        data: state.gen.hours.map((_, i) => shareAt(fuel, caps, i)),
        borderColor: FUELS[fuel].color,
        backgroundColor: FUELS[fuel].color,
        borderWidth: 2,
        borderCapStyle: "round",
        borderJoinStyle: "round",
        pointRadius: 0,
        pointHoverRadius: 4,
        pointHoverBorderWidth: 2,
        pointHoverBorderColor: t.ring,
        tension: 0.25,
      })),
    },
    plugins: [historyMarks],
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "index", intersect: false },
      layout: { padding: { top: 4, right: 4 } },
      scales: {
        x: {
          grid: { display: false },
          border: { color: t.grid },
          ticks: {
            color: t.ticks, autoSkip: false, maxRotation: 0, font: { size: 10.5 },
            // A day name at local midnight and noon in between; the tooltip has every hour.
            callback(_, i) {
              const d = new Date(state.gen.hours[i]);
              if (d.getHours() === 0) return d.toLocaleDateString(undefined, { weekday: "short" });
              if (d.getHours() === 12) return "noon";
              return "";
            },
          },
        },
        y: {
          min: 0, max: 100,
          border: { display: false },
          grid: { color: t.grid, lineWidth: 1 },
          ticks: { color: t.ticks, stepSize: 25, font: { size: 10.5 }, callback: (v) => `${v}%` },
        },
      },
      plugins: {
        legend: { display: false }, // the HTML legend above carries the line keys
        tooltip: {
          ...t.tooltip, borderWidth: 1, padding: 8, usePointStyle: true, boxWidth: 14, boxHeight: 2,
          callbacks: {
            title: (items) => hourLabel(state.gen.hours[items[0].dataIndex],
              { weekday: "short", hour: "numeric", minute: "2-digit", timeZoneName: "short" }),
            // Value first, then the series.
            label: (item) => {
              const mw = state.gen.totals[item.dataset.fuel]?.[item.dataIndex];
              return item.parsed.y == null ? ` – ${item.dataset.label}`
                : ` ${fmt(item.parsed.y)}%  ·  ${fmt(Math.max(mw ?? 0, 0) / 1000, 1)} GW  ${item.dataset.label}`;
            },
            labelPointStyle: () => ({ pointStyle: "line", rotation: 0 }),
          },
        },
      },
      onClick: (event, _, chart) => scrubTo(event, chart),
    },
  });

  // Drag along the chart to move through the hours.
  const canvas = $("#history");
  let dragging = false;
  canvas.addEventListener("pointerdown", (e) => { dragging = true; canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener("pointermove", (e) => dragging && scrubTo(e, historyChart));
  canvas.addEventListener("pointerup", () => (dragging = false));
  buildHistoryTable(caps);
}

function scrubTo(event, chart) {
  const rect = chart.canvas.getBoundingClientRect();
  const x = (event.native ?? event).clientX - rect.left;
  const i = Math.round(chart.scales.x.getValueForPixel(x));
  if (Number.isFinite(i) && i >= 0 && i < state.gen.hours.length && i !== state.hour) {
    stop();
    setHour(i);
  }
}

function updateHistoryTheme() {
  if (!historyChart) return;
  const t = historyTheme(), o = historyChart.options;
  o.scales.x.ticks.color = o.scales.y.ticks.color = t.ticks;
  o.scales.x.border.color = o.scales.y.grid.color = t.grid;
  Object.assign(o.plugins.tooltip, t.tooltip);
  for (const ds of historyChart.data.datasets) ds.pointHoverBorderColor = t.ring;
  historyChart.update("none");
}

// The chart's table twin: every hourly value, readable without hovering.
function buildHistoryTable(caps) {
  const table = $("#history-table");
  const head = `<thead><tr><th>Hour</th>${Object.values(FUELS).map((f) => `<th class="num">${f.label}</th>`).join("")}</tr></thead>`;
  const rows = state.gen.hours.map((iso, i) => {
    const cells = Object.keys(FUELS).map((fuel) => {
      const share = shareAt(fuel, caps, i);
      const mw = state.gen.totals[fuel]?.[i];
      return `<td class="num">${share == null ? "–" : `${fmt(share)}% · ${fmt(Math.max(mw, 0) / 1000, 1)}`}</td>`;
    }).join("");
    const when = hourLabel(iso, { weekday: "short", hour: "numeric" });
    return `<tr><td>${when}${state.partialHours[i] ? " *" : ""}</td>${cells}</tr>`;
  }).reverse().join("");
  table.innerHTML = `${head}<tbody>${rows}</tbody>`;
}

// --- Plant table: sortable, filtered by country and name ----------------------------

const SORT_KEYS = {
  name: (r) => r.p.name.toLowerCase(),
  fuel: (r) => r.p.fuel,
  mw: (r) => r.mw ?? -1,
  cap: (r) => r.p.cap,
  pct: (r) => r.pct ?? -1,
};
const TABLE_ROWS = 50;

function setupTable() {
  for (const button of document.querySelectorAll("#top th button")) {
    button.addEventListener("click", () => {
      const key = button.dataset.sort;
      const t = state.table;
      // Numbers start high-to-low, names A-to-Z; a second click reverses.
      t.dir = t.sort === key ? (t.dir === "asc" ? "desc" : "asc") : (key === "name" || key === "fuel" ? "asc" : "desc");
      t.sort = key;
      renderTop();
    });
  }
  for (const button of document.querySelectorAll(".segmented button")) {
    button.addEventListener("click", () => {
      state.table.country = button.dataset.country;
      for (const b of document.querySelectorAll(".segmented button")) b.setAttribute("aria-pressed", String(b === button));
      renderTop();
    });
  }
  $("#plant-search").addEventListener("input", (e) => {
    state.table.query = e.target.value;
    renderTop();
  });
}

function renderTop() {
  const t = state.table;
  const query = t.query.trim().toLowerCase();
  let rows = state.plants.features
    .map((f) => {
      const p = f.properties;
      const mw = mwFor(p.id);
      return { p, mw, pct: mw == null ? null : (100 * Math.max(mw, 0)) / p.cap };
    })
    .filter((r) => state.visible.has(r.p.fuel)
      && (t.country === "all" || (r.p.country ?? "US") === t.country)
      && (!query || r.p.name.toLowerCase().includes(query))
      && (!state.gen || r.mw != null));
  const key = SORT_KEYS[t.sort];
  rows.sort((a, b) => {
    const [x, y] = [key(a), key(b)];
    return (x < y ? -1 : x > y ? 1 : 0) * (t.dir === "asc" ? 1 : -1);
  });
  const total = rows.length;
  rows = rows.slice(0, TABLE_ROWS);

  const tbody = $("#top tbody");
  tbody.replaceChildren(...rows.map(({ p, mw, pct }) => {
    const tr = document.createElement("tr");
    tr.dataset.id = p.id;
    tr.tabIndex = 0;
    const name = document.createElement("td");
    name.textContent = p.name; // names come from outside data: never innerHTML
    name.title = p.name;
    const fuel = document.createElement("td");
    fuel.innerHTML = `<span class="fuel-key"><span class="swatch" style="background:${FUELS[p.fuel].color}"></span>${FUELS[p.fuel].short}</span>`;
    const num = (v, digits = 0) => {
      const td = document.createElement("td");
      td.className = "num";
      td.textContent = v == null ? "–" : fmt(v, digits);
      return td;
    };
    tr.append(name, fuel, num(mw == null ? null : Math.max(mw, 0)), num(p.cap), num(pct));
    tr.addEventListener("click", () => flyTo(p.id));
    tr.addEventListener("keydown", (e) => e.key === "Enter" && flyTo(p.id));
    return tr;
  }));
  for (const th of document.querySelectorAll("#top th")) {
    const active = th.querySelector("button")?.dataset.sort === t.sort;
    active ? th.setAttribute("aria-sort", t.dir === "asc" ? "ascending" : "descending") : th.removeAttribute("aria-sort");
  }
  $("#top-count").textContent = total > TABLE_ROWS
    ? `Showing the top ${TABLE_ROWS} of ${fmt(total)} plants. Click a row to find it on the map.`
    : `${fmt(total)} plants. Click a row to find it on the map.`;
}

function renderSources() {
  const g = state.gen;
  const plantsSrc = state.plants.source?.split("/").pop() ?? "EIA-860M";
  const updated = g?.generated_at && new Date(g.generated_at).toLocaleString(undefined, {
    weekday: "short", hour: "numeric", minute: "2-digit", timeZoneName: "short",
  });
  $("#sources").innerHTML = `${updated ? `<strong>Updated ${updated}.</strong> ` : ""}
    Plants: <a href="https://www.eia.gov/electricity/data/eia860m/">EIA-860M</a> (${plantsSrc}); Canada: NRCan,
    NACEI, and <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> (ODbL) for Alberta's solar.
    Generation: <a href="https://www.eia.gov/electricity/gridmonitor/">EIA-930</a>${g ? ` via ${g.generation_source}` : ""}.
    ${Object.keys(g?.live ?? {}).length ? `Live data from the operators: ${[...new Set(Object.values(g.live).map((l) => l.feed))].join(", ")}
      (the US ISOs via <a href="https://github.com/gridstatus/gridstatus">gridstatus</a>); flows also from NB Power.` : ""}
    ${g?.weighting === "weather" ? `Weather: <a href="https://open-meteo.com/">Open-Meteo</a> (CC BY 4.0).` : ""}
    Grid regions: HIFLD Control Areas (archived on <a href="https://source.coop/seerai/hifld">source.coop</a>),
    <a href="https://www.naturalearthdata.com/">Natural Earth</a>.
    Basemap: <a href="https://openfreemap.org">OpenFreeMap</a> © OpenMapTiles © OpenStreetMap contributors.`;
}

// --- Popups -----------------------------------------------------------------

function findPlant(id) {
  return state.plants.features.find((f) => f.properties.id === id);
}

function flyTo(id) {
  const f = findPlant(id);
  map.flyTo({ center: f.geometry.coordinates, zoom: Math.max(map.getZoom(), 8) });
  map.once("moveend", () => showPopup(id));
}

function showPopup(id) {
  const f = findPlant(id);
  const p = f.properties;
  const mw = mwFor(id);
  const cf = mw == null ? null : (100 * mw) / p.cap;
  const local = state.gen?.weather?.[id]?.[state.hour];
  const weighted = state.gen?.weighting === "weather" && local != null;
  const weatherRow = local == null ? ""
    : p.fuel === "wind" ? `<dt>Wind at 100 m</dt><dd>${fmt(local, 1)} m/s</dd>`
    : `<dt>Sunshine</dt><dd>${fmt(local)} W/m²</dd>`;
  const popup = new maplibregl.Popup({ maxWidth: "260px" })
    .setLngLat(f.geometry.coordinates)
    .setHTML(`
      <div class="popup">
        <h3>${escapeHtml(p.name)}</h3>
        <span class="fuel-key"><span class="swatch" style="background:${FUELS[p.fuel].color}"></span>${FUELS[p.fuel].label}</span>
        <dl>
          <dt>Est. output</dt><dd>${mw == null ? "no data" : `${fmt(mw, mw < 10 ? 1 : 0)} MW`}</dd>
          <dt>Capacity</dt><dd>${fmt(p.cap, 1)} MW</dd>
          <dt>Of capacity</dt><dd>${cf == null ? "–" : `${fmt(cf)}%`}</dd>
          ${weatherRow}
          <dt>Grid region</dt><dd>${p.ba || "–"}</dd>
          <dt>State</dt><dd>${p.state}</dd>
          <dt>Units</dt><dd>${p.units}</dd>
          <dt>Since</dt><dd>${p.op_year ?? "–"}</dd>
        </dl>
        <div class="note">${popupNote(p, mw, weighted)}</div>
        ${p.fuel === "solar" ? sunChartHtml(f) : ""}
      </div>`)
    .addTo(map);
  if (p.fuel === "solar") wireSunChart(popup.getElement(), f);
}

// The ISOs and BPA post every 5 minutes; Hydro-Québec and IESO post hourly figures;
// AESO, NS Power and SaskPower show only the current moment, read just after each hour.
const HOURLY_FEEDS = new Set(["Hydro-Québec", "IESO"]);
const SNAPSHOT_FEEDS = new Set(["AESO", "NS Power", "SaskPower"]);

function popupNote(p, mw, weighted) {
  const region = p.ba || "its region";
  const live = state.gen.live?.[p.ba];
  if (mw == null && live && state.gen.hours[state.hour] > live.until)
    return `No data for this hour yet: ${live.feed}'s live feed runs a little behind the others.`;
  if (state.gen.unreported?.includes(p.id))
    return `Not reported: ${live?.feed ?? region} publishes output only for plants on its transmission grid, and most smaller farms feed local distribution grids instead.`;
  if (mw == null) return `No data for this hour yet: ${region}'s figures haven't been published.`;
  if (state.gen.metered?.includes(p.id)) {
    const reading = SNAPSHOT_FEEDS.has(live?.feed) ? "reading for this plant, taken just after the hour"
      : "hourly reading for this plant";
    return `Metered: ${live?.feed ?? region}'s ${reading} (shared by capacity where one meter covers several plants).`;
  }
  const fromLive = live && state.gen.hours[state.hour] >= live.from;
  const source = !fromLive ? `${region}'s ${p.fuel} total reported to EIA`
    : live.feed === "SaskPower" ? `SaskPower's latest published ${p.fuel} figure (a summary of the previous day)`
    : SNAPSHOT_FEEDS.has(live.feed) ? `${live.feed}'s ${p.fuel} reading, taken just after the hour`
    : HOURLY_FEEDS.has(live.feed) ? `${live.feed}'s hourly ${p.fuel} figure`
    : `${live.feed}'s live ${p.fuel} feed (5-minute readings averaged over the hour)`;
  const weighting = weighted ? `, weighted by local ${p.fuel === "wind" ? "wind" : "sunshine"}` : "";
  return `Estimated: a share of ${source}${weighting}, not metered at the plant.`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

init();
