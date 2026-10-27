// Weather layers from the NOAA GFS grids pipeline/gfs.py writes to data/weather/:
// animated wind particles at 100 m (a canvas over the map) and cloud cover (a
// MapLibre image layer under the plants). Loaded before app.js; uses its globals.

const weather = {
  meta: null,
  frames: null,   // per hour: { u, v } Float32Arrays on the GFS grid, or null where missing
  particles: null,
};

// Pixels moved per frame for each m/s of wind; a visual speed, the same at every zoom.
const PARTICLE_SPEED = 0.16;
const PARTICLE_DENSITY = 1 / 340; // particles per square pixel of map
const PARTICLE_MAX_AGE = 90;      // frames before a particle restarts somewhere new
const TRAIL_FADE = 0.92;          // share of each trail kept per frame
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

async function addWeatherLayers() {
  try {
    weather.meta = await loadJSON("data/weather/meta.json");
    weather.frames = await loadWindFrames(weather.meta.wind);
  } catch {
    return; // not built yet: python -m pipeline.gfs
  }
  addCloudLayer();
  weather.particles = new WindParticles(map);
  $("#show-wind").closest("label").hidden = false;
  $("#show-wind").addEventListener("change", (e) => weather.particles.setVisible(e.target.checked));
  weather.roses = new WindRoses(map);
  $("#show-roses").closest("label").hidden = false;
  $("#show-roses").addEventListener("change", (e) => weather.roses.setVisible(e.target.checked));
  renderRoseLegend();
  $("#sources").insertAdjacentHTML("beforeend",
    ` Wind and clouds: <a href="https://www.nco.ncep.noaa.gov/pmb/products/gfs/">NOAA GFS</a> (public domain).`);
  updateWeather();
}

// The wind image stacks every hour top to bottom: red = u, green = v, 0–255 for ±range m/s.
async function loadWindFrames(w) {
  const img = new Image();
  img.src = `data/weather/${w.file}?${weather.meta.hours.at(-1)}`;
  await img.decode();
  const canvas = document.createElement("canvas");
  canvas.width = img.width;
  canvas.height = img.height;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(img, 0, 0);
  const px = ctx.getImageData(0, 0, img.width, img.height).data;
  const missing = new Set(w.missing);
  const cells = w.nx * w.ny;
  return weather.meta.hours.map((_, f) => {
    if (missing.has(f)) return null;
    const u = new Float32Array(cells);
    const v = new Float32Array(cells);
    for (let i = 0; i < cells; i++) {
      const o = (f * cells + i) * 4;
      u[i] = (px[o] / 255) * 2 * w.range - w.range;
      v[i] = (px[o + 1] / 255) * 2 * w.range - w.range;
    }
    return { u, v };
  });
}

function weatherFrame() {
  if (!weather.meta || !state.gen) return -1;
  return weather.meta.hours.indexOf(state.gen.hours[state.hour]);
}

// Called by setHour in app.js.
function updateWeather() {
  if (!weather.meta) return;
  const f = weatherFrame();
  weather.particles?.setField(f >= 0 ? weather.frames[f] : null);
  weather.roses?.render(); // the needle follows the selected hour
  const key = f >= 0 ? weather.meta.hours[f].replace(/[-T:Z]/g, "").slice(0, 10) : null;
  const file = key && weather.meta.clouds.files[key];
  const source = map.getSource("clouds");
  if (source && file) source.updateImage({ url: `data/weather/${file}`, coordinates: weather.meta.clouds.corners });
  if (map.getLayer("clouds")) {
    map.setLayoutProperty("clouds", "visibility", $("#show-clouds").checked && file ? "visible" : "none");
  }
}

function addCloudLayer() {
  const files = Object.values(weather.meta.clouds.files);
  if (!files.length) return;
  map.addSource("clouds", { type: "image", url: `data/weather/${files.at(-1)}`, coordinates: weather.meta.clouds.corners });
  const c = colors();
  map.addLayer({
    id: "clouds", type: "raster", source: "clouds",
    layout: { visibility: "none" },
    paint: {
      "raster-opacity": 0.6,
      "raster-fade-duration": 0,
      "raster-resampling": "linear",
      "raster-brightness-min": c.cloudBrightness[0],
      "raster-brightness-max": c.cloudBrightness[1],
    },
  }, map.getLayer("region-fill") ? "region-fill" : "capacity"); // under the regions and plants
  $("#show-clouds").closest("label").hidden = false;
  $("#show-clouds").addEventListener("change", updateWeather);
}

class WindParticles {
  constructor(map) {
    this.map = map;
    this.field = null;
    this.visible = true;
    this.moving = false;
    this.canvas = document.createElement("canvas");
    this.canvas.id = "wind-canvas";
    map.getContainer().appendChild(this.canvas);
    this.ctx = this.canvas.getContext("2d");
    this.resize();
    map.on("resize", () => this.resize());
    // Trails would smear while the map moves, so clear, pause and restart afterwards.
    map.on("movestart", () => { this.moving = true; this.clear(); });
    map.on("moveend", () => { this.moving = false; this.reseed(); });
    reducedMotion.addEventListener("change", () => this.reseed());
    this.loop = this.loop.bind(this);
    requestAnimationFrame(this.loop);
  }

  resize() {
    const { clientWidth: w, clientHeight: h } = this.map.getContainer();
    const dpr = window.devicePixelRatio || 1;
    this.w = w;
    this.h = h;
    this.canvas.width = w * dpr;
    this.canvas.height = h * dpr;
    this.canvas.style.width = `${w}px`;
    this.canvas.style.height = `${h}px`;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.reseed();
  }

  setField(field) {
    this.field = field;
    this.reseed();
  }

  setVisible(on) {
    this.visible = on;
    this.reseed();
  }

  clear() {
    this.ctx.clearRect(0, 0, this.w, this.h);
  }

  spawn(p = {}) {
    p.x = Math.random() * this.w;
    p.y = Math.random() * this.h;
    p.age = Math.floor(Math.random() * PARTICLE_MAX_AGE);
    return p;
  }

  reseed() {
    this.clear();
    const n = Math.min(4500, Math.max(500, Math.round(this.w * this.h * PARTICLE_DENSITY)));
    this.particles = Array.from({ length: n }, () => this.spawn());
    // With reduced motion, draw a still picture of short streaks instead of animating.
    if (reducedMotion.matches && this.visible && this.field && !this.moving) {
      for (let i = 0; i < 12; i++) this.step(false);
    }
  }

  // Bilinear wind at a lon/lat from the GFS grid, or null outside it.
  sample(lng, lat) {
    const w = weather.meta.wind;
    const gx = (lng - w.west) / w.step;
    const gy = (w.north - lat) / w.step;
    if (!(gx >= 0 && gy >= 0 && gx < w.nx - 1 && gy < w.ny - 1)) return null;
    const x0 = Math.floor(gx), y0 = Math.floor(gy), fx = gx - x0, fy = gy - y0;
    const i = y0 * w.nx + x0;
    const mix = (a) => (a[i] * (1 - fx) + a[i + 1] * fx) * (1 - fy) + (a[i + w.nx] * (1 - fx) + a[i + w.nx + 1] * fx) * fy;
    return [mix(this.field.u), mix(this.field.v)];
  }

  // Advance every particle one frame and draw its segment, in three brightness bands by speed.
  step(fade = true) {
    const ctx = this.ctx;
    if (fade) {
      ctx.globalCompositeOperation = "destination-in";
      ctx.fillStyle = `rgba(0, 0, 0, ${TRAIL_FADE})`;
      ctx.fillRect(0, 0, this.w, this.h);
      ctx.globalCompositeOperation = "source-over";
    }
    const bands = [new Path2D(), new Path2D(), new Path2D()];
    for (const p of this.particles) {
      const ll = this.map.unproject([p.x, p.y]);
      const wind = this.sample(ll.lng, ll.lat);
      if (!wind || ++p.age > PARTICLE_MAX_AGE) {
        this.spawn(p);
        p.age = 0;
        continue;
      }
      const [u, v] = wind;
      const nx = p.x + u * PARTICLE_SPEED;
      const ny = p.y - v * PARTICLE_SPEED; // screen y runs south
      const speed = Math.hypot(u, v);
      const band = bands[speed < 5 ? 0 : speed < 11 ? 1 : 2];
      band.moveTo(p.x, p.y);
      band.lineTo(nx, ny);
      p.x = nx;
      p.y = ny;
      if (nx < 0 || ny < 0 || nx > this.w || ny > this.h) this.spawn(p);
    }
    const [r, g, b] = colors().particles;
    ctx.lineCap = "round";
    [[0.16, 0.7], [0.36, 1], [0.62, 1.4]].forEach(([alpha, width], k) => {
      ctx.strokeStyle = `rgba(${r}, ${g}, ${b}, ${alpha})`;
      ctx.lineWidth = width;
      ctx.stroke(bands[k]);
    });
  }

  loop() {
    requestAnimationFrame(this.loop);
    if (!this.visible || !this.field || this.moving || reducedMotion.matches) {
      if (!this.visible || !this.field) this.clear();
      return;
    }
    this.step();
  }
}

// --- Wind roses ---------------------------------------------------------------------
// For a point: how often the 100 m wind blew from each of 16 directions over the
// slider's 48 hours, each petal stacked by speed class, with a needle for the
// selected hour. Petals point where the wind came FROM (the meteorological way).

const ROSE_SECTORS = 16;
const ROSE_SPEEDS = [4, 8, 12];            // m/s class breaks: ~cut-in, mid, ~rated
const ROSE_SPEED_LABELS = ["< 4", "4–8", "8–12", "≥ 12"];
// One hue, light→dark; validated as ordinal ramps against each basemap.
const ROSE_RAMPS = {
  light: ["#5db7a6", "#2f9a87", "#177767", "#0a5046"],
  dark: ["#1b6b5e", "#269280", "#52bba8", "#a4e3d7"],
};

function sampleWind(frame, lng, lat) {
  const w = weather.meta.wind;
  const gx = (lng - w.west) / w.step;
  const gy = (w.north - lat) / w.step;
  if (!frame || !(gx >= 0 && gy >= 0 && gx < w.nx - 1 && gy < w.ny - 1)) return null;
  const x0 = Math.floor(gx), y0 = Math.floor(gy), fx = gx - x0, fy = gy - y0;
  const i = y0 * w.nx + x0;
  const mix = (a) => (a[i] * (1 - fx) + a[i + 1] * fx) * (1 - fy) + (a[i + w.nx] * (1 - fx) + a[i + w.nx + 1] * fx) * fy;
  return [mix(frame.u), mix(frame.v)];
}

// Direction the wind blows FROM, in degrees clockwise from north.
const fromDirection = (u, v) => (Math.atan2(-u, -v) * 180 / Math.PI + 360) % 360;

function roseCounts(lng, lat) {
  const counts = Array.from({ length: ROSE_SECTORS }, () => [0, 0, 0, 0]);
  let hours = 0;
  for (const frame of weather.frames) {
    const wind = sampleWind(frame, lng, lat);
    if (!wind) continue;
    const speed = Math.hypot(...wind);
    const sector = Math.round(fromDirection(...wind) / (360 / ROSE_SECTORS)) % ROSE_SECTORS;
    counts[sector][ROSE_SPEEDS.filter((b) => speed >= b).length]++;
    hours++;
  }
  return { counts, hours };
}

// An SVG string: grid rings and spokes, stacked petals, and the selected hour's needle.
function roseSvg(lng, lat, size) {
  const { counts, hours } = roseCounts(lng, lat);
  if (!hours) return "";
  const ramp = ROSE_RAMPS[resolvedTheme()];
  const grid = cssVar("--line"), ink = cssVar("--text"), surface = cssVar("--bg");
  const c = size / 2, R = c - 2;
  const max = Math.max(...counts.map((s) => s.reduce((a, b) => a + b, 0)), 1);
  const half = (360 / ROSE_SECTORS / 2 - 1.5) * Math.PI / 180;  // a little air between petals
  const point = (deg, r) => {
    const a = (deg - 90) * Math.PI / 180;
    return [c + r * Math.cos(a), c + r * Math.sin(a)];
  };
  const wedge = (deg, r0, r1) => {
    const a = (deg - 90) * Math.PI / 180;
    const p = (ang, r) => `${(c + r * Math.cos(ang)).toFixed(1)},${(c + r * Math.sin(ang)).toFixed(1)}`;
    return `M${p(a - half, r0)} L${p(a - half, r1)} A${r1},${r1} 0 0 1 ${p(a + half, r1)} L${p(a + half, r0)}` +
      (r0 > 0 ? ` A${r0},${r0} 0 0 0 ${p(a - half, r0)}Z` : "Z");
  };
  let svg = `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" aria-hidden="true">`;
  for (const f of [1 / 3, 2 / 3, 1]) svg += `<circle cx="${c}" cy="${c}" r="${(R * f).toFixed(1)}" fill="none" stroke="${grid}" stroke-width="1"/>`;
  for (let d = 0; d < 360; d += 45) {
    const [x, y] = point(d, R);
    svg += `<line x1="${c}" y1="${c}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="${grid}" stroke-width="1"/>`;
  }
  counts.forEach((bins, s) => {
    let r0 = 0;
    bins.forEach((n, k) => {
      if (!n) return;
      const r1 = r0 + (R * n) / max;
      // A surface-coloured edge is the 2px gap between stacked segments, not a border.
      svg += `<path d="${wedge(s * (360 / ROSE_SECTORS), r0, r1)}" fill="${ramp[k]}" stroke="${surface}" stroke-width="1"/>`;
      r0 = r1;
    });
  });
  const now = weatherFrame() >= 0 ? sampleWind(weather.frames[weatherFrame()], lng, lat) : null;
  if (now) {
    const [x, y] = point(fromDirection(...now), R);
    svg += `<line x1="${c}" y1="${c}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="${ink}" stroke-width="2" stroke-linecap="round"/>`;
  }
  svg += `<circle cx="${c}" cy="${c}" r="3" fill="${ink}" stroke="${surface}" stroke-width="2"/></svg>`;
  return svg;
}

// Words for the popup: the selected hour's wind at a point.
function windNowText(lng, lat) {
  const f = weatherFrame();
  const wind = f >= 0 ? sampleWind(weather.frames[f], lng, lat) : null;
  if (!wind) return "";
  const names = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
  const deg = fromDirection(...wind);
  return `${Math.hypot(...wind).toFixed(1)} m/s from the ${names[Math.round(deg / 22.5) % 16]}`;
}

function renderRoseLegend() {
  $("#rose-legend").innerHTML = `Roses: how often the wind at 100 m blew from each direction over these
    48 hours, shaded by speed ${roseKeyHtml()}; the needle is the selected hour.`;
}

function roseKeyHtml() {
  const ramp = ROSE_RAMPS[resolvedTheme()];
  return ROSE_SPEED_LABELS.map((label, k) =>
    `<span class="rose-key"><span class="swatch" style="background:${ramp[k]}"></span>${label}</span>`).join("") + " m/s";
}

// Roses on the map: one per cluster of wind farms, or per large farm when zoomed in.
class WindRoses {
  constructor(map) {
    this.map = map;
    this.markers = [];
    this.visible = false;
    map.on("moveend", () => this.render());
  }

  setVisible(on) {
    this.visible = on;
    $("#rose-legend").hidden = !on;
    this.render();
  }

  sites() {
    const zoom = this.map.getZoom();
    const farms = state.plants.features.filter((f) => f.properties.fuel === "wind");
    let sites;
    if (zoom >= 6.5) {
      sites = farms.filter((f) => f.properties.cap >= 100)
        .map((f) => ({ lng: f.geometry.coordinates[0], lat: f.geometry.coordinates[1], cap: f.properties.cap }));
    } else {
      // Capacity-weighted centres of wind in grid cells; bigger cells when zoomed out.
      const cell = zoom < 4.5 ? 5 : 3;
      const cells = new Map();
      for (const f of farms) {
        const [lng, lat] = f.geometry.coordinates;
        const key = `${Math.floor(lng / cell)},${Math.floor(lat / cell)}`;
        const c = cells.get(key) ?? { lng: 0, lat: 0, cap: 0 };
        c.lng += lng * f.properties.cap;
        c.lat += lat * f.properties.cap;
        c.cap += f.properties.cap;
        cells.set(key, c);
      }
      sites = [...cells.values()].filter((c) => c.cap >= 1500)
        .map((c) => ({ lng: c.lng / c.cap, lat: c.lat / c.cap, cap: c.cap }));
    }
    return sites.sort((a, b) => b.cap - a.cap);
  }

  render() {
    for (const m of this.markers) m.remove();
    this.markers = [];
    if (!this.visible || !weather.frames) return;
    const size = this.map.getZoom() >= 6.5 ? 84 : 104;
    const bounds = this.map.getBounds();
    const placed = [];
    for (const site of this.sites()) {
      if (!bounds.contains([site.lng, site.lat])) continue;
      // Biggest first; skip a rose that would overlap one already placed.
      const p = this.map.project([site.lng, site.lat]);
      if (placed.some((q) => Math.hypot(q.x - p.x, q.y - p.y) < size * 0.9)) continue;
      const svg = roseSvg(site.lng, site.lat, size);
      if (!svg) continue;
      placed.push(p);
      const el = document.createElement("div");
      el.className = "wind-rose";
      el.innerHTML = svg;
      this.markers.push(new maplibregl.Marker({ element: el, anchor: "center" }).setLngLat([site.lng, site.lat]).addTo(this.map));
    }
  }
}
