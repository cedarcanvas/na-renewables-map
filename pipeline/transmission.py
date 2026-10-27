"""Transmission lines for the map, styled by voltage.

US: HIFLD "Transmission Lines", archived on source.coop after HIFLD Open shut down in
2025: lines in service or of unknown status. Canada: OpenStreetMap power=line ways
(ODbL), fetched from Overpass province by province. NRCan's CanVec has power lines too,
but with no voltage (and a 2019 national file); most OSM lines carry one (about 90% in
Québec).

Output: web/data/transmission.pmtiles, one vector layer "lines" with properties
kv (highest voltage in kV, -1 if unknown), cls (voltage class 0–4, -1 if unknown),
dc (1 for direct current), owner, name, ends (substations at each end, US only) and
country. Lower classes only enter the tiles from higher zooms (CLASSES), so the
continental tiles carry just the backbone.
"""

import itertools
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

from .canada import OVERPASS, OVERPASS_AGENT
from .common import RAW, WEB_DATA, download

TRANSMISSION_PMTILES = WEB_DATA / "transmission.pmtiles"
HIFLD_URL = ("https://data.source.coop/seerai/hifld/transmission-lines/transmission-lines/transmission-lines.parquet/"
             "part-00000-tid-2544231510869868098-1f58cb0d-7488-4777-823f-5046fbe7c5a9-163-1-c000.zstd.parquet")
OSM_DIR = RAW / "osm"
# Provinces and territories (the territories' small grids are drawn too).
CANADA = ["AB", "BC", "MB", "NB", "NL", "NS", "ON", "PE", "QC", "SK", "NT", "NU", "YT"]
# Ontario and Québec are fetched in latitude bands: whole, Overpass's gateway gives up.
BANDS = {"ON": [(41, 44.5), (44.5, 47), (47, 57)], "QC": [(44, 46.5), (46.5, 49), (49, 63)]}

# Voltage classes: (lowest kV, zoom the class first appears at). Class 0 is index 0.
CLASSES = [(0, 7), (100, 6), (200, 5), (300, 2), (500, 2)]
UNKNOWN_MIN_ZOOM = 7
MAX_ZOOM = 10
# HIFLD's class names, for lines whose own voltage field is missing.
HIFLD_CLASS = {"UNDER 100": 0, "SUB 100": 0, "100-161": 1, "220-287": 2, "345": 3, "500": 4, "735 AND ABOVE": 4}


def voltage_class(kv):
    if kv is None or kv <= 0:
        return -1
    return max(i for i, (low, _) in enumerate(CLASSES) if kv >= low)


def _clean(s):
    """HIFLD fills blanks with 'NOT AVAILABLE' and invents 'UNKNOWN123' substations."""
    s = (s or "").strip()
    return "" if not s or s.upper().startswith(("NOT AVAILABLE", "UNKNOWN")) else s


def us_lines():
    """HIFLD lines in service (or of unknown status) as GeoJSON features."""
    src = download(HIFLD_URL, RAW / "hifld" / "transmission-lines.parquet")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "us.geojsonl"
        subprocess.run(["ogr2ogr", "-f", "GeoJSONSeq", str(out), str(src), "transmission-lines",
                        "-a_srs", "EPSG:4326", "-where", "STATUS IN ('IN SERVICE', 'NOT AVAILABLE')",
                        "-select", "VOLTAGE,VOLT_CLASS,TYPE,OWNER,SUB_1,SUB_2",
                        # ~20 m: finer than the tiles' own resolution at the highest zoom.
                        "-simplify", "0.0002", "-lco", "COORDINATE_PRECISION=5"], check=True)
        with open(out) as f:
            for line in f:
                feat = json.loads(line.lstrip("\x1e"))
                p = feat["properties"]
                try:
                    kv = float(p["VOLTAGE"])
                except (TypeError, ValueError):
                    kv = -1
                kv = kv if kv > 0 else -1
                dc = p["VOLT_CLASS"] == "DC" or (p["TYPE"] or "").startswith("DC")
                cls = voltage_class(kv) if kv > 0 else HIFLD_CLASS.get(p["VOLT_CLASS"], 4 if dc else -1)
                ends = " – ".join(e for e in (_clean(p["SUB_1"]).title(), _clean(p["SUB_2"]).title()) if e)
                feat["properties"] = {"kv": round(kv), "cls": cls, "dc": int(dc), "owner": _clean(p["OWNER"]).title(),
                                      "name": "", "ends": ends, "country": "US"}
                yield feat


def _overpass(province, refresh, band=None, part=""):
    """Raw Overpass JSON of a province's power lines (or one latitude band of them),
    cached under data/raw/osm."""
    path = OSM_DIR / f"power-lines-CA-{province}{part}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    bbox = f"({band[0]},-141,{band[1]},-52)" if band else ""
    query = f"""[out:json][timeout:300];
area["ISO3166-2"="CA-{province}"]->.p;
way["power"="line"](area.p){bbox};
out tags geom qt;"""
    for attempt in range(4):
        r = requests.post(OVERPASS, data={"data": query}, headers={"User-Agent": OVERPASS_AGENT}, timeout=360)
        if r.status_code in (429, 502, 503, 504):  # busy or overloaded: wait and retry
            time.sleep(30 * (attempt + 1))
            continue
        r.raise_for_status()
        break
    else:
        r.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(r.text)
    time.sleep(2)  # be gentle with the public Overpass server
    return r.json()


def _province_elements(province, refresh):
    if province not in BANDS:
        return _overpass(province, refresh)["elements"]
    return [e for i, band in enumerate(BANDS[province])
            for e in _overpass(province, refresh, band, f"-{i + 1}")["elements"]]


def _osm_kv(tag):
    """'230000;69000' -> 230.0. Values under 1,000 are taken as kV already."""
    volts = [float(v) for v in re.findall(r"\d+(?:\.\d+)?", tag or "")]
    return max((v / 1000 if v >= 1000 else v) for v in volts) if volts else -1


def canada_lines(refresh=False):
    """OSM power lines in each province, once each (a way on a border, or crossing a
    band's edge, comes back twice)."""
    seen = set()
    for province in CANADA:
        for e in _province_elements(province, refresh):
            if e["id"] in seen or "geometry" not in e:
                continue
            seen.add(e["id"])
            t = e.get("tags", {})
            if t.get("line") in ("busbar", "bay"):  # wiring inside substations
                continue
            kv = _osm_kv(t.get("voltage"))
            yield {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[round(g["lon"], 5), round(g["lat"], 5)] for g in e["geometry"]]},
                "properties": {"kv": round(kv), "cls": voltage_class(kv), "dc": int(t.get("frequency") == "0"),
                               "owner": t.get("operator", ""), "name": t.get("name", ""), "ends": "", "country": "CA"},
            }


def build(refresh=False):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # One file (and source layer) per class: c0–c4, and cu for unknown voltage.
        name = lambda cls: f"c{cls}" if cls >= 0 else "cu"
        files = {cls: open(tmp / f"{name(cls)}.geojsonl", "w") for cls in range(-1, len(CLASSES))}
        counts = {}
        for feat in itertools.chain(us_lines(), canada_lines(refresh)):
            p = feat["properties"]
            files[p["cls"]].write(json.dumps(feat, separators=(",", ":")) + "\n")
            counts[(p["country"], p["cls"])] = counts.get((p["country"], p["cls"]), 0) + 1
        for f in files.values():
            f.close()

        # One source layer per class, all written to the tiles' single "lines" layer
        # from the zoom that class first appears at.
        vrt = tmp / "lines.vrt"
        vrt.write_text("<OGRVRTDataSource>" + "".join(
            f'<OGRVRTLayer name="{name(cls)}"><SrcDataSource>{tmp / f"{name(cls)}.geojsonl"}</SrcDataSource>'
            f"<SrcLayer>{name(cls)}</SrcLayer><LayerSRS>EPSG:4326</LayerSRS></OGRVRTLayer>" for cls in files)
            + "</OGRVRTDataSource>")
        conf = {name(cls): {"target_name": "lines",
                            "minzoom": CLASSES[cls][1] if cls >= 0 else UNKNOWN_MIN_ZOOM, "maxzoom": MAX_ZOOM}
                for cls in files}
        out = tmp / "transmission.pmtiles"
        subprocess.run(["ogr2ogr", "-f", "PMTiles", str(out), str(vrt),
                        "-dsco", "MINZOOM=2", "-dsco", f"MAXZOOM={MAX_ZOOM}",
                        "-dsco", f"CONF={json.dumps(conf)}", "-dsco", "SIMPLIFICATION=4",
                        "-dsco", "NAME=Transmission lines"], check=True)
        TRANSMISSION_PMTILES.parent.mkdir(parents=True, exist_ok=True)
        out.replace(TRANSMISSION_PMTILES)

    for country in ("US", "CA"):
        by_cls = ", ".join(f"{label}: {counts.get((country, cls), 0):,}" for cls, label in
                           [(4, "≥500 kV"), (3, "300–499"), (2, "200–299"), (1, "100–199"), (0, "<100"), (-1, "unknown")])
        print(f"  {country} lines – {by_cls}")
    print(f"Wrote {TRANSMISSION_PMTILES} ({TRANSMISSION_PMTILES.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    build(refresh="--refresh" in sys.argv[1:])
