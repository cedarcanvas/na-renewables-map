"""Hourly hub-height wind and sunshine at each wind and solar plant, from Open-Meteo.

Plants are grouped into WEATHER_CELL_DEG grid cells and each cell is fetched
once. Hours line up with EIA-930's end-of-hour labels:
  wind_ms – mean of the 80 m and 120 m speeds (≈100 m, a typical modern hub
            height), averaged over the hour ending at `utc`
  ghi     – global horizontal irradiance, W/m², which Open-Meteo already
            reports as the mean of the preceding hour

This uses the Forecast API, which serves recent past hours with no delay; the
Historical Weather API's ERA5 data runs about 5 days behind real time.

Output: data/weather.csv with columns cell, utc, wind_ms, ghi, running from the
start of the EIA window to FORECAST_AHEAD past now.
Open-Meteo data is CC BY 4.0; the free API is for non-commercial use.
"""

import json
import time

import numpy as np
import pandas as pd
import requests

from .common import (
    BA_GENERATION_CSV, PLANTS_GEOJSON, USER_AGENT, WEATHER_CELL_DEG, WEATHER_CSV, WEATHER_MAX_AGE_HOURS,
)

API_URL = "https://api.open-meteo.com/v1/forecast"
BATCH = 100               # locations per request
LOCATIONS_PER_MINUTE = 500  # stay under the free tier's 600/minute
WEATHER_FUELS = ("wind", "solar")
# Cache this much forecast beyond now. EIA hours that arrive before the next
# refresh are then already covered, so hourly runs cost no calls; refreshing
# every WEATHER_MAX_AGE_HOURS (6) uses ~4 × 1,600 of the 10,000 daily calls.
FORECAST_AHEAD = pd.Timedelta(hours=24)
# Also cache this much before the EIA window, so a window that starts a few hours
# earlier next run (EIA's lag varies) doesn't force a refetch. It costs nothing:
# Open-Meteo counts locations, not days.
LEAD_IN = pd.Timedelta(hours=24)


def cell_of(lat, lon):
    step = WEATHER_CELL_DEG
    return f"{round(lat / step) * step:.2f},{round(lon / step) * step:.2f}"


def plant_cells():
    """{plant id: cell key} for every wind and solar plant."""
    features = json.loads(PLANTS_GEOJSON.read_text())["features"]
    return {
        f["properties"]["id"]: cell_of(f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0])
        for f in features
        if f["properties"]["fuel"] in WEATHER_FUELS
    }


def _get(params, tries=4):
    for attempt in range(tries):
        r = requests.get(API_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=120)
        if r.status_code == 429 and attempt < tries - 1:
            time.sleep(60)  # minutely limit; the next minute starts fresh
            continue
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else [data]


def fetch(cells, start, end):
    """Hourly weather for each cell between two UTC timestamps (inclusive)."""
    frames = []
    seconds_per_batch = 60 * BATCH / LOCATIONS_PER_MINUTE
    for i in range(0, len(cells), BATCH):
        began = time.monotonic()
        batch = cells[i:i + BATCH]
        lats, lons = zip(*(c.split(",") for c in batch))
        results = _get({
            "latitude": ",".join(lats),
            "longitude": ",".join(lons),
            "hourly": "wind_speed_80m,wind_speed_120m,shortwave_radiation",
            "wind_speed_unit": "ms",
            "timezone": "GMT",
            "timeformat": "unixtime",
            "start_date": start.strftime("%Y-%m-%d"),
            "end_date": end.strftime("%Y-%m-%d"),
        })
        for cell, res in zip(batch, results):
            h = res["hourly"]
            frames.append(pd.DataFrame({
                "cell": cell,
                "utc": pd.to_datetime(h["time"], unit="s", utc=True),
                "w80": h["wind_speed_80m"],
                "w120": h["wind_speed_120m"],
                "ghi": h["shortwave_radiation"],
            }))
        print(f"  weather: {min(i + BATCH, len(cells)):,}/{len(cells):,} cells", flush=True)
        if i + BATCH < len(cells):
            time.sleep(max(0.0, seconds_per_batch - (time.monotonic() - began)))

    df = pd.concat(frames, ignore_index=True).sort_values(["cell", "utc"])
    hub = df[["w80", "w120"]].astype(float).mean(axis=1)
    # Speeds are instantaneous; average each hour's two ends to match EIA's hourly means.
    df["wind_ms"] = hub.groupby(df["cell"]).transform(lambda s: s.rolling(2, min_periods=1).mean())
    return df[["cell", "utc", "wind_ms", "ghi"]]


def cached(cells, hours):
    """The cached weather if it's recent and covers these cells and hours, else None."""
    if not WEATHER_CSV.exists():
        return None
    if time.time() - WEATHER_CSV.stat().st_mtime > WEATHER_MAX_AGE_HOURS * 3600:
        return None
    df = pd.read_csv(WEATHER_CSV, parse_dates=["utc"])
    if not set(cells) <= set(df["cell"]) or not set(hours) <= set(df["utc"]):
        return None
    return df


def build(force=False):
    cells = sorted(set(plant_cells().values()))
    hours = sorted(pd.read_csv(BA_GENERATION_CSV, parse_dates=["utc"])["utc"].unique())
    df = None if force else cached(cells, hours)
    if df is not None:
        print(f"Weather: reusing {WEATHER_CSV.name} ({len(cells):,} cells)")
        return df

    print(f"Weather: fetching {len(cells):,} cells from Open-Meteo "
          f"(~{len(cells) / LOCATIONS_PER_MINUTE:.0f} min, {len(cells):,} of 10,000 daily free calls)")
    now = pd.Timestamp.now(tz="UTC").floor("h")
    start = hours[0] - LEAD_IN
    df = fetch(cells, start, max(hours[-1], now) + FORECAST_AHEAD)
    # The first hour has no previous reading to average its wind with.
    df = df[df["utc"] > start]
    WEATHER_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(WEATHER_CSV, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
    window = df[df["utc"].isin(hours)]
    print(f"Wrote {WEATHER_CSV}: {df['utc'].min():%m-%d %H:%M} to {df['utc'].max():%m-%d %H:%M} UTC; "
          f"in the EIA window, median hub wind {np.nanmedian(window['wind_ms']):.1f} m/s, "
          f"peak sunshine {window['ghi'].max():.0f} W/m²")
    return df


if __name__ == "__main__":
    build(force=True)
