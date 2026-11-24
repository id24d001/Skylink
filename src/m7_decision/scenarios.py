"""M7 building blocks: candidates, perturbed networks, a parallel day runner and the cost model.

Everything reuses M5's simulator (m5_simulation.model.simulate_day) and M6's configurations
(m6_moo.problem.Problem / Config). M7 only perturbs the inputs the brief names:

    wind        which days of wind_days.csv are simulated (calm / typical / worst monsoon week)
    demand      every PHC's daily rate of every class x a multiplier (brief: +-25 %, +15 %/yr)
    availability each drone is out of service for a whole day with probability 1 - a
    vehicle     M1 re-run with battery specific energy x 1.2 (brief: +20 %)
    price       drone capital cost x 0.7 (brief: -30 %)
"""
import dataclasses
import math
from multiprocessing import get_context

import numpy as np
import pandas as pd

from common import RESULTS, load_costs
from m4_network.model import crf
from m5_simulation.model import Rules, Vehicle, hourly_profile, simulate_day
from m6_moo.problem import Config, Problem

# ---------------------------------------------------------------- candidates (M6, 365-day check)
CANDIDATES = {
    "knee": "E n4-3-4 s4-4-6 b3-2-4 k1-1-1",
    "best S45": "E n5-4-4 s4-6-5 b3-2-2 k1-1-1",
    "no reserve": "E n4-3-4 s5-5-4 b4-2-3 k0-0-0",
}
DESIGN_OF = {"C": "C cover-all", "D": "D 4-hub", "E": "E 3-hub"}

CALM_WEEK = list(range(40, 47))        # 7 consecutive days with the lowest mean wind (Feb)
WORST_WEEK = list(range(209, 216))     # Jun-Sep 7-day window with the most gust-grounded hours
TYPICAL = list(range(1, 366))          # the whole year, M6's 365-day seeds (500000 + day)
TYPICAL_SEEDS = [500000 + d for d in TYPICAL]


def parse_label(label):
    """'E n4-3-4 s4-4-6 b3-2-4 k1-1-1' -> Config."""
    parts = label.split()
    t = lambda s: tuple(int(v) for v in s[1:].split("-"))
    return Config(DESIGN_OF[parts[0]], t(parts[1]), t(parts[2]), t(parts[3]), t(parts[4]))


def wind_weeks():
    """Find the calm and worst-monsoon weeks from the data (verifies the constants above)."""
    from common import load_csv
    lim = load_costs()["operations"]["gust_grounding_limit_ms"]
    w = load_csv("wind_days.csv")
    g = w.groupby("day").agg(mean=("mean_wind_ms", "mean"), month=("month", "first"),
                             grounded=("gust_ms", lambda s: int((s >= lim).sum())))
    roll = g[["mean", "grounded"]].rolling(7).sum()
    calm_end = int(roll["mean"].idxmin())
    mon = roll[g.month.between(6, 9)]     # window ending in Jun-Sep (the monsoon months)
    worst_end = int(mon["grounded"].idxmax())
    return dict(calm=list(range(calm_end - 6, calm_end + 1)),
                worst=list(range(worst_end - 6, worst_end + 1)),
                calm_mean_ms=float(roll["mean"].min() / 7),
                worst_grounded_h=int(mon["grounded"].max()),
                year_grounded_h=int(g.grounded.sum()), daily=g)


# ---------------------------------------------------------------- perturbed networks
def scaled_hubs(hubs, demand=1.0):
    return [dataclasses.replace(h, phcs=[(p, L, tuple(demand * x for x in lam)) for p, L, lam in h.phcs])
            for h in hubs]


def available_hubs(hubs, avail, rng):
    """Each drone is unavailable for the whole day with probability 1 - avail (independent)."""
    out = []
    for h in hubs:
        n = int(rng.binomial(h.drones, avail)) if avail < 1 else h.drones
        out.append(dataclasses.replace(h, drones=n, reserve=min(h.reserve, n)))
    return out


# ---------------------------------------------------------------- parallel day runner
_W = {}


def _init():
    _W["rules"], _W["profile"] = Rules.from_costs(), hourly_profile()
    _W["prob"] = {}


def _one(job):
    """job = (label, vehicle, demand, avail, day, seed, avail_seed) -> compact day KPIs."""
    label, veh, demand, avail, day, seed, aseed = job
    cfg = parse_label(label)
    p = _W["prob"].setdefault(cfg.design, Problem(cfg.design))
    hubs = scaled_hubs(p.hubs(cfg), demand)
    if avail < 1:
        hubs = available_hubs(hubs, avail, np.random.default_rng(aseed))
    k = simulate_day(hubs, veh, _W["rules"], _W["profile"], day, seed)[0]
    return {key: k[key] for key in ("S45", "emergencies", "em_road", "orders", "road", "road_routine",
                                    "road_lab", "sorties", "grid_kWh", "genset_kWh", "utilisation",
                                    "em_times")}


class Runner:
    def __init__(self, workers=None):
        import os
        self.pool = get_context("fork").Pool(workers or os.cpu_count() or 1, _init)
        self.n = 0

    def run(self, jobs):
        self.n += len(jobs)
        return self.pool.map(_one, jobs, chunksize=16)

    def close(self):
        self.pool.close()
        self.pool.join()


def reduce(kp):
    """Same definitions as M5/M6: mean daily S45, pooled P95, shares over all orders."""
    s = np.array([k["S45"] for k in kp], float)
    s = s[np.isfinite(s)]
    t = np.concatenate([k["em_times"] for k in kp])
    tot = lambda key: float(sum(k[key] for k in kp))
    n = len(kp)
    hw = 1.96 * s.std(ddof=1) / math.sqrt(len(s)) if len(s) > 1 else np.nan
    return dict(days=n, S45=float(s.mean()), S45_ci=hw, S45_min_day=float(s.min()),
                P95=float(np.percentile(t, 95)), road_share=tot("road") / tot("orders"),
                em_road_share=tot("em_road") / tot("emergencies"),
                utilisation=float(np.mean([k["utilisation"] for k in kp])),
                means={key: tot(key) / n for key in ("sorties", "grid_kWh", "genset_kWh", "em_road",
                                                    "road_routine", "road_lab", "orders", "emergencies")})


# ---------------------------------------------------------------- vehicles
def vehicle_row(design="E 3-hub", ctx=None):
    d = (ctx or {}).get("m1_fm_designs")
    d = pd.read_csv(RESULTS / "m1_fm_designs.csv") if d is None else d
    return d[d.design == design].iloc[0]


def fm_surrogate(ctx=None):
    """M3's cubic RBF and its sampled domain (from ctx, or refitted on the oracle data)."""
    if ctx and "fm_surrogate" in ctx and "fm_domain" in ctx:
        return ctx["fm_surrogate"], ctx["fm_domain"]
    from common import ORACLE
    from m3_surrogate import FMDomain, FMFunction, RBFSurrogate, average_repeats, load_oracle, make_features
    df, _ = average_repeats(load_oracle(ORACLE))
    rbf = RBFSurrogate("cubic", 0.0).fit(make_features(df.D_m, df.T_N), df.FM.values)
    return FMFunction(rbf, "RBF cubic"), FMDomain(df.D_m.values, df.T_N.values)


def plate_mass(ctx=None):
    if ctx and "m2_plate_mass" in ctx:
        return float(ctx["m2_plate_mass"])
    d = pd.read_csv(RESULTS / "m2_design.csv", index_col=0)["value"]
    return float(d["plate_mass_kg"])


def resize_vehicle(R_req, e_factor=1.0, ctx=None):
    """M1 snapped design for radius R_req with battery specific energy x e_factor.

    Both the M1 model (E / e_b) and the catalogue packs (mass / e_factor, same Wh, same price per
    Wh) see the better cells. Same FM surrogate, plate mass and constraints as M1/M3."""
    import m1_vehicle.catalogue as cat
    from m1_vehicle.mdo import Problem as M1Problem
    from m1_vehicle.model import Params
    fm, dom = fm_surrogate(ctx)
    base = Params()
    p = Params(m_plate=plate_mass(ctx), fm=fm, complex_safe=False, e_b=base.e_b * e_factor)
    orig = cat.load_parts

    def lp():
        b, m, t = orig()
        b = b.copy()
        b["m_batt"] = b["m_batt"] / e_factor
        return b, m, t

    cat.load_parts = lp
    try:
        row = cat.snap(M1Problem(R_req, 0.0, p, fm_domain=dom))
    finally:
        cat.load_parts = orig
    return row


def vehicle_from(row, energy_factor=1.0, name=None):
    v = Vehicle.from_row(dict(row, design=name or row["design"]))
    return dataclasses.replace(v, E_use=v.E_use * energy_factor)


# ---------------------------------------------------------------- cost model (M6's, parameterised)
def annual_cost(cfg, means, m, E, drone_price=1.0):
    """Annual cost terms (INR/yr) exactly as m6_moo.problem.cost_terms, with the vehicle mass m (kg),
    pack energy E (Wh) and a drone-price factor as inputs. Verified equal to M6 at nominal."""
    c = load_costs()
    r = c["discount_rate"]
    hub, dr, bay, st = c["hub"], c["drone"], c["charging_bay"], c["staff"]
    pack, en, road = c["battery_pack"], c["energy"], c["road_fallback"]["cost_per_trip"]
    H, N, B, S = len(cfg.drones), sum(cfg.drones), sum(cfg.bays), sum(cfg.spares)
    ops = sum(max(1, math.ceil(n / st["drones_per_operator"])) for n in cfg.drones)
    pack_price = pack["cost_per_Wh"] * E
    return {
        "hubs": H * (hub["capex"] * crf(r, hub["life_years"]) + hub["opex_per_year"]),
        "drones": drone_price * N * (dr["capex_fixed"] + dr["capex_per_kg_mtow"] * m)
                    * (crf(r, dr["life_years"]) + dr["maintenance_frac_of_capex_per_year"]),
        "bays": B * bay["capex"] * crf(r, bay["life_years"]),
        "operators": ops * st["shifts_per_day"] * st["relief_factor"] * st["salary_per_person_year"],
        "pack wear (500 cycles)": 365 * means["sorties"] * pack_price / pack["cycle_life"],
        "spare packs (capital, 10 %)": S * pack_price * r,
        "energy": 365 * (means["grid_kWh"] * en["grid_per_kWh"] + means["genset_kWh"] * en["genset_per_kWh"]),
        "road fallback": 365 * (means["em_road"] * road["emergency"]
                                + means["road_routine"] * road["routine"] + means["road_lab"] * road["lab"]),
    }


def wilson(k, n, z=1.96):
    """Wilson score interval for a binomial proportion k / n."""
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)
