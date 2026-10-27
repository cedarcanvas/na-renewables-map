"""Live fuel mix from the ISOs and BPA, averaged into hourly totals per BA.

EIA-930 generation has run up to a day behind, but the ISOs post their own fuel
mix every 5 minutes. This fetches yesterday and today (ISO local time) through
the gridstatus library and turns it into the same end-of-hour MW as EIA-930, so
generation.py can use it for the hours EIA hasn't published yet.

A feed's boundaries and categories don't always match EIA-930's (SPP's feed
includes its western RTO, which EIA reports as SWPW), so generation.py
calibrates each feed against EIA over the hours both cover before using it.

PJM's feed comes from Data Miner and needs PJM_API_KEY in .env; without it PJM
stays on EIA-930. Data Miner's terms: a non-member key is for internal use only,
and publishing data from it, or derived from it, needs PJM membership.
Output: data/live_generation.csv with columns utc, iso, fuel, mw.
"""

import datetime as dt
import logging
import time
import warnings

import pandas as pd

from .common import LIVE_GENERATION_CSV, USER_AGENT, env_value

# ISO -> (gridstatus class, EIA BAs the feed covers, {our fuel: ISO columns to add up}).
# A fuel left out here stays on EIA for those BAs.
ISOS = {
    "CAISO": ("CAISO", ["CISO"], {"wind": ["Wind"], "solar": ["Solar"],
                                  "hydro": ["Small Hydro", "Large Hydro"], "geothermal": ["Geothermal"]}),
    "ERCOT": ("Ercot", ["ERCO"], {"wind": ["Wind"], "solar": ["Solar"], "hydro": ["Hydro"]}),
    # SPP's feed covers both of its RTOs; EIA-930 splits them into SWPP and SWPW.
    "SPP": ("SPP", ["SWPP", "SWPW"], {"wind": ["Wind"], "solar": ["Solar"], "hydro": ["Hydro"]}),
    # MISO's feed has no hydro category, so MISO hydro stays on EIA. It also only
    # serves today (yesterday's rows come back empty), so its overlap with EIA
    # comes from the hours earlier runs kept.
    "MISO": ("MISO", ["MISO"], {"wind": ["Wind"], "solar": ["Solar"]}),
    # NYISO folds solar into "Other Renewables", so NYISO solar stays on EIA.
    "NYISO": ("NYISO", ["NYIS"], {"wind": ["Wind"], "hydro": ["Hydro"]}),
    "ISO-NE": ("ISONE", ["ISNE"], {"wind": ["Wind"], "solar": ["Solar"], "hydro": ["Hydro"]}),
    # Not an ISO, but Bonneville posts its own 5-minute data. "VER" is wind plus
    # solar; it's calibrated against EIA's BPAT wind, BPA's solar being small.
    "BPA": ("BPA", ["BPAT"], {"wind": ["VER"], "hydro": ["Hydro"]}),
    # Hourly, and only with PJM_API_KEY (see the module notes on Data Miner's terms).
    "PJM": ("PJM", ["PJM"], {"wind": ["Wind"], "solar": ["Solar"], "hydro": ["Hydro"]}),
}

# An hour needs at least this many readings to count; ISO-NE posts irregularly.
MIN_READINGS_PER_HOUR = 3

# Hours from earlier runs are kept this long. A feed's "yesterday" request can
# come back empty, and with hourly runs the history still overlaps EIA's data,
# which is what calibration needs.
KEEP = pd.Timedelta(days=4)


class BPAFeed:
    """Bonneville's public 5-minute BA data (last 7 days), shaped like a gridstatus fuel mix."""

    URL = "https://transmission.bpa.gov/business/operations/wind/baltwg.txt"
    default_timezone = "America/Los_Angeles"

    def get_fuel_mix(self, date):
        import io

        import requests

        r = requests.get(self.URL, headers={"User-Agent": USER_AGENT}, timeout=60)
        r.raise_for_status()
        # Eleven lines of notes, then a tab-separated table in Pacific time.
        df = pd.read_csv(io.StringIO(r.text), sep="\t", skiprows=11)
        df.columns = [c.strip() for c in df.columns]
        df["Time"] = (pd.to_datetime(df["Date/Time"], format="%m/%d/%Y %H:%M")
                      .dt.tz_localize(self.default_timezone, ambiguous="NaT", nonexistent="NaT"))
        return df.dropna(subset=["Time"])  # one week covers any `date` asked for


def _iso(cls_name):
    if cls_name == "BPA":
        return BPAFeed()

    import gridstatus as gs  # imported here so the rest of the pipeline works without it

    if cls_name == "PJM":
        key = env_value("PJM_API_KEY")
        return gs.PJM(api_key=key) if key else None
    return getattr(gs, cls_name)()


def hourly(df, columns):
    """5-minute (or irregular) fuel mix -> end-of-hour mean MW per fuel, complete hours only."""
    # Interval feeds are labelled by interval end; snapshot feeds by reading time.
    # Either way a reading at 18:40 or 19:00 belongs to the hour ending 19:00.
    stamp = df["Interval End"] if "Interval End" in df else df["Time"]
    stamp = pd.to_datetime(stamp, utc=True)
    # Requests can overlap (MISO's "yesterday" also returns today), so drop repeats.
    unique = ~stamp.duplicated(keep="last")
    df, stamp = df[unique], stamp[unique]
    hour = stamp.dt.ceil("h")
    # Hourly feeds (PJM) have one reading an hour; ask for several only from faster ones.
    spacing = stamp.sort_values().diff().median()
    min_readings = 1 if pd.notna(spacing) and spacing >= pd.Timedelta(minutes=45) else MIN_READINGS_PER_HOUR
    rows = []
    for fuel, cols in columns.items():
        present = [c for c in cols if c in df]
        if not present:
            continue
        mw = df[present].apply(pd.to_numeric, errors="coerce").sum(axis=1, min_count=1)
        g = pd.DataFrame({"utc": hour, "mw": mw}).dropna().groupby("utc")["mw"].agg(["mean", "size"])
        # The current hour is still filling up; keep only hours the feed has passed.
        # Judged by readings with values: BPA's file carries empty rows for later today.
        last_reading = stamp[mw.notna()].max()
        g = g[(g["size"] >= min_readings) & (g.index <= last_reading)]
        rows.append(pd.DataFrame({"utc": g.index, "fuel": fuel, "mw": g["mean"].to_numpy()}))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["utc", "fuel", "mw"])


def fetch_iso(name, cls_name, columns):
    iso = _iso(cls_name)
    if iso is None:
        return None, "skipped (no PJM_API_KEY)"
    today = pd.Timestamp.now(tz=iso.default_timezone).date()
    frames, notes = [], []
    for day in (today - dt.timedelta(days=1), "today"):
        try:
            frames.append(iso.get_fuel_mix(day))
        except Exception as e:  # one ISO or one day failing shouldn't stop the others
            notes.append(f"{day}: {type(e).__name__}")
    if not frames:
        return None, "failed (" + ", ".join(notes) + ")"
    df = hourly(pd.concat(frames, ignore_index=True), columns)
    df.insert(1, "iso", name)
    note = "ok" if not notes else "partial (" + ", ".join(notes) + ")"
    return df, note


def build():
    logging.getLogger("gridstatus").setLevel(logging.WARNING)
    warnings.filterwarnings("ignore", module="gridstatus")
    frames = []
    for name, (cls_name, _, columns) in ISOS.items():
        began = time.monotonic()
        df, note = fetch_iso(name, cls_name, columns)
        if df is not None and len(df):
            frames.append(df)
            last = df["utc"].max()
            print(f"  live {name:6s} {note}: {df['utc'].nunique()} hours to {last:%m-%d %H:%M} UTC "
                  f"({time.monotonic() - began:.0f}s)")
        else:
            print(f"  live {name:6s} {note}")
    fresh = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["utc", "iso", "fuel", "mw"])
    previous = pd.read_csv(LIVE_GENERATION_CSV, parse_dates=["utc"]) if LIVE_GENERATION_CSV.exists() else None
    if previous is not None and "iso" in previous:
        # This run's values win for hours both have; older hours carry over.
        live = pd.concat([previous, fresh], ignore_index=True).drop_duplicates(["utc", "iso", "fuel"], keep="last")
        now = pd.Timestamp.now(tz="UTC")
        # An hour ending after now can't be complete, whatever an earlier run stored.
        live = live[(live["utc"] >= now - KEEP) & (live["utc"] <= now.floor("h"))].sort_values(["iso", "fuel", "utc"])
    else:
        live = fresh
    LIVE_GENERATION_CSV.parent.mkdir(parents=True, exist_ok=True)
    live.to_csv(LIVE_GENERATION_CSV, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
    return live


if __name__ == "__main__":
    build()
