"""M4 hub-location and fleet-sizing MILP (one solve per M1 design), following the brief's M4 rules.

Brief, M4: "Variables: binary y_j (open hub j), binary x_ij (PHC i served by hub j), integer n_j
(drones), integer b_j (bays). A PHC can be assigned to a hub only if the flight path around the
no-fly zones, out and back, is within your M1 range with reserve. Capacity: drone busy time at a
hub must cover its assigned demand at no more than 70% utilization. Objective: annualized cost
from costs (hubs, drones, bays, operators, energy, road fallback). Solve once per M1 design."
Brief, M5: "The MILP uses averages." -> mean daily demand, mean wind.

Sets: j candidate hubs (H0-H6, P09), i PHCs (P01-P24), c in {emergency, routine, lab}.
Variables (scipy.optimize.milp / HiGHS):
  y_j   open hub j                        binary
  x_ij  PHC i served by hub j             binary (bound to 0 when out of reach)
  n_j   drones at hub j                   integer 0..N_max_j
  b_j   charging bays at hub j            integer 0..B_max_j
  o_j   operators per shift at hub j      integer 0..O_max_j (linearises ceil(n_j / 4), costs.yaml)
  r_i   PHC i by road                     binary, only in the "what-if" run for PHCs that no
                                          candidate hub reaches (fixed to 0 in the real model)
Objective: annual cost, INR (CRF at 10 %): hubs, drones (+ battery packs), bays, operators,
energy, road fallback (emergencies placed in gust-grounded hours: they wait > 30 min -> road).
Constraints:
  (1) sum_j x_ij = 1                                      every PHC is served by a hub
  (2) x_ij <= y_j                                         only open hubs serve (no big-M)
  (3) reach: L_ij <= R (still-air M1 radius, 15 % reserve), and 3 + L/V + 1 + 1 <= 45 min
  (4) sum_i busy_ij x_ij <= 0.70 * 1440 * a * n_j         drone time per day, a = flyable share
  (5) sum_i chg_ij x_ij <= 24 b_j  and  <= (24 - h_out) b_j + 2 h_out y_j
                                                          bay-hours per day; in an outage the
                                                          3 kW genset runs only 2 x 1.5 kW bays
  (6) 4 o_j >= n_j,  o_j >= y_j,  n_j >= y_j,  b_j >= y_j
  (7) big-M: n_j <= N_max y_j,  b_j <= B_max y_j,  o_j <= O_max y_j
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix

from common import load_costs, load_csv
from m1_vehicle.model import Params

CLASSES = ("emergency", "routine", "lab")
DAY_MIN = 24 * 60


def crf(r, n):
    return r * (1 + r) ** n / ((1 + r) ** n - 1)


@dataclass
class Settings:
    util_max: float = 0.70           # brief M4: <= 70 % utilisation of drone time
    gust_limit: float = 14.0         # costs.yaml: no take-offs in an hour with gust >= 14 m/s
    road_whatif: bool = False        # True only for the what-if that prices full coverage


# ---------------------------------------------------------------- data
def demand_profiles():
    """Hour-of-day fractions per class, each rescaled to sum to 1 (routine sums to 1.1993 in
    the raw file; planted error, handled in M0)."""
    hp = load_csv("hourly_profile.csv").set_index("hour")
    f = np.stack([hp[f"{c}_frac"].values for c in CLASSES])        # (3, 24)
    return f / f.sum(axis=1, keepdims=True)


def wind_stats(V, gust_limit=14.0):
    """Flyable share of hours, and the mean of the brief's headwind time factor V^2/(V^2-w^2)
    over flyable hours (the average a MILP should use; applied to both time and energy)."""
    w = load_csv("wind_days.csv")
    fly = w.gust_ms < gust_limit
    wf = np.minimum(w.loc[fly, "mean_wind_ms"].values, 0.95 * V)
    return float(fly.mean()), float(np.mean(V**2 / (V**2 - wf**2))), float(wf.mean())


def grounded_share(gust_limit=14.0):
    """Share of each class's orders placed in a grounded hour (hourly profile x grounding)."""
    w = load_csv("wind_days.csv")
    g = (w.gust_ms >= gust_limit).groupby(w.hour).mean().reindex(range(24)).values
    return demand_profiles() @ g                                          # (3,)


def outage_hours_per_day():
    c = load_costs()["grid_outage"]
    return (int(c["end"][:2]) - int(c["start"][:2])) * len(c["days"]) / 7   # 3 h x 3/7 = 1.29 h


@dataclass
class Instance:
    """Everything the MILP (and the brute-force check) needs, for one design."""
    hubs: list
    phcs: list
    L: np.ndarray            # (J, I) one-way flight distance around the no-fly zones, km
    reach: np.ndarray        # (J, I) bool
    lam: np.ndarray          # (I, 3) mean orders per day by class
    busy: np.ndarray         # (J, I) drone-minutes per day if i is served by j
    chg: np.ndarray          # (J, I) bay-hours per day to recharge those sorties
    c_x: np.ndarray          # (J, I) annual variable cost (energy + packs + grounded road)
    c_energy: np.ndarray
    c_packs: np.ndarray
    c_grounded: np.ndarray
    c_road: np.ndarray       # (I,) annual cost of all of i's orders by road (what-if only)
    road_ok: np.ndarray      # (I,) bool, road variable allowed (what-if and unreachable only)
    c_hub: float
    c_drone: float
    c_bay: float
    c_staff: float
    cap_drone: float         # usable drone-minutes per drone per day
    h_out: float             # outage hours per day (average)
    outage_bays: int         # bays the genset can run
    design: dict


def build_instance(design, dist, settings=Settings(), p=Params(), hubs=None, phcs=None):
    """design: one row of m1_fm_designs (m, R_km, V, E, P_* columns); dist: hub x PHC km."""
    costs = load_costs()
    ops = costs["operations"]
    hubs = list(dist.index) if hubs is None else list(hubs)
    phcs = list(dist.columns) if phcs is None else list(phcs)
    L = dist.loc[hubs, phcs].values.astype(float)
    V, R = float(design["V"]), float(design["R_km"])

    # (3) reach: M1 still-air radius (eq. 5 includes the 15 % reserve), and the 45 min promise
    hover_min = 2 * p.t_hover / 60                                       # 60 s at each end (eq. 5)
    t_deliver = ops["load_and_takeoff_min"] + L * 1000 / V / 60 + p.t_hover / 60 + ops["drop_or_pickup_min"]
    reach = (L <= R + 1e-9) & (t_deliver <= ops["emergency_target_min"])

    # averages: flight time and energy at the mean headwind factor over flyable hours
    avail, k_wind, w_mean = wind_stats(V, settings.gust_limit)
    t_cruise_min = 2 * L * 1000 / V / 60 * k_wind
    ground = ops["load_and_takeoff_min"] + ops["drop_or_pickup_min"] + ops["battery_swap_turnaround_min"]
    E_sortie = (0.5 * (design["P_cruise_loaded"] + design["P_cruise_empty"]) * t_cruise_min * 60
                + p.t_hover * (design["P_hover_loaded"] + design["P_hover_empty"])) / 3600   # Wh
    zero = L < 1e-9                    # P09 served by a hub at P09: hand-over, no sortie
    busy_sortie = np.where(zero, 0.0, t_cruise_min + hover_min + ground)
    E_sortie = np.where(zero, 0.0, E_sortie)

    dem = load_csv("demand.csv").set_index("phc_id").loc[phcs]
    lam = dem[[f"{c}_per_day" for c in CLASSES]].values                 # (I, 3)
    g = grounded_share(settings.gust_limit)                             # (3,)
    # emergencies placed in a grounded hour wait > 30 min and go by road (costs.yaml);
    # routine ("same day") and lab orders wait for the next flyable hour and are still flown
    flown = lam.copy()
    flown[:, 0] *= 1 - g[0]
    sorties = flown.sum(axis=1)                                          # (I,) per day
    busy = busy_sortie * sorties[None, :]
    bay, e = costs["charging_bay"], costs["energy"]
    grid_Wh = E_sortie / bay["charge_efficiency"]
    chg = grid_Wh * sorties[None, :] / (bay["power_kW"] * 1000)

    h_out = outage_hours_per_day()
    price = (e["grid_per_kWh"] * (24 - h_out) + e["genset_per_kWh"] * h_out) / 24 / 1000   # INR/Wh
    road = costs["road_fallback"]["cost_per_trip"]
    pack = costs["battery_pack"]
    c_energy = 365 * price * grid_Wh * sorties[None, :]
    # every sortie ends in a battery swap and a full recharge, so it uses one of the pack's
    # 500 cycles: pack cost 22 INR/Wh x E, spread over 500 sorties
    c_packs = np.where(zero, 0.0, 365 * pack["cost_per_Wh"] * float(design["E"]) / pack["cycle_life"]
                       * sorties[None, :])
    c_grounded = np.broadcast_to(365 * lam[:, 0] * g[0] * road["emergency"], L.shape).copy()
    c_x = c_energy + c_packs + c_grounded
    c_road = 365 * (lam * np.array([road[c] for c in CLASSES])).sum(axis=1)
    road_ok = (~reach.any(axis=0)) & settings.road_whatif

    r = costs["discount_rate"]
    hub, dr, st = costs["hub"], costs["drone"], costs["staff"]
    c_hub = hub["capex"] * crf(r, hub["life_years"]) + hub["opex_per_year"]
    drone_capex = dr["capex_fixed"] + dr["capex_per_kg_mtow"] * float(design["m"])
    c_drone = drone_capex * (crf(r, dr["life_years"]) + dr["maintenance_frac_of_capex_per_year"])
    c_bay = bay["capex"] * crf(r, bay["life_years"])
    c_staff = st["salary_per_person_year"] * st["shifts_per_day"] * st["relief_factor"]
    outage_bays = int(costs["grid_outage"]["genset_kW_per_hub"] // bay["power_kW"])

    return Instance(hubs, phcs, L, reach, lam, busy, chg, c_x, c_energy, c_packs, c_grounded,
                    c_road, road_ok, c_hub, c_drone, c_bay, c_staff,
                    cap_drone=settings.util_max * DAY_MIN * avail, h_out=h_out,
                    outage_bays=outage_bays,
                    design=dict(design, avail=avail, k_wind=k_wind, w_mean=w_mean,
                                grounded_emergency_share=float(g[0]),
                                drones_per_operator=st["drones_per_operator"],
                                max_delivery_min=float(np.where(reach, t_deliver, 0).max())))


# ---------------------------------------------------------------- MILP
def bays_needed(chg, h_out, k_out):
    """Smallest b with chg <= 24 b and chg <= (24 - h_out) b + k_out h_out."""
    b = np.maximum(chg / 24, (chg - k_out * h_out) / (24 - h_out))
    return np.maximum(1, np.ceil(b - 1e-9))


def big_m(inst):
    """Smallest safe bounds: what hub j would need if it served every PHC it can reach. No
    feasible plan needs more at hub j, so n_j <= N_max y_j cuts nothing off."""
    busy = np.where(inst.reach, inst.busy, 0).sum(axis=1)
    chg = np.where(inst.reach, inst.chg, 0).sum(axis=1)
    N = np.maximum(1, np.ceil(busy / inst.cap_drone - 1e-9)).astype(int)
    B = bays_needed(chg, inst.h_out, inst.outage_bays).astype(int)
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


def solve(inst, relax=False, time_limit=60, bigm=None):
    J, I = len(inst.hubs), len(inst.phcs)
    y, x, r, n, b, o, nv = index(J, I)
    N, B, O = big_m(inst) if bigm is None else bigm
    dpo = inst.design["drones_per_operator"]

    c = np.zeros(nv)
    c[y], c[x], c[r] = inst.c_hub, inst.c_x, inst.c_road
    c[n], c[b], c[o] = inst.c_drone, inst.c_bay, inst.c_staff

    lb, ub = np.zeros(nv), np.ones(nv)
    ub[x] = inst.reach.astype(float)              # (3)
    ub[r] = inst.road_ok.astype(float)            # 0 in the real model
    ub[n], ub[b], ub[o] = N, B, O

    rows = I + J * I + 10 * J
    A = lil_matrix((rows, nv))
    lo, hi = np.full(rows, -np.inf), np.zeros(rows)
    k = 0
    for i in range(I):                                                   # (1)
        A[k, x[:, i]] = 1; A[k, r[i]] = 1; lo[k] = hi[k] = 1; k += 1
    for j in range(J):                                                   # (2)
        for i in range(I):
            A[k, x[j, i]] = 1; A[k, y[j]] = -1; k += 1
    for j in range(J):
        A[k, x[j]] = inst.busy[j]; A[k, n[j]] = -inst.cap_drone; k += 1  # (4)
        A[k, x[j]] = inst.chg[j]; A[k, b[j]] = -24; k += 1               # (5)
        A[k, x[j]] = inst.chg[j]; A[k, b[j]] = -(24 - inst.h_out)
        A[k, y[j]] = -inst.outage_bays * inst.h_out; k += 1
        A[k, n[j]] = 1; A[k, o[j]] = -dpo; k += 1                        # (6)
        A[k, o[j]] = -1; A[k, y[j]] = 1; k += 1
        A[k, n[j]] = -1; A[k, y[j]] = 1; k += 1
        A[k, b[j]] = -1; A[k, y[j]] = 1; k += 1
        A[k, n[j]] = 1; A[k, y[j]] = -N[j]; k += 1                       # (7)
        A[k, b[j]] = 1; A[k, y[j]] = -B[j]; k += 1
    assert k == rows - J
    for j in range(J):
        A[k, o[j]] = 1; A[k, y[j]] = -O[j]; k += 1

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
        "battery packs (500 cycles)": float((inst.c_packs * xr).sum()),
        "bays": inst.c_bay * s["b"].sum(),
        "operators": inst.c_staff * s["o"].sum(),
        "energy": float((inst.c_energy * xr).sum()),
        "road, grounded emergencies": float((inst.c_grounded * xr).sum()),
        "road, PHCs out of reach (what-if)": float(inst.c_road @ s["r"]),
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
        busy, chg = inst.busy[j][xs].sum(), inst.chg[j][xs].sum()
        n = s["n"][j]
        rows.append(dict(hub=h, phcs=" ".join(np.array(inst.phcs)[xs]), drones=n, bays=s["b"][j],
                         operators_per_shift=s["o"][j], orders_per_day=round(float(inst.lam[xs].sum()), 2),
                         busy_min_per_day=round(float(busy), 1),
                         utilisation=round(float(busy / (DAY_MIN * inst.design["avail"] * n)), 3) if n else 0.0,
                         bay_hours_per_day=round(float(chg), 2)))
    return pd.DataFrame(rows)
