"""M4 verification (brief): "brute-force enumeration on a 4-hub, 8-PHC sub-instance matches your
solver; report the LP-relaxation gap; justify any big-M." Plus a hand check of the coefficients.

The brute force does not use the MILP matrix. For every assignment of the 8 PHCs to a hub that
reaches them (and to road only where the what-if allows it) it opens exactly the hubs used,
sizes each one directly (drones = max(1, ceil(busy / capacity)), the fewest bays that meet both
bay-hour limits, operators = max(1, ceil(drones / 4))) and adds up the cost. Opening a hub that
serves nobody only adds cost, so the minimum over assignments is the true optimum.
"""
import itertools

import numpy as np
import pandas as pd

from .model import DAY_MIN, Settings, big_m, build_instance, solve
from m1_vehicle.model import Params

SUB_HUBS = ["H0", "H1", "H3", "P09"]                # central-west: contested assignments
SUB_PHCS = ["P04", "P05", "P06", "P07", "P08", "P10", "P22", "P23"]


def brute_force(inst):
    J, I = len(inst.hubs), len(inst.phcs)
    dpo = inst.design["drones_per_operator"]
    allowed = [list(np.flatnonzero(inst.reach[:, i])) + ([J] if inst.road_ok[i] else [])
               for i in range(I)]
    if any(len(a) == 0 for a in allowed):
        return np.inf, None, 0
    a = np.array(list(itertools.product(*allowed)))
    road = a == J
    cost = (road * inst.c_road).sum(axis=1).astype(float)
    h = inst.h_out
    for j in range(J):
        m = a == j
        used = m.any(axis=1)
        busy = m @ inst.busy[j]
        chg = m @ inst.chg[j]
        n = np.maximum(1, np.ceil(busy / inst.cap_drone - 1e-9))
        b = np.maximum.reduce([np.ones(len(a)), np.ceil(chg / 24 - 1e-9),
                               np.ceil((chg - inst.outage_bays * h) / (24 - h) - 1e-9)])
        o = np.maximum(1, np.ceil(n / dpo - 1e-9))
        cost += used * (inst.c_hub + inst.c_drone * n + inst.c_bay * b + inst.c_staff * o) \
            + m @ inst.c_x[j]
    k = int(np.argmin(cost))
    return float(cost[k]), a[k], len(a)


def brute_force_table(designs, dist):
    rows = []
    for _, d in designs.iterrows():
        for whatif in (False, True):
            inst = build_instance(d.to_dict(), dist, Settings(road_whatif=whatif),
                                  hubs=SUB_HUBS, phcs=SUB_PHCS)
            s = solve(inst)
            bf, a, n_all = brute_force(inst)
            lab = lambda k: inst.hubs[k] if k < len(inst.hubs) else "road"
            bf_lab = [lab(k) for k in a] if a is not None else []
            milp_lab = ([inst.hubs[np.flatnonzero(s["x"][:, i])[0]] if s["x"][:, i].any() else "road"
                         for i in range(len(inst.phcs))] if np.isfinite(s["cost"]) else [])
            rows.append(dict(design=d.design, road_whatif=whatif, assignments=n_all,
                             brute_force_cost=bf, milp_cost=s["cost"],
                             rel_diff=abs(bf - s["cost"]) / bf if np.isfinite(bf) else 0.0,
                             same_assignment=bf_lab == milp_lab,
                             brute_force_assignment=" ".join(bf_lab),
                             milp_assignment=" ".join(milp_lab)))
    return pd.DataFrame(rows)


def lp_gap_table(instances):
    rows = []
    for name, inst in instances.items():
        s, lp = solve(inst), solve(inst, relax=True)
        if not np.isfinite(s["cost"]):
            rows.append(dict(design=name, milp_cost=np.inf, lp_cost=lp["cost"], gap=np.nan))
            continue
        N, B, O = s["big_m"]
        rows.append(dict(design=name, milp_cost=s["cost"], lp_cost=lp["cost"],
                         gap=(s["cost"] - lp["cost"]) / s["cost"],
                         lp_fractional_y=int(np.sum((lp["y"] > 1e-6) & (lp["y"] < 1 - 1e-6))),
                         lp_sum_y=float(lp["y"].sum()), milp_hubs=int(s["y"].sum()),
                         N_max=" ".join(map(str, N)), max_n_used=int(s["n"].max()),
                         B_max=" ".join(map(str, B)), max_b_used=int(s["b"].max())))
    return pd.DataFrame(rows)


def big_m_check(inst):
    """Re-solve with every big-M doubled: the optimum must not move (bound not binding)."""
    N, B, O = big_m(inst)
    return solve(inst)["cost"], solve(inst, bigm=(2 * N, 2 * B, 2 * O))["cost"]


def hand_check(inst, p=Params()):
    """Coefficients recomputed by hand from costs.yaml and eq. (5), against the model's."""
    m, V, E = float(inst.design["m"]), float(inst.design["V"]), float(inst.design["E"])
    k, a = inst.design["k_wind"], inst.design["avail"]
    j, i = inst.hubs.index("H1"), inst.phcs.index("P01")
    L = inst.L[j, i]
    sorties = inst.lam[i, 1] + inst.lam[i, 2] + inst.lam[i, 0] * (1 - inst.design["grounded_emergency_share"])
    rows = [
        ("hub, INR/yr", "18,00,000 x CRF(10%,10y) + 4,00,000",
         1_800_000 * 0.1 * 1.1**10 / (1.1**10 - 1) + 400_000, inst.c_hub),
        ("drone, INR/yr", f"(2,00,000 + 45,000 x {m:.2f} kg) x (CRF(10%,3y) + 0.15)",
         (200_000 + 45_000 * m) * (0.1 * 1.1**3 / (1.1**3 - 1) + 0.15), inst.c_drone),
        ("bay, INR/yr", "90,000 x CRF(10%,5y)", 90_000 * 0.1 * 1.1**5 / (1.1**5 - 1), inst.c_bay),
        ("operator post (1 per shift), INR/yr", "4,20,000 x 3 shifts x 1.2", 420_000 * 3 * 1.2, inst.c_staff),
        ("usable drone-min per drone per day", "0.70 x 1440 x flyable share", 0.70 * 1440 * a, inst.cap_drone),
        ("H1->P01 drone-min per day", f"sorties/day x (2 x {L:.3f} km / V x {k:.3f} + 2 + 10)",
         sorties * (2 * L * 1000 / V / 60 * k + 2 + 10), inst.busy[j, i]),
        ("H1->P01 packs, INR/yr", f"365 x sorties x 22 x {E:.0f} Wh / 500",
         365 * sorties * 22 * E / 500, inst.c_packs[j, i]),
        ("outage hours per day", "3 h x 3/7", 9 / 7, inst.h_out),
    ]
    df = pd.DataFrame(rows, columns=["quantity", "hand formula", "hand", "model"])
    df["rel_diff"] = (df.hand - df.model).abs() / df.hand
    return df
