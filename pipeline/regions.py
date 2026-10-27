"""Grid-region outlines for the map: US balancing authorities and Canadian provinces.

US: HIFLD "Control Areas", archived on source.coop after HIFLD Open shut down
in 2025, matched to EIA-930 codes by EIA utility ID. EIA notes that BAs have no
precise geographic boundaries, so these are rough extents, and some overlap.
Canada: Natural Earth provinces, since each province is its own grid operator's
area (Ontario is IESO, Québec is Hydro-Québec, and so on).

Output: web/data/regions.geojson with a polygon and a label point per region,
properties code, name, short, country.
"""

import json
import subprocess
import tempfile
from pathlib import Path

from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from .common import BA_ALIASES, RAW, WEB_DATA, download

REGIONS_GEOJSON = WEB_DATA / "regions.geojson"
HIFLD_URL = ("https://data.source.coop/seerai/hifld/control-areas/control-areas/control-areas.parquet/"
             "part-00000-tid-1366803119161179266-3b93870a-5d6f-46f5-a28f-bb86485e7f9b-223-1-c000.zstd.parquet")
PROVINCES_URL = "https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_1_states_provinces_lakes.zip"

# Degrees; about 1–2 km, plenty for outlines at continental zooms.
SIMPLIFY = 0.015

# HIFLD control-area ID (EIA utility ID) -> EIA-930 BA code.
EIA_CODES = {
    "317": "YAD", "803": "AZPS", "56812": "DEAA", "924": "AECI", "15399": "AVRN", "20169": "AVA",
    "16534": "BANC", "1738": "BPAT", "2775": "CISO", "3522": "CEA", "8795": "HST", "18429": "TPWR",
    "18445": "TAL", "5416": "DUK", "6455": "FPC", "3046": "CPLE", "58786": "CPLW", "5701": "EPE",
    "5748": "EEI", "5723": "ERCO", "14610": "FMPP", "6452": "FPL", "6909": "GVL", "14412": "GRMA",
    "58790": "GRID", "56545": "GRIS", "56090": "GRIF", "19547": "HECO", "9191": "IPCO", "9216": "IID",
    "13434": "ISNE", "9617": "JEA", "11208": "LDWP", "11249": "LGEE", "56669": "MISO", "56365": "GWA",
    "58791": "WWA", "13407": "NEVP", "1": "NBSO", "55372": "HGMA", "13485": "NSB", "13501": "NYIS",
    "12825": "NWMT", "14015": "OVEC", "14379": "PACE", "14378": "PACW", "14725": "PJM", "15248": "PGE",
    "189": "AEC", "15466": "PSCO", "15473": "PNM", "3413": "CHPD", "14624": "GCPD", "5326": "DOPD",
    "15500": "PSEI", "16572": "SRP", "16868": "SCL", "21554": "SEC", "17539": "SCEG", "17543": "SC",
    "29304": "SEPA", "18195": "SOCO", "59504": "SWPP", "17716": "SPA", "18454": "TEC", "18642": "TVA",
    "24211": "TEPC", "19281": "TIDC", "25471": "WALC", "28503": "WACM", "19610": "WAUW",
}

# Canadian province (Natural Earth name) -> grid region code and name. New Brunswick
# comes from Natural Earth too, so all of Canada is drawn the same way; PEI sits
# inside NB Power's balancing area.
PROVINCES = {
    "Ontario": ("IESO", "IESO (Ontario)"),
    "Québec": ("HQT", "Hydro-Québec"),
    "New Brunswick": ("NBSO", "New Brunswick"),
    "Prince Edward Island": ("NBSO", "New Brunswick"),
    "Nova Scotia": ("NSPI", "Nova Scotia Power"),
    "Newfoundland and Labrador": ("NLH", "Newfoundland and Labrador Hydro"),
    "Manitoba": ("MHEB", "Manitoba Hydro"),
    "Saskatchewan": ("SPC", "SaskPower"),
    "Alberta": ("AESO", "AESO (Alberta)"),
    "British Columbia": ("BCHA", "BC Hydro"),
}

# Short labels for the map; anything missing falls back to its code.
SHORT = {
    "ERCO": "ERCOT", "CISO": "CAISO", "SWPP": "SPP", "SWPW": "SPP West", "NYIS": "NYISO",
    "ISNE": "ISO-NE", "BPAT": "BPA", "SOCO": "Southern", "DUK": "Duke", "CPLE": "Duke Prog. E",
    "CPLW": "Duke Prog. W", "PACE": "PacifiCorp E", "PACW": "PacifiCorp W", "HQT": "Hydro-Québec",
    "BCHA": "BC Hydro", "MHEB": "Manitoba Hydro", "SPC": "SaskPower", "NSPI": "NS Power",
    "NLH": "NL Hydro", "IESO": "IESO", "AESO": "AESO", "NBSO": "NB Power",
}


def _tidy(name):
    """HIFLD names are upper case; title-case them without 'Council Of Texas'."""
    small = {"Of", "And", "The", "For", "De"}
    words = name.strip().rstrip(".").title().split()
    return " ".join(w.lower() if i and w in small else w for i, w in enumerate(words))


def _ogr_to_geojson(src, dst, *args):
    subprocess.run(["ogr2ogr", "-f", "GeoJSON", "-simplify", str(SIMPLIFY),
                    "-lco", "COORDINATE_PRECISION=4", dst, src, *args], check=True)
    return json.loads(Path(dst).read_text())["features"]


def build():
    hifld = download(HIFLD_URL, RAW / "hifld" / "control-areas.parquet")
    with tempfile.TemporaryDirectory() as tmp:
        us = _ogr_to_geojson(str(hifld), f"{tmp}/us.geojson", "-a_srs", "EPSG:4326", "-select", "ID,NAME")
        ca = _ogr_to_geojson(f"/vsizip//vsicurl/{PROVINCES_URL}", f"{tmp}/ca.geojson",
                             "-where", "iso_a2 = 'CA'", "-select", "name")

    shapes, names, country = {}, {}, {}
    for f in us:
        code = EIA_CODES.get(str(f["properties"]["ID"]))
        if not code:
            print(f"  no EIA code for control area {f['properties']['ID']} {f['properties']['NAME']}")
            continue
        code = BA_ALIASES.get(code, code)
        shapes.setdefault(code, []).append(shape(f["geometry"]))
        names.setdefault(code, _tidy(f["properties"]["NAME"]))
        country[code] = "US"
    names["SWPW"] = "Southwest Power Pool RTO West"
    for f in ca:
        if f["properties"]["name"] not in PROVINCES:
            continue  # territories run small isolated grids
        code, name = PROVINCES[f["properties"]["name"]]
        shapes.setdefault(code, []).append(shape(f["geometry"]))
        names[code], country[code] = name, "CA"

    features = []
    for code, parts in sorted(shapes.items()):
        geom = unary_union([g.buffer(0) for g in parts])
        props = {"code": code, "name": names[code], "short": SHORT.get(code, code), "country": country[code]}
        features.append({"type": "Feature", "geometry": mapping(geom), "properties": props})
        # Inside the shape, unlike a centroid, which can fall outside an odd outline.
        point = geom.representative_point()
        features.append({"type": "Feature", "geometry": mapping(point), "properties": props})

    REGIONS_GEOJSON.parent.mkdir(parents=True, exist_ok=True)
    REGIONS_GEOJSON.write_text(json.dumps({"type": "FeatureCollection", "features": features},
                                          separators=(",", ":")))
    print(f"Wrote {len(features) // 2} grid regions to {REGIONS_GEOJSON} "
          f"({REGIONS_GEOJSON.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    build()
