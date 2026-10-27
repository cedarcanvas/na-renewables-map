const FUELS = {
  wind: { label: "Wind", short: "Wind", color: "#1f9e89" },
  solar: { label: "Solar", short: "Solar", color: "#e9a800" },
  hydro: { label: "Hydro", short: "Hydro", color: "#3a6fd8" },
  geothermal: { label: "Geothermal", short: "Geo", color: "#c2410c" },
};

const state = {
  plants: null,       // GeoJSON FeatureCollection from plants.geojson
  gen: null,          // generation.json, or null before the pipeline has run
  hour: 0,
  visible: new Set(Object.keys(FUELS)),
  playing: null,
};

const $ = (sel) => document.querySelector(sel);
const fmt = (n, digits = 0) => n.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits });

// Circle area ∝ MW, so radius ∝ √MW, growing with zoom.
const radius = (mw) => [
  "interpolate", ["exponential", 1.6], ["zoom"],
  3, ["*", 0.22, ["sqrt", mw]],
  7, ["*", 0.9, ["sqrt", mw]],
  11, ["*", 4, ["sqrt", mw]],
];
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
    region: "#6d28d9", regionCasing: "#ffffff", regionLabel: "#5b21b6", halo: "#ffffff", hover: "#7c3aed",
    noData: "#9aa3ad", particles: [30, 41, 59], cloudBrightness: [0, 0.75],
    flowLabel: [40, 60, 110, 255], flowHalo: [255, 255, 255, 230],
  },
  dark: {
    region: "#a78bfa", regionCasing: "#0c0c0c", regionLabel: "#c4b5fd", halo: "#0c0c0c", hover: "#a78bfa",
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
const OWN_SOURCES = new Set(["plants", "regions", "clouds"]);

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
  paint("region-casing", "line-color", c.regionCasing);
  paint("region-lines", "line-color", c.region);
  paint("region-labels", "text-color", c.regionLabel);
  paint("region-labels", "text-halo-color", c.halo);
  paint("capacity", "circle-color", ringColor());
  paint("capacity", "circle-stroke-color", ringColor());
  paint("clouds", "raster-brightness-min", c.cloudBrightness[0]);
  paint("clouds", "raster-brightness-max", c.cloudBrightness[1]);
  updateFlows();
}

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

  state.plants = await loadJSON("data/plants.geojson");
  try {
    state.gen = await loadJSON("data/generation.json");
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

  buildTotalsTable();
  renderPanel();
  renderSources();

  await mapLoaded;
  await addRegionLayers(); // first, so the plant circles draw on top
  addPlantLayers();
  addFlowOverlay();
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

const REGION_LAYERS = ["region-fill", "region-casing", "region-lines", "region-labels"];
let hoveredRegion = null;

async function addRegionLayers() {
  let regions;
  try {
    regions = await loadJSON("data/regions.geojson");
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
  // A casing in the basemap's own colour keeps the line legible over roads and borders.
  map.addLayer({
    id: "region-casing", type: "line", source: "regions", filter: areas,
    layout: { "line-join": "round" },
    paint: {
      "line-color": c.regionCasing,
      "line-opacity": 0.85,
      "line-width": ["interpolate", ["linear"], ["zoom"], 3, 2.6, 8, 4.4],
    },
  });
  map.addLayer({
    id: "region-lines", type: "line", source: "regions", filter: areas,
    layout: { "line-join": "round" },
    paint: {
      "line-color": c.region,
      "line-opacity": ["case", ["boolean", ["feature-state", "hover"], false], 1, 0.75],
      "line-width": ["interpolate", ["linear"], ["zoom"], 3,
        ["case", ["boolean", ["feature-state", "hover"], false], 2.2, 1.1], 8,
        ["case", ["boolean", ["feature-state", "hover"], false], 3.6, 2.2]],
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
    // A click on a plant opens the plant's popup instead.
    if (map.queryRenderedFeatures(e.point, { layers: ["capacity"] }).length) return;
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
      "circle-radius": radius(["get", "cap"]),
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
      "circle-radius": radius(["get", "mw"]),
      "circle-color": fuelColor,
      "circle-opacity": 0.8,
    },
  });
  applyFuelFilter();

  for (const layer of ["capacity", "output"]) {
    map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
  }
  map.on("click", "capacity", (e) => showPopup(e.features[0].properties.id));
}

// --- Data for the selected hour -------------------------------------------

function mwFor(id) {
  const s = state.gen?.series[id];
  return s ? s[state.hour] : null;
}

function featuresForHour() {
  return {
    type: "FeatureCollection",
    features: state.plants.features.map((f) => ({
      type: "Feature",
      geometry: f.geometry,
      properties: { ...f.properties, mw: mwFor(f.properties.id) ?? -1 },
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

function play() {
  $("#play").textContent = "❚❚";
  if (state.hour >= state.gen.hours.length - 1) setHour(0);
  state.playing = setInterval(() => {
    if (state.hour >= state.gen.hours.length - 1) return stop();
    setHour(state.hour + 1);
  }, 350);
}

function stop() {
  clearInterval(state.playing);
  state.playing = null;
  $("#play").textContent = "▶";
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

function buildTotalsTable() {
  const tbody = $("#totals tbody");
  tbody.innerHTML = "";
  for (const [key, fuel] of Object.entries(FUELS)) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input type="checkbox" checked aria-label="Show ${fuel.label}"></td>
      <td><span class="swatch" style="background:${fuel.color}"></span>${fuel.label}</td>
      <td class="num" data-now></td><td class="num" data-cap></td><td class="num" data-pct></td>`;
    tr.querySelector("input").addEventListener("change", (e) => {
      e.target.checked ? state.visible.add(key) : state.visible.delete(key);
      if (map.getLayer("capacity")) applyFuelFilter();
      renderTop();
    });
    tr.dataset.fuel = key;
    tbody.appendChild(tr);
  }
}

// Capacity (MW) of plants whose region reported this hour; all of it for older data files.
function coveredCap(fuel, caps) {
  return state.gen?.covered_cap?.[fuel]?.[state.hour] ?? caps[fuel];
}

function renderPanel() {
  const caps = capacityByFuel();
  for (const tr of document.querySelectorAll("#totals tbody tr")) {
    const fuel = tr.dataset.fuel;
    const now = state.gen?.totals[fuel]?.[state.hour];
    const covered = coveredCap(fuel, caps);
    const partial = covered < PARTIAL * caps[fuel];
    tr.querySelector("[data-cap]").textContent = fmt(caps[fuel] / 1000, 1);
    // Solar reads slightly negative at night (inverters draw power); show that as zero.
    const mw = now == null ? null : Math.max(now, 0);
    tr.querySelector("[data-now]").textContent = mw == null ? "–" : fmt(mw / 1000, 1) + (partial ? "*" : "");
    // % of the capacity that reported, so a partial hour isn't read as a collapse.
    tr.querySelector("[data-pct]").textContent = mw == null || !covered ? "–" : fmt((100 * mw) / covered);
  }
  renderCoverage(caps);

  if (state.gen) {
    const t = new Date(state.gen.hours[state.hour]);
    $("#time-local").textContent = t.toLocaleString(undefined, {
      weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short",
    });
    $("#time-utc").textContent = `Hour ending ${state.gen.hours[state.hour].replace("T", " ").replace(":00Z", " UTC")}`;
  }
  renderTop();
}

// Live feeds with data at the selected hour, and live feeds that haven't reached it yet.
function liveFeedsNow() {
  const hour = state.gen.hours[state.hour];
  const started = Object.values(state.gen.live ?? {}).filter((l) => l.from <= hour);
  const now = new Set(started.filter((l) => hour <= l.until).map((l) => l.feed));
  const behind = new Set(started.filter((l) => hour > l.until && !now.has(l.feed)).map((l) => l.feed));
  return { now: [...now], behind: [...behind] };
}

function renderCoverage(caps) {
  const el = $("#coverage");
  if (!state.gen) return (el.textContent = "");
  const hour = state.gen.hours[state.hour];
  const { now: feeds, behind } = liveFeedsNow();
  const list = (items) => new Intl.ListFormat("en", { type: "conjunction" }).format(items);
  const lagging = behind.length
    ? ` ${list(behind)} ${behind.length > 1 ? "haven't" : "hasn't"} reached this hour yet.`
    : "";
  let text;
  if (!feeds.length && !behind.length) text = "Source: EIA-930.";
  else if (state.gen.eia_until && hour > state.gen.eia_until)
    text = `Source: live grid-operator feeds (${feeds.join(", ")}).${lagging} Other regions appear once EIA-930 publishes them.`;
  else text = `Source: EIA-930, plus live grid-operator feeds (${feeds.join(", ")}) where EIA hasn't reported yet.${lagging}`;

  const partial = Object.entries(FUELS)
    .filter(([fuel]) => coveredCap(fuel, caps) < PARTIAL * caps[fuel])
    .map(([fuel, f]) => `${fmt((100 * coveredCap(fuel, caps)) / caps[fuel])}% of ${f.label.toLowerCase()}`);
  if (partial.length) text += ` *Regions reporting this hour hold ${partial.join(", ")} capacity.`;
  el.textContent = text;
}

function renderTop() {
  const tbody = $("#top tbody");
  const rows = state.plants.features
    .map((f) => ({ p: f.properties, mw: mwFor(f.properties.id) }))
    .filter((r) => state.visible.has(r.p.fuel) && (state.gen ? r.mw != null : true))
    .sort((a, b) => (state.gen ? b.mw - a.mw : b.p.cap - a.p.cap))
    .slice(0, 30);
  tbody.innerHTML = rows.map(({ p, mw }) => `
    <tr data-id="${p.id}">
      <td title="${escapeHtml(p.name)}">${escapeHtml(p.name)}</td>
      <td><span class="tag" style="background:${FUELS[p.fuel].color}">${FUELS[p.fuel].short}</span></td>
      <td class="num">${mw == null ? "–" : fmt(mw)}</td>
      <td class="num">${fmt(p.cap)}</td>
    </tr>`).join("");
  for (const tr of tbody.querySelectorAll("tr")) tr.addEventListener("click", () => flyTo(tr.dataset.id));
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
  new maplibregl.Popup({ maxWidth: "260px" })
    .setLngLat(f.geometry.coordinates)
    .setHTML(`
      <div class="popup">
        <h3>${escapeHtml(p.name)}</h3>
        <span class="tag" style="background:${FUELS[p.fuel].color}">${p.fuel}</span>
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
      </div>`)
    .addTo(map);
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
