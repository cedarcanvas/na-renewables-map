"""Feeds that only show the current moment: AESO (Alberta, every asset), NS Power,
NB Power and SaskPower.

Each run reads them once and adds the readings to data/snapshots.csv, so the
hourly runs build up the history these feeds don't keep themselves. A reading
taken in the first half hour stands for the hour just ended (the hourly job runs
at :10); later ones for the hour in progress. Readings in the same hour are
averaged.

  AESO       each wind, solar and hydro asset's net generation (TNG) and capability
  NS Power   wind generation, plus the Maritime Link flow from Newfoundland
  NB Power   net *scheduled* interchange with each neighbour (no generation by fuel)
  SaskPower  wind, hydro and solar totals; in practice a summary of the previous day
"""

import io
import json
import re

import pandas as pd
import requests
from lxml import html

from .common import RAW, USER_AGENT

SNAPSHOTS_CSV = RAW.parent / "snapshots.csv"
ASSETS_CSV = RAW / "aeso_assets.csv"
# The map needs only 2 days; the rest is Alberta's per-plant history for backtest.py,
# which AESO doesn't keep for us (about 4 MB a month).
KEEP = pd.Timedelta(days=30)

AESO_URL = "http://ets.aeso.ca/ets_web/ip/Market/Reports/CSDReportServlet"
NS_URL = "https://resourcesprd-nspower.aws.silvertech.net/oasis/current_report.shtml"
NB_URL = "https://tso.nbpower.com/Public/en/SystemInformation_realtime.asp"
SK_URL = "https://www.saskpower.com/ignitionapi/PowerUseDashboard/GetPowerUseDashboardData"

AESO_FUELS = {"WIND": "wind", "SOLAR": "solar", "HYDRO": "hydro"}
# NB Power's column -> the neighbour it schedules with. EMEC and MPS both serve northern Maine.
# Québec is left out: Hydro-Québec reports that link itself, hourly.
NB_LINKS = {"ISO-NE": "New England", "EMEC": "Northern Maine", "MPS": "Northern Maine",
            "NOVA SCOTIA": "Nova Scotia", "PEI": "PEI"}
COLUMNS = ["utc", "source", "kind", "key", "fuel", "mw"]


def _get(url):
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
    r.raise_for_status()
    return r.content


def _local(text, fmt, tz):
    return pd.Timestamp(pd.to_datetime(text, format=fmt)).tz_localize(tz).tz_convert("UTC")


def aeso():
    """One row per wind, solar and hydro asset; also refreshes the asset list."""
    doc = html.fromstring(_get(AESO_URL))
    stamp = re.search(r"Last Update\s*:\s*([A-Za-z]{3} \d+, \d{4} \d+:\d+)", doc.text_content())
    utc = _local(stamp.group(1), "%b %d, %Y %H:%M", "America/Edmonton")
    rows, assets = [], []
    for table in doc.xpath("//table"):
        cells = [[" ".join(c.text_content().split()) for c in tr.xpath("./td|./th")] for tr in table.xpath(".//tr")]
        cells = [r for r in cells if any(r)]
        if not cells or cells[0][0] not in AESO_FUELS or len(cells[0]) != 1:
            continue
        fuel = AESO_FUELS[cells[0][0]]
        for name, mc, tng, *_ in (r for r in cells[1:] if len(r) >= 3 and r[0] != "ASSET"):
            code = re.search(r"\(([A-Z0-9]+)\)", name)
            if not code:
                continue
            rows.append([utc, "AESO", "asset", code.group(1), fuel, float(tng)])
            assets.append({"code": code.group(1), "name": re.sub(r"\s*\([A-Z0-9]+\)\*?$", "", name),
                           "fuel": fuel, "mc": float(mc)})
    pd.DataFrame(assets).drop_duplicates("code").to_csv(ASSETS_CSV, index=False)
    return rows


def ns_power():
    doc = html.fromstring(_get(NS_URL))
    for s in doc.xpath("//style|//script"):
        s.drop_tree()
    text = " ".join(doc.text_content().split())
    stamp = re.search(r"Current System Conditions Last Updated: (\d+-\w+-\d+ \d+:\d+)", text)
    utc = _local(stamp.group(1), "%d-%b-%y %H:%M", "America/Halifax")
    value = lambda label: float(re.search(re.escape(label) + r"\s*(-?[\d.]+)", text).group(1))
    return [
        [utc, "NS Power", "total", "NSPI", "wind", value("Wind Generation")],
        # The Maritime Link brings Newfoundland's hydro in. (NS–NB comes from NB Power.)
        [utc, "NS Power", "flow", "Newfoundland→Nova Scotia", "", value("Maritime Link Import")],
    ]


def nb_power():
    doc = html.fromstring(_get(NB_URL))
    rows = [[" ".join(c.text_content().replace("\xa0", " ").split()) for c in tr.xpath("./td|./th")]
            for tr in doc.xpath("//table//tr")]
    header = next(r for r in rows if "ISO-NE" in r)
    values = rows[rows.index(header) + 1]
    stamp = re.search(r"(\w{3} \d+, \d{4} \d+:\d+:\d+) Atlantic Time", doc.text_content())
    utc = _local(stamp.group(1), "%b %d, %Y %H:%M:%S", "America/Halifax")
    flows = {}
    for col, val in zip(header, values):
        if col in NB_LINKS:  # positive = export from New Brunswick
            flows[NB_LINKS[col]] = flows.get(NB_LINKS[col], 0.0) + float(val)
    return [[utc, "NB Power", "flow", f"New Brunswick→{n}", "", mw] for n, mw in flows.items()]


def saskpower():
    data = json.loads(_get(SK_URL))["PowerCacheData"]
    utc = pd.Timestamp(int(re.search(r"-?\d+", data["asOfDate"]).group()), unit="ms", tz="UTC")
    fuels = {"Wind": "wind", "Hydro": "hydro", "Solar": "solar"}
    return [[utc, "SaskPower", "total", "SPC", fuels[t["type"]], float(t["totalGenerationForType"])]
            for t in data["generationByType"] if t["type"] in fuels]


def build():
    """Read every feed once, add to the stored readings, and return all of them."""
    rows = []
    for name, fetch in (("AESO", aeso), ("NS Power", ns_power), ("NB Power", nb_power), ("SaskPower", saskpower)):
        try:
            got = fetch()
            rows += got
            print(f"  snapshot {name:9s} ok: {len(got)} readings at {got[0][0]:%m-%d %H:%M} UTC")
        except Exception as e:  # one feed failing (or changing its page) shouldn't stop the rest
            print(f"  snapshot {name:9s} failed ({type(e).__name__}: {e})")
    new = pd.DataFrame(rows, columns=COLUMNS)
    if SNAPSHOTS_CSV.exists():
        old = pd.read_csv(SNAPSHOTS_CSV, parse_dates=["utc"])
        both = pd.concat([old, new], ignore_index=True).drop_duplicates(["utc", "source", "kind", "key", "fuel"], keep="last")
    else:
        both = new
    both = both[both["utc"] >= pd.Timestamp.now(tz="UTC") - KEEP]
    SNAPSHOTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    both.to_csv(SNAPSHOTS_CSV, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
    return both


def hourly(snapshots):
    """Readings -> end-of-hour values: utc, source, kind, key, fuel, mw."""
    s = snapshots.copy()
    s["fuel"] = s["fuel"].fillna("")
    early = s["utc"].dt.minute < 30
    s["utc"] = s["utc"].dt.floor("h").where(early, s["utc"].dt.ceil("h"))
    return s.groupby(["utc", "source", "kind", "key", "fuel"], as_index=False)["mw"].mean()


if __name__ == "__main__":
    snaps = build()
    print(hourly(snaps).groupby(["source", "kind"]).agg(rows=("mw", "size"), last=("utc", "max")).to_string())
