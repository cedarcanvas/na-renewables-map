# US Renewables Map (prototype)

A map of where US wind, solar, hydro and geothermal power is coming from, hour
by hour, in the spirit of Robin Hawkes' [GB Renewables Map](https://renewables-map.robinhawkes.com/).

Unlike Great Britain, the US doesn't publish live output per plant. EIA-930
gives hourly totals by fuel for each balancing authority (grid region), so
each plant's output here is an **estimate**: its share of its region's total.

## Data

| What | Source | Refresh |
|---|---|---|
| Plants, capacity, coordinates, region | [EIA-860M](https://www.eia.gov/electricity/data/eia860m/) | monthly |
| Hourly totals by region and fuel | [EIA-930](https://www.eia.gov/electricity/gridmonitor/): API v2 with a key, otherwise the six-month bulk CSV | hourly (API) / daily (bulk) |
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

## Pipeline

- `pipeline/plants.py`: EIA-860M → `web/data/plants.geojson`, one point per plant and fuel
- `pipeline/generation.py`: EIA-930 → `data/ba_generation.csv`, the last 48 hours by region and fuel
- `pipeline/allocate.py`: splits each region's hourly total among its plants → `web/data/generation.json`
- `pipeline/run.py`: runs all three; re-downloads plants when they're more than 30 days old (`--refresh-plants` to force)

Region codes that differ between the two EIA datasets are mapped in
`BA_ALIASES` in `pipeline/common.py`.

## Next

- Weight the split by local wind speed and sunshine (Open-Meteo) instead of capacity alone
- Real per-plant output for Alberta (AESO) and Ontario (IESO)
- Generation chart, hosting, scheduled refresh
