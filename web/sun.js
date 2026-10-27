// Sunshine at solar plants against a clear sky, in two views: sun arcs on the map and a
// 48-hour chart in each solar farm's popup. The sunshine is Open-Meteo's (generation.json
// "weather" for solar plants: W/m², the mean of the hour ending at each slider hour); the
// clear sky is computed here from the sun's position. Loaded after weather.js (SiteGlyphs)
// and before app.js; uses app.js's globals at run time.

const RAD = Math.PI / 180;
const HOUR_MS = 3600e3;

// The sun's altitude and azimuth (degrees; azimuth clockwise from north) at a UTC time,
// from NOAA's general solar position equations: good to a few tenths of a degree.
function sunPosition(ms, lng, lat) {
  const d = new Date(ms);
  const days = (ms - Date.UTC(d.getUTCFullYear(), 0, 1)) / 86400e3; // since Jan 1, 00:00 UTC
  const g = (2 * Math.PI / 365) * (days - 0.5);                      // fractional year, radians
  const eqtime = 229.18 * (0.000075 + 0.001868 * Math.cos(g) - 0.032077 * Math.sin(g)
    - 0.014615 * Math.cos(2 * g) - 0.040849 * Math.sin(2 * g));      // minutes
  const decl = 0.006918 - 0.399912 * Math.cos(g) + 0.070257 * Math.sin(g) - 0.006758 * Math.cos(2 * g)
    + 0.000907 * Math.sin(2 * g) - 0.002697 * Math.cos(3 * g) + 0.00148 * Math.sin(3 * g);
  const utcMinutes = (ms - Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate())) / 60e3;
  const hourAngle = ((utcMinutes + eqtime + 4 * lng) / 4 - 180) * RAD;
  const phi = lat * RAD;
  const cosZ = Math.sin(phi) * Math.sin(decl) + Math.cos(phi) * Math.cos(decl) * Math.cos(hourAngle);
  const azimuth = (Math.atan2(Math.sin(hourAngle),
    Math.cos(hourAngle) * Math.sin(phi) - Math.tan(decl) * Math.cos(phi)) / RAD + 180) % 360;
  return { cosZ, altitude: 90 - Math.acos(Math.max(-1, Math.min(1, cosZ))) / RAD, azimuth };
}

// Haurwitz's clear-sky model: sunshine on flat ground under a cloudless sky, W/m². It
// ignores elevation and haze, so a high, dry site on a clear day can beat it a little.
const clearSkyGhi = (cosZ) => (cosZ > 0 ? 1098 * cosZ * Math.exp(-0.057 / cosZ) : 0);

// Clear-sky sunshine for each slider hour, averaged over the hour ENDING at its
// timestamp from six samples, as Open-Meteo's figures are. Computed at the centre of the
// plant's 0.5° weather cell (where its sunshine figures come from) and cached per cell.
const SAMPLES_PER_HOUR = 6;
const clearSkyCache = new Map();

function clearSkyHours(lng, lat) {
  const cellLng = Math.round(lng * 2) / 2, cellLat = Math.round(lat * 2) / 2;
  const key = `${cellLat},${cellLng}`;
  if (!clearSkyCache.has(key)) {
    clearSkyCache.set(key, state.gen.hours.map((iso) => {
      const start = Date.parse(iso) - HOUR_MS;
      let sum = 0;
      for (let k = 0; k < SAMPLES_PER_HOUR; k++) {
        sum += clearSkyGhi(sunPosition(start + ((k + 0.5) / SAMPLES_PER_HOUR) * HOUR_MS, cellLng, cellLat).cosZ);
      }
      return sum / SAMPLES_PER_HOUR;
    }));
  }
  return clearSkyCache.get(key);
}

// Hours whose clear sky gives less than this (around sunrise and sunset) are too dim to
// judge cloud by: a few W/m² either way would swing the share.
const MIN_CLEAR_SKY = 20;

// Each hour's sunshine as a share of a clear sky's, for a region's plants
// (capacity-weighted, so big farms count for more); null where it's dark or there's no data.
function clearness(plants) {
  return state.gen.hours.map((_, i) => {
    let got = 0, possible = 0, cap = 0;
    for (const f of plants) {
      const ghi = state.gen.weather?.[f.properties.id]?.[i];
      if (ghi == null) continue;
      got += f.properties.cap * ghi;
      possible += f.properties.cap * clearSkyHours(...f.geometry.coordinates)[i];
      cap += f.properties.cap;
    }
    return cap && possible / cap >= MIN_CLEAR_SKY ? got / possible : null;
  });
}

// --- Sun arcs on the map ------------------------------------------------------------
// For a region's solar farms: the sun's path across the sky for each day in the slider's
// 48 hours, drawn at its true compass bearing (east to west through the south), newest
// day outside. Each hour is shaded by how much of a clear sky's sunshine reached the
// farms; the needle points at the sun in the selected hour.

const SUN_CLASSES = [0.25, 0.5, 0.75];     // share of a clear sky's sunshine
const SUN_CLASS_LABELS = ["< 25", "25–50", "50–75", "≥ 75"];
// One hue, light→dark (dark: dim→bright); validated as ordinal ramps against each theme.
const SUN_RAMPS = {
  light: ["#d09a1c", "#a87800", "#7a5700", "#4d3700"],
  dark: ["#6e4f0c", "#a07410", "#d6a227", "#f6d77c"],
};
const SUN_STEP_MS = 10 * 60e3;

// Sunrise-to-sunset stretches inside a run of hours (ms timestamps, each the END of its
// hour), to the nearest 10 minutes.
function daylightRuns(lng, lat, ends) {
  const runs = [];
  let run = null;
  for (let t = ends[0] - HOUR_MS; t <= ends.at(-1); t += SUN_STEP_MS) {
    if (sunPosition(t, lng, lat).altitude > 0) {
      if (!run) runs.push((run = { rise: t, set: t }));
      run.set = t;
    } else run = null;
  }
  return runs;
}

// The sun's bearing from time a to b, every 5 minutes, with extra points wherever it
// swings fast (a high noon sun) so the arc follows the circle rather than cutting a chord.
function sunBearings(lng, lat, a, b) {
  const raw = [];
  for (let t = a; ; t = Math.min(t + 5 * 60e3, b)) {
    raw.push(sunPosition(t, lng, lat).azimuth);
    if (t >= b) break;
  }
  const out = [raw[0]];
  for (let k = 1; k < raw.length; k++) {
    const turn = ((raw[k] - raw[k - 1] + 540) % 360) - 180;
    const n = Math.max(1, Math.ceil(Math.abs(turn) / 5));
    for (let j = 1; j <= n; j++) out.push(raw[k - 1] + (turn * j) / n);
  }
  return out;
}

// An SVG path for a band between radii r0 and r1 along the given bearings.
function bandPath(c, bearings, r0, r1) {
  const p = (deg, r) => `${(c + r * Math.cos((deg - 90) * RAD)).toFixed(1)},${(c + r * Math.sin((deg - 90) * RAD)).toFixed(1)}`;
  return `M${bearings.map((d) => p(d, r1)).join("L")}L${[...bearings].reverse().map((d) => p(d, r0)).join("L")}Z`;
}

function sunArcSvg(site, size) {
  return drawSunArc(site.lng, site.lat, state.gen.hours.map(Date.parse), clearness(site.plants), state.hour, size);
}

// An SVG string: the sun's path at a point over the given hours, each hour shaded by its
// share of a clear sky's sunshine (null: no figure), with a needle at hour `now`.
function drawSunArc(lng, lat, ends, share, now, size) {
  const runs = daylightRuns(lng, lat, ends).slice(-3); // a 48-hour window touches at most 3 days
  if (!runs.length || share.every((v) => v == null)) return "";
  const ramp = SUN_RAMPS[resolvedTheme()];
  const grid = cssVar("--line"), ink = cssVar("--text"), surface = cssVar("--bg");
  const c = size / 2, R = c - 2;
  const width = Math.round(size * 0.1), gap = 2;
  const point = (deg, r) => [c + r * Math.cos((deg - 90) * RAD), c + r * Math.sin((deg - 90) * RAD)];

  let svg = `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" aria-hidden="true">`;
  svg += `<circle cx="${c}" cy="${c}" r="${R}" fill="none" stroke="${grid}" stroke-width="1"/>`;
  for (let d = 0; d < 360; d += 90) {
    const [x, y] = point(d, R);
    svg += `<line x1="${c}" y1="${c}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="${grid}" stroke-width="1"/>`;
  }
  let needle = null;
  runs.forEach((run, j) => {
    const mid = R - width / 2 - (runs.length - 1 - j) * (width + gap); // newest day outermost
    const [r0, r1] = [mid - width / 2, mid + width / 2];
    // The whole day's track first, so hours without a reading show as gaps in it.
    svg += `<path d="${bandPath(c, sunBearings(lng, lat, run.rise, run.set), r0, r1)}" fill="${grid}"/>`;
    ends.forEach((end, i) => {
      const a = Math.max(end - HOUR_MS, run.rise), b = Math.min(end, run.set);
      if (b <= a) return;
      if (i === now) needle = { bearing: sunPosition((a + b) / 2, lng, lat).azimuth, r: mid };
      if (share[i] == null) return;
      const k = SUN_CLASSES.filter((x) => share[i] >= x).length;
      // The surface-coloured edge is the 2px gap between hours, as in the roses.
      svg += `<path d="${bandPath(c, sunBearings(lng, lat, a, b), r0, r1)}" fill="${ramp[k]}" stroke="${surface}" stroke-width="1"/>`;
    });
  });
  if (needle) {
    const [x, y] = point(needle.bearing, needle.r);
    svg += `<line x1="${c}" y1="${c}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="${ink}" stroke-width="2" stroke-linecap="round"/>`;
    svg += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3.5" fill="${ink}" stroke="${surface}" stroke-width="1.5"/>`;
  }
  svg += `<circle cx="${c}" cy="${c}" r="3" fill="${ink}" stroke="${surface}" stroke-width="2"/></svg>`;
  return svg;
}

// The key's sample: two made-up days in the middle of the US, on the slider's dates. The
// older day clouds over in the afternoon; the newer starts hazy and clears. Sunrise and
// sunset hours are left without a figure, as dim hours are on the map.
function sunKeySample() {
  const [lng, lat] = [-98, 38];
  // 48 hours from the first local midnight in the window (06:00 UTC is midnight here).
  const first = new Date(state.gen.hours[0]);
  const midnight = Date.UTC(first.getUTCFullYear(), first.getUTCMonth(), first.getUTCDate(), 6)
    + (first.getUTCHours() > 6 ? 86400e3 : 0);
  const ends = Array.from({ length: 48 }, (_, i) => midnight + (i + 1) * HOUR_MS);
  const runs = daylightRuns(lng, lat, ends);
  let now = null;
  const share = ends.map((end, i) => {
    const t = end - HOUR_MS / 2;
    const j = runs.findIndex((r) => t >= r.rise && t <= r.set);
    if (j < 0) return null;
    const f = (t - runs[j].rise) / (runs[j].set - runs[j].rise); // how far through the day
    if (f < 0.06 || f > 0.94) return null;
    if (j === runs.length - 1) {
      if (now == null && f > 0.62) now = i;
      return f < 0.3 ? 0.45 : 0.9;
    }
    return f < 0.45 ? 0.9 : f < 0.62 ? 0.6 : f < 0.8 ? 0.35 : 0.15;
  });
  return drawSunArc(lng, lat, ends, share, now, KEY_SIZE);
}

function sunKeyHtml() {
  return `${keySample(sunKeySample())}
    <div class="key-head">
      <h3>Sun arcs</h3>
      <p>The sun's path through the sky each day, and how much of its sunshine got through.</p>
    </div>
    <ul class="key-list">
      <li>Up in the east, round through the south, down in the west. One ring per day, newest outside.</li>
      <li>Shade is sunshine as a share of a clear sky's: ${rampKeyHtml(SUN_RAMPS, SUN_CLASS_LABELS)} %.
        Grey: no figure, or too near sunrise or sunset to judge.</li>
      <li>The needle points at the sun in the selected hour; there's none at night.</li>
    </ul>`;
}

function addSunArcs() {
  if (state.gen?.weighting !== "weather") return; // no sunshine figures in this build
  weather.sunArcs = new SiteGlyphs(map, { fuel: "solar", draw: sunArcSvg });
  $("#show-sun").closest("label").hidden = false;
  $("#show-sun").addEventListener("change", (e) => weather.sunArcs.setVisible(e.target.checked));
}

// --- Popup chart --------------------------------------------------------------------
// A solar farm's 48 hours: the sunshine at its weather cell (filled) against a clear
// sky's (dashed), with the selected hour marked. Hover for any hour's figures.

const SUN_CHART = { width: 236, height: 92, top: 6, axis: 14 };

function sunChartHtml(f) {
  const got = state.gen?.weather?.[f.properties.id];
  if (!got || state.gen.weighting !== "weather") return "";
  const clear = clearSkyHours(...f.geometry.coordinates);
  const { width: W, height: H, top, axis } = SUN_CHART;
  const n = got.length, base = H - axis, plot = base - top;
  const yMax = Math.max(200, Math.ceil(Math.max(...clear, ...got.map((v) => v ?? 0)) / 200) * 200);
  const x = (i) => ((i + 0.5) * W) / n;
  const y = (v) => base - (v / yMax) * plot;
  const line = (vals) => vals.map((v, i) => (v == null ? null : `${x(i).toFixed(1)},${y(v).toFixed(1)}`));
  const solar = FUELS.solar.color, grid = cssVar("--line"), muted = cssVar("--muted"), ink = cssVar("--text");

  let svg = `<svg class="sun-chart-plot" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img"
    aria-label="Sunshine and clear-sky sunshine over the last 48 hours">`;
  // Local midnights, with the weekday centred on the day's hours.
  const days = [];
  state.gen.hours.forEach((iso, i) => {
    // "Sunday", not "Sun", which on a sunshine chart reads as the sun.
    const day = hourLabel(new Date(Date.parse(iso) - HOUR_MS / 2).toISOString(), { weekday: "long" });
    if (days.at(-1)?.day !== day) {
      if (i) svg += `<line x1="${(i * W) / n}" y1="${top}" x2="${(i * W) / n}" y2="${base}" stroke="${grid}"/>`;
      days.push({ day, from: i, to: i });
    } else days.at(-1).to = i;
  });
  for (const d of days) {
    if (d.to - d.from < 5) continue; // too few hours to label
    svg += `<text x="${(((d.from + d.to + 1) / 2) * W) / n}" y="${H - 2}" text-anchor="middle" fill="${muted}" font-size="10">${d.day}</text>`;
  }
  svg += `<line x1="0" y1="${y(yMax)}" x2="${W}" y2="${y(yMax)}" stroke="${grid}"/>`;
  svg += `<line x1="0" y1="${base}" x2="${W}" y2="${base}" stroke="${grid}"/>`;
  svg += `<text x="2" y="${y(yMax) + 11}" fill="${muted}" font-size="10">${fmt(yMax)} W/m²</text>`;
  // Sunshine: an area per unbroken run of readings.
  let run = [];
  const flush = () => {
    if (run.length > 1) {
      const first = run[0].split(",")[0], last = run.at(-1).split(",")[0];
      svg += `<path d="M${first},${base}L${run.join("L")}L${last},${base}Z" fill="${solar}" fill-opacity="0.28"/>`;
      svg += `<path d="M${run.join("L")}" fill="none" stroke="${solar}" stroke-width="2" stroke-linejoin="round"/>`;
    }
    run = [];
  };
  for (const pt of line(got)) pt ? run.push(pt) : flush();
  flush();
  svg += `<path d="M${line(clear).join("L")}" fill="none" stroke="${muted}" stroke-width="1.5" stroke-dasharray="3 2"/>`;
  // The selected hour, and a crosshair that follows the pointer.
  svg += `<line x1="${x(state.hour)}" y1="${top}" x2="${x(state.hour)}" y2="${base}" stroke="${ink}" stroke-width="1"/>`;
  svg += `<line class="sun-chart-cursor" y1="${top}" y2="${base}" stroke="${ink}" stroke-width="1" stroke-dasharray="2 2" visibility="hidden"/>`;
  svg += `</svg>`;

  return `<div class="sun-chart">
    <div class="sun-chart-key">
      <span class="rose-key"><span class="swatch" style="background:${solar}"></span>Sunshine</span>
      <span class="rose-key"><span class="dash" style="border-color:${muted}"></span>Clear sky</span>
    </div>
    ${svg}
    <div class="note sun-chart-readout">${sunReadout(got, clear, state.hour)}</div>
  </div>`;
}

function sunReadout(got, clear, i) {
  const when = hourLabel(state.gen.hours[i], { weekday: "long", hour: "numeric" });
  if (clear[i] < MIN_CLEAR_SKY) return `${when}: dark or nearly so.`;
  if (got[i] == null) return `${when}: no sunshine figure.`;
  return `${when}: ${fmt(got[i])} W/m², ${fmt((100 * got[i]) / clear[i])}% of a clear sky's ${fmt(clear[i])}.`;
}

// Hover: move the crosshair and show that hour; leaving puts back the selected hour.
function wireSunChart(root, f) {
  const plot = root?.querySelector(".sun-chart-plot");
  if (!plot) return;
  const got = state.gen.weather[f.properties.id];
  const clear = clearSkyHours(...f.geometry.coordinates);
  const cursor = plot.querySelector(".sun-chart-cursor");
  const readout = root.querySelector(".sun-chart-readout");
  plot.addEventListener("pointermove", (e) => {
    const box = plot.getBoundingClientRect();
    const i = Math.max(0, Math.min(got.length - 1, Math.floor(((e.clientX - box.left) / box.width) * got.length)));
    const cx = ((i + 0.5) * SUN_CHART.width) / got.length;
    cursor.setAttribute("x1", cx);
    cursor.setAttribute("x2", cx);
    cursor.setAttribute("visibility", "visible");
    readout.textContent = sunReadout(got, clear, i);
  });
  plot.addEventListener("pointerleave", () => {
    cursor.setAttribute("visibility", "hidden");
    readout.textContent = sunReadout(got, clear, state.hour);
  });
}
