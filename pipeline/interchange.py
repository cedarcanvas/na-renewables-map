"""Power moving between the grids, from EIA-930 interchange.

EIA reports, hourly, how much each balancing authority sends to each neighbour. This keeps
only the pairs whose two regions sit on different grids (grids.py): the DC links and
back-to-back converters that tie North America's separate AC grids together, such as
ERCOT to SPP, or Hydro-Québec to New England. Each tie gets a spot on the map: the middle
of the border between its two regions.

Output: data/ties.csv with columns utc, a, b, mw (MW from a to b; negative the other way).
"""

import json
import time

import pandas as pd
from shapely.geometry import shape

from .common import HOURS, TIES_CSV, WEB_DATA, eia_api_key
from .generation import API_LAG_MARGIN, _get_page
from .grids import grid_of

API_URL = "https://api.eia.gov/v2/electricity/rto/interchange-data/data/"
REGIONS = WEB_DATA / "regions.geojson"


def fetch(key, hours=HOURS):
    start = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=hours) - API_LAG_MARGIN).strftime("%Y-%m-%dT%H")
    params = {"api_key": key, "frequency": "hourly", "data[0]": "value", "start": start,
              "sort[0][column]": "period", "sort[0][direction]": "desc", "length": 5000}
    rows, offset = [], 0
    while True:
        r = _get_page(params, offset, url=API_URL)
        if not r.ok:
            raise RuntimeError(f"EIA interchange API {r.status_code}: {r.text[:200].replace(key, '<key>')}")
        page = r.json()["response"]
        rows += page["data"]
        offset += len(page["data"])
        if not page["data"] or offset >= int(page["total"]):
            break
        time.sleep(0.2)
    return pd.DataFrame(rows)


def ties_from(df):
    """Net MW from a to b per hour, for pairs on different grids (a < b by code). EIA
    lists each pair twice, once from each side, with opposite signs; both are averaged."""
    df = df.assign(mw=pd.to_numeric(df["value"], errors="coerce"),
                   utc=pd.to_datetime(df["period"], format="%Y-%m-%dT%H", utc=True))
    df = df[df["fromba"].map(grid_of).ne("other") & df["toba"].map(grid_of).ne("other")]
    df = df[df["fromba"].map(grid_of) != df["toba"].map(grid_of)]
    flip = df["fromba"] > df["toba"]
    df = df.assign(a=df["fromba"].where(~flip, df["toba"]), b=df["toba"].where(~flip, df["fromba"]),
                   mw=df["mw"].where(~flip, -df["mw"]))
    return df.groupby(["utc", "a", "b"], as_index=False)["mw"].mean().dropna()


def tie_points(pairs):
    """{(a, b): [lon, lat]}: the middle of the border between two regions' outlines."""
    shapes = {f["properties"]["code"]: shape(f["geometry"])
              for f in json.loads(REGIONS.read_text())["features"] if f["geometry"]["type"] != "Point"}
    out = {}
    for a, b in pairs:
        if a not in shapes or b not in shapes:
            continue
        for grow in (0.05, 0.3, 1.0):  # outlines are generalised, so nearby regions may not quite touch
            shared = shapes[a].buffer(grow).intersection(shapes[b].buffer(grow))
            if not shared.is_empty:
                p = shared.representative_point()
                out[(a, b)] = [round(p.x, 3), round(p.y, 3)]
                break
    return out


def build():
    key = eia_api_key()
    if not key:
        print("Ties: no EIA API key; skipping interchange")
        return
    ties = ties_from(fetch(key))
    TIES_CSV.parent.mkdir(parents=True, exist_ok=True)
    ties.to_csv(TIES_CSV, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
    pairs = ties.groupby(["a", "b"])["mw"].agg(["count", lambda s: s.abs().mean()])
    print(f"Ties: {len(pairs)} pairs between grids, {len(ties):,} hourly values")


if __name__ == "__main__":
    build()
