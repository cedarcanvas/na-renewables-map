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

const map = new maplibregl.Map({
  container: "map",
  style: "https://tiles.openfreemap.org/styles/positron",
  bounds: [[-125, 24.5], [-66.5, 49.5]], // lower 48; a URL hash overrides this
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
  addPlantLayers();
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
      "circle-color": fuelColor,
      "circle-opacity": 0.08,
      "circle-stroke-color": fuelColor,
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

function renderPanel() {
  const caps = capacityByFuel();
  for (const tr of document.querySelectorAll("#totals tbody tr")) {
    const fuel = tr.dataset.fuel;
    const now = state.gen?.totals[fuel]?.[state.hour];
    tr.querySelector("[data-cap]").textContent = fmt(caps[fuel] / 1000, 1);
    tr.querySelector("[data-now]").textContent = now == null ? "–" : fmt(now / 1000, 1);
    tr.querySelector("[data-pct]").textContent = now == null ? "–" : fmt((100 * now) / caps[fuel]);
  }

  if (state.gen) {
    const t = new Date(state.gen.hours[state.hour]);
    $("#time-local").textContent = t.toLocaleString(undefined, {
      weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short",
    });
    $("#time-utc").textContent = `Hour ending ${state.gen.hours[state.hour].replace("T", " ").replace(":00Z", " UTC")}`;
  }
  renderTop();
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
  $("#sources").innerHTML = `Plants: <a href="https://www.eia.gov/electricity/data/eia860m/">EIA-860M</a> (${plantsSrc}).
    Generation: <a href="https://www.eia.gov/electricity/gridmonitor/">EIA-930</a>${g ? ` via ${g.generation_source}` : ""}.
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
          <dt>Grid region</dt><dd>${p.ba || "–"}</dd>
          <dt>State</dt><dd>${p.state}</dd>
          <dt>Units</dt><dd>${p.units}</dd>
          <dt>Since</dt><dd>${p.op_year ?? "–"}</dd>
        </dl>
        <div class="note">Estimated: a share of ${p.ba || "its region"}'s reported ${p.fuel} total, not metered at the plant.</div>
      </div>`)
    .addTo(map);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

init();
