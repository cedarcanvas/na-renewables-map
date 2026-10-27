"""Live fuel mix from the seven ISOs, averaged into hourly totals per BA.

EIA-930 generation has run up to a day behind, but the ISOs post their own fuel
mix every 5 minutes. This fetches yesterday and today (ISO local time) through
the gridstatus library and turns it into the same end-of-hour MW as EIA-930, so
generation.py can use it for the hours EIA hasn't published yet.

A feed's boundaries and categories don't always match EIA-930's (SPP's feed
includes its western RTO, which EIA reports as SWPW), so generation.py
calibrates each feed against EIA over the hours both cover before using it.

PJM needs a free Data Miner API key (PJM_API_KEY in .env); without it PJM stays
on EIA. Output: data/live_generation.csv with columns utc, iso, fuel, mw.
"""

import datetime as dt
import logging
import time
import warnings

import pandas as pd

from .common import LIVE_GENERATION_CSV, env_value

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
    "PJM": ("PJM", ["PJM"], {"wind": ["Wind"], "solar": ["Solar"], "hydro": ["Hydro"]}),
}

# An hour needs at least this many readings to count; ISO-NE posts irregularly.
MIN_READINGS_PER_HOUR = 3

# Hours from earlier runs are kept this long. A feed's "yesterday" request can
# come back empty, and with hourly runs the history still overlaps EIA's data,
# which is what calibration needs.
KEEP = pd.Timedelta(days=4)


def _iso(cls_name):
    import gridstatus as gs  # imported here so the rest of the pipeline works without it

    if cls_name == "PJM":
        key = env_value("PJM_API_KEY")
        if not key:
            return None
        return gs.PJM(api_key=key)
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
    rows = []
    for fuel, cols in columns.items():
        present = [c for c in cols if c in df]
        if not present:
            continue
        mw = df[present].apply(pd.to_numeric, errors="coerce").sum(axis=1, min_count=1)
        g = pd.DataFrame({"utc": hour, "mw": mw}).dropna().groupby("utc")["mw"].agg(["mean", "size"])
        # The current hour is still filling up; keep only hours the feed has passed.
        g = g[(g["size"] >= MIN_READINGS_PER_HOUR) & (g.index <= stamp.max())]
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
        live = live[live["utc"] >= pd.Timestamp.now(tz="UTC") - KEEP].sort_values(["iso", "fuel", "utc"])
    else:
        live = fresh
    LIVE_GENERATION_CSV.parent.mkdir(parents=True, exist_ok=True)
    live.to_csv(LIVE_GENERATION_CSV, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
    return live


if __name__ == "__main__":
    build()
