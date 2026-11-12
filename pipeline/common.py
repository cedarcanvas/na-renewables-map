"""Shared paths, fuel definitions and small helpers for the pipeline."""

import json
import os
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
WEB_DATA = ROOT / "web" / "data"

PLANTS_GEOJSON = WEB_DATA / "plants.geojson"
BA_GENERATION_CSV = ROOT / "data" / "ba_generation.csv"
LIVE_GENERATION_CSV = ROOT / "data" / "live_generation.csv"
CALIBRATION_JSON = ROOT / "data" / "calibration.json"
METERED_CSV = ROOT / "data" / "metered_generation.csv"   # per-plant readings (Ontario)
FLOWS_CSV = ROOT / "data" / "flows.csv"                  # Hydro-Québec's flows to neighbours
WEATHER_CSV = ROOT / "data" / "weather.csv"
PLANT_GENERATION_JSON = WEB_DATA / "generation.json"
# The same for the public copy (publish.py), without PJM's Data Miner feed (see live.py).
PUBLIC_BA_GENERATION_CSV = ROOT / "data" / "ba_generation_public.csv"
# EIA-930 as fetched, before the window cut, for the nowcast (nowcast.py) to calibrate on.
EIA_HISTORY_CSV = ROOT / "data" / "eia_history.csv"
PUBLIC_GENERATION_JSON = ROOT / "data" / "generation_public.json"

# How many hours of history the map's time slider covers.
HOURS = 48

# Weather is fetched once per grid cell, not per plant. Open-Meteo's free tier
# counts every location as a call (600/minute, 10,000/day). Wind gets 0.1° cells,
# about one per farm: in backtest.py, 0.5° cells left Ontario's wind split with
# 15% more error (10.8% of capacity against 9.4%). Solar stays on 0.5°, where its
# ~7,800 farms need ~1,400 cells (0.1° would take ~4,400). ~2,700 locations in all.
WEATHER_CELL_DEG = {"wind": 0.1, "solar": 0.5}
# Reuse fetched weather for this long before asking Open-Meteo again: at most three
# fetches a day, ~8,100 calls, leaves room for backtest.py under the 10,000.
WEATHER_MAX_AGE_HOURS = 8
WEATHER_DAILY_CALLS = 10_000

USER_AGENT = "Mozilla/5.0 (na-renewables-map prototype)"

# Our fuel keys, and how each source names them.
#   eia860: (energy source codes, prime mover codes to exclude)
#   eia930_columns: bulk-file column stems, summed together
#   eia930_api: API fuel-type codes, summed together
FUELS = {
    "wind": {
        "eia860": ({"WND"}, set()),
        "eia930_columns": [
            "Wind without Integrated Battery Storage",
            "Wind with Integrated Battery Storage",
        ],
        "eia930_api": ["WND", "WNB"],
    },
    "solar": {
        "eia860": ({"SUN"}, set()),
        "eia930_columns": [
            "Solar without Integrated Battery Storage",
            "Solar with Integrated Battery Storage",
        ],
        "eia930_api": ["SUN", "SNB"],
    },
    "hydro": {
        # Conventional hydro only; pumped storage (prime mover PS) is a battery, not a source.
        "eia860": ({"WAT"}, {"PS"}),
        "eia930_columns": ["Hydropower Excluding Pumped Storage"],
        "eia930_api": ["WAT"],
    },
    "geothermal": {
        "eia860": ({"GEO"}, set()),
        "eia930_columns": ["Geothermal"],
        "eia930_api": ["GEO"],
    },
}


# EIA-860M BA codes that EIA-930 now reports under a different code. WAPA's
# Rocky Mountain (WACM) and Upper Great Plains West (WAUW) areas joined SPP's
# western RTO in 2026 and show up in EIA-930 as SWPW; 860M still has the old codes.
BA_ALIASES = {
    "WACM": "SWPW",
    "WAUW": "SWPW",
}


def env_value(name):
    """A setting from the environment or a .env file at the repo root, else None."""
    if os.environ.get(name):
        return os.environ[name]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == name and value.strip():
                return value.strip().strip('"').strip("'")
    return None


def eia_api_key():
    return env_value("EIA_API_KEY")


def download(url, dest, force=False):
    """Download url to dest unless it already exists. Returns dest."""
    dest = Path(dest)
    if dest.exists() and not force:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, headers={"User-Agent": USER_AGENT}, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    tmp.rename(dest)
    return dest


def url_exists(url):
    r = requests.head(url, headers={"User-Agent": USER_AGENT}, allow_redirects=False, timeout=30)
    return r.status_code == 200


def write_json(obj, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":")))
