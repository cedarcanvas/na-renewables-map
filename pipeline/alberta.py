"""Alberta's metered output per plant, from AESO's per-asset snapshots.

snapshots.py stores AESO's reading for every wind, solar and hydro asset each
hour; this matches AESO's asset names to canada.py's plants (wind from NRCan's
turbine database, solar from OpenStreetMap, hydro from NACEI). A confident
name match is taken automatically; the overrides cover names that differ.
Output from unmatched assets still counts: allocate.py spreads it across
Alberta's unmatched plants of that fuel.
"""

import difflib
import re

import pandas as pd

from .snapshots import ASSETS_CSV

AUTO_MATCH = 0.85  # name similarity needed to accept a match without an override

# AESO asset code -> plant name(s). Several names split the asset's output by capacity.
# "likely" marks links inferred from location or project history, not confirmed.
OVERRIDES = {
    # TransAlta's Bow River chain reports as one asset.
    "BOW1": ["Barrier", "Bearspaw", "Cascade", "Ghost", "Horseshoe", "Interlakes", "Kananaskis",
             "Pocaterra", "Rundle", "Spray", "Three Sisters"],
    "RYMD": ["Raymond"],
    "CHIN": ["Chin Hydro Plant"],
    "TAY1": ["Taylor Coulee"],
    "CHP1": ["Chappice Lake Solar-Storage Project"],
    "WEF1": ["Yellow Lake Solar"],
    "KKP1": ["kīsikāw pīsim Solar Farm"],
    "KKP2": ["kīsikāw pīsim Solar Farm"],
    "VCN1": ["Vulcan Solar"],
    "MON1": ["Monarch Solar"],
    "COL1": ["Coaldale Solar"],
    "NMK1": ["East Strathmore Solar Project"],
    "MCH1": ["Michichi Creek Solar Farm"],
    # Elemental's two Brooks farms: the 15 MW original, then Brooks Solar II (28 MW), which
    # AESO reports as two assets ("Brooks Solar 1" and "2": 13 + 14 MW).
    "BSC1": ["Brooks Solar 1"],
    "BRK1": ["Brooks Solar II Project"],
    "BRK2": ["Brooks Solar II Project"],
    # Two assets named for Burdett: BluEarth's 20 MW farm, and the 11 MW one beside it,
    # which OSM calls Alberta Solar One (10.5 MW, Enbridge and Morgan Solar).
    "BUR1": ["Burdett Solar"],
    "BRD1": ["Alberta Solar One"],       # likely
    "ICP1": ["St. Mary River"],          # likely: Irrican Power's 7 MW hydro plant on the St. Mary canal
    # Solar assets named for their substations (OSM or the Global Energy Monitor wiki for the
    # farms; codes as on Wikipedia's list of Canadian PV stations and dispatcho.app).
    "ACD1": ["Big Sky Solar"],           # Acadia Valley
    "CRD1": ["Wrentham Solar Project"],  # Conrad substation; the two phases, 23 + 18 MW
    "CRD2": ["Wrentham Solar Project"],
    "FCS1": ["Fox Coulee Solar"],
    "HUL1": ["Prairie Sunlight II Solar"],
    "VXH1": ["Prairie Sunlight III Solar"],
    "DUS1": ["Duchess Solar"],
    "GLE1": ["Gleichen Solar"],
    "JFS1": ["Joffre Solar"],
    "JFS2": ["Joffre Solar"],
    "KIL1": ["Killarney Lake Solar"],
    "CLD1": ["Lethbridge Solar"],
    "SDL1": ["Saddlebrook Solar"],
    "SGC1": ["Spring Coulee Solar"],
    "YNG1": ["Youngstown Solar"],
    "TAB1": ["Taber Wind Farm"],
    "SCR2": ["Magrath"],
    "SCR3": ["Chin Chute"],
    "AKE1": ["McBride Lake"],
    "CRR1": ["Castle Rock Ridge"],
    "GRZ1": ["Grizzly Bear Creek Wind"],
    "WHE1": ["Wheatland WAGF Project"],
    "OWF1": ["Oldman River"],            # likely: Oldman 2 wind farm near Pincher Creek
    "CRE3": ["Cowley North"],            # likely: the repowered Cowley Ridge site
}

_STOP = {"wind", "solar", "hydro", "power", "project", "farm", "energy", "facility", "park", "centre",
         "plant", "generating", "station", "the", "inc", "lp", "ltd", "renewable", "windfarm"}


def _norm(name):
    name = re.sub(r"^[A-Z0-9]{3,5}\s+", "", str(name))  # AESO prefixes some names with the code
    return "".join(w for w in re.split(r"[^a-z0-9]+", name.lower()) if w and w not in _STOP)


def crosswalk(assets, plants):
    """{asset code: [plant ids]} from OVERRIDES or a confident name match."""
    out = {}
    for a in assets.itertuples():
        cands = plants[plants["fuel"] == a.fuel]
        if a.code in OVERRIDES:
            ids = cands.loc[cands["name"].isin(OVERRIDES[a.code]), "id"].tolist()
        else:
            key = _norm(a.name)
            scored = [(difflib.SequenceMatcher(None, key, _norm(n)).ratio(), pid) for n, pid in zip(cands["name"], cands["id"])]
            best = max(scored, default=(0, None))
            ids = [best[1]] if best[0] >= AUTO_MATCH else []
        if ids:
            out[a.code] = ids
    return out


def build(hourly_snapshots, plants, since):
    """(per-plant rows: utc, plant_id, mw; Alberta totals: utc, fuel, mw) from `since` on."""
    readings = hourly_snapshots[(hourly_snapshots["source"] == "AESO") & (hourly_snapshots["kind"] == "asset")
                                & (hourly_snapshots["utc"] >= since)]
    assets = pd.read_csv(ASSETS_CSV)
    ab = plants[plants["ba"] == "AESO"]
    links = crosswalk(assets, ab)
    cap = ab.set_index("id")["cap"]
    rows = []
    for code, ids in links.items():
        g = readings[readings["key"] == code]
        for pid, share in (cap[ids] / cap[ids].sum()).items():
            rows.append(pd.DataFrame({"utc": g["utc"], "plant_id": pid, "mw": g["mw"].to_numpy() * share}))
    per_plant = (pd.concat(rows).groupby(["utc", "plant_id"], as_index=False)["mw"].sum()
                 if rows else pd.DataFrame(columns=["utc", "plant_id", "mw"]))
    totals = readings.groupby(["utc", "fuel"], as_index=False)["mw"].sum()
    unmatched = sorted(set(assets["code"]) - set(links))
    print(f"  Alberta: {len(links)} of {len(assets)} AESO assets matched to {per_plant['plant_id'].nunique()} plants; "
          f"{readings['utc'].nunique()} hours of readings; unmatched: {', '.join(unmatched) or 'none'}")
    return per_plant, totals
