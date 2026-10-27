"""Turn local weather into each plant's expected capacity factor (0–1).

allocate() uses these only as relative weights within one region and hour:
the region's reported total still decides how much power there is, and these
decide where it came from. So the shape of each curve matters more than its
exact level.
"""

import numpy as np


def wind_capacity_factor(speed_ms):
    """Expected share of nameplate output for a wind farm at this hub-height wind speed.

    speed_ms – numpy array of hour-mean wind speeds at ~100 m in m/s, NaN where unknown.
    Returns a numpy array of the same shape with values from 0 to 1 (NaN stays NaN).
    """
    # Farm-level power curve: hub-height wind (m/s) -> share of nameplate.
    # Gentler and lower than a single turbine's: wakes, uneven wind across the
    # site and turbines down for maintenance keep real farms below ~90%.
    # Nothing below 3 m/s (cut-in); turbines shut down between 25 and 27 m/s.
    speeds = [0, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 25, 27]
    factors = [0, 0, 0.05, 0.12, 0.22, 0.34, 0.48, 0.62, 0.74, 0.82, 0.87, 0.88, 0.88, 0]
    return np.interp(np.asarray(speed_ms, dtype=float), speeds, factors)

def solar_capacity_factor(ghi):
    """Expected share of nameplate output for a solar farm, from sunlight on the ground.

    Output tracks irradiance closely, and 1,000 W/m² is the standard full-sun
    rating condition. Trackers and panel tilt lift real output above this in the
    morning and evening; Phase 2 ignores that.
    """
    return np.clip(np.asarray(ghi, dtype=float) / 1000.0, 0.0, 1.0)
