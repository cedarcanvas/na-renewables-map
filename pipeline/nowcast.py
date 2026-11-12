"""Bridge the hours between a region's last report and the end of the map's window.

EIA-930 reaches the map up to a day or more late for regions with no live ISO feed
(PJM, the Southeast, much of the interior West). Their last hours would show as "not
reporting". For wind and solar, the weather already says how the region's output moved
since then, so this scales the weather-expected output of the region's plants to its
recent reported output and carries on from there. Hydro and geothermal have no such
signal and repeat the recent daily profile. Backtested on 13 days of EIA-930 in 37
regions (error as % of nameplate, 1-40 h ahead): wind 5.5-6.5 (last value 11-20),
solar 3.0-3.5 (daily profile 4.5-5.1), hydro 5.5-6.7 (last value 12.5-13.4).

The filled hours are rows with source "model" in the totals CSV, so allocate.py splits
them among plants like any other hour, and the page marks them as modelled.

    python -m pipeline.nowcast --backtest        # score the methods on ~13 days of real EIA data
"""

import sys

import numpy as np
import pandas as pd

from . import allocate as al
from .common import BA_GENERATION_CSV, EIA_HISTORY_CSV, FUELS, PUBLIC_BA_GENERATION_CSV, ROOT, WEATHER_CSV

# Hours of recent reports a region's weather fit is calibrated on.
CALIBRATION_HOURS = 72
# Fewer reported hours than this, or a fit that tracks the weather worse than this
# (correlation), and the region stays "not reporting" instead of getting a poor guess.
MIN_HOURS = 24
MIN_CORRELATION = 0.6
# Hours over which the last report's miss against the weather model fades out.
RESIDUAL_HOURS = {"wind": 6.0}
# Hydro and geothermal: the profile of the most recent whole day, repeated.
PROFILE_HOURS = 24


# --- Forecasting one region and fuel ------------------------------------------

def weather_fit(obs, expected, targets, residual_hours=0.0, kind="ratio"):
    """Predict `targets` as a scaling of `expected` (MW the plants would make at that
    hour's weather) fitted to `obs` (reported MW). Returns (prediction, correlation)."""
    both = obs.index.intersection(expected.dropna().index)[-CALIBRATION_HOURS:]
    if len(both) < MIN_HOURS or expected[both].sum() <= 0:
        return None, 0.0
    o, e = obs[both].to_numpy(float), expected[both].to_numpy(float)
    corr = float(np.corrcoef(o, e)[0, 1]) if o.std() > 0 and e.std() > 0 else 0.0
    if kind == "linear":
        b, a = np.polyfit(e, o, 1)
        model = lambda x: np.maximum(a + b * np.asarray(x, float), 0.0)
    else:
        k = o.sum() / e.sum()
        model = lambda x: np.maximum(k * np.asarray(x, float), 0.0)
    et = expected.reindex(targets)
    if et.isna().any():
        return None, corr
    pred = model(et.to_numpy())
    if residual_hours > 0:
        last = both[-1]
        miss = obs[last] - float(model(expected[last]))
        ahead = (targets - obs.index.max()) / pd.Timedelta(hours=1)
        pred = pred + miss * np.exp(-np.asarray(ahead, float) / residual_hours)
    ceiling = 1.1 * max(o.max(), obs.max())  # EIA-860M can miss plants, but not by much
    return pd.Series(np.clip(pred, 0, ceiling), targets), corr


def daily_profile(obs, targets):
    """Each target hour takes the value from the most recent day's same clock hour."""
    last = obs.index.max()
    out = []
    for t in targets:
        src = t - pd.Timedelta(hours=24) * int(np.ceil((t - last) / pd.Timedelta(hours=24)))
        out.append(obs.get(src, obs.iloc[-PROFILE_HOURS:].mean()))
    return pd.Series(out, targets)


def daily_scaled(obs, expected, targets, cap_ratio=3.0):
    """The recent day's real output curve, each hour scaled by how the weather-expected
    output changed since that hour. Keeps the true shape (trackers, panel tilt) that a
    plain sunlight curve lacks, and still follows clouds."""
    last = obs.index.max()
    out = []
    for t in targets:
        src = t - pd.Timedelta(hours=24) * int(np.ceil((t - last) / pd.Timedelta(hours=24)))
        base = obs.get(src, np.nan)
        e_t, e_s = expected.get(t, np.nan), expected.get(src, np.nan)
        if np.isnan(base) or np.isnan(e_t) or np.isnan(e_s):
            out.append(np.nan)
        elif e_s < 0.02 * expected.max():
            out.append(max(base, 0.0))  # dark or calm at the source hour: nothing to scale
        else:
            out.append(max(base, 0.0) * min(e_t / e_s, cap_ratio))
    pred = pd.Series(out, targets)
    return pred if pred.notna().all() else None


def level(obs, targets):
    """The mean of the last day, held flat."""
    return pd.Series(obs.iloc[-PROFILE_HOURS:].mean(), targets)


# --- Weather-expected output of a region's plants -------------------------------

def expected_output(group, wide, cells, curve):
    """Hourly MW a region's plants would make at the weather alone: nameplate × the
    expected capacity factor, summed. A plant with no weather takes the region's mean."""
    local = wide.reindex(columns=[cells[pid] for pid in group["id"]])
    cf = curve(local.to_numpy(dtype=float))
    known = ~np.isnan(cf)
    row_mean = np.nansum(cf, axis=1, keepdims=True) / np.maximum(known.sum(axis=1, keepdims=True), 1)
    cf = np.where(known, cf, np.where(known.any(axis=1, keepdims=True), row_mean, np.nan))
    caps = group["cap"].to_numpy(float)
    out = pd.Series(np.nansum(cf * caps, axis=1), local.index)
    return out.where(~np.isnan(cf).all(axis=1))


def weather_columns(weather):
    weather = weather.assign(utc=pd.to_datetime(weather["utc"], utc=True))
    return {col: weather.pivot(index="utc", columns="cell", values=col) for col, _ in al.WEIGHTING.values()}


# --- Filling the window ---------------------------------------------------------

def forecast(fuel, obs, targets, group, wide, cells, options=None):
    """One region and fuel: (prediction or None, note)."""
    if len(obs) < MIN_HOURS:
        return None, f"only {len(obs)} reported hours"
    if fuel in al.WEIGHTING and wide is not None:
        col, curve = al.WEIGHTING[fuel]
        expected = expected_output(group, wide[col], cells, curve)
        if fuel == "solar":
            # Backtested best for solar: yesterday's real curve, scaled by the weather change.
            pred = daily_scaled(obs, expected, targets)
            if pred is not None:
                return pred, "yesterday's curve scaled by weather"
        pred, corr = weather_fit(obs, expected, targets, RESIDUAL_HOURS.get(fuel, 0.0), **(options or {}))
        if pred is None:
            return None, "no weather for these hours"
        if corr < MIN_CORRELATION:
            return None, f"weather tracks it poorly (r={corr:.2f})"
        return pred, f"weather fit r={corr:.2f}"
    if fuel == "hydro":
        return daily_profile(obs, targets), "daily profile"
    return level(obs, targets), "held level"


def fill(totals_csv, weather=None):
    """Append model rows to `totals_csv` for each region and fuel's hours after its last report."""
    gen = pd.read_csv(totals_csv, parse_dates=["utc"])
    gen = gen[gen.get("source", "eia") != "model"]
    window = sorted(gen["utc"].unique())
    end = window[-1]
    history = pd.read_csv(EIA_HISTORY_CSV, parse_dates=["utc"]) if EIA_HISTORY_CSV.exists() else gen.iloc[:0]
    # Window rows win over EIA's where both exist (live hours calibrated to EIA).
    known = pd.concat([gen, history.assign(source="eia")]).dropna(subset=["mw"])
    known = known.drop_duplicates(["utc", "ba", "fuel"], keep="first")

    if weather is None and WEATHER_CSV.exists():
        weather = pd.read_csv(WEATHER_CSV)
    wide = weather_columns(weather) if weather is not None else None
    cells = al.wx.plant_cells()
    plants, _ = al.load_plants()

    out, notes = [], []
    for (ba, fuel), group in plants.groupby(["ba", "fuel"]):
        if fuel not in FUELS:
            continue
        obs = known[(known["ba"] == ba) & (known["fuel"] == fuel)].set_index("utc")["mw"].sort_index()
        if obs.empty or obs.index.max() >= end:
            continue
        targets = pd.DatetimeIndex([h for h in window if h > obs.index.max()])
        pred, note = forecast(fuel, obs, targets, group, wide, cells)
        if pred is None:
            notes.append(f"{ba}/{fuel}: not modelled ({note})")
            continue
        out.append(pd.DataFrame({"utc": pred.index, "ba": ba, "fuel": fuel, "mw": pred.to_numpy(),
                                 "source": "model", "feed": "weather model"}))
        notes.append(f"{ba}/{fuel}: {len(targets)} h modelled ({note})")
    if out:
        gen = pd.concat([gen, *out], ignore_index=True)
    gen.sort_values(["utc", "ba", "fuel"]).to_csv(totals_csv, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
    modelled = sum(len(o) for o in out)
    print(f"Nowcast: {len(out)} region-fuels, {modelled:,} hours modelled in {totals_csv.name}")
    return notes


def build(weather=None):
    for path in (BA_GENERATION_CSV, PUBLIC_BA_GENERATION_CSV):
        notes = fill(path, weather)
    for note in notes:
        print(f"  {note}")


# --- Backtest ---------------------------------------------------------------------

BACKTEST_DAYS = 13
BACKTEST_WEATHER = ROOT / "data" / "nowcast_backtest_weather.csv"
BACKTEST_EIA = ROOT / "data" / "nowcast_backtest_eia.csv"
CUTOFF_EVERY = 6   # hours between simulated "last reports"
HORIZON = 40       # hours ahead scored


def backtest_data(days=BACKTEST_DAYS):
    """EIA-930 and past weather for the regions the nowcast would fill, cached on disk."""
    from . import generation, weather as wx
    from .common import eia_api_key

    plants, _ = al.load_plants()
    cells = wx.plant_cells()
    if not BACKTEST_EIA.exists():
        eia = generation.from_api(eia_api_key(), hours=days * 24)
        eia.to_csv(BACKTEST_EIA, index=False)
    eia = pd.read_csv(BACKTEST_EIA, parse_dates=["utc"])
    live = {"AESO", "BPAT", "CISO", "ERCO", "HQT", "IESO", "ISNE", "MISO", "NSPI", "NYIS", "SWPP", "SWPW"}
    want = plants[(plants["country"] == "US") & ~plants["ba"].isin(live)]
    if not BACKTEST_WEATHER.exists():
        need = sorted({cells[i] for i in want[want["fuel"].isin(wx.WEATHER_FUELS)]["id"]})
        start, end = eia["utc"].min().floor("h"), eia["utc"].max().floor("h")
        w = wx.fetch(need, start, end)
        w.to_csv(BACKTEST_WEATHER, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
    return want, eia, pd.read_csv(BACKTEST_WEATHER)


def backtest(days=BACKTEST_DAYS):
    want, eia, weather = backtest_data(days)
    wide = weather_columns(weather)
    cells = al.wx.plant_cells()
    rows = []
    variants = {
        "persist": None, "daily": None,
        "ratio": dict(kind="ratio"), "ratio+resid": dict(kind="ratio", resid=True),
        "linear": dict(kind="linear"), "linear+resid": dict(kind="linear", resid=True),
        "daily*wx": None,
    }
    for (ba, fuel), group in want.groupby(["ba", "fuel"]):
        obs_all = eia[(eia["ba"] == ba) & (eia["fuel"] == fuel)].set_index("utc")["mw"].dropna().sort_index()
        if len(obs_all) < 200 or group["cap"].sum() < 300:
            continue
        cap = group["cap"].sum()
        expected = None
        if fuel in al.WEIGHTING:
            col, curve = al.WEIGHTING[fuel]
            expected = expected_output(group, wide[col], cells, curve)
        cutoffs = obs_all.index[(obs_all.index >= obs_all.index[0] + pd.Timedelta(hours=CALIBRATION_HOURS))
                                & (obs_all.index <= obs_all.index[-1] - pd.Timedelta(hours=12))][::CUTOFF_EVERY]
        for c in cutoffs:
            past = obs_all[obs_all.index <= c]
            future = obs_all[(obs_all.index > c) & (obs_all.index <= c + pd.Timedelta(hours=HORIZON))]
            if future.empty:
                continue
            for name, opts in variants.items():
                if name == "persist":
                    pred = pd.Series(past.iloc[-1], future.index)
                elif name == "daily":
                    pred = daily_profile(past, future.index)
                elif expected is None:
                    continue
                elif name == "daily*wx":
                    pred = daily_scaled(past, expected, future.index)
                    if pred is None:
                        continue
                else:
                    pred, corr = weather_fit(past, expected, future.index,
                                             RESIDUAL_HOURS[fuel] if opts.get("resid") else 0.0, kind=opts["kind"])
                    if pred is None:
                        continue
                err = (pred - future).abs() / cap
                for t, e in err.items():
                    rows.append((ba, fuel, name, int((t - c) / pd.Timedelta(hours=1)), float(e), cap))
    df = pd.DataFrame(rows, columns=["ba", "fuel", "method", "ahead", "err", "cap"])
    df["bucket"] = pd.cut(df["ahead"], [0, 12, 24, 40], labels=["1-12h", "13-24h", "25-40h"])
    print(f"{df['ba'].nunique()} regions, {len(df):,} scored hours (mean absolute error, % of region nameplate; "
          f"weighted by capacity)")
    for fuel, g in df.groupby("fuel"):
        table = g.groupby(["method", "bucket"], observed=True).apply(
            lambda x: np.average(x["err"], weights=x["cap"]) * 100, include_groups=False).unstack().round(1)
        print(f"\n{fuel}\n{table.to_string()}")
    df.to_csv(ROOT / "data" / "nowcast_backtest.csv", index=False)
    return df


if __name__ == "__main__":
    if "--backtest" in sys.argv:
        backtest()
    else:
        build()
