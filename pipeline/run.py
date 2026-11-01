"""Run the whole pipeline: plants, regions and transmission lines (monthly) -> BA hourly
totals -> weather -> plant estimates.

    python -m pipeline.run                  # refresh generation, reuse plants if < 30 days old
    python -m pipeline.run --refresh-plants # also re-download EIA-860M, regions and lines
"""

import sys
import time

from . import allocate, generation, gfs, plants, regions, transmission, weather
from .common import PLANTS_GEOJSON, PUBLIC_BA_GENERATION_CSV, PUBLIC_GENERATION_JSON

PLANTS_MAX_AGE_DAYS = 30


def stale(path, days=PLANTS_MAX_AGE_DAYS):
    return not path.exists() or time.time() - path.stat().st_mtime > days * 86400


def main(argv):
    if stale(PLANTS_GEOJSON) or "--refresh-plants" in argv:
        plants.build()
    if stale(regions.REGIONS_GEOJSON) or "--refresh-plants" in argv:
        regions.build()
    if stale(transmission.TRANSMISSION_PMTILES) or "--refresh-plants" in argv:
        try:
            transmission.build(refresh=True)  # re-fetch OpenStreetMap's Canadian lines too
        except Exception as e:  # Overpass busy or down: keep last month's tiles
            print(f"Transmission lines not rebuilt ({type(e).__name__}: {e})")
    _, source = generation.build()
    try:
        wx = weather.build()
    except Exception as e:  # Open-Meteo down or over quota
        print(f"Weather fetch failed ({e}); using cached weather where it covers these hours, else capacity only")
        wx = None
    allocate.build(source, wx)
    # The public copy's estimates (publish.py), without PJM's live feed.
    allocate.build(source, wx, PUBLIC_BA_GENERATION_CSV, PUBLIC_GENERATION_JSON)
    try:
        gfs.build()  # wind and cloud layers for the map; not needed for the estimates
    except Exception as e:
        print(f"GFS weather layers not updated ({type(e).__name__}: {e})")


if __name__ == "__main__":
    main(sys.argv[1:])
