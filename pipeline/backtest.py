"""Check the estimates against plants that have their own meters (Ontario, Alberta).

A US plant's output on the map is an estimate: allocate() splits its region's
total among the region's plants by capacity, weighted by local weather
(curves.py). Ontario and Alberta meter every large wind and solar farm, which
makes them a test bench: hand allocate() only a province's total, as if it were
a US region, and compare its split with what each farm's meter read. The same
split by capacity alone is the baseline the weather weighting has to beat.

A unit is what one meter measures: usually one farm, but where one meter code
covers several farms, or one farm reports under several codes, they make one
unit, so no "reading" is itself a capacity split. Units are scored at the grid
operator's capacity; where the plant list disagrees with it, the codes are
probably matched to the wrong plants (ontario.py, alberta.py), and the unit is
listed instead of scored.

    python -m pipeline.backtest            # the hours already on disk (back to the last --days run)
    python -m pipeline.backtest --days 21  # first fetch 21 days of Ontario readings and weather

Alberta can't be extended: AESO only shows the current moment, so its history is
what snapshots.py has kept. Its readings are snapshots near the end of each hour,
while the weather is an hourly mean, so expect Alberta to score a little worse.

Writes data/backtest/units.csv (each unit-hour: reading and both estimates) and
prints the scores, the curves against what farms actually made, and the units
the estimates miss most.
"""

import sys
import time

import numpy as np
import pandas as pd
import requests

from . import alberta, ontario, snapshots
from . import weather as wx
from .allocate import WEIGHTING, allocate, load_plants
from .common import ROOT, WEATHER_CSV

BACKTEST_DIR = ROOT / "data" / "backtest"
UNITS_CSV = BACKTEST_DIR / "units.csv"
WEATHER_HISTORY_CSV = BACKTEST_DIR / "weather.csv"
TESTED = ("wind", "solar")
# Solar hours count only when a province's farms make at least this share of capacity:
# at night both splits are trivially right.
DAYLIGHT = 0.02
# Units are scored at the grid operator's capacity for their codes (IESO's most available MW,
# AESO's maximum capability), as a US region's plants are at EIA-860M's. Where the plant
# list's capacity differs from it by more than this, the meter and the plants don't match
# (or the list is wrong), and the unit is scored separately.
CAPACITY_MISMATCH = 0.15


def ontario_readings(start, plants):
    """IESO's hourly output per generator code, {code: [plant ids]} and {code: capacity}."""
    days = pd.date_range(start.tz_convert("EST").normalize(), pd.Timestamp.now(tz="EST").normalize())
    df = pd.concat([_report(d.date()) for d in days], ignore_index=True)
    df = df[df["Fuel Type"].isin(["WIND", "SOLAR"]) & df["Output MW"].notna()]
    capacity = df.groupby("Generator Name")["Available Capacity MW"].max().dropna().to_dict()
    df = pd.DataFrame({"utc": pd.to_datetime(df["Interval End"], utc=True), "code": df["Generator Name"],
                       "fuel": df["Fuel Type"].map(ontario.IESO_FUELS), "mw": df["Output MW"]})
    links = ontario.crosswalk(df[["code", "fuel"]].drop_duplicates().itertuples(index=False),
                              plants[plants["ba"] == "IESO"])
    return df[df["utc"] >= start], {code: ids for (code, _), ids in links.items()}, capacity


def _report(day, tries=4):
    """ontario.report(), retried: IESO's server drops connections when asked for many days in a row."""
    for attempt in range(tries):
        try:
            return ontario.report(day)
        except requests.ConnectionError:
            if attempt == tries - 1:
                raise
            time.sleep(15 * (attempt + 1))


def alberta_readings(start, plants):
    """AESO's per-asset snapshots as end-of-hour values, {code: [plant ids]} and {code: capacity}."""
    if not snapshots.SNAPSHOTS_CSV.exists():
        return pd.DataFrame(columns=["utc", "code", "fuel", "mw"]), {}, {}
    s = snapshots.hourly(pd.read_csv(snapshots.SNAPSHOTS_CSV, parse_dates=["utc"]))
    s = s[(s["source"] == "AESO") & (s["kind"] == "asset") & s["fuel"].isin(TESTED) & (s["utc"] >= start)]
    assets = pd.read_csv(snapshots.ASSETS_CSV)
    links = alberta.crosswalk(assets, plants[plants["ba"] == "AESO"])
    return s.rename(columns={"key": "code"})[["utc", "code", "fuel", "mw"]], links, assets.set_index("code")["mc"].to_dict()


def meter_groups(links):
    """{code: [plant ids]} -> [(codes, plant ids)], merging codes that share a plant."""
    groups = []
    for code, ids in links.items():
        codes, pids = {code}, set(ids)
        for g in [g for g in groups if g[1] & pids]:
            groups.remove(g)
            codes |= g[0]
            pids |= g[1]
        groups.append((codes, pids))
    return groups


def unit_table(province, links, plants, capacity):
    p = plants.set_index("id")
    rows = []
    for codes, pids in meter_groups(links):
        pids = sorted(pids, key=lambda pid: (-p.at[pid, "cap"], p.at[pid, "name"]))
        if p.at[pids[0], "fuel"] not in TESTED:
            continue
        more = f" (+{len(pids) - 1} more)" if len(pids) > 1 else ""
        listed = p.loc[pids, "cap"].sum()
        known = [capacity.get(c, 0) for c in codes]
        rows.append({"unit": p.at[pids[0], "name"][:38 - len(more)] + more, "province": province, "fuel": p.at[pids[0], "fuel"],
                     "cap": sum(known) if all(k > 0 for k in known) else listed, "listed": listed,
                     "plants": pids, "codes": sorted(codes)})
    return pd.DataFrame(rows)


def load_weather(cells, start, fetch_new):
    """The weather history for these cells: fetched over the test window, plus the map's own."""
    if fetch_new:
        print(f"Weather: fetching {len(cells)} cells from {start:%Y-%m-%d} (Open-Meteo)")
        df = wx.fetch(cells, start - pd.Timedelta(hours=1), pd.Timestamp.now(tz="UTC"))
        df = df[df["utc"] >= start]  # the first hour has no previous reading to average its wind with
        BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
        df.to_csv(WEATHER_HISTORY_CSV, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
    frames = [pd.read_csv(p, parse_dates=["utc"]) for p in (WEATHER_HISTORY_CSV, WEATHER_CSV) if p.exists()]
    return pd.concat(frames, ignore_index=True).drop_duplicates(["cell", "utc"], keep="last")


def split(meter, caps, cf):
    """Each hour, hand allocate() the units' summed readings to split by weather and by capacity alone."""
    est_w, est_c = np.full_like(meter, np.nan), np.full_like(meter, np.nan)
    for i in range(len(meter)):
        ok = ~np.isnan(meter[i]) & ~np.isnan(cf[i])
        if ok.sum() < 2:
            continue
        total = float(meter[i, ok].sum())
        est_w[i, ok] = allocate(total, caps[ok], cf[i, ok])
        est_c[i, ok] = allocate(total, caps[ok])
    return est_w, est_c


def test_group(units, readings, grid, curve, cells, cap):
    """One province's units of one fuel -> long rows of reading and estimates per unit-hour."""
    hours = pd.DatetimeIndex(sorted(readings["utc"].unique()))
    wide = readings.pivot_table(index="utc", columns="code", values="mw", aggfunc="mean").reindex(hours)
    meter, local, cf = [], [], []
    for u in units.itertuples():
        # A unit's reading counts only when every one of its codes reported.
        meter.append(wide.reindex(columns=u.codes).sum(axis=1, min_count=len(u.codes)).to_numpy())
        w = grid.reindex(index=hours, columns=[cells[pid] for pid in u.plants]).to_numpy(dtype=float)
        share = (cap[u.plants] / cap[u.plants].sum()).to_numpy()
        local.append(w @ share)          # capacity-weighted weather, for the curve check
        cf.append(curve(w) @ share)      # capacity-weighted expected capacity factor
    meter, local, cf = (np.column_stack(a) for a in (meter, local, cf))
    caps = units["cap"].to_numpy(dtype=float)
    est_w, est_c = split(meter, caps, cf)
    n_h, n_u = meter.shape
    out = pd.DataFrame({
        "utc": np.repeat(hours, n_u), "province": units["province"].iloc[0], "fuel": units["fuel"].iloc[0],
        "unit": np.tile(units["unit"], n_h), "cap": np.tile(caps, n_h), "listed": np.tile(units["listed"], n_h),
        "local": local.ravel(),
        "mw": meter.ravel(), "est_weather": est_w.ravel(), "est_capacity": est_c.ravel(),
    })
    return out.dropna(subset=["est_weather"])


def daylight_only(df):
    """Solar rows from hours when the province's farms were making real power; wind rows as they are."""
    sums = df.groupby(["province", "fuel", "utc"])[["mw", "cap"]].transform("sum")
    return df[(df["fuel"] != "solar") | (sums["mw"] >= DAYLIGHT * sums["cap"])]


def scores(df):
    err_w, err_c = (df["est_weather"] - df["mw"]).abs(), (df["est_capacity"] - df["mw"]).abs()
    r = df.groupby("unit")[["mw", "est_weather", "est_capacity"]].corr().unstack()["mw"]
    return {
        "units": df["unit"].nunique(), "hours": df["utc"].nunique(),
        "cap_w": err_w.sum() / df["cap"].sum(), "cap_c": err_c.sum() / df["cap"].sum(),
        "out_w": err_w.sum() / df["mw"].sum(), "out_c": err_c.sum() / df["mw"].sum(),
        "better": 1 - err_w.sum() / err_c.sum(),
        "r_w": r["est_weather"].median(), "r_c": r["est_capacity"].median(),
    }


def mismatched(df):
    """Units whose plants don't fit their meter: {(province, fuel, unit): reason}."""
    per = df.groupby(["province", "fuel", "unit"]).agg(cap=("cap", "first"), listed=("listed", "first"),
                                                       peak=("mw", "max"))
    # IESO's figure is what was available, so a farm with turbines out all window shows here too.
    operator = {"ON": "IESO had at most {:.0f} MW available", "AB": "AESO lists {:.0f} MW"}
    out = {}
    for (prov, fuel, unit), r in per.iterrows():
        if abs(r["listed"] / r["cap"] - 1) > CAPACITY_MISMATCH:
            out[(prov, fuel, unit)] = f"listed {r['listed']:.0f} MW; " + operator[prov].format(r["cap"])
        elif r["peak"] > 1.1 * r["cap"]:
            out[(prov, fuel, unit)] = f"{r['cap']:.0f} MW, read up to {r['peak']:.0f} MW"
    return out


def report(df):
    names = {"ON": "Ontario", "AB": "Alberta"}
    odd = mismatched(df)
    fits = ~df.set_index(["province", "fuel", "unit"]).index.isin(list(odd))
    print(f"\nScores, {df['utc'].min():%Y-%m-%d %H:%M} to {df['utc'].max():%Y-%m-%d %H:%M} UTC "
          f"(solar: daylight hours only; units whose plants fit their meter)")
    print(f"{'':16s}{'units':>6s}{'hours':>7s}  {'error, % of capacity':>22s}  {'error, % of output':>20s}"
          f"  {'weather':>8s}  {'timing (median r)':>18s}")
    print(f"{'':29s}{'weather':>11s}{'capacity':>11s}{'weather':>10s}{'capacity':>10s}  {'better by':>9s}"
          f"{'weather':>10s}{'capacity':>10s}")
    clean = df[fits]
    groups = [((p, f), g) for (p, f), g in clean.groupby(["province", "fuel"])]
    groups += [(("all", f), g) for f, g in clean.groupby("fuel")]
    for (prov, fuel), g in groups:
        s = scores(g)
        label = f"{names.get(prov, 'Both')} {fuel}"
        print(f"{label:16s}{s['units']:6d}{s['hours']:7d}  {s['cap_w']:11.1%}{s['cap_c']:11.1%}"
              f"{s['out_w']:10.1%}{s['out_c']:10.1%}  {s['better']:9.0%}{s['r_w']:10.2f}{s['r_c']:10.2f}")
    if odd:
        print("With every unit:  " + ";  ".join(
            f"{fuel} {s['cap_w']:.1%} by weather, {s['cap_c']:.1%} by capacity"
            for fuel, g in df.groupby("fuel") for s in [scores(g)]))

    print("\nCurves against what farms made (capacity factor)")
    for fuel, step, unit in (("wind", 2, "m/s"), ("solar", 100, "W/m²")):
        g = clean[(clean["fuel"] == fuel) & clean["local"].notna()]
        if g.empty:
            continue
        curve = WEIGHTING[fuel][1]
        bins = (g["local"] // step * step).clip(upper=20 if fuel == "wind" else 900)
        t = g.assign(bin=bins, actual=g["mw"] / g["cap"], curve=curve(g["local"].to_numpy())).groupby("bin").agg(
            n=("actual", "size"), actual=("actual", "mean"), curve=("curve", "mean"))
        print(f"  {fuel}: local {unit:5s} " + "".join(f"{b:>6.0f}" for b in t.index))
        print(f"  {'':6s}unit-hours  " + "".join(f"{n:>6d}" for n in t["n"]))
        print(f"  {'':6s}actual      " + "".join(f"{v:>6.2f}" for v in t["actual"]))
        print(f"  {'':6s}curve       " + "".join(f"{v:>6.2f}" for v in t["curve"]))

    print("\nUnits the weather split misses most (mean error and bias, % of capacity)")
    per = clean.assign(err=(clean["est_weather"] - clean["mw"]).abs(), bias=clean["est_weather"] - clean["mw"]).groupby(
        ["province", "fuel", "unit"]).agg(cap=("cap", "first"), err=("err", "mean"), bias=("bias", "mean"),
                                          made=("mw", "mean"))
    per[["err", "bias", "made"]] = per[["err", "bias", "made"]].div(per["cap"], axis=0)
    for (prov, fuel, unit), r in per.sort_values("err", ascending=False).head(10).iterrows():
        print(f"  {prov} {fuel:5s} {unit:38s} {r['cap']:6.0f} MW  error {r['err']:5.1%}  "
              f"bias {r['bias']:+6.1%}  made {r['made']:5.1%} of capacity on average")

    if odd:
        print("\nPlant list and grid operator disagree on capacity (left out of the scores above):")
        for (prov, fuel, unit), reason in odd.items():
            print(f"  {prov} {fuel:5s} {unit:38s} {reason}")


def build(days=None):
    now = pd.Timestamp.now(tz="UTC").floor("h")
    if days:
        start = now - pd.Timedelta(days=days)
    elif WEATHER_HISTORY_CSV.exists():  # as far back as an earlier --days run fetched weather
        start = pd.read_csv(WEATHER_HISTORY_CSV, usecols=["utc"], parse_dates=["utc"])["utc"].min()
    else:
        start = now - pd.Timedelta(days=4)
    plants, _ = load_plants()
    cells = wx.plant_cells()

    tested = []
    for province, (readings, links, capacity) in (("ON", ontario_readings(start, plants)),
                                                  ("AB", alberta_readings(start, plants))):
        units = unit_table(province, links, plants, capacity)
        print(f"{province}: {len(units)} metered wind and solar units, {readings['utc'].nunique()} hours of readings")
        tested.append((province, readings, units))

    needed = sorted({cells[pid] for _, _, units in tested for pids in units["plants"] for pid in pids})
    weather = load_weather(needed, start, fetch_new=days is not None)
    cap = plants.set_index("id")["cap"]
    rows = []
    for province, readings, units in tested:
        for fuel, fuel_units in units.groupby("fuel"):
            col, curve = WEIGHTING[fuel]
            grid = weather.pivot(index="utc", columns="cell", values=col)
            r = readings[readings["fuel"] == fuel]
            if len(fuel_units) >= 2 and len(r):
                rows.append(test_group(fuel_units.reset_index(drop=True), r, grid, curve, cells, cap))
    df = daylight_only(pd.concat(rows, ignore_index=True))
    BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(UNITS_CSV, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
    report(df)
    print(f"\nWrote {UNITS_CSV} ({len(df):,} unit-hours)")


if __name__ == "__main__":
    args = sys.argv[1:]
    build(days=int(args[args.index("--days") + 1]) if "--days" in args else None)
