"""M4 verification (brief): brute-force enumeration on a 4-hub, 8-PHC sub-instance matches the
solver; LP-relaxation gap; big-M justification; plus a hand check of the cost coefficients.

The brute force does not use the MILP matrix. For every assignment of the 8 PHCs to one of the
4 hubs or to road (5^8 = 390,625 assignments) it opens exactly the hubs used, sizes each hub
directly (n = max(1, ceil(peak busy / capacity)), bays and operators likewise), checks reach and
the emergency guard, and adds up the cost. The minimum must equal the MILP optimum.
"""
import itertools

import numpy as np
import pandas as pd

from .model import Settings, build_instance, crf, solve, big_m
from common import load_costs

SUB_HUBS = ["H1", "H3", "H5", "P09"]
SUB_PHCS = ["P01", "P02", "P03", "P04", "P05", "P06", "P07", "P08"]


def brute_force(inst, chunk=50_000):
    J, I = len(inst.hubs), len(inst.phcs)
    dpo = inst.design["drones_per_operator"]
    best = (np.inf, None)
    allowed = [list(np.flatnonzero(inst.reach[:, i])) + [J] for i in range(I)]   # J = road
    n_assign = int(np.prod([len(a) for a in allowed]))
    combos = itertools.product(*allowed)
    guard = inst.emerg_road_max * inst.emerg.sum()
    n_feasible = 0
    while True:
        a = np.array(list(itertools.islice(combos, chunk)))
        if a.size == 0:
            break
        road = a == J
        ok = (road * inst.emerg).sum(axis=1) <= guard + 1e-12
        cost = (road * inst.c_road).sum(axis=1).astype(float)
        for j in range(J):
            m = a == j                                                     # (k, I)
            used = m.any(axis=1)
            busy = np.einsum("ki,ih->kh", m, inst.busy_h[j]).max(axis=1)
            bay = np.einsum("ki,ih->kh", m, inst.bay_h[j]).max(axis=1)
            n = np.maximum(1, np.ceil(busy / inst.cap_drone - 1e-9))
            b = np.maximum(1, np.ceil(bay / inst.cap_bay - 1e-9))
            o = np.maximum(1, np.ceil(n / dpo - 1e-9))
            cost += used * (inst.c_hub + inst.c_drone * n + inst.c_bay * b + inst.c_staff * o
                            ) + (m * inst.c_x[j]).sum(axis=1)
        cost = np.where(ok, cost, np.inf)
        n_feasible += int(ok.sum())
        k = int(np.argmin(cost))
        if cost[k] < best[0]:
            best = (float(cost[k]), a[k])
    return best[0], best[1], n_assign, n_feasible


def brute_force_table(designs, dist, guards=(0.05, 1.0)):
    rows = []
    for _, d in designs.iterrows():
        for g in guards:
            inst = build_instance(d.to_dict(), dist, Settings(emerg_road_max=g),
                                  hubs=SUB_HUBS, phcs=SUB_PHCS)
            s = solve(inst)
            bf, a, n_all, n_ok = brute_force(inst)
            labels = [inst.hubs[k] if k < len(inst.hubs) else "road" for k in a] if a is not None else []
            milp_lab = [inst.hubs[np.flatnonzero(s["x"][:, i])[0]] if s["x"][:, i].any() else "road"
                        for i in range(len(inst.phcs))] if np.isfinite(s["cost"]) else []
            rows.append(dict(design=d.design, emergency_road_cap=g, assignments=n_all,
                             feasible=n_ok, brute_force_cost=bf, milp_cost=s["cost"],
                             rel_diff=abs(bf - s["cost"]) / bf if np.isfinite(bf) else 0.0,
                             same_assignment=labels == milp_lab,
                             brute_force_assignment=" ".join(labels),
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
                         N_max=" ".join(map(str, N)), max_n_used=int(s["n"].max()),
                         B_max=" ".join(map(str, B)), max_b_used=int(s["b"].max())))
    return pd.DataFrame(rows)


def big_m_check(inst):
    """N_max is the fleet one hub needs if it flies every PHC it can reach; any feasible n_j is
    at most that. Re-solving with N_max doubled must give the same optimum (bound not binding)."""
    s1 = solve(inst)
    N, B, O = big_m(inst)
    import m4_network.model as M
    orig = M.big_m
    M.big_m = lambda i: (2 * N, 2 * B, 2 * O)
    try:
        s2 = solve(inst)
    finally:
        M.big_m = orig
    return s1["cost"], s2["cost"]


def hand_check(inst):
    """Cost coefficients recomputed by hand from costs.yaml, against the model's values."""
    c = load_costs()
    m = float(inst.design["m"])
    staff = c["staff"]
    rows = [
        ("hub, INR/yr", "18,00,000 x CRF(10%,10y) + 4,00,000",
         1_800_000 * 0.1 * 1.1**10 / (1.1**10 - 1) + 400_000, inst.c_hub),
        ("drone, INR/yr", f"(2,00,000 + 45,000 x {m:.2f} kg) x (CRF(10%,3y) + 0.15)",
         (200_000 + 45_000 * m) * (0.1 * 1.1**3 / (1.1**3 - 1) + 0.15), inst.c_drone),
        ("bay, INR/yr", "90,000 x CRF(10%,5y)", 90_000 * 0.1 * 1.1**5 / (1.1**5 - 1), inst.c_bay),
        ("operator per shift, INR/yr", "4,20,000 x 3 shifts x 1.2", 420_000 * 3 * 1.2, inst.c_staff),
        ("drone-minutes per drone per hour", "0.70 x 60 x flyable share",
         0.70 * 60 * inst.design["avail"], inst.cap_drone),
        ("bay Wh per hour", "0.70 x 1.5 kW", 0.70 * 1500, inst.cap_bay),
    ]
    j, i = inst.hubs.index("H1"), inst.phcs.index("P01")
    V, w, L = inst.design["V"], inst.design["wind"], inst.L[j, i]
    rows.append(("H1->P01 busy min per trip", f"2 x {L:.3f} km x V/(V^2-w^2) + 10 min",
                 2 * L * 1000 * V / (V**2 - w**2) / 60 + 10,
                 inst.busy_h[j, i].sum() / inst.lam[i].sum()))
    df = pd.DataFrame(rows, columns=["quantity", "hand formula", "hand", "model"])
    df["rel_diff"] = (df.hand - df.model).abs() / df.hand
    assert crf(0.1, 10) > 0
    return df
