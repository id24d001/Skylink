"""M4 hub-location and fleet-sizing MILP (one solve per M1 design).

Sets:  j in candidate hubs (H0-H6, P09), i in PHCs (P01-P24), c in {emergency, routine, lab},
       h in hours of the day (0-23).
Variables (all integer, solved with scipy.optimize.milp / HiGHS):
  y_j   open hub j                                   binary
  x_ij  PHC i flown from hub j                       binary (fixed to 0 if out of reach)
  r_i   PHC i served by road instead                 binary
  n_j   drones at hub j                              integer, 0..N_max
  b_j   charging bays at hub j                       integer, 0..B_max
  o_j   operators per shift at hub j                 integer, 0..O_max
Objective: annual cost in INR (capital annualised with the 10 % CRF from costs.yaml).
Constraints:
  (1) sum_j x_ij + r_i = 1                           every PHC served once
  (2) x_ij <= y_j                                    only open hubs serve (tight, no big-M)
  (3) reach: x_ij = 0 unless L_ij <= R (1 - w^2/V^2) (variable bound)
  (4) drone time, every hour h:  sum_i x_ij busy_ijh <= 0.70 * 60 * avail * n_j
  (5) bay energy, every hour h:  sum_i x_ij grid_Wh_ijh <= 0.70 * 1500 * b_j ;  b_j >= y_j
  (6) staff: 4 o_j >= n_j ;  o_j >= y_j ;  n_j >= y_j (an open hub has at least one drone)
  (7) big-M: n_j <= N_max y_j,  b_j <= B_max y_j,  o_j <= O_max y_j
  (8) emergency guard: sum_i r_i e_i <= 0.05 sum_i e_i
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix

from common import load_costs, load_csv, RESULTS
from m1_vehicle.model import Params, radius_in_wind

CLASSES = ("emergency", "routine", "lab")


def crf(r, n):
    return r * (1 + r) ** n / ((1 + r) ** n - 1)


@dataclass
class Settings:
    wind: float = 6.4                 # design headwind, m/s (P90 of flyable hours; user-confirmed)
    util_max: float = 0.70            # brief: drone busy time at <= 70 % utilisation
    bay_util_max: float = 0.70        # same cap applied to charging bays
    emerg_road_max: float = 0.05      # at most 5 % of daily emergencies at road-only PHCs
    gust_limit: float = 14.0


# ---------------------------------------------------------------- data
def demand_profiles():
    """Hour-of-day fractions per class, each rescaled to sum to 1 (routine sums to 1.1993 in
    the raw file, logged in M0)."""
    hp = load_csv("hourly_profile.csv").set_index("hour")
    f = np.stack([hp[f"{c}_frac"].values for c in CLASSES])        # (3, 24)
    return f / f.sum(axis=1, keepdims=True)


def grounded_by_hour(gust_limit=14.0):
    """Share of days in which hour h is grounded (gust >= limit)."""
    w = load_csv("wind_days.csv")
    return (w.gust_ms >= gust_limit).groupby(w.hour).mean().reindex(range(24)).values


def flyable_fraction(gust_limit=14.0):
    w = load_csv("wind_days.csv")
    return float((w.gust_ms < gust_limit).mean())


def outage_hours():
    c = load_costs()["grid_outage"]
    h0, h1 = int(c["start"][:2]), int(c["end"][:2])
    return list(range(h0, h1)), len(c["days"]) / 7


@dataclass
class Instance:
    """Everything the MILP (and the brute-force check) needs, for one design."""
    hubs: list
    phcs: list
    L: np.ndarray            # (J, I) one-way flight distance around no-fly zones, km
    reach: np.ndarray        # (J, I) bool, L <= R_wind
    lam: np.ndarray          # (I, 3) orders per day by class
    busy_h: np.ndarray       # (J, I, 24) expected drone-minutes in hour h if i is served by j
    bay_h: np.ndarray        # (J, I, 24) expected grid Wh to recharge in hour h
    c_x: np.ndarray          # (J, I) annual variable cost of flying i from j (sum of the three below)
    c_energy: np.ndarray     # (J, I) grid + genset energy
    c_wear: np.ndarray       # (J, I) battery wear (pack cost / cycle life per Wh cycled)
    c_grounded: np.ndarray   # (J, I) road trips for orders placed in gust-grounded hours
    c_road: np.ndarray       # (I,) annual cost of serving i by road
    c_hub: float
    c_drone: float
    c_bay: float
    c_staff: float
    cap_drone: float         # drone-minutes per drone per hour usable
    cap_bay: float           # Wh per bay per hour usable
    emerg: np.ndarray        # (I,) emergencies per day
    emerg_road_max: float
    design: dict


def build_instance(design, dist, settings=Settings(), p=Params(), hubs=None, phcs=None):
    """design: one row of m1_fm_designs (needs m, R_km, V, E, P_* columns); dist: hub x PHC km."""
    costs = load_costs()
    ops = costs["operations"]
    hubs = list(dist.index) if hubs is None else list(hubs)
    phcs = list(dist.columns) if phcs is None else list(phcs)
    L = dist.loc[hubs, phcs].values.astype(float)

    V, w = float(design["V"]), settings.wind
    R_wind = float(radius_in_wind(design["R_km"], V, w))
    reach = L <= R_wind + 1e-9

    # flight time (brief's headwind model) and energy per sortie, out and back
    t_flight_min = 2 * L * 1000 * V / (V**2 - w**2) / 60
    P_cruise = 0.5 * (design["P_cruise_loaded"] + design["P_cruise_empty"])   # half the time each
    E_trip = (P_cruise * t_flight_min * 60 + p.t_hover * (design["P_hover_loaded"]
                                                          + design["P_hover_empty"])) / 3600  # Wh
    ground = ops["load_and_takeoff_min"] + ops["drop_or_pickup_min"] + ops["battery_swap_turnaround_min"]
    zero = L < 1e-9                       # PHC that is itself the hub (P09): no flight needed
    busy = np.where(zero, 0.0, t_flight_min + ground)
    E_trip = np.where(zero, 0.0, E_trip)
    eff = costs["charging_bay"]["charge_efficiency"]
    grid_Wh = E_trip / eff

    dem = load_csv("demand.csv").set_index("phc_id").loc[phcs]
    lam = dem[[f"{c}_per_day" for c in CLASSES]].values                   # (I, 3)
    f = demand_profiles()                                                 # (3, 24)
    orders_h = lam @ f                                                    # (I, 24) orders in hour h
    busy_h = busy[:, :, None] * orders_h[None, :, :]
    bay_h = grid_Wh[:, :, None] * orders_h[None, :, :]

    # annual variable cost of flying: energy (grid, genset in outage hours), battery wear,
    # and road trips for orders that fall in gust-grounded hours
    g = grounded_by_hour(settings.gust_limit)
    g_c = f @ g                                                           # (3,) share grounded by class
    out_h, out_days = outage_hours()
    out_c = f[:, out_h].sum(axis=1) * out_days                            # (3,) share in outage
    e = costs["energy"]
    price_c = (e["grid_per_kWh"] * (1 - out_c) + e["genset_per_kWh"] * out_c) / 1000   # INR/Wh
    wear = costs["battery_pack"]["cost_per_Wh"] / costs["battery_pack"]["cycle_life"]   # INR per Wh cycled
    road = np.array([costs["road_fallback"]["cost_per_trip"][c] for c in CLASSES])
    flown = lam * (1 - g_c)                                               # (I, 3) flown orders/day
    c_energy = 365 * np.einsum("ic,c,ji->ji", flown, price_c, grid_Wh)
    c_wear = 365 * wear * flown.sum(axis=1)[None, :] * E_trip
    c_grounded = np.broadcast_to(365 * (lam * g_c * road).sum(axis=1)[None, :], L.shape).copy()
    c_x = c_energy + c_wear + c_grounded
    c_road = 365 * (lam * road).sum(axis=1)

    r = costs["discount_rate"]
    hub, dr, bay, st = costs["hub"], costs["drone"], costs["charging_bay"], costs["staff"]
    c_hub = hub["capex"] * crf(r, hub["life_years"]) + hub["opex_per_year"]
    drone_capex = dr["capex_fixed"] + dr["capex_per_kg_mtow"] * float(design["m"])
    c_drone = drone_capex * (crf(r, dr["life_years"]) + dr["maintenance_frac_of_capex_per_year"])
    c_bay = bay["capex"] * crf(r, bay["life_years"])
    c_staff = st["salary_per_person_year"] * st["shifts_per_day"] * st["relief_factor"]

    avail = flyable_fraction(settings.gust_limit)
    return Instance(hubs, phcs, L, reach, lam, busy_h, bay_h, c_x, c_energy, c_wear, c_grounded,
                    c_road, c_hub, c_drone, c_bay,
                    c_staff, cap_drone=settings.util_max * 60 * avail,
                    cap_bay=settings.bay_util_max * bay["power_kW"] * 1000,
                    emerg=lam[:, 0], emerg_road_max=settings.emerg_road_max,
                    design=dict(design, R_wind_km=R_wind, wind=w, avail=avail,
                                drones_per_operator=st["drones_per_operator"]))


# ---------------------------------------------------------------- MILP
def big_m(inst):
    """Smallest safe upper bounds: the fleet/bays one hub would need if it flew every PHC it can
    reach. No feasible solution needs more at any hub, so n_j <= N_max y_j cuts nothing off."""
    busy = np.where(inst.reach[:, :, None], inst.busy_h, 0).sum(axis=1).max(axis=1)   # (J,)
    bay = np.where(inst.reach[:, :, None], inst.bay_h, 0).sum(axis=1).max(axis=1)
    N = np.maximum(1, np.ceil(busy / inst.cap_drone - 1e-9)).astype(int)
    B = np.maximum(1, np.ceil(bay / inst.cap_bay - 1e-9)).astype(int)
    O = np.maximum(1, np.ceil(N / inst.design["drones_per_operator"] - 1e-9)).astype(int)
    return N, B, O


def index(J, I):
    """Variable layout: y (J) | x (J*I) | r (I) | n (J) | b (J) | o (J)."""
    y = np.arange(J)
    x = J + np.arange(J * I).reshape(J, I)
    r = J + J * I + np.arange(I)
    n = J + J * I + I + np.arange(J)
    b = n + J
    o = b + J
    return y, x, r, n, b, o, J + J * I + I + 3 * J


def solve(inst, relax=False, time_limit=60):
    J, I = len(inst.hubs), len(inst.phcs)
    y, x, r, n, b, o, nv = index(J, I)
    N, B, O = big_m(inst)
    dpo = inst.design["drones_per_operator"]

    c = np.zeros(nv)
    c[y] = inst.c_hub
    c[x] = inst.c_x
    c[r] = inst.c_road
    c[n] = inst.c_drone
    c[b] = inst.c_bay
    c[o] = inst.c_staff

    lb, ub = np.zeros(nv), np.ones(nv)
    ub[x] = inst.reach.astype(float)          # (3) reach as a bound
    ub[n], ub[b], ub[o] = N, B, O

    rows = I + J * I + 2 * 24 * J + J + 3 * J + 3 * J + 1
    A = lil_matrix((rows, nv))
    lo, hi = np.full(rows, -np.inf), np.zeros(rows)
    k = 0
    for i in range(I):                         # (1)
        A[k, x[:, i]] = 1; A[k, r[i]] = 1; lo[k] = hi[k] = 1; k += 1
    for j in range(J):                         # (2)
        for i in range(I):
            A[k, x[j, i]] = 1; A[k, y[j]] = -1; k += 1
    for j in range(J):                         # (4), (5) every hour
        for h in range(24):
            A[k, x[j]] = inst.busy_h[j, :, h]; A[k, n[j]] = -inst.cap_drone; k += 1
            A[k, x[j]] = inst.bay_h[j, :, h]; A[k, b[j]] = -inst.cap_bay; k += 1
        A[k, b[j]] = -1; A[k, y[j]] = 1; k += 1                       # b_j >= y_j
        A[k, n[j]] = 1; A[k, o[j]] = -dpo; k += 1                     # (6) 4 o_j >= n_j
        A[k, o[j]] = -1; A[k, y[j]] = 1; k += 1                       #     o_j >= y_j
        A[k, n[j]] = -1; A[k, y[j]] = 1; k += 1                       #     n_j >= y_j
        A[k, n[j]] = 1; A[k, y[j]] = -N[j]; k += 1                    # (7) big-M
        A[k, b[j]] = 1; A[k, y[j]] = -B[j]; k += 1
        A[k, o[j]] = 1; A[k, y[j]] = -O[j]; k += 1
    A[k, r] = inst.emerg; hi[k] = inst.emerg_road_max * inst.emerg.sum(); k += 1   # (8)
    assert k == rows

    integrality = np.zeros(nv) if relax else np.ones(nv)
    res = milp(c, constraints=LinearConstraint(A.tocsr(), lo, hi), bounds=Bounds(lb, ub),
               integrality=integrality, options={"time_limit": time_limit, "mip_rel_gap": 0})
    if res.x is None:
        return dict(status=res.status, message=res.message, cost=np.inf)
    v = res.x
    out = dict(status=res.status, message=res.message, cost=res.fun,
               y=v[y], x=v[x], r=v[r], n=v[n], b=v[b], o=v[o], big_m=(N, B, O))
    if not relax:
        for key in ("y", "x", "r", "n", "b", "o"):
            out[key] = np.rint(out[key]).astype(int)
        out["breakdown"] = cost_breakdown(inst, out)
    return out


def cost_breakdown(inst, s):
    xr = s["x"].astype(bool)
    return {
        "hubs": inst.c_hub * s["y"].sum(),
        "drones": inst.c_drone * s["n"].sum(),
        "bays": inst.c_bay * s["b"].sum(),
        "staff": inst.c_staff * s["o"].sum(),
        "energy": float((inst.c_energy * xr).sum()),
        "battery wear": float((inst.c_wear * xr).sum()),
        "road, grounded hours": float((inst.c_grounded * xr).sum()),
        "road, PHCs out of reach": float(inst.c_road @ s["r"]),
    }


def assignment_table(inst, s):
    rows = []
    for i, p in enumerate(inst.phcs):
        j = np.flatnonzero(s["x"][:, i])
        hub = inst.hubs[j[0]] if len(j) else "road"
        rows.append(dict(phc=p, hub=hub, flight_km=round(float(inst.L[j[0], i]), 3) if len(j) else np.nan,
                         emergency_per_day=inst.lam[i, 0], routine_per_day=inst.lam[i, 1],
                         lab_per_day=inst.lam[i, 2]))
    return pd.DataFrame(rows)


def hub_table(inst, s):
    rows = []
    for j, h in enumerate(inst.hubs):
        if not s["y"][j]:
            continue
        xs = s["x"][j].astype(bool)
        peak_busy = inst.busy_h[j][xs].sum(axis=0).max()
        rows.append(dict(hub=h, phcs=" ".join(np.array(inst.phcs)[xs]), drones=s["n"][j],
                         bays=s["b"][j], operators_per_shift=s["o"][j],
                         peak_hour_busy_min=round(float(peak_busy), 1),
                         peak_utilisation=round(float(peak_busy / (60 * inst.design["avail"] * s["n"][j])), 3)
                         if s["n"][j] else 0.0,
                         orders_per_day=round(float(inst.lam[xs].sum()), 2)))
    return pd.DataFrame(rows)
