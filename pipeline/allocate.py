"""Estimate each plant's hourly output by splitting its BA's EIA-930 total.

EIA-930 only says "BA X produced N MW of wind this hour". allocate() decides
how that N is shared among the BA's wind plants: in proportion to capacity,
weighted for wind and solar by each plant's local weather (see curves.py).

Output: web/data/generation.json
  hours     – UTC end-of-hour timestamps
  totals    – {fuel: [MW per hour]} summed straight from the BA reports
  series    – {plant id: [estimated MW, or null where the BA didn't report]}
  unreported – plant ids its grid operator's figures leave out (null throughout)
  weather   – {plant id: [hub wind m/s (wind) or sunshine W/m² (solar)]}
  weighting – "weather", or "capacity" if no weather was available
"""

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import weather as wx
from .common import (
    BA_GENERATION_CSV, FLOWS_CSV, FUELS, METERED_CSV, PLANT_GENERATION_JSON, PLANTS_GEOJSON, WEATHER_CSV, write_json,
)
from .curves import solar_capacity_factor, wind_capacity_factor

# For each weather-weighted fuel: the weather column it uses and its curve.
WEIGHTING = {
    "wind": ("wind_ms", wind_capacity_factor),
    "solar": ("ghi", solar_capacity_factor),
}


def allocate(total_mw, capacities, weights=None):
    """Split one BA's total for one fuel and hour across that BA's plants.

    total_mw   – float, the BA's reported MW. Solar can be slightly negative at
                 night (inverters draw power), and the total can exceed the sum
                 of capacities when EIA-860M is missing some of the BA's plants.
    capacities – numpy array of nameplate MW, one per plant, all > 0.
    weights    – optional numpy array of each plant's weather-expected capacity
                 factor (0–1). Without it the split follows capacity alone.
    Returns a numpy array of estimated MW, the same length as capacities.
    """
    total = max(total_mw, 0.0)
    expected = capacities if weights is None else capacities * weights
    est = np.zeros_like(capacities)
    room = np.ones(len(capacities), dtype=bool)
    # A plant never exceeds nameplate. Its surplus passes to plants with headroom;
    # once every plant is full the rest is dropped. Each pass fills at least one
    # plant, so this ends within len(capacities) passes.
    for _ in range(len(capacities)):
        if total <= 1e-6 or not room.any():
            break
        share = expected[room]
        if share.sum() <= 0:
            share = capacities[room]  # weather says calm or dark, but the region reports output
        add = np.minimum(total * share / share.sum(), capacities[room] - est[room])
        est[room] += add
        total -= add.sum()
        room = est < capacities * (1 - 1e-9)
    return est


def load_plants():
    gj = json.loads(PLANTS_GEOJSON.read_text())
    return pd.DataFrame([f["properties"] for f in gj["features"]]), gj.get("source")


def stamp(t):
    return pd.Timestamp(t).strftime("%Y-%m-%dT%H:%M:%SZ")


def live_isos():
    """The ISO feed table, or {} if gridstatus isn't installed."""
    try:
        from .live import ISOS
    except ImportError:
        return {}
    return ISOS


def load_metered(hours):
    """{plant id: array of MW per hour} from METERED_CSV (Ontario's IESO readings)."""
    if not METERED_CSV.exists():
        return {}
    m = pd.read_csv(METERED_CSV, parse_dates=["utc"])
    index = {h: i for i, h in enumerate(hours)}
    out = {}
    for pid, g in m[m["utc"].isin(index)].groupby("plant_id"):
        arr = np.full(len(hours), np.nan)
        arr[[index[h] for h in g["utc"]]] = g["mw"].to_numpy()
        out[pid] = arr
    return out


def aligned_flows(hours):
    """[{from, to, source, mw: [MW from→to, or null per hour]}] on the map's hours."""
    if not FLOWS_CSV.exists():
        return []
    f = pd.read_csv(FLOWS_CSV, parse_dates=["utc"])
    if "link" not in f:
        return []  # written by an older version of the pipeline
    out = []
    for (link, source), g in f.groupby(["link", "source"]):
        series = g.groupby("utc")["mw"].mean().reindex(pd.DatetimeIndex(hours))
        start, end = link.split("→")
        out.append({"from": start, "to": end, "source": source, "mw": [compact(v) for v in series.to_numpy()]})
    return out


def ba_totals(totals):
    """{(ba, fuel): array} -> {ba: {fuel: [MW or null per hour]}}."""
    out = {}
    for (ba, fuel), arr in totals.items():
        out.setdefault(ba, {})[fuel] = [compact(v) for v in arr]
    return out


def compact(v):
    if v is None or np.isnan(v):
        return None
    return round(float(v)) if abs(v) >= 10 else round(float(v), 1)


def weather_grids(weather, hours):
    """{column: DataFrame of hours × cells} from the long weather table."""
    if weather is None:
        return None
    weather = weather.assign(utc=pd.to_datetime(weather["utc"], utc=True))
    return {
        col: weather.pivot(index="utc", columns="cell", values=col).reindex(pd.DatetimeIndex(hours, tz="UTC"))
        for col, _ in WEIGHTING.values()
    }


def build(source="EIA-930", weather=None, totals_csv=BA_GENERATION_CSV, out=PLANT_GENERATION_JSON):
    plants, plants_source = load_plants()
    gen = pd.read_csv(totals_csv, parse_dates=["utc"])
    if "source" not in gen:
        gen["source"] = "eia"
    hours = sorted(gen["utc"].unique())
    hour_index = {h: i for i, h in enumerate(hours)}

    if weather is None and WEATHER_CSV.exists():
        weather = pd.read_csv(WEATHER_CSV)
    grids = weather_grids(weather, hours)
    cells = wx.plant_cells()
    metered = load_metered(hours)

    # (ba, fuel) -> array of MW per hour, NaN where the BA didn't report.
    totals = {}
    for (ba, fuel), g in gen.groupby(["ba", "fuel"]):
        arr = np.full(len(hours), np.nan)
        arr[[hour_index[h] for h in g["utc"]]] = g["mw"].to_numpy()
        totals[(ba, fuel)] = arr

    # (ba, fuel) -> mask of hours that are weather-model estimates (nowcast.py), not reports.
    model_hours = {}
    for (ba, fuel), g in gen[gen["source"] == "model"].groupby(["ba", "fuel"]):
        mask = np.zeros(len(hours), dtype=bool)
        mask[[hour_index[h] for h in g["utc"]]] = True
        model_hours[(ba, fuel)] = mask

    series, plant_weather = {}, {}
    matched, unreported = set(), []
    # Capacity of plants whose BA reported each hour: in live-only hours the
    # regions EIA hasn't published yet drop out, and the map says so.
    covered = {fuel: np.zeros(len(hours)) for fuel in FUELS}
    modelled_cap = {fuel: np.zeros(len(hours)) for fuel in FUELS}  # the part of `covered` that is modelled
    for (ba, fuel), group in plants.groupby(["ba", "fuel"]):
        ba_total = totals.get((ba, fuel))
        if ba_total is None:
            continue  # no EIA-930 data for this BA and fuel (e.g. Alaska, Hawaii)
        matched.add((ba, fuel))
        caps = group["cap"].to_numpy(dtype=float)

        # hours × plants matrix of expected capacity factors, or None.
        cf = None
        if grids is not None and fuel in WEIGHTING:
            col, curve = WEIGHTING[fuel]
            local = grids[col].reindex(columns=[cells[pid] for pid in group["id"]]).to_numpy(dtype=float)
            for pid, values in zip(group["id"], local.T):
                plant_weather[pid] = [compact(v) for v in values]
            cf = curve(local)
            # A plant with no weather gets the region's average for that hour.
            known = ~np.isnan(cf)
            row_mean = np.nansum(cf, axis=1, keepdims=True) / np.maximum(known.sum(axis=1, keepdims=True), 1)
            cf = np.where(known, cf, np.where(known.any(axis=1, keepdims=True), row_mean, np.nan))

        # Plants with their own meter readings (Ontario) take those; what's left of
        # the region's total is split among the rest as usual.
        has_meter = group["id"].isin(metered).to_numpy()
        free = ~has_meter
        reported = ~np.isnan(ba_total)
        if has_meter.any():
            meter = np.column_stack([metered[pid] for pid in group["id"][has_meter]])
            ba_total = ba_total - np.nansum(meter, axis=1)
            # Every reported MW already sits on a metered plant, so the others aren't in the
            # grid operator's figures at all (in Ontario, farms on local distribution grids):
            # they show as not reporting rather than as idle.
            if np.nanmax(np.abs(ba_total), initial=0) < 1:
                unreported += group["id"][free].tolist()
                free[:] = False
        covered[fuel] += caps[has_meter | free].sum() * reported
        modelled_cap[fuel] += caps[has_meter | free].sum() * model_hours.get((ba, fuel), 0)

        est = np.full((len(hours), len(caps)), np.nan)
        for i, total in enumerate(ba_total):
            if np.isnan(total) or not free.any():
                continue
            weights = None if cf is None or np.isnan(cf[i][free]).all() else cf[i][free]
            est[i, free] = allocate(float(total), caps[free], weights)
        if has_meter.any():
            est[:, has_meter] = meter
        for j, pid in enumerate(group["id"]):
            series[pid] = [compact(v) for v in est[:, j]]

    fuel_totals = {
        fuel: [compact(v) for v in np.nansum([a for (ba, f), a in totals.items() if f == fuel], axis=0)]
        for fuel in FUELS
    }
    # BA -> first and last hours taken from a live feed, and which feed.
    feed_of = {ba: name for name, (_, bas, _) in live_isos().items() for ba in bas}
    live_rows = gen[gen["source"] == "live"]
    live = {}
    for ba, g in live_rows.groupby("ba"):
        feed = g["feed"].dropna().iloc[0] if "feed" in g and g["feed"].notna().any() else feed_of.get(ba, ba)
        live[ba] = {"from": stamp(g["utc"].min()), "until": stamp(g["utc"].max()), "feed": feed}
    eia_until = gen.loc[gen["source"] == "eia", "utc"].max()
    # Region -> fuel -> first hour that is a weather-model estimate, not a report.
    modelled = {}
    for (ba, fuel), mask in model_hours.items():
        modelled.setdefault(ba, {})[fuel] = stamp(hours[int(mask.argmax())])
    write_json({
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generation_source": source,
        "plants_source": plants_source,
        "weighting": "weather" if grids is not None else "capacity",
        "hours": [stamp(h) for h in hours],
        "totals": fuel_totals,
        "covered_cap": {fuel: [round(v) for v in arr] for fuel, arr in covered.items()},
        # How much of covered_cap in each hour is a weather-model estimate (nowcast.py).
        "modelled_cap": {fuel: [round(v) for v in arr] for fuel, arr in modelled_cap.items()},
        # Each region's own reported totals, for the region popups.
        "ba_totals": ba_totals(totals),
        "eia_until": stamp(eia_until),
        "live": live,
        "modelled": modelled,
        "metered": sorted(metered),
        # Plants in a reporting region that its grid operator's figures leave out.
        "unreported": sorted(unreported),
        "flows": aligned_flows(hours),
        "series": series,
        "weather": plant_weather,
    }, out)

    # Where the two datasets don't line up, so gaps on the map can be explained.
    unplaced = {k: np.nanmax(v) for k, v in totals.items() if k not in matched and np.nanmax(v) > 1}
    no_data = plants[~plants.set_index(["ba", "fuel"]).index.isin(list(totals))]
    print(f"Estimated {len(series):,} of {len(plants):,} plants over {len(hours)} hours, "
          f"split by {'weather' if grids is not None else 'capacity only'}")
    print(f"Plants with no BA data: {len(no_data):,} ({no_data['cap'].sum() / 1000:.1f} GW), "
          f"mostly BA codes: {no_data['ba'].replace('', '(blank)').value_counts().head(5).to_dict()}")
    if unplaced:
        print("BA totals with no matching plants (peak MW):",
              {f"{ba}/{fuel}": round(v) for (ba, fuel), v in sorted(unplaced.items(), key=lambda kv: -kv[1])[:8]})
    print(f"Wrote {out}")


if __name__ == "__main__":
    build()
