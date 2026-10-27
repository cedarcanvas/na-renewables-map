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
// Plants whose region has no data for the selected hour (mw = -1) are drawn grey.
const NO_DATA = "#9aa3ad";
const ringColor = ["case", ["<", ["get", "mw"], 0], NO_DATA, fuelColor];
// Below this share of a fuel's capacity reporting, its total is marked partial.
const PARTIAL = 0.9;

const map = new maplibregl.Map({
  container: "map",
  style: "https://tiles.openfreemap.org/styles/positron",
  bounds: [[-125, 24.5], [-63, 50.5]], // lower 48 and southern Canada; a URL hash overrides this
  fitBoundsOptions: { padding: 20 },
  hash: true,
  attributionControl: { compact: true },
});
map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), "top-right");
// Captured now: with a cached style, "load" can fire before our data has downloaded.
const mapLoaded = new Promise((resolve) => map.once("load", resolve));

async function loadJSON(url) {
  const r = await fetch(url, { cache: "no-cache" });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

async function init() {
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
}

// --- Québec's flows to its neighbours (deck.gl arcs) -------------------------------

// Representative points, not the actual converter stations or line routes.
const QUEBEC = [-73.57, 45.5]; // Montréal
const NEIGHBOURS = {
  "New England": [-71.6, 42.6],
  "New York": [-75.0, 42.9],
  Ontario: [-75.7, 45.42],
  "New Brunswick": [-66.64, 45.96],
};
let flowOverlay = null;

function addFlowOverlay() {
  if (typeof deck === "undefined" || !state.gen?.flows || !Object.keys(state.gen.flows).length) {
    $("#show-flows").closest("label").hidden = true;
    return;
  }
  flowOverlay = new deck.MapboxOverlay({
    // deck.gl 9.4's interleaved mode (drawing inside MapLibre's WebGL context) blanks
    // MapLibre 5.24's map; its own canvas on top works and the arcs sit above everything.
    interleaved: false,
    layers: flowLayers(),
    getTooltip: ({ object }) => object && {
      text: `${object.mw >= 0 ? `Québec → ${object.name}` : `${object.name} → Québec`}: ${fmt(Math.abs(object.mw))} MW\n` +
        "As reported by Hydro-Québec (includes wheel-through).\nEndpoints are approximate.",
    },
  });
  map.addControl(flowOverlay);
  $("#show-flows").addEventListener("change", updateFlows);
}

function flowLayers() {
  if (!$("#show-flows").checked) return [];
  const flows = Object.entries(state.gen.flows)
    .map(([name, series]) => ({ name, mw: series[state.hour] }))
    .filter((f) => f.mw != null && Math.abs(f.mw) >= 1 && NEIGHBOURS[f.name]);
  const ends = (f) => (f.mw >= 0 ? [QUEBEC, NEIGHBOURS[f.name]] : [NEIGHBOURS[f.name], QUEBEC]);
  return [
    new deck.ArcLayer({
      id: "quebec-flows",
      data: flows,
      getSourcePosition: (f) => ends(f)[0],
      getTargetPosition: (f) => ends(f)[1],
      getSourceColor: [58, 111, 216, 230], // hydro blue at the sending end
      getTargetColor: [58, 111, 216, 90],
      getWidth: (f) => Math.max(1.5, Math.abs(f.mw) / 150),
      widthUnits: "pixels",
      getHeight: 0.35,
      pickable: true,
      updateTriggers: { getSourcePosition: state.hour, getTargetPosition: state.hour, getWidth: state.hour },
    }),
    new deck.TextLayer({
      id: "quebec-flow-labels",
      data: flows,
      getPosition: (f) => NEIGHBOURS[f.name],
      getText: (f) => `${fmt(Math.abs(f.mw))} MW ${f.mw >= 0 ? "from" : "to"} Québec`,
      getSize: 12,
      getColor: [40, 60, 110, 255],
      getPixelOffset: [0, -26], // clear of the basemap's city label
      fontFamily: "system-ui, -apple-system, sans-serif",
      fontWeight: 600,
      outlineWidth: 3,
      outlineColor: [255, 255, 255, 230],
      fontSettings: { sdf: true },
      characterSet: "auto", // the default is ASCII only, which drops the é in Québec
      updateTriggers: { getText: state.hour },
    }),
  ];
}

function updateFlows() {
  flowOverlay?.setProps({ layers: flowLayers() });
}

// --- Grid regions -------------------------------------------------------------

const REGION_LAYERS = ["region-fill", "region-lines", "region-labels"];

async function addRegionLayers() {
  let regions;
  try {
    regions = await loadJSON("data/regions.geojson");
  } catch {
    $(".layers").hidden = true; // not built yet: python -m pipeline.regions
    return;
  }
  const polygons = ["==", ["geometry-type"], "Polygon"];
  const multi = ["==", ["geometry-type"], "MultiPolygon"];
  map.addSource("regions", { type: "geojson", data: regions });
  // Invisible fill, there only so a click inside a region can be detected.
  map.addLayer({ id: "region-fill", type: "fill", source: "regions", filter: ["any", polygons, multi],
    paint: { "fill-opacity": 0 } });
  map.addLayer({
    id: "region-lines", type: "line", source: "regions", filter: ["any", polygons, multi],
    paint: {
      "line-color": "#7b8794",
      "line-opacity": 0.7,
      "line-width": ["interpolate", ["linear"], ["zoom"], 3, 0.6, 8, 1.4],
      "line-dasharray": [3, 2],
    },
  });
  map.addLayer({
    id: "region-labels", type: "symbol", source: "regions", filter: ["==", ["geometry-type"], "Point"],
    layout: {
      "text-field": ["get", "short"],
      "text-font": ["Noto Sans Regular"],
      "text-size": ["interpolate", ["linear"], ["zoom"], 3, 9, 7, 12],
      "text-transform": "uppercase",
      "text-letter-spacing": 0.08,
      "text-max-width": 8,
    },
    paint: { "text-color": "#5b6675", "text-halo-color": "#ffffff", "text-halo-width": 1.5 },
  });

  $("#show-regions").addEventListener("change", (e) => {
    for (const id of REGION_LAYERS) map.setLayoutProperty(id, "visibility", e.target.checked ? "visible" : "none");
  });
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
      "circle-color": ringColor,
      "circle-opacity": 0.08,
      "circle-stroke-color": ringColor,
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
    tr.querySelector("[data-now]").textContent = now == null ? "–" : fmt(now / 1000, 1) + (partial ? "*" : "");
    // % of the capacity that reported, so a partial hour isn't read as a collapse.
    tr.querySelector("[data-pct]").textContent = now == null || !covered ? "–" : fmt((100 * now) / covered);
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
  const lagging = behind.length
    ? ` ${behind.join(" and ")} ${behind.length > 1 ? "haven't" : "hasn't"} reached this hour yet.`
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
    Plants: <a href="https://www.eia.gov/electricity/data/eia860m/">EIA-860M</a> (${plantsSrc}).
    Generation: <a href="https://www.eia.gov/electricity/gridmonitor/">EIA-930</a>${g ? ` via ${g.generation_source}` : ""}.
    ${Object.keys(g?.live ?? {}).length ? `Live fuel mix: ${[...new Set(Object.values(g.live).map((l) => l.feed))].join(", ")},
      via <a href="https://github.com/gridstatus/gridstatus">gridstatus</a>.` : ""}
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

const HOURLY_FEEDS = new Set(["Hydro-Québec", "IESO"]);

function popupNote(p, mw, weighted) {
  const region = p.ba || "its region";
  const live = state.gen.live?.[p.ba];
  if (mw == null && live && state.gen.hours[state.hour] > live.until)
    return `No data for this hour yet: ${live.feed}'s live feed runs a little behind the others.`;
  if (mw == null) return `No data for this hour yet: ${region}'s figures haven't been published.`;
  if (state.gen.metered?.includes(p.id))
    return `Metered: ${live?.feed ?? region}'s hourly reading for this plant (shared by capacity where one meter covers several plants).`;
  const fromLive = live && state.gen.hours[state.hour] >= live.from;
  // The ISOs and BPA post every 5 minutes; Hydro-Québec and IESO post hourly figures.
  const source = !fromLive ? `${region}'s ${p.fuel} total reported to EIA`
    : HOURLY_FEEDS.has(live.feed) ? `${live.feed}'s hourly ${p.fuel} figure`
    : `${live.feed}'s live ${p.fuel} feed (5-minute readings averaged over the hour)`;
  const weighting = weighted ? `, weighted by local ${p.fuel === "wind" ? "wind" : "sunshine"}` : "";
  return `Estimated: a share of ${source}${weighting}, not metered at the plant.`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

init();
