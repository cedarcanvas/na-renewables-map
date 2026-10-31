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
report. Farms on local distribution grids, which neither report, show as
not reporting. Québec's production by source comes from Hydro-Québec's
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
hour's output, circle area ∝ MW; "Normalized" draws every ring the same size,
filled by share of capacity, and "Exponential" sizes by MW^0.75 so differences
stand out), grid regions, transmission lines, Québec flows, wind at 100 m,
cloud cover, and wind roses and sun arcs. Transmission lines come as vector
tiles in one PMTiles file (read with the pmtiles library, so a plain static
server still works), drawn as fine dotted lines (white on the dark map, grey on the
light one) with bigger dots for higher voltages; lines under 200 kV only
appear as you zoom in, and clicking one shows its voltage, owner and end
substations. Roses and arcs are regional symbols, never one plant's: each
stands for all of a fuel's plants in a 5° grid cell (3° from zoom 4.5) with at
least 1,500 MW, drawn at their capacity-weighted centre. A wind rose is 48 hours
of GFS wind at those farms (each farm's hours weighted by its capacity) as 16
sectors stacked by speed, with a needle for the selected hour. A sun arc
(`sun.js`) is the sun's path across the sky for each day in the window, drawn at
its true compass bearing with the newest day outside, each hour shaded by the
region's Open-Meteo sunshine as a share of a clear sky's. Clear-sky sunshine is
computed in the browser from the sun's position (NOAA's solar position
equations, Haurwitz's clear-sky model), averaged over each hour like
Open-Meteo's figures. A key in the side panel explains both, with samples drawn
by the same code from made-up weather. Each solar farm's popup charts its 48
hours of sunshine against that clear sky. HIFLD outlines trace utility service territories,
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

## How good are the estimates?

Ontario and Alberta meter every large wind and solar farm, so they double as a
test: `pipeline/backtest.py` hands `allocate()` only a province's total, as if
it were a US region, and compares its split with each farm's meter. Farms are
scored at the grid operator's capacity (IESO's most available MW, AESO's
maximum capability), as US plants are split by EIA-860M's. Four weeks of
Ontario (29 Sep to 27 Oct 2026):

| | Farms | Error, % of capacity: weather split | Capacity-only split | Weather is better by |
|---|---|---|---|---|
| Wind | 41 | 8.6% | 14.4% | 40% |
| Solar (daylight) | 7 | 9.1% | 11.1% | 18% |

A typical wind farm-hour is still off by over a quarter of what the farm made,
so read single plants as rough and regions as solid. The farm curve in
`curves.py` runs higher than real farms above 6 m/s, but only its shape matters
for a split: a curve fitted to Ontario's farms scored the same on held-out
hours. Coarse weather cost more: wind used to come from 0.5° cells, where
nearby farms shared one wind speed; on 0.1° cells (one point per farm, in
effect) the wind error on the 31 farms tested then fell from 10.3% to 8.7% of
capacity. The test also lists farms whose listed capacity disagrees with the
grid operator's, which is how it found IESO codes matched to the wrong plants
in `ontario.py` (Port Alma's two codes on one farm, a 99 MW code on a 59 MW
farm); those are now matched by IESO's capacity and by whose local wind each
code's output follows. Alberta's history is what `snapshots.py` keeps (now 30
days), so its scores firm up over the month.

```bash
.venv/bin/python -m pipeline.backtest --days 28   # fetch 4 weeks of Ontario readings and weather, then score
.venv/bin/python -m pipeline.backtest             # rescore with what's on disk
```

## Data

| What | Source | Refresh |
|---|---|---|
| Plants, capacity, coordinates, region | [EIA-860M](https://www.eia.gov/electricity/data/eia860m/) | monthly |
| Hourly totals by region and fuel | [EIA-930](https://www.eia.gov/electricity/gridmonitor/): API v2 with a key, otherwise the six-month bulk CSV | generation up to a day behind |
| Live fuel mix for CAISO, ERCOT, SPP, MISO, NYISO, ISO-NE (and PJM with a key) | the ISOs' public feeds via [gridstatus](https://github.com/gridstatus/gridstatus) | every 5 minutes (PJM hourly); SPP an hour or two behind |
| Bonneville (BPA) wind + solar and hydro | [BPA's 5-minute BA data](https://transmission.bpa.gov/business/operations/wind/baltwg.txt) | every 5 minutes |
| Canadian wind farms | NRCan [Canadian Wind Turbine Database](https://open.canada.ca/data/en/dataset/79fdad93-9025-49ad-ba16-c26d718cc070) (map service) | monthly with plants |
| Canadian hydro and solar plants | NACEI renewable plants ≥ 1 MW (2017; thin for newer solar), plus Ontario's Loyalist and Nanticoke solar farms (`EXTRA_PLANTS` in `canada.py`) | monthly with plants |
| Ontario output per generator | IESO Generator Output and Capability report, via gridstatus; codes matched to plants in `pipeline/ontario.py` | hourly |
| Alberta output per asset | AESO [Current Supply Demand](http://ets.aeso.ca/ets_web/ip/Market/Reports/CSDReportServlet), read each run; assets matched to plants in `pipeline/alberta.py` | snapshot per run |
| Alberta solar farm locations | [OpenStreetMap](https://www.openstreetmap.org/copyright) via Overpass (ODbL; the last answer is kept in case Overpass is busy), plus farms AESO reports that OSM lacks, located from the [Global Energy Monitor wiki](https://www.gem.wiki/) (`EXTRA_PLANTS` in `canada.py`) | monthly with plants |
| Nova Scotia wind, Maritime Link | NS Power [OASIS current report](https://resourcesprd-nspower.aws.silvertech.net/oasis/current_report.shtml) | snapshot per run (2-minute averages) |
| New Brunswick interchange | NB Power [system information](https://tso.nbpower.com/Public/en/SystemInformation_realtime.asp) (scheduled, not metered) | snapshot per run |
| Saskatchewan wind, hydro, solar | SaskPower dashboard API | in practice a previous-day summary |
| Québec production by source, and flows to neighbours | [Hydro-Québec open data](https://donnees.hydroquebec.com/) | hourly, ~3 h behind |
| Grid regions | HIFLD Control Areas ([archived on source.coop](https://source.coop/seerai/hifld)) matched to EIA-930 codes; Natural Earth provinces | monthly with plants |
| Wind at 100 m and cloud cover for the map's weather layers | NOAA [GFS](https://www.nco.ncep.noaa.gov/pmb/products/gfs/) 0.25°, hourly steps, via the NOMADS filter (public domain) | each run fetches only new hours |
| Hub-height wind per 0.1° cell, sunshine per 0.5° cell | [Open-Meteo](https://open-meteo.com/) Forecast API (CC BY 4.0; free API is non-commercial) | recent hours plus 24 h of forecast, refreshed every 8 hours |
| Transmission lines, US | HIFLD Transmission Lines ([archived on source.coop](https://source.coop/seerai/hifld)), in service or status unknown | monthly with plants (the archive is fixed) |
| Transmission lines, Canada | [OpenStreetMap](https://www.openstreetmap.org/copyright) `power=line` via Overpass (ODbL); NRCan's CanVec lines have no voltage | monthly with plants |
| Basemap | [OpenFreeMap](https://openfreemap.org) Positron | — |

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # optional: add your EIA API key for fresher data
.venv/bin/python -m pipeline.run
python3 scripts/serve.py
```

Then open http://localhost:8765. `scripts/serve.py` is Python's static server
plus HTTP Range requests, which the transmission-line tiles need (the pmtiles
library reads the one file a few kilobytes at a time); `python3 -m http.server`
serves everything else but leaves the lines off. Any real host supports Range.

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
- `pipeline/transmission.py`: HIFLD and OpenStreetMap lines → `web/data/transmission.pmtiles` (GDAL's PMTiles writer; a few minutes, mostly Overpass)
- `pipeline/ontario.py`, `pipeline/quebec.py`: IESO per-generator readings; Hydro-Québec production and flows
- `pipeline/snapshots.py`, `pipeline/alberta.py`: AESO, NS Power, NB Power and SaskPower readings kept as hourly history; AESO assets matched to plants
- `pipeline/live.py`: the ISOs' (and BPA's) fuel mix for yesterday and today → `data/live_generation.csv`, averaged into end-of-hour MW (about 2 minutes, mostly SPP)
- `pipeline/generation.py`: EIA-930, plus calibrated live ISO hours after EIA ends → `data/ba_generation.csv`, the last 48 hours by region and fuel
- `pipeline/weather.py`: Open-Meteo → `data/weather.csv`, hourly wind at ~100 m for each 0.1° cell with wind farms and sunshine for each 0.5° cell with solar farms (~2,700 locations, about 6 minutes). The free tier allows 10,000 calls a day, so it refreshes every 8 hours; `data/weather_calls.json` logs each day's calls, and a fetch that would go over is skipped (the last weather is reused). Plants added between refreshes get just their own cells fetched
- `pipeline/curves.py`: turns local weather into each plant's expected capacity factor
- `pipeline/allocate.py`: splits each region's hourly total among its plants, weighted by those capacity factors → `web/data/generation.json`
- `pipeline/gfs.py`: NOAA GFS → `web/data/weather/` (all hours' wind in one `wind.png`, a Web Mercator cloud PNG per hour, `meta.json`); GDAL reads and reprojects the GRIB files
- `pipeline/backtest.py`: scores the split against Ontario's and Alberta's metered farms → `data/backtest/units.csv` (not part of the hourly run)
- `pipeline/run.py`: runs them all; re-downloads plants when they're more than 30 days old (`--refresh-plants` to force)

Region codes that differ between the two EIA datasets are mapped in
`BA_ALIASES` in `pipeline/common.py`.

## Next

- Per-farm hub heights from the US Wind Turbine Database instead of a flat 100 m
- From the GB map: a colour-scale display option, curtailment,
  outage notices, pressure / temperature / solar radiation layers, forecast
  hours, satellite imagery, individual turbines
- Hosting
