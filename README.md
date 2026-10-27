# North American Renewables Map (prototype)

A map of where US and Canadian wind, solar, hydro and geothermal power is
coming from, hour by hour, in the spirit of Robin Hawkes'
[GB Renewables Map](https://renewables-map.robinhawkes.com/).

Unlike Great Britain, the US doesn't publish live output per plant. EIA-930
gives hourly totals by fuel for each balancing authority (grid region), so
each plant's output here is an **estimate**: its share of its region's total.
Wind and solar shares are weighted by each plant's local hub-height wind or
sunshine, so a windy corner of a region gets more than a calm one.

EIA-930 generation has run up to a day behind (it seems to arrive in daily
batches), so for the hours after it ends the map uses the ISOs' own 5-minute
fuel-mix feeds, calibrated against EIA over the hours both cover. They hold
about 82% of US wind and 66% of solar capacity. Bonneville (BPA) posts its own
5-minute data too, which covers most of the Pacific Northwest's hydro and wind.
PJM's hourly feed (Data Miner) is used when `PJM_API_KEY` is set in `.env`.
Data Miner's terms: a non-member key is for internal use only, and publishing
data from it, or derived from it, needs PJM membership, so check that before
publishing a build that includes PJM.
Other regions show as "no data" (grey outlines) until EIA publishes them.

Canada: Ontario's and Alberta's large plants show **metered** output, from
IESO's hourly per-generator report and AESO's per-asset Current Supply Demand
report. Québec's production by source comes from Hydro-Québec's
open data and is split across its plants like a US region, and Hydro-Québec's
hourly flows to New England, New York, Ontario and New Brunswick are drawn as
arcs (deck.gl), with New Brunswick's scheduled interchange and Nova Scotia's
Maritime Link from Newfoundland. Nova Scotia's wind and Saskatchewan's wind,
hydro and solar totals are split across their plants. AESO, NS Power, NB Power
and SaskPower only show the current moment, so `pipeline/snapshots.py` reads
them each run and keeps the history (SaskPower's is a previous-day summary).
BC, Manitoba and Newfoundland and Labrador show capacity only.

## The page

`web/` is static: MapLibre for the map, deck.gl for Québec's flow arcs, and a
canvas over the map for wind particles (`weather.js`, the technique behind
earth.nullschool and Windy). Layers: plants (outline = capacity, fill = the
hour's output), grid regions, Québec flows, wind at 100 m, cloud cover, and
wind roses: 48 hours of GFS wind as 16 sectors stacked by speed, with a needle
for the selected hour. Zoomed out, one rose stands for each cluster of wind
capacity; from zoom 6.5 each farm of 100 MW or more gets its own, and every
wind farm's popup shows one. HIFLD outlines trace utility service territories,
so `regions.py` closes gaps under about 10 km, drops islands and holes under
about 500 km² and simplifies, which takes them from about 5,000 parts to 130.
The side panel is a small dashboard: a stat tile per fuel (GW now and the share
of reporting capacity generating; click to show or hide that fuel), a 48-hour
chart of those shares (Chart.js; click or drag it to change the hour; shaded
hours have only part of the fleet reporting) with an hourly table view, status
chips for each data source, and a sortable plant table filtered by country and
name. The fuel colours pass the dataviz palette checks (contrast, lightness,
colour-blind separation) in both themes.
A theme button switches Auto (follows the system), Light and Dark, including
the basemap (OpenFreeMap Positron / Dark). With "reduce motion" set in the
system, wind is drawn as still streaks.

## Data

| What | Source | Refresh |
|---|---|---|
| Plants, capacity, coordinates, region | [EIA-860M](https://www.eia.gov/electricity/data/eia860m/) | monthly |
| Hourly totals by region and fuel | [EIA-930](https://www.eia.gov/electricity/gridmonitor/): API v2 with a key, otherwise the six-month bulk CSV | generation up to a day behind |
| Live fuel mix for CAISO, ERCOT, SPP, MISO, NYISO, ISO-NE (and PJM with a key) | the ISOs' public feeds via [gridstatus](https://github.com/gridstatus/gridstatus) | every 5 minutes (PJM hourly); SPP an hour or two behind |
| Bonneville (BPA) wind + solar and hydro | [BPA's 5-minute BA data](https://transmission.bpa.gov/business/operations/wind/baltwg.txt) | every 5 minutes |
| Canadian wind farms | NRCan [Canadian Wind Turbine Database](https://open.canada.ca/data/en/dataset/79fdad93-9025-49ad-ba16-c26d718cc070) (map service) | monthly with plants |
| Canadian hydro and solar plants | NACEI renewable plants ≥ 1 MW (2017; thin for newer solar) | monthly with plants |
| Ontario output per generator | IESO Generator Output and Capability report, via gridstatus; codes matched to plants in `pipeline/ontario.py` | hourly |
| Alberta output per asset | AESO [Current Supply Demand](http://ets.aeso.ca/ets_web/ip/Market/Reports/CSDReportServlet), read each run; assets matched to plants in `pipeline/alberta.py` | snapshot per run |
| Alberta solar farm locations | [OpenStreetMap](https://www.openstreetmap.org/copyright) via Overpass (ODbL) | monthly with plants |
| Nova Scotia wind, Maritime Link | NS Power [OASIS current report](https://resourcesprd-nspower.aws.silvertech.net/oasis/current_report.shtml) | snapshot per run (2-minute averages) |
| New Brunswick interchange | NB Power [system information](https://tso.nbpower.com/Public/en/SystemInformation_realtime.asp) (scheduled, not metered) | snapshot per run |
| Saskatchewan wind, hydro, solar | SaskPower dashboard API | in practice a previous-day summary |
| Québec production by source, and flows to neighbours | [Hydro-Québec open data](https://donnees.hydroquebec.com/) | hourly, ~3 h behind |
| Grid regions | HIFLD Control Areas ([archived on source.coop](https://source.coop/seerai/hifld)) matched to EIA-930 codes; Natural Earth provinces | monthly with plants |
| Wind at 100 m and cloud cover for the map's weather layers | NOAA [GFS](https://www.nco.ncep.noaa.gov/pmb/products/gfs/) 0.25°, hourly steps, via the NOMADS filter (public domain) | each run fetches only new hours |
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
- `pipeline/canada.py`: Canadian wind, hydro and solar plants, appended to `plants.geojson`
- `pipeline/regions.py`: grid-region outlines and label points → `web/data/regions.geojson`
- `pipeline/ontario.py`, `pipeline/quebec.py`: IESO per-generator readings; Hydro-Québec production and flows
- `pipeline/snapshots.py`, `pipeline/alberta.py`: AESO, NS Power, NB Power and SaskPower readings kept as hourly history; AESO assets matched to plants
- `pipeline/live.py`: the ISOs' (and BPA's) fuel mix for yesterday and today → `data/live_generation.csv`, averaged into end-of-hour MW (about 2 minutes, mostly SPP)
- `pipeline/generation.py`: EIA-930, plus calibrated live ISO hours after EIA ends → `data/ba_generation.csv`, the last 48 hours by region and fuel
- `pipeline/weather.py`: Open-Meteo → `data/weather.csv`, hourly wind at ~100 m and sunshine for each 0.5° cell with plants (~1,600 cells, about 3 minutes; the free tier allows 10,000 locations a day)
- `pipeline/curves.py`: turns local weather into each plant's expected capacity factor
- `pipeline/allocate.py`: splits each region's hourly total among its plants, weighted by those capacity factors → `web/data/generation.json`
- `pipeline/gfs.py`: NOAA GFS → `web/data/weather/` (all hours' wind in one `wind.png`, a Web Mercator cloud PNG per hour, `meta.json`); GDAL reads and reprojects the GRIB files
- `pipeline/run.py`: runs them all; re-downloads plants when they're more than 30 days old (`--refresh-plants` to force)

Region codes that differ between the two EIA datasets are mapped in
`BA_ALIASES` in `pipeline/common.py`.

## Next

- Per-farm hub heights from the US Wind Turbine Database instead of a flat 100 m
- From the GB map: Normalise and colour-scale display options, curtailment,
  outage notices, pressure / temperature / solar radiation layers, forecast
  hours, transmission lines, satellite imagery, individual turbines
- Hosting
