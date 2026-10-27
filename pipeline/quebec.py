"""Hydro-Québec's open data: Québec production by source, and flows to its neighbours.

Both datasets hold a rolling 48 hours, about three hours behind real time, and
include empty rows for hours still to come. Production is hourly, stamped at
the half hour (taken here as the middle of the hour it averages); flows are
hourly, stamped on the hour (taken as the hour's end, like EIA-930).
"""

import pandas as pd
import requests

from .common import USER_AGENT

API = "https://donnees.hydroquebec.com/api/explore/v2.1/catalog/datasets/{}/records"

PRODUCTION = {"hydro": "valeurs_hydraulique", "wind": "valeurs_eolien", "solar": "valeurs_solaire"}

# Neighbour -> (export column, import column). Positive MW is power leaving Québec.
NEIGHBOURS = {
    "New England": ("exportations_newengland", "importations_sources_newengland_total"),
    "New York": ("exportations_newyork", "importations_sources_newyork_total"),
    "Ontario": ("exportations_ontario", "importations_sources_ontario_total"),
    "New Brunswick": ("exportations_newbrunswick", "importations_sources_newbrunswick_total"),
}


def _records(dataset):
    r = requests.get(API.format(dataset), params={"limit": 100, "order_by": "date"},
                     headers={"User-Agent": USER_AGENT}, timeout=60)
    r.raise_for_status()
    df = pd.DataFrame(r.json()["results"])
    df["date"] = pd.to_datetime(df["date"], utc=True)
    return df


def production():
    """Québec totals by fuel: utc (end of hour), fuel, mw; reported hours only."""
    df = _records("production-electricite-quebec")
    df = df[df["valeurs_total"].fillna(0) > 0]
    utc = (df["date"] + pd.Timedelta(minutes=30)).dt.floor("h")
    rows = [pd.DataFrame({"utc": utc, "fuel": fuel, "mw": pd.to_numeric(df[col], errors="coerce")})
            for fuel, col in PRODUCTION.items()]
    return pd.concat(rows, ignore_index=True).dropna()


def flows():
    """Net flow out of Québec to each neighbour: utc, neighbour, mw (negative = into Québec)."""
    df = _records("importations-exportations-avec-transits")
    # Placeholder rows for coming hours are all zero; a real hour has some exchange somewhere.
    exports = [cols[0] for cols in NEIGHBOURS.values()]
    df = df[df[exports].fillna(0).abs().sum(axis=1) > 0]
    rows = []
    for name, (out_col, in_col) in NEIGHBOURS.items():
        net = pd.to_numeric(df[out_col], errors="coerce").fillna(0) - pd.to_numeric(df[in_col], errors="coerce").fillna(0)
        rows.append(pd.DataFrame({"utc": df["date"], "neighbour": name, "mw": net}))
    return pd.concat(rows, ignore_index=True)


if __name__ == "__main__":
    p, f = production(), flows()
    print("production:", p["utc"].min(), "->", p["utc"].max())
    print(p[p["utc"] == p["utc"].max()].to_string(index=False))
    print("flows:", f["utc"].min(), "->", f["utc"].max())
    print(f[f["utc"] == f["utc"].max()].to_string(index=False))
