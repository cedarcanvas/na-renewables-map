"""Ontario's metered hourly output per generator (IESO), matched to plants.

IESO publishes every transmission-connected generator's output each hour (the
Generator Output and Capability report), so Ontario plants show measured output
instead of estimates. IESO's generator codes ("BECK2", "PORTALMA-T1") are matched
to canada.py's plants by name, with the overrides below where names differ.
Output from codes left unmatched still counts: allocate.py spreads it across
Ontario's unmatched plants of that fuel, as it does for US regions.

Returns per-plant rows and Ontario totals by fuel; past days are cached.
"""

import difflib
import re

import pandas as pd

from .common import RAW

IESO_FUELS = {"WIND": "wind", "SOLAR": "solar", "HYDRO": "hydro"}

# IESO code -> plant name(s) in canada.py's data, where automatic matching fails
# or picks wrong. Several names split the code's output between them by capacity.
# "likely" marks links inferred from location or project history, not confirmed.
OVERRIDES = {
    "BECK2": ["Sir Adam Beck 2"],
    "BECK2 PGS": ["Sir Adam Beck 2"],          # pump-generating station beside Beck 2
    "BECK1": ["Sir Adam Beck 1"],
    "SAUNDERS": ["Robert H Saunders"],
    "CANYON": ["Abitibi Canyon"],
    "SMOKY 2": ["Smoky Falls"],
    "HOLDEN": ["Otto Holden"],
    "BARRETT": ["Barrett Chute"],
    "HARMON 2": ["Harmon"],
    "KIPLING 2": ["Kipling"],
    "LITTLELONG 2": ["Little Long"],
    "DECEWFALLS": ["Decew Falls 2"],
    "DECEWND1": ["Decew Falls 1"],
    "CAMERONFALLS": ["Cameron Falls - OPG"],
    "WHITEDOG": ["Whitedog Falls"],
    "MACKAYGS": ["MacKay"],
    "HOLINGSWTH": ["Hollingsworth"],
    "LONGSAULTE": ["Long Sault Rapids"],
    "REDROCK": ["Red Rock Falls"],
    "MTNCHUTE": ["Mountain Chute"],
    "STEWARTVLE": ["Stewartville"],
    "EARFALLS": ["Lac Seul / Ear Falls"],
    "ABKENORA": ["Kenora"],
    "FORTFRANCSWC": ["Fort Frances"],
    "NAGAGAMI": ["Shekak (Nagagami)"],
    "PETER SUTHERLAND SR": ["Peter Sutherlan Sr."],
    "APIROQUOIS": ["Iroquois Falls", "Island Falls (H2O)", "Twin Falls"],  # likely: the Abitibi River stations
    "KINGSTONSF": ["Sol-Luce Kingston"],
    "GRANDSF": ["Grand Renewable Energy Park"],
    "NORTHLAND POWER SOLAR FACILITIES": "Northland Power",  # every plant whose name contains this
    "K2WIND": ["K2 Wind Power Facility"],
    "AMARANTH": ["Melancthon"],
    "PORT BURWELL": ["Erie Shores"],
    "WEST LINCOLN NRWF": ["Niagara Region"],
    "HENVEY NORTH": ["Henvey Inlet Wind Farm"],
    "HENVEY SOUTH": ["Henvey Inlet Wind Farm"],
    "PAROCHES": ["Pointes Aux Roches"],
    "EAST LAKE": ["East Lake St. Clair Wind"],
    "RIPLEY SOUTH": ["Ripley"],
    "MCLEANSMTNWF-LT.AG_T1": ["McLean's Mountain"],
    "PRINCEFARM": ["Prince Wind Energy Project"],
    "GRANDWF": ["Grand Renewable Wind"],
    "BOW LAKE 2": ["Bow Lake Wind Project"],
    "CEDAR POINT 2": ["Cedar Point"],
    "GRAND VALLEY 3": ["Grand Valley"],
    "KINGSBRIDGE": ["Kingsbridge I Wind Power"],
    "ZURICH": ["Bluewater Wind Farm"],         # likely: the 0.8 MW "Zurich" turbine is a different project
    "CRYSLER": ["Nation Rise Wind Farm"],      # likely: Nation Rise is beside Crysler
}

_STOP = {"wind", "energy", "project", "farm", "centre", "center", "power", "generating", "station", "gs",
         "wf", "sf", "solar", "facility", "facilities", "inc", "lp", "limited", "ltd", "the", "park",
         "renewable", "hydro", "electric", "hydroelectric", "plant"}


def _norm(name):
    name = re.sub(r"-LT\.AG_\w+|-T\d+$|WGS$|NRWF$", "", str(name), flags=re.I)
    return "".join(w for w in re.split(r"[^a-z0-9]+", name.lower()) if w and w not in _STOP)


def crosswalk(ieso_names, plants):
    """{(IESO code, fuel): [plant ids]}, from OVERRIDES or an exact match on normalised names."""
    out = {}
    for code, fuel in ieso_names:
        cands = plants[plants["fuel"] == fuel]
        target = OVERRIDES.get(code)
        if isinstance(target, str):
            ids = cands.loc[cands["name"].str.contains(target, regex=False), "id"].tolist()
        elif target:
            ids = cands.loc[cands["name"].isin(target), "id"].tolist()
        else:
            key = _norm(code)
            ids = cands.loc[cands["name"].map(_norm) == key, "id"].tolist()[:1]
        if ids:
            out[(code, fuel)] = ids
    return out


def report(day):
    """IESO's hourly generator report for one day (EST), cached once the day is over."""
    import gridstatus as gs

    path = RAW / "ieso" / f"{day:%Y%m%d}.csv"
    if path.exists():
        return pd.read_csv(path, parse_dates=["Interval End"])
    df = gs.IESO().get_generator_report_hourly(day)
    if day < pd.Timestamp.now(tz="EST").date():
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)
    return df


def build(since, plants):
    """(per-plant rows: utc, plant_id, mw; Ontario totals: utc, fuel, mw) from `since` on."""
    days = pd.date_range(since.tz_convert("EST").normalize(), pd.Timestamp.now(tz="EST").normalize())
    df = pd.concat([report(d.date()) for d in days], ignore_index=True)
    df = df[df["Fuel Type"].isin(IESO_FUELS) & df["Output MW"].notna()]
    df = df.assign(utc=pd.to_datetime(df["Interval End"], utc=True), fuel=df["Fuel Type"].map(IESO_FUELS))
    df = df[df["utc"] >= since]

    on = plants[plants["ba"] == "IESO"]
    links = crosswalk(df[["Generator Name", "fuel"]].drop_duplicates().itertuples(index=False), on)
    cap = on.set_index("id")["cap"]
    rows = []
    for (code, fuel), ids in links.items():
        g = df[(df["Generator Name"] == code) & (df["fuel"] == fuel)]
        share = cap[ids] / cap[ids].sum()
        for pid, s in share.items():
            rows.append(pd.DataFrame({"utc": g["utc"], "plant_id": pid, "mw": g["Output MW"].to_numpy() * s}))
    per_plant = (pd.concat(rows).groupby(["utc", "plant_id"], as_index=False)["mw"].sum()
                 if rows else pd.DataFrame(columns=["utc", "plant_id", "mw"]))
    totals = df.groupby(["utc", "fuel"], as_index=False)["Output MW"].sum().rename(columns={"Output MW": "mw"})

    codes = df[["Generator Name", "fuel"]].drop_duplicates()
    unmatched = [c for c in codes.itertuples(index=False) if tuple(c) not in links]
    print(f"  Ontario: {len(links)} of {len(codes)} IESO generators matched to "
          f"{per_plant['plant_id'].nunique()} plants; unmatched: {', '.join(c[0] for c in unmatched) or 'none'}")
    return per_plant, totals
