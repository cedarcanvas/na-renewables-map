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
