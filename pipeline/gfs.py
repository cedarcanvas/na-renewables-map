"""Hourly wind at 100 m and cloud cover over North America from NOAA's GFS model.

For each hour on the map's slider this fetches the GFS file valid at that hour
(public domain, via NOMADS' subsetting filter, ~290 KB at 0.25°) from the newest
model run that has been published, and writes what the page draws:

  web/data/weather/wind.png      every hour's u/v wind, stacked into one image
                                 (red = u, green = v, scaled ±WIND_RANGE m/s)
  web/data/weather/clouds/*.png  cloud cover per hour, reprojected to Web
                                 Mercator so MapLibre can lay it over the map
  web/data/weather/meta.json     hours, grid and file names

GDAL does the reading and reprojecting; GFS runs four times a day and each run
is published about four hours later.
"""

import json
import shutil
import struct
import subprocess
import tempfile
import time
import os
import zlib
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .common import BA_GENERATION_CSV, RAW, USER_AGENT, WEB_DATA

# The 0.25° product is the one with hourly steps (0.5° is three-hourly).
FILTER = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
# North America from Alaska's panhandle to Newfoundland; the model's 0–360° longitudes.
AREA = {"leftlon": 225, "rightlon": 310, "toplat": 62, "bottomlat": 18}
BOUNDS = (-135.0, 18.0, -50.0, 62.0)  # cell centres, west south east north
WIND_STEP = 2                         # keep every 2nd 0.25° cell: a 0.5° grid is plenty for particles
WIND_RANGE = 40.0                     # m/s; 100 m winds beyond this are clipped
CLOUD_WIDTH = 600                     # px of each reprojected cloud image
OUT = WEB_DATA / "weather"
CACHE = RAW / "gfs"
KEEP = pd.Timedelta(days=4)
GDAL_ENV = {**os.environ, "GDAL_PAM_ENABLED": "NO"}


def _cycles_for(hour):
    """Model runs to try for a valid hour, nearest first (each runs every 6 h)."""
    newest = (hour - pd.Timedelta(hours=1)).floor("6h")
    return [newest - pd.Timedelta(hours=6 * k) for k in range(4)]


def _fetch(cycle, step):
    params = {
        "dir": f"/gfs.{cycle:%Y%m%d}/{cycle:%H}/atmos",
        "file": f"gfs.t{cycle:%H}z.pgrb2.0p25.f{step:03d}",
        "var_UGRD": "on", "var_VGRD": "on", "var_TCDC": "on",
        "lev_100_m_above_ground": "on", "lev_entire_atmosphere": "on",
        "subregion": "", **AREA,
    }
    r = requests.get(FILTER, params=params, headers={"User-Agent": USER_AGENT}, timeout=60)
    # A run that isn't published yet comes back as an HTML error page, not GRIB.
    return r.content if r.ok and r.content[:4] == b"GRIB" else None


def grib_for(hour, index):
    """Path of a GRIB file valid at `hour`, downloading if the cached one could be better."""
    key = f"{hour:%Y%m%d%H}"
    path = CACHE / f"{key}.grb2"
    cached = index.get(key)
    if path.exists() and cached and cached["step"] <= 6:
        return path  # already from the nearest run
    for cycle in _cycles_for(hour):
        step = int((hour - cycle) / pd.Timedelta(hours=1))
        if path.exists() and cached and cached["cycle"] == f"{cycle:%Y%m%d%H}":
            return path  # nothing newer published since last time
        data = _fetch(cycle, step)
        time.sleep(0.3)  # NOMADS asks for under 120 requests a minute
        if data:
            CACHE.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            index[key] = {"cycle": f"{cycle:%Y%m%d%H}", "step": step}
            return path
    return path if path.exists() else None


def _bands(path):
    """{'u': n, 'v': n, 'cloud': n} band numbers; cloud is the instantaneous total."""
    info = json.loads(subprocess.run(["gdalinfo", "-json", str(path)], capture_output=True, check=True).stdout)
    out = {}
    for b in info["bands"]:
        m = b["metadata"].get("", {})
        element, level = m.get("GRIB_ELEMENT"), m.get("GRIB_SHORT_NAME")
        if element == "UGRD" and level == "100-HTGL":
            out["u"] = b["band"]
        elif element == "VGRD" and level == "100-HTGL":
            out["v"] = b["band"]
        elif element == "TCDC" and m.get("GRIB_PDS_PDTN") == "0":
            out["cloud"] = b["band"]
    return out


def _read(path, band, tmp):
    """One band as a float32 array (rows north to south)."""
    dst = Path(tmp) / f"b{band}.bin"
    subprocess.run(["gdal_translate", "-q", "-of", "ENVI", "-ot", "Float32", "-b", str(band), str(path), str(dst)],
                   check=True)
    info = json.loads(subprocess.run(["gdalinfo", "-json", str(dst)], capture_output=True, check=True).stdout)
    nx, ny = info["size"]
    return np.fromfile(dst, dtype=np.float32).reshape(ny, nx)


def _png(path, rgba):
    """Write an RGBA uint8 array as a PNG (no imaging library needed)."""
    h, w, _ = rgba.shape
    raw = b"".join(b"\x00" + rgba[y].tobytes() for y in range(h))
    chunk = lambda tag, data: (struct.pack(">I", len(data)) + tag + data
                               + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _cloud_png(path, band, dst, tmp):
    """Cloud cover reprojected to Web Mercator: grey, alpha rising with cover."""
    ramp = Path(tmp) / "ramp.txt"
    ramp.write_text("0 128 128 128 0\n20 128 128 128 30\n100 128 128 128 210\n")
    merc = Path(tmp) / "cloud_merc.tif"
    west, south, east, north = BOUNDS
    subprocess.run(["gdalwarp", "-q", "-overwrite", "-b", str(band), "-t_srs", "EPSG:3857",
                    "-te_srs", "EPSG:4326", "-te", str(west), str(south), str(east), str(north),
                    "-ts", str(CLOUD_WIDTH), "0", "-r", "bilinear", str(path), str(merc)], check=True)
    subprocess.run(["gdaldem", "color-relief", "-q", "-alpha", "-of", "PNG", str(merc), str(ramp), str(dst)],
                   check=True, env=GDAL_ENV)


def build(hours=None):
    if hours is None:
        hours = sorted(pd.read_csv(BA_GENERATION_CSV, parse_dates=["utc"])["utc"].unique())
    hours = [pd.Timestamp(h) for h in hours]
    index_path = CACHE / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    (OUT / "clouds").mkdir(parents=True, exist_ok=True)

    frames, clouds, missing = [], {}, []
    fetched_before = dict(index)
    with tempfile.TemporaryDirectory() as tmp:
        for i, hour in enumerate(hours):
            path = grib_for(hour, index)
            key = f"{hour:%Y%m%d%H}"
            if path is None:
                missing.append(i)
                frames.append(None)
                continue
            # GDAL only runs for hours whose GRIB is new or changed; the rest come
            # from the decoded wind saved last time (launchd runs us at low priority).
            changed = index.get(key) != fetched_before.get(key)
            wind_file = CACHE / f"{key}.npy"
            cloud_file = OUT / "clouds" / f"{key}.png"
            if changed or not wind_file.exists() or not cloud_file.exists():
                bands = _bands(path)
                if changed or not wind_file.exists():
                    u, v = _read(path, bands["u"], tmp), _read(path, bands["v"], tmp)
                    np.save(wind_file, np.dstack([u, v])[::WIND_STEP, ::WIND_STEP])
                if "cloud" in bands and (changed or not cloud_file.exists()):
                    _cloud_png(path, bands["cloud"], cloud_file, tmp)
            frames.append(np.load(wind_file))
            if cloud_file.exists():
                clouds[key] = f"clouds/{key}.png"

    # One image, hours stacked top to bottom: R = u, G = v, scaled to 0–255.
    shape = next((f.shape for f in frames if f is not None), (89, 171, 2))
    frames = [np.zeros(shape, np.float32) if f is None else f for f in frames]
    uv = np.concatenate(frames, axis=0)
    ny, nx = shape[:2]
    scaled = np.clip((uv + WIND_RANGE) / (2 * WIND_RANGE) * 255, 0, 255).round().astype(np.uint8)
    rgba = np.dstack([scaled[..., 0], scaled[..., 1], np.zeros_like(scaled[..., 0]), np.full_like(scaled[..., 0], 255)])
    _png(OUT / "wind.png", rgba)

    west, south, east, north = BOUNDS
    meta = {
        "source": "NOAA GFS 0.25°, wind at 100 m and total cloud cover",
        "hours": [f"{h:%Y-%m-%dT%H:%M:%SZ}" for h in hours],
        "wind": {"file": "wind.png", "nx": nx, "ny": ny, "west": west, "north": north, "step": 0.25 * WIND_STEP,
                 "range": WIND_RANGE, "missing": missing},
        "clouds": {"corners": [[west, north], [east, north], [east, south], [west, south]], "files": clouds},
        "runs": {k: index[k]["cycle"] for k in (f"{h:%Y%m%d%H}" for h in hours) if k in index},
    }
    (OUT / "meta.json").write_text(json.dumps(meta, separators=(",", ":")))
    index_path.write_text(json.dumps(index))

    # Forget hours that have left the window.
    cutoff = f"{pd.Timestamp.now(tz='UTC') - KEEP:%Y%m%d%H}"
    for p in list(CACHE.glob("*.grb2")) + list(CACHE.glob("*.npy")) + list((OUT / "clouds").glob("*.png")):
        if p.stem < cutoff:
            p.unlink()
    new = sum(1 for k in index if fetched_before.get(k) != index[k])
    print(f"GFS weather: {len(hours) - len(missing)} of {len(hours)} hours ({new} downloaded), "
          f"{len(clouds)} cloud images; wind.png {(OUT / 'wind.png').stat().st_size / 1e3:.0f} KB")


if __name__ == "__main__":
    build()
