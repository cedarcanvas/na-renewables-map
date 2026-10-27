# US Renewables Map (prototype)

A map of where US wind, solar, hydro and geothermal power is coming from, hour
by hour, in the spirit of Robin Hawkes' [GB Renewables Map](https://renewables-map.robinhawkes.com/).

Unlike Great Britain, the US doesn't publish live output per plant. EIA-930
gives hourly totals by fuel for each balancing authority (grid region), so
each plant's output here is an **estimate**: its share of its region's total.
Wind and solar shares are weighted by each plant's local hub-height wind or
sunshine, so a windy corner of a region gets more than a calm one.

EIA-930 generation has run up to a day behind (it seems to arrive in daily
batches), so for the hours after it ends the map uses the ISOs' own 5-minute
fuel-mix feeds, calibrated against EIA over the hours both cover. They hold
about 82% of US wind and 66% of solar capacity; other regions show as "no
data" (grey outlines) until EIA publishes them.

## Data

| What | Source | Refresh |
|---|---|---|
| Plants, capacity, coordinates, region | [EIA-860M](https://www.eia.gov/electricity/data/eia860m/) | monthly |
| Hourly totals by region and fuel | [EIA-930](https://www.eia.gov/electricity/gridmonitor/): API v2 with a key, otherwise the six-month bulk CSV | generation up to a day behind |
| Live fuel mix for CAISO, ERCOT, SPP, MISO, NYISO, ISO-NE (PJM with a key) | the ISOs' public feeds via [gridstatus](https://github.com/gridstatus/gridstatus) | every 5 minutes; SPP an hour or two behind |
| Hub-height wind and sunshine per 0.5° cell | [Open-Meteo](https://open-meteo.com/) Forecast API (CC BY 4.0; free API is non-commercial) | recent hours plus 24 h of forecast, refreshed every 6 hours |
| Basemap | [OpenFreeMap](https://openfreemap.org) Positron | — |

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # optional: add your EIA API key for fresher data
.venv/bin/python -m pipeline.run
python3 -m http.server 8765 --directory web
```

Then open http://localhost:8765.

## Hourly refresh (macOS)

```bash
scripts/install-hourly.sh            # install or update the launchd job; also runs it once now
scripts/install-hourly.sh --remove   # stop and remove it
```

The job (`com.ridgelinemaps.renewables-map`) runs `scripts/hourly.sh` at 10
minutes past every hour and at login; a run missed while the Mac sleeps happens
once on wake. Each run appends a timestamped section ending in `exit 0` (or the
error) to `logs/pipeline.log`, which keeps about a week. If a run never starts,
look in `logs/launchd.log`. The map's credits line shows when its data was last
rebuilt.

```bash
tail -40 logs/pipeline.log
```

```bash
launchctl print gui/$(id -u)/com.ridgelinemaps.renewables-map | grep -E "state|last exit"
```

The job uses `.venv`'s Python, which points at Homebrew's python@3.14. If a
Homebrew upgrade moves that, recreate the venv (see "Run it").

Runs build on each other: `data/live_generation.csv` keeps 4 days of live
hours (MISO only serves today, so its overlap with EIA comes from this
history), and `data/calibration.json` keeps each feed's last calibration, reused
for up to a week when a feed briefly shares too few hours with EIA. The log says
when that happens ("reusing its calibration from …"). MISO's first entry was
seeded from a measured comparison on 2026-10-26 and is replaced automatically
once EIA publishes hours the history covers.

## Pipeline

- `pipeline/plants.py`: EIA-860M → `web/data/plants.geojson`, one point per plant and fuel
- `pipeline/live.py`: the ISOs' fuel mix for yesterday and today → `data/live_generation.csv`, averaged into end-of-hour MW (about 2 minutes, mostly SPP)
- `pipeline/generation.py`: EIA-930, plus calibrated live ISO hours after EIA ends → `data/ba_generation.csv`, the last 48 hours by region and fuel
- `pipeline/weather.py`: Open-Meteo → `data/weather.csv`, hourly wind at ~100 m and sunshine for each 0.5° cell with plants (~1,600 cells, about 3 minutes; the free tier allows 10,000 locations a day)
- `pipeline/curves.py`: turns local weather into each plant's expected capacity factor
- `pipeline/allocate.py`: splits each region's hourly total among its plants, weighted by those capacity factors → `web/data/generation.json`
- `pipeline/run.py`: runs them all; re-downloads plants when they're more than 30 days old (`--refresh-plants` to force)

Region codes that differ between the two EIA datasets are mapped in
`BA_ALIASES` in `pipeline/common.py`.

## Next

- Per-farm hub heights from the US Wind Turbine Database instead of a flat 100 m
- PJM's live feed (needs `PJM_API_KEY`)
- Real per-plant output for Alberta (AESO) and Ontario (IESO)
- Generation chart and hosting
