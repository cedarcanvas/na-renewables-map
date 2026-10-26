"""Hourly wind/solar/hydro/geothermal totals per balancing authority (EIA-930).

With EIA_API_KEY set (environment or .env) this uses the EIA API v2, which is
about an hour behind real time. Without a key it falls back to EIA's six-month
bulk CSV, which needs no key but is only refreshed about once a day.

Output: data/ba_generation.csv with columns utc, ba, fuel, mw. Times are UTC,
end of hour, matching how EIA-930 labels its hours.
"""

import datetime as dt
import re
import time

import pandas as pd
import requests

from .common import BA_GENERATION_CSV, FUELS, HOURS, RAW, USER_AGENT, download, eia_api_key

API_URL = "https://api.eia.gov/v2/electricity/rto/fuel-type-data/data/"
BULK_URL = "https://www.eia.gov/electricity/gridmonitor/sixMonthFiles/{name}"

# Pick the last hour that at least this share of BAs has reported. BAs lag by
# different amounts; ending earlier avoids a map where half the country is blank.
REPORTING_SHARE = 0.75


# --- API path -------------------------------------------------------------

def from_api(key, hours=HOURS):
    codes = {code: fuel for fuel, spec in FUELS.items() for code in spec["eia930_api"]}
    start = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours + 12)).strftime("%Y-%m-%dT%H")
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
        r.raise_for_status()
        page = r.json()["response"]
        rows += page["data"]
        offset += len(page["data"])
        if not page["data"] or offset >= int(page["total"]):
            break
        time.sleep(0.2)

    df = pd.DataFrame(rows)
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


# --- Window selection -------------------------------------------------------

def last_window(df, hours=HOURS):
    """The last `hours` hours, ending at the latest hour most BAs have reported."""
    reported = df.dropna(subset=["mw"])
    latest_per_ba = reported.groupby("ba")["utc"].max()
    end = latest_per_ba.quantile(1 - REPORTING_SHARE, interpolation="lower")
    start = end - pd.Timedelta(hours=hours - 1)
    window = reported[(reported["utc"] >= start) & (reported["utc"] <= end)]
    return window.sort_values(["utc", "ba", "fuel"]).reset_index(drop=True)


def build():
    key = eia_api_key()
    source = "EIA API v2" if key else "EIA-930 bulk CSV (no API key; refreshed daily)"
    df = from_api(key) if key else from_bulk()
    window = last_window(df)
    BA_GENERATION_CSV.parent.mkdir(parents=True, exist_ok=True)
    window.to_csv(BA_GENERATION_CSV, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")

    hours = window["utc"].drop_duplicates()
    latest = window[window["utc"] == hours.max()]
    print(f"Source: {source}")
    print(f"{len(hours)} hours, {hours.min():%Y-%m-%d %H:%M} to {hours.max():%Y-%m-%d %H:%M} UTC, "
          f"{window['ba'].nunique()} BAs")
    print("Latest hour, GW:", (latest.groupby("fuel")["mw"].sum() / 1000).round(1).to_dict())
    return window, source


if __name__ == "__main__":
    build()
