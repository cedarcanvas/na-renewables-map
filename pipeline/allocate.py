"""Estimate each plant's hourly output by splitting its BA's EIA-930 total.

EIA-930 only says "BA X produced N MW of wind this hour". allocate() decides
how that N is shared among the BA's wind plants. Phase 2 will weight the split
by local weather; for now the only per-plant input is nameplate capacity.

Output: web/data/generation.json
  hours   – UTC end-of-hour timestamps
  totals  – {fuel: [MW per hour]} summed straight from the BA reports
  series  – {plant id: [estimated MW, or null where the BA didn't report]}
"""

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .common import BA_GENERATION_CSV, FUELS, PLANT_GENERATION_JSON, PLANTS_GEOJSON, write_json


def allocate(total_mw, capacities):
    """Split one BA's total for one fuel and hour across that BA's plants.

    total_mw   – float, the BA's reported MW. Solar can be slightly negative at
                 night (inverters draw power), and the total can exceed the sum
                 of capacities when EIA-860M is missing some of the BA's plants.
    capacities – numpy array of nameplate MW, one per plant, all > 0.
    Returns a numpy array of estimated MW, the same length as capacities.
    """
    total = max(total_mw, 0.0)
    est = total * capacities / capacities.sum()
    return np.minimum(est, capacities)


def load_plants():
    gj = json.loads(PLANTS_GEOJSON.read_text())
    return pd.DataFrame([f["properties"] for f in gj["features"]]), gj.get("source")


def compact(v):
    if v is None or np.isnan(v):
        return None
    return round(float(v)) if abs(v) >= 10 else round(float(v), 1)


def build(source="EIA-930"):
    plants, plants_source = load_plants()
    gen = pd.read_csv(BA_GENERATION_CSV, parse_dates=["utc"])
    hours = sorted(gen["utc"].unique())
    hour_index = {h: i for i, h in enumerate(hours)}

    # (ba, fuel) -> array of MW per hour, NaN where the BA didn't report.
    totals = {}
    for (ba, fuel), g in gen.groupby(["ba", "fuel"]):
        arr = np.full(len(hours), np.nan)
        arr[[hour_index[h] for h in g["utc"]]] = g["mw"].to_numpy()
        totals[(ba, fuel)] = arr

    series = {}
    matched = set()
    for (ba, fuel), group in plants.groupby(["ba", "fuel"]):
        ba_total = totals.get((ba, fuel))
        if ba_total is None:
            continue  # no EIA-930 data for this BA and fuel (e.g. Alaska, Hawaii)
        matched.add((ba, fuel))
        caps = group["cap"].to_numpy(dtype=float)
        est = np.full((len(hours), len(caps)), np.nan)
        for i, total in enumerate(ba_total):
            if not np.isnan(total):
                est[i] = allocate(float(total), caps)
        for j, pid in enumerate(group["id"]):
            series[pid] = [compact(v) for v in est[:, j]]

    fuel_totals = {
        fuel: [compact(v) for v in np.nansum([a for (ba, f), a in totals.items() if f == fuel], axis=0)]
        for fuel in FUELS
    }
    write_json({
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generation_source": source,
        "plants_source": plants_source,
        "hours": [pd.Timestamp(h).strftime("%Y-%m-%dT%H:%M:%SZ") for h in hours],
        "totals": fuel_totals,
        "series": series,
    }, PLANT_GENERATION_JSON)

    # Where the two datasets don't line up, so gaps on the map can be explained.
    unplaced = {k: np.nanmax(v) for k, v in totals.items() if k not in matched and np.nanmax(v) > 1}
    no_data = plants[~plants.set_index(["ba", "fuel"]).index.isin(list(totals))]
    print(f"Estimated {len(series):,} of {len(plants):,} plants over {len(hours)} hours")
    print(f"Plants with no BA data: {len(no_data):,} ({no_data['cap'].sum() / 1000:.1f} GW), "
          f"mostly BA codes: {no_data['ba'].replace('', '(blank)').value_counts().head(5).to_dict()}")
    if unplaced:
        print("BA totals with no matching plants (peak MW):",
              {f"{ba}/{fuel}": round(v) for (ba, fuel), v in sorted(unplaced.items(), key=lambda kv: -kv[1])[:8]})
    print(f"Wrote {PLANT_GENERATION_JSON}")


if __name__ == "__main__":
    build()
