"""Hourly wind/solar/hydro/geothermal totals per balancing authority (EIA-930).

With EIA_API_KEY set (environment or .env) this uses the EIA API v2, which
updates hourly. Without a key it falls back to EIA's six-month bulk CSV, which
needs no key but is only refreshed about once a day. Either way, generation by
fuel has been seen to stop at the previous local midnight while demand was
current to the hour, so expect this data to run up to a day behind.

Output: data/ba_generation.csv with columns utc, ba, fuel, mw. Times are UTC,
end of hour, matching how EIA-930 labels its hours.
"""

import datetime as dt
import json
import re
import time

import pandas as pd
import requests

from .common import (
    BA_GENERATION_CSV, CALIBRATION_JSON, FLOWS_CSV, FUELS, HOURS, METERED_CSV, PLANTS_GEOJSON,
    PUBLIC_BA_GENERATION_CSV, RAW, USER_AGENT, download, eia_api_key,
)

API_URL = "https://api.eia.gov/v2/electricity/rto/fuel-type-data/data/"
BULK_URL = "https://www.eia.gov/electricity/gridmonitor/sixMonthFiles/{name}"

# The API also returns EIA's regional and national roll-ups as respondents;
# keeping them would count every MW two or three times.
REGION_AGGREGATES = {
    "CAL", "CAR", "CENT", "FLA", "MIDA", "MIDW", "NE", "NW", "NY", "SE", "SW", "TEN", "TEX", "US48",
}

# Generation has run up to a day behind demand, so ask the API for this much
# more than the window itself.
API_LAG_MARGIN = pd.Timedelta(hours=48)

# Pick the last hour that at least this share of BAs has reported. BAs lag by
# different amounts; ending earlier avoids a map where half the country is blank.
REPORTING_SHARE = 0.75


# --- API path -------------------------------------------------------------

def from_api(key, hours=HOURS):
    codes = {code: fuel for fuel, spec in FUELS.items() for code in spec["eia930_api"]}
    start = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=hours) - API_LAG_MARGIN).strftime("%Y-%m-%dT%H")
    params = {
        "api_key": key,
        "frequency": "hourly",
        "data[0]": "value",
        "start": start,
        "sort[0][column]": "period",
        "sort[0][direction]": "desc",
        "length": 5000,
    }
    for i, code in enumerate(codes):
        params[f"facets[fueltype][{i}]"] = code

    rows, offset = [], 0
    while True:
        r = requests.get(API_URL, params={**params, "offset": offset},
                         headers={"User-Agent": USER_AGENT}, timeout=60)
        if not r.ok:
            # requests' own error message includes the URL, and with it the key.
            raise RuntimeError(f"EIA API {r.status_code}: {r.text[:300].replace(key, '<key>')}")
        page = r.json()["response"]
        rows += page["data"]
        offset += len(page["data"])
        if not page["data"] or offset >= int(page["total"]):
            break
        time.sleep(0.2)

    df = pd.DataFrame(rows)
    df = df[~df["respondent"].isin(REGION_AGGREGATES)]
    df = pd.DataFrame({
        "utc": pd.to_datetime(df["period"], format="%Y-%m-%dT%H", utc=True),
        "ba": df["respondent"],
        "fuel": df["fueltype"].map(codes),
        "mw": pd.to_numeric(df["value"], errors="coerce"),
    })
    return df.groupby(["utc", "ba", "fuel"], as_index=False)["mw"].sum(min_count=1)


# --- Bulk CSV path ----------------------------------------------------------

def bulk_name(day):
    half = "Jan_Jun" if day.month <= 6 else "Jul_Dec"
    return f"EIA930_BALANCE_{day.year}_{half}.csv"


def _normalise(col):
    # EIA's Adjusted headers have typos ("Solar witho Integrated…", "Pumped Storage  (Adjusted)").
    return re.sub(r"\s+", " ", col.replace(" witho ", " with ")).strip()


def read_bulk(path):
    df = pd.read_csv(path, low_memory=False, thousands=",")
    df["utc"] = pd.to_datetime(df["UTC Time at End of Hour"], format="%m/%d/%Y %I:%M:%S %p", utc=True)
    by_name = {_normalise(c): c for c in df.columns}

    out = []
    for fuel, spec in FUELS.items():
        parts = []
        for stem in spec["eia930_columns"]:
            raw = by_name.get(f"Net Generation (MW) from {stem}")
            adjusted = by_name.get(f"Net Generation (MW) from {stem} (Adjusted)")
            if raw is None and adjusted is None:
                continue
            # Prefer EIA's cleaned (Adjusted) value, falling back to the reported one.
            series = df[adjusted] if adjusted else df[raw]
            if adjusted and raw:
                series = df[adjusted].combine_first(df[raw])
            parts.append(series)
        if parts:
            mw = pd.concat(parts, axis=1).sum(axis=1, min_count=1)
            out.append(pd.DataFrame({"utc": df["utc"], "ba": df["Balancing Authority"], "fuel": fuel, "mw": mw}))
    return pd.concat(out, ignore_index=True)


def from_bulk(hours=HOURS):
    today = dt.date.today()
    path = RAW / bulk_name(today)
    # The file is rewritten daily; refresh our copy when it's more than 6 hours old.
    stale = path.exists() and time.time() - path.stat().st_mtime > 6 * 3600
    download(BULK_URL.format(name=path.name), path, force=stale)
    df = read_bulk(path)

    # Near the start of a half-year, the window reaches back into the previous file.
    if df.dropna(subset=["mw"])["utc"].nunique() < hours:
        prev = today.replace(month=1 if today.month > 6 else 7, day=1) - dt.timedelta(days=1)
        prev_path = download(BULK_URL.format(name=bulk_name(prev)), RAW / bulk_name(prev))
        df = pd.concat([read_bulk(prev_path), df], ignore_index=True)
    return df


# --- Live ISO hours -----------------------------------------------------------

# Calibrating a feed needs at least this many hours that it and EIA both cover.
MIN_OVERLAP_HOURS = 6
# A calibration factor outside this range means the feed and EIA aren't measuring
# the same thing, and that feed's fuel stays on EIA.
FACTOR_RANGE = (0.4, 2.5)
# When a feed briefly shares too few hours with EIA, its last good calibration is
# reused for up to this long instead of dropping the feed.
CALIBRATION_MAX_AGE = pd.Timedelta(days=7)


def _load_calibration():
    return json.loads(CALIBRATION_JSON.read_text()) if CALIBRATION_JSON.exists() else {}


def live_after_eia(eia, live):
    """Live ISO hours after each BA's EIA data ends, scaled to EIA's numbers.

    Over the hours both cover, each feed's total is compared with what EIA-930
    reported for the BAs it serves, and the live hours after EIA ends are split
    and scaled by those ratios. Each BA's series then carries on from EIA's last
    hour without a jump, and SPP's one feed fills both SWPP and SWPW.
    Returns (rows, notes).
    """
    from .live import ISOS

    reported = eia.dropna(subset=["mw"])
    stored = _load_calibration()
    now = pd.Timestamp.now(tz="UTC")
    out, notes = [], []
    for (iso, fuel), g in live.groupby(["iso", "fuel"]):
        key = f"{iso}/{fuel}"
        targets = ISOS[iso][1]
        ref = (reported[reported["ba"].isin(targets) & (reported["fuel"] == fuel)]
               .pivot_table(index="utc", columns="ba", values="mw").reindex(columns=targets))
        feed = g.set_index("utc")["mw"]
        overlap = ref.dropna().index.intersection(feed.index)
        previous = stored.get(key)
        if len(overlap) >= MIN_OVERLAP_HOURS and feed[overlap].sum() > 0:
            factors = ref.loc[overlap].sum() / feed[overlap].sum()
            if not FACTOR_RANGE[0] <= factors.sum() <= FACTOR_RANGE[1]:
                notes.append(f"{key}: feed is {1 / factors.sum():.2f}× EIA; not used")
                continue
            stored[key] = {"factors": factors.round(4).to_dict(), "at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
        elif previous and now - pd.Timestamp(previous["at"]) <= CALIBRATION_MAX_AGE:
            factors = pd.Series(previous["factors"]).reindex(targets)
            if factors.isna().any():
                notes.append(f"{key}: too few hours shared with EIA and no usable earlier calibration; not used")
                continue
            notes.append(f"{key}: too few hours shared with EIA; reusing its calibration from "
                         f"{pd.Timestamp(previous['at']):%m-%d %H:%M} UTC")
        else:
            notes.append(f"{key}: too few hours shared with EIA to calibrate; not used")
            continue
        for ba, factor in factors.items():
            last_eia = ref[ba].dropna().index.max()
            after = feed if pd.isna(last_eia) else feed[feed.index > last_eia]
            out.append(pd.DataFrame({"utc": after.index, "ba": ba, "fuel": fuel, "mw": after.to_numpy() * factor,
                                     "feed": iso}))
        if len(targets) > 1 or abs(factors.sum() - 1) > 0.05:
            split = ", ".join(f"{ba} ×{f:.2f}" for ba, f in factors.items())
            notes.append(f"{key}: calibrated to EIA as {split}")
    CALIBRATION_JSON.parent.mkdir(parents=True, exist_ok=True)
    CALIBRATION_JSON.write_text(json.dumps(stored, indent=1))
    rows = pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=["utc", "ba", "fuel", "mw"])
    return rows, notes


# --- Window selection -------------------------------------------------------

# With live data the window ends where most live feeds have reached. Slower feeds
# (SPP an hour or two, Hydro-Québec about three) show their last hours as "no
# data" rather than holding the whole map back.
LIVE_REPORTING_SHARE = 0.5


def last_window(df, hours=HOURS):
    """The last `hours` hours, ending at the latest hour most sources have reported.

    With live hours, the vote is one per live feed, not per BA: SPP's one feed
    covers two BAs and shouldn't count twice. Without them, it's one per BA.
    """
    reported = df.dropna(subset=["mw"])
    is_live = reported["source"] == "live"
    if is_live.any():
        pool, share = reported[is_live], LIVE_REPORTING_SHARE
        voter = pool["feed"].fillna(pool["ba"]) if "feed" in pool else pool["ba"]
    else:
        pool, share, voter = reported, REPORTING_SHARE, reported["ba"]
    latest = pool.groupby(voter)["utc"].max()
    end = latest.quantile(1 - share, interpolation="lower")
    start = end - pd.Timedelta(hours=hours - 1)
    window = reported[(reported["utc"] >= start) & (reported["utc"] <= end)]
    return window.sort_values(["utc", "ba", "fuel"]).reset_index(drop=True)


def canada_rows():
    """Canadian provinces' totals by fuel as live rows. None report to EIA-930, so
    there's nothing to calibrate against. Also saves per-plant readings (Ontario,
    Alberta) and cross-border flows for allocate.py."""
    from . import alberta, ontario, quebec, snapshots

    since = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=HOURS) - API_LAG_MARGIN
    plants = pd.DataFrame([f["properties"] for f in json.loads(PLANTS_GEOJSON.read_text())["features"]])
    rows, metered, flows = [], [], []
    try:
        per_plant, totals = ontario.build(since, plants)
        metered.append(per_plant)
        rows.append(totals.assign(ba="IESO", feed="IESO"))
    except Exception as e:
        print(f"  Ontario (IESO) unavailable ({type(e).__name__}: {e})")
    try:
        rows.append(quebec.production().assign(ba="HQT", feed="Hydro-Québec"))
        flows.append(quebec.flows())
    except Exception as e:
        print(f"  Québec (Hydro-Québec) unavailable ({type(e).__name__}: {e})")
    try:
        snaps = snapshots.hourly(snapshots.build())
        snaps = snaps[snaps["utc"] >= since]
        per_plant, totals = alberta.build(snaps, plants, since)
        metered.append(per_plant)
        rows.append(totals.assign(ba="AESO", feed="AESO"))
        # Provincial totals: Nova Scotia's wind, SaskPower's wind/hydro/solar.
        for _, t in snaps[snaps["kind"] == "total"].groupby("source"):
            rows.append(pd.DataFrame({"utc": t["utc"], "fuel": t["fuel"], "mw": t["mw"],
                                      "ba": t["key"], "feed": t["source"]}))
        f = snaps[snaps["kind"] == "flow"]
        flows.append(pd.DataFrame({"utc": f["utc"], "link": f["key"], "source": f["source"], "mw": f["mw"]}))
    except Exception as e:
        print(f"  Snapshot feeds (AESO, NS, NB, SK) unavailable ({type(e).__name__}: {e})")
    if metered:
        pd.concat(metered, ignore_index=True).to_csv(METERED_CSV, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
    if flows:
        pd.concat(flows, ignore_index=True).to_csv(FLOWS_CSV, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
    return pd.concat(rows, ignore_index=True).assign(source="live") if rows else pd.DataFrame()


def build():
    key = eia_api_key()
    source = "EIA API v2" if key else "EIA-930 bulk CSV (no API key; refreshed daily)"
    eia = (from_api(key) if key else from_bulk()).assign(source="eia")
    df = eia
    try:
        from . import live as live_feeds

        extra, notes = live_after_eia(eia, live_feeds.build())
        for note in notes:
            print(f"  {note}")
        if len(extra):
            df = pd.concat([eia, extra.assign(source="live")], ignore_index=True)
            source += " + live grid-operator feeds"
    except Exception as e:  # gridstatus missing or an ISO site down: the map still works on EIA alone
        print(f"Live grid-operator feeds unavailable ({type(e).__name__}: {e}); using EIA only")
    canada = canada_rows()
    if len(canada):
        df = pd.concat([df, canada], ignore_index=True)
        source += " + Canadian grid operators"

    window = last_window(df)
    BA_GENERATION_CSV.parent.mkdir(parents=True, exist_ok=True)
    window.to_csv(BA_GENERATION_CSV, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
    # The public copy can't use PJM's Data Miner feed (see live.py), so there PJM
    # stays on EIA-930 and its hours after EIA's last show as not reported.
    pjm = (df["source"] == "live") & (df["feed"] == "PJM") if "feed" in df else pd.Series(False, df.index)
    public = last_window(df[~pjm]) if pjm.any() else window
    public.to_csv(PUBLIC_BA_GENERATION_CSV, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")

    hours = window["utc"].drop_duplicates()
    latest = window[window["utc"] == hours.max()]
    eia_end = window.loc[window["source"] == "eia", "utc"].max()
    print(f"Source: {source}")
    print(f"{len(hours)} hours, {hours.min():%Y-%m-%d %H:%M} to {hours.max():%Y-%m-%d %H:%M} UTC, "
          f"{window['ba'].nunique()} BAs; EIA reaches {eia_end:%m-%d %H:%M}, "
          f"live BAs at the last hour: {sorted(latest['ba'].unique())}")
    print("Latest hour, GW:", (latest.groupby("fuel")["mw"].sum() / 1000).round(1).to_dict())
    return window, source


if __name__ == "__main__":
    build()
