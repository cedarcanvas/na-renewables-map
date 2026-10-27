"""Canadian wind, hydro and solar plants, in the same shape as the US ones.

Wind: NRCan's Canadian Wind Turbine Database (current; one point per turbine,
grouped here into projects). Hydro and solar: the North American Cooperation on
Energy Information's renewable plants of 1 MW or more (2017; fine for hydro,
which rarely changes, thin for solar). Both come from NRCan's map services, as
their FTP downloads weren't reachable. Alberta's solar farms come from
OpenStreetMap (ODbL) instead, since most were built after 2017.

Each plant's `ba` is its province's grid region code, matching regions.py.
"""

import re

import pandas as pd
import requests

from .common import RAW, USER_AGENT

CWTD = "https://maps-cartes.services.geo.ca/server_serveur/rest/services/NRCan/wind_turbine_database2_en/MapServer"
NACEI = ("https://geoappext.nrcan.gc.ca/arcgis/rest/services/NACEI/"
         "energy_infrastructure_of_north_america_en/MapServer/28")
# Overpass asks clients to identify themselves; it refuses browser-style user agents.
OVERPASS = "https://overpass-api.de/api/interpreter"
OVERPASS_AGENT = "na-renewables-map/0.1 (prototype renewables map)"

# Province -> (abbreviation, grid region code). Territories run small isolated grids.
PROVINCES = {
    "Ontario": ("ON", "IESO"), "Quebec": ("QC", "HQT"), "Québec": ("QC", "HQT"),
    "New Brunswick": ("NB", "NBSO"), "Prince Edward Island": ("PE", "NBSO"),
    "Nova Scotia": ("NS", "NSPI"), "Newfoundland and Labrador": ("NL", "NLH"),
    "Manitoba": ("MB", "MHEB"), "Saskatchewan": ("SK", "SPC"), "Alberta": ("AB", "AESO"),
    "British Columbia": ("BC", "BCHA"),
}
ABBREV = {abbr: name for name, (abbr, _) in PROVINCES.items()}


def _query(layer_url, where):
    """All features of an ArcGIS layer matching `where`, as attribute dicts."""
    rows, offset = [], 0
    while True:
        r = requests.get(f"{layer_url}/query", headers={"User-Agent": USER_AGENT}, timeout=120, params={
            "where": where, "outFields": "*", "returnGeometry": "false", "f": "json",
            "resultOffset": offset, "resultRecordCount": 2000,
        })
        r.raise_for_status()
        page = r.json()
        rows += [f["attributes"] for f in page.get("features", [])]
        if not page.get("exceededTransferLimit"):
            return rows
        offset = len(rows)


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def wind_projects():
    layers = requests.get(CWTD, params={"f": "json"}, timeout=60).json()["layers"]
    rows = []
    for layer in layers:
        if layer["name"] in PROVINCES:
            rows += _query(f"{CWTD}/{layer['id']}", "1=1")
    t = pd.DataFrame(rows)
    t.to_csv(RAW / "cwtd_turbines.csv", index=False)
    # Several fields are text in the service (hub heights can read "8080…" for mixed turbines).
    for src, col in (("Turbine_Rated_Capacity_kW", "kw"), ("Hub_Height_m", "hub"), ("Latitude", "lat"),
                     ("Longitude", "lon"), ("Total_Project_Capacity_MW", "project_mw")):
        t[col] = pd.to_numeric(t[src], errors="coerce")
    t["year"] = pd.to_numeric(t["Commissioning"].astype(str).str[:4], errors="coerce")
    g = t.groupby(["Province_Territory", "Project_Name"]).agg(
        lat=("lat", "mean"), lon=("lon", "mean"), turbines=("kw", "size"),
        turbine_mw=("kw", lambda s: s.sum() / 1000), project_mw=("project_mw", "first"),
        op_year=("year", "min"), hub_m=("hub", "mean"),
    ).reset_index()
    # NRCan notes project capacity can differ from the sum of turbine ratings; prefer the project figure.
    g["cap"] = pd.to_numeric(g["project_mw"], errors="coerce").fillna(g["turbine_mw"])
    return pd.DataFrame({
        "name": g["Project_Name"], "fuel": "wind", "cap": g["cap"], "province": g["Province_Territory"],
        "lat": g["lat"], "lon": g["lon"], "units": g["turbines"], "op_year": g["op_year"], "hub_m": g["hub_m"],
    })


def hydro_solar_plants():
    rows = _query(NACEI, "Country = 'Canada'")
    p = pd.DataFrame(rows)
    p.to_csv(RAW / "nacei_canada.csv", index=False)
    out = []
    for fuel, col in (("hydro", "Hydro_MW"), ("solar", "Solar_MW")):
        mw = pd.to_numeric(p[col], errors="coerce")
        sel = p[mw > 0]
        out.append(pd.DataFrame({
            "name": sel["Facility"], "fuel": fuel, "cap": mw[mw > 0],
            "province": sel["StateProv"].map(lambda s: ABBREV.get(s, s)),
            "lat": pd.to_numeric(sel["Latitude"], errors="coerce"), "lon": pd.to_numeric(sel["Longitude"], errors="coerce"),
            "units": 1, "op_year": float("nan"), "hub_m": float("nan"),
        }))
    return pd.concat(out, ignore_index=True)


def _mw(tag):
    """OSM capacity tags ("37 MW", "1,080 KW", "20 MWp") -> MW, or NaN."""
    m = re.match(r"\s*([\d.,]+)\s*([kKmMgG])?[wW]", str(tag or ""))
    if not m:
        return float("nan")
    value = float(m.group(1).replace(",", ""))
    return value * {"k": 0.001, "m": 1, "g": 1000}.get((m.group(2) or "m").lower(), 1)


def osm_solar(province_code="CA-AB"):
    """Solar farms mapped in OpenStreetMap (ODbL). Alberta's solar is mostly newer than
    the 2017 NACEI data, and AESO reports each farm, so they need locations."""
    query = f"""[out:json][timeout:90];
        area["ISO3166-2"="{province_code}"]->.a;
        nwr["power"="plant"]["plant:source"="solar"](area.a);
        out center tags;"""
    r = requests.post(OVERPASS, data={"data": query}, headers={"User-Agent": OVERPASS_AGENT}, timeout=180)
    r.raise_for_status()
    rows = []
    for e in r.json()["elements"]:
        tags, centre = e.get("tags", {}), e.get("center", e)
        rows.append({
            "name": tags.get("name") or f"Solar farm (OSM {e['type']} {e['id']})", "fuel": "solar",
            "cap": _mw(tags.get("plant:output:electricity")), "province": ABBREV[province_code[-2:]],
            "lat": centre.get("lat"), "lon": centre.get("lon"), "units": 1,
            "op_year": float("nan"), "hub_m": float("nan"),
        })
    return pd.DataFrame(rows)


def plants():
    """Canadian plants as GeoJSON features in plants.geojson's shape."""
    nacei = hydro_solar_plants()
    # Alberta's solar comes from OpenStreetMap instead of the 2017 layer.
    nacei = nacei[~((nacei["province"] == "Alberta") & (nacei["fuel"] == "solar"))]
    df = pd.concat([wind_projects(), nacei, osm_solar("CA-AB")], ignore_index=True)
    df = df[df["province"].isin(PROVINCES) & df["lat"].notna() & df["lon"].notna() & (df["cap"] > 0)]
    features = []
    for p in df.itertuples():
        abbr, ba = PROVINCES[p.province]
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(p.lon, 5), round(p.lat, 5)]},
            "properties": {
                "id": f"CA-{abbr}-{slug(p.name)}-{p.fuel}",
                "name": p.name, "fuel": p.fuel, "cap": round(float(p.cap), 1), "ba": ba, "state": abbr,
                "country": "CA", "units": int(p.units),
                "op_year": None if pd.isna(p.op_year) else int(p.op_year),
            },
        })
    # Two NACEI facilities can share a name; keep ids unique.
    seen = {}
    for f in features:
        pid = f["properties"]["id"]
        seen[pid] = seen.get(pid, 0) + 1
        if seen[pid] > 1:
            f["properties"]["id"] = f"{pid}-{seen[pid]}"
    summary = df.groupby(["province", "fuel"])["cap"].agg(["size", "sum"]).round(0)
    print("Canadian plants (count, MW):")
    print(summary.unstack("fuel").fillna(0).astype(int).to_string())
    return features


if __name__ == "__main__":
    plants()
