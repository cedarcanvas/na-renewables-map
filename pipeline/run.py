"""Run the whole pipeline: plants (monthly) -> BA hourly totals -> weather -> plant estimates.

    python -m pipeline.run                  # refresh generation, reuse plants if < 30 days old
    python -m pipeline.run --refresh-plants # also re-download EIA-860M
"""

import sys
import time

from . import allocate, generation, gfs, plants, regions, weather
from .common import PLANTS_GEOJSON

PLANTS_MAX_AGE_DAYS = 30


def stale(path, days=PLANTS_MAX_AGE_DAYS):
    return not path.exists() or time.time() - path.stat().st_mtime > days * 86400


def main(argv):
    if stale(PLANTS_GEOJSON) or "--refresh-plants" in argv:
        plants.build()
    if stale(regions.REGIONS_GEOJSON) or "--refresh-plants" in argv:
        regions.build()
    _, source = generation.build()
    try:
        wx = weather.build()
    except Exception as e:  # Open-Meteo down or over quota
        print(f"Weather fetch failed ({e}); using cached weather where it covers these hours, else capacity only")
        wx = None
    allocate.build(source, wx)
    try:
        gfs.build()  # wind and cloud layers for the map; not needed for the estimates
    except Exception as e:
        print(f"GFS weather layers not updated ({type(e).__name__}: {e})")


if __name__ == "__main__":
    main(sys.argv[1:])
