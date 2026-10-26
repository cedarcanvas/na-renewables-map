"""Build web/data/plants.geojson from EIA-860M: one point per plant and fuel.

EIA-860M lists every US generator of 1 MW or more with coordinates and the
balancing authority (BA) it reports to. The BA code is what links a plant to
the hourly EIA-930 totals in generation.py.
"""

import datetime as dt

import pandas as pd

from .common import BA_ALIASES, FUELS, PLANTS_GEOJSON, RAW, download, url_exists, write_json

BASE = "https://www.eia.gov/electricity/data/eia860m"


def latest_860m_url(today=None, months_back=8):
    """The newest monthly workbook. The current month lives in xls/, older ones in archive/xls/."""
    day = today or dt.date.today()
    for _ in range(months_back):
        name = f"{day.strftime('%B').lower()}_generator{day.year}.xlsx"
        for folder in ("xls", "archive/xls"):
            url = f"{BASE}/{folder}/{name}"
            if url_exists(url):
                return url
        day = day.replace(day=1) - dt.timedelta(days=1)
    raise RuntimeError("No EIA-860M workbook found in the last few months")


def fuel_for(energy_source, prime_mover):
    for fuel, spec in FUELS.items():
        sources, excluded_movers = spec["eia860"]
        if energy_source in sources and prime_mover not in excluded_movers:
            return fuel
    return None


def load_generators(path):
    df = pd.read_excel(path, sheet_name="Operating", header=2, engine="openpyxl")
    df = df.rename(columns={
        "Plant ID": "plant_id",
        "Plant Name": "name",
        "Plant State": "state",
        "Balancing Authority Code": "ba",
        "Nameplate Capacity (MW)": "cap",
        "Energy Source Code": "esc",
        "Prime Mover Code": "pm",
        "Operating Year": "op_year",
        "Status": "status",
        "Latitude": "lat",
        "Longitude": "lon",
    })
    # The sheet ends with footnote rows that have no plant id.
    df = df[pd.to_numeric(df["plant_id"], errors="coerce").notna()].copy()
    df["plant_id"] = df["plant_id"].astype(int)
    for col in ("cap", "lat", "lon", "op_year"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["ba"] = df["ba"].fillna("").astype(str).str.strip().replace(BA_ALIASES)
    # Out-of-service units aren't generating, so they shouldn't take a share of the BA total.
    df = df[~df["status"].astype(str).str.startswith("(OS)")]
    df["fuel"] = [fuel_for(e, p) for e, p in zip(df["esc"], df["pm"])]
    return df[df["fuel"].notna() & df["lat"].notna() & df["lon"].notna()]


def build(today=None):
    url = latest_860m_url(today)
    path = download(url, RAW / url.rsplit("/", 1)[1])
    gens = load_generators(path)

    plants = (
        gens.groupby(["plant_id", "fuel"], as_index=False)
        .agg(
            name=("name", "first"),
            state=("state", "first"),
            ba=("ba", "first"),
            lat=("lat", "first"),
            lon=("lon", "first"),
            cap=("cap", "sum"),
            units=("cap", "size"),
            op_year=("op_year", "min"),
        )
        .sort_values("cap", ascending=False)
    )

    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(p.lon, 5), round(p.lat, 5)]},
            "properties": {
                "id": f"{p.plant_id}-{p.fuel}",
                "name": p.name,
                "fuel": p.fuel,
                "cap": round(float(p.cap), 1),
                "ba": p.ba,
                "state": p.state,
                "units": int(p.units),
                "op_year": None if pd.isna(p.op_year) else int(p.op_year),
            },
        }
        for p in plants.itertuples()
    ]
    write_json(
        {"type": "FeatureCollection", "source": url, "features": features},
        PLANTS_GEOJSON,
    )

    summary = plants.groupby("fuel").agg(plants=("cap", "size"), capacity_mw=("cap", "sum")).round(0)
    print(f"EIA-860M: {url}")
    print(summary.to_string())
    print(f"Wrote {len(features):,} plant points to {PLANTS_GEOJSON}")
    return plants


if __name__ == "__main__":
    build()
