"""Run the whole pipeline: plants (monthly) -> BA hourly totals -> weather -> plant estimates.

    python -m pipeline.run                  # refresh generation, reuse plants if < 30 days old
    python -m pipeline.run --refresh-plants # also re-download EIA-860M
"""

import sys
import time

from . import allocate, generation, plants, weather
from .common import PLANTS_GEOJSON

PLANTS_MAX_AGE_DAYS = 30


def main(argv):
    old = not PLANTS_GEOJSON.exists() or time.time() - PLANTS_GEOJSON.stat().st_mtime > PLANTS_MAX_AGE_DAYS * 86400
    if old or "--refresh-plants" in argv:
        plants.build()
    _, source = generation.build()
    try:
        wx = weather.build()
    except Exception as e:  # Open-Meteo down or over quota
        print(f"Weather fetch failed ({e}); using cached weather where it covers these hours, else capacity only")
        wx = None
    allocate.build(source, wx)


if __name__ == "__main__":
    main(sys.argv[1:])
