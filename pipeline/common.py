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
PLANT_GENERATION_JSON = WEB_DATA / "generation.json"

# How many hours of history the map's time slider covers.
HOURS = 48

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


def eia_api_key():
    """EIA_API_KEY from the environment or a .env file at the repo root, else None."""
    if os.environ.get("EIA_API_KEY"):
        return os.environ["EIA_API_KEY"]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "EIA_API_KEY" and value.strip():
                return value.strip().strip('"').strip("'")
    return None


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
