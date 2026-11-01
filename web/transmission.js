// Transmission lines from data/transmission.pmtiles (pipeline/transmission.py): HIFLD
// for the US, OpenStreetMap for Canada. Fine dotted lines, bigger dots for higher
// voltages; the tiles hold 200–299 kV from zoom 5, lower voltages from 6 or 7.
// Loaded before app.js; uses its globals at run time.

const LINE_CLASS_LABELS = ["Under 100 kV", "100–199 kV", "200–299 kV", "300–499 kV", "500 kV and up"];
// White on the dark basemap; on Positron's near-white land white dots vanish, so grey.
const LINE_COLORS = { light: "#8a93a3", dark: "#ffffff" };
// Dot diameter by class at zooms 3, 7 and 10; unknown voltage gets the smallest dots.
const LINE_WIDTHS = { 3: [0.8, 0.9, 1, 1.2, 1.6], 7: [1, 1.2, 1.5, 1.9, 2.5], 10: [1.3, 1.6, 2, 2.6, 3.4] };
// A zero-length dash with round caps is a dot; the gap is in line widths, so it grows with the dot.
const LINE_DOTS = [0, 3];
const LINE_SOURCES = "Transmission lines: HIFLD (US, archived 2025) and © OpenStreetMap contributors (Canada).";

const byClass = (values) => ["match", ["get", "cls"], ...values.flatMap((v, k) => [k, v]), values[0]];
const lineColor = () => LINE_COLORS[resolvedTheme()];
const lineWidth = () => ["interpolate", ["linear"], ["zoom"],
  ...Object.entries(LINE_WIDTHS).flatMap(([z, widths]) => [Number(z), byClass(widths)])];

async function addTransmissionLayers() {
  const url = dataURL("transmission.pmtiles");
  if (typeof pmtiles === "undefined" || !(await fetch(url, { method: "HEAD" }).then((r) => r.ok, () => false))) {
    $("#show-lines").closest("label").hidden = true; // not built yet: python -m pipeline.transmission
    return;
  }
  maplibregl.addProtocol("pmtiles", new pmtiles.Protocol().tile);
  map.addSource("transmission", {
    type: "vector", url: `pmtiles://${url}`,
    attribution: "Lines: HIFLD; © <a href=\"https://www.openstreetmap.org/copyright\">OpenStreetMap</a> contributors",
  });
  // Faint at continental zooms, where the backbone alone is dense; firmer as you zoom in.
  // Lines of unknown voltage stay fainter still.
  const unknown = ["<", ["get", "cls"], 0];
  const opacity = ["interpolate", ["linear"], ["zoom"],
    3, ["case", unknown, 0.25, 0.4], 7, ["case", unknown, 0.35, 0.6], 10, ["case", unknown, 0.45, 0.75]];
  // Under the region outlines and the plants.
  const before = map.getLayer("region-lines") ? "region-lines" : undefined;
  map.addLayer({
    id: "lines", type: "line", source: "transmission", "source-layer": "lines",
    layout: { "line-join": "round", "line-cap": "round" },
    paint: { "line-color": lineColor(), "line-width": lineWidth(), "line-opacity": opacity, "line-dasharray": LINE_DOTS },
  }, before);

  $("#show-lines").addEventListener("change", (e) => {
    map.setLayoutProperty("lines", "visibility", e.target.checked ? "visible" : "none");
    updateMapKey();
  });
  map.on("mousemove", (e) => {
    if (!map.queryRenderedFeatures(e.point, { layers: ["capacity"] }).length) {
      map.getCanvas().style.cursor = lineAt(e.point) ? "pointer" : "";
    }
  });
  map.on("click", (e) => {
    if (map.queryRenderedFeatures(e.point, { layers: ["capacity"] }).length) return; // the plant's popup wins
    const line = lineAt(e.point);
    if (line) showLinePopup(line.properties, e.lngLat);
  });
  $("#sources").insertAdjacentHTML("beforeend", ` ${LINE_SOURCES}`);
}

function linesVisible() {
  return Boolean(map.getLayer("lines")) && map.getLayoutProperty("lines", "visibility") !== "none";
}

// The highest-voltage line within a few pixels: lines are too thin to hit exactly.
function lineAt(point) {
  if (!linesVisible()) return null;
  const box = [[point.x - 4, point.y - 4], [point.x + 4, point.y + 4]];
  const hits = map.queryRenderedFeatures(box, { layers: ["lines"] });
  return hits.sort((a, b) => b.properties.cls - a.properties.cls)[0] ?? null;
}

function showLinePopup(p, lngLat) {
  const voltage = p.kv > 0 ? `${fmt(p.kv)} kV` : p.cls >= 0 ? LINE_CLASS_LABELS[p.cls] : "Not recorded";
  const rows = [
    ["Voltage", `${voltage}${p.dc ? " DC" : ""}`],
    [p.country === "CA" ? "Operator" : "Owner", p.owner],
    ["Between", p.ends],
  ].filter(([, v]) => v);
  new maplibregl.Popup({ maxWidth: "260px" })
    .setLngLat(lngLat)
    .setHTML(`<div class="popup">
      <h3>${escapeHtml(p.name || "Transmission line")}</h3>
      <dl>${rows.map(([k, v]) => `<dt>${k}</dt><dd>${escapeHtml(v)}</dd>`).join("")}</dl>
      <div class="note">${p.country === "CA" ? "From OpenStreetMap." : "From HIFLD's transmission lines (archived 2025)."}</div>
    </div>`)
    .addTo(map);
}

function applyLineTheme() {
  if (map.getLayer("lines")) map.setPaintProperty("lines", "line-color", lineColor());
}

// The key: a dotted sample per class, biggest dots first, then unknown voltage.
function linesKeyHtml() {
  // Zoom 10's dots, so the steps between classes are easy to see.
  const sample = (k, opacity = 0.9) => {
    const w = LINE_WIDTHS[10][k];
    return `<svg width="34" height="10" aria-hidden="true"><line x1="${w}" y1="5" x2="33" y2="5" stroke="${lineColor()}"
      stroke-width="${w}" stroke-linecap="round" stroke-dasharray="0 ${w * LINE_DOTS[1]}" opacity="${opacity}"/></svg>`;
  };
  const rows = LINE_CLASS_LABELS.map((label, k) => [sample(k), label]).reverse();
  rows.push([sample(0, 0.55), "Voltage not recorded"]);
  return `<h3>Transmission lines</h3>
    <ul class="line-key">${rows.map(([svg, label]) => `<li>${svg}${label}</li>`).join("")}</ul>
    <p class="muted">Lines under 300 kV appear as you zoom in. Click a line for its voltage and owner.</p>`;
}
