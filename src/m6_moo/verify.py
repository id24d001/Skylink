"""M6 verification.

1. zdt1_check      the NSGA-II engine on ZDT1 (known front f2 = 1 - sqrt(f1)): generational
                   distance and spread of the final population.
2. ws_check        brief: "on a two-objective slice, the weighted-sum points lie on or behind
                   your NSGA-II front". Slice = design D (the M4 choice), objectives cost and S45.
3. resim_table     brief: "re-simulated front points agree with any surrogate predictions within
                   the stated error". No surrogate is used (NSGA-II on CRN), so the matching check
                   is that front points hold up off the 73 search days: each is re-simulated on all
                   365 wind days with fresh demand seeds, and the change is compared with the
                   73-day 95 % confidence interval.
4. cost_hand_check the fixed cost terms of "D as M4" equal M4's cost breakdown.
"""
import numpy as np
import pandas as pd
from scipy import stats

from .nsga2 import nsga2, pareto_mask


def zdt1_check(pop=100, gens=250, seed=1):
    n = 30
    lo, hi = np.zeros(n), np.ones(n)

    def ev(X):
        f1 = X[:, 0]
        g = 1 + 9 * X[:, 1:].mean(axis=1)
        return np.c_[f1, g * (1 - np.sqrt(f1 / g))], np.zeros(len(X))

    res = nsga2(ev, lo, hi, np.zeros(n, bool), pop=pop, gens=gens, seed=seed)
    F = res["F"][res["rank"] == 0]
    gd = float(np.mean(np.abs(F[:, 1] - (1 - np.sqrt(F[:, 0])))))
    return pd.DataFrame([dict(problem="ZDT1 (30 variables)", pop=pop, gens=gens, n_front=len(F),
                              mean_gap_to_true_front=gd, f1_min=F[:, 0].min(), f1_max=F[:, 0].max(),
                              passed=gd < 0.01 and F[:, 0].min() < 0.02 and F[:, 0].max() > 0.98)])


def front_2d(cost, s45):
    """Non-dominated (cost min, S45 max) points, sorted by cost."""
    F = np.c_[cost, -np.asarray(s45)]
    m = pareto_mask(F)
    idx = np.where(m)[0]
    return idx[np.argsort(np.asarray(cost)[idx])]


def behind_front(point, front, scale):
    """Normalised shortfall of a (cost, S45) point behind a staircase front (<= 0: on or behind).

    The front is the attainment staircase of its points. A point is behind it when some front
    point is at least as cheap and at least as good on S45. Otherwise the gap is the smallest
    normalised move that would put it there."""
    c, s = point
    fc, fs = front[:, 0], front[:, 1]
    gaps = np.maximum((fc - c) / scale[0], 0) + np.maximum((s - fs) / scale[1], 0)
    return float(gaps.min())


def ws_table(ws_points, front, scale, tol=0.01):
    rows = []
    for w, c, s, label in ws_points:
        g = behind_front((c, s), front, scale)
        # absolute view: best front S45 at no more than this cost, and the S45 it is ahead by
        cheaper = front[front[:, 0] <= c + 1e-12]
        s_front = cheaper[:, 1].max() if len(cheaper) else np.nan
        rows.append(dict(w_cost=w, config=label, cost_cr=c, S45=s, gap_normalised=g,
                         on_or_behind=g <= tol, front_S45_at_cost=s_front,
                         S45_ahead_pp=100 * max(s - s_front, 0) if np.isfinite(s_front) else np.nan))
    return pd.DataFrame(rows)


def resim_table(ev, recs, days365, seeds365):
    """Re-simulate configurations on all 365 days (fresh seeds); compare with the 73-day value."""
    cfgs = [c for c, _ in recs]
    full = ev.run(cfgs, days=days365, seeds=seeds365)
    rows = []
    for (c, o), f in zip(recs, full):
        n = o["days"]
        hw73 = stats.t.ppf(0.975, n - 1) * o["S45_sd"] / np.sqrt(n)
        hw365 = stats.t.ppf(0.975, f["days"] - 1) * f["S45_sd"] / np.sqrt(f["days"])
        d = f["S45"] - o["S45"]
        rows.append(dict(config=c.label, design=c.design, cost_cr_73=o["cost"] / 1e7,
                         cost_cr_365=f["cost"] / 1e7, S45_73=o["S45"], S45_73_ci=hw73,
                         S45_365=f["S45"], S45_365_ci=hw365, dS45=d,
                         within_73_ci=abs(d) <= np.hypot(hw73, hw365),
                         road_73=o["road_share"], road_365=f["road_share"],
                         P95_73=o["P95"], P95_365=f["P95"],
                         em_road_73=o["em_road_share"], em_road_365=f["em_road_share"]))
    df = pd.DataFrame(rows)
    rho = stats.spearmanr(df.S45_73, df.S45_365).correlation if len(df) > 2 else np.nan
    return df, float(rho)


def cost_hand_check(terms, m4_cost_row):
    rows = []
    for k_sim, k_m4 in (("hubs", "hubs_INR"), ("drones", "drones_INR"), ("bays", "bays_INR"),
                        ("operators", "operators_INR")):
        a, b = terms[k_sim], float(m4_cost_row[k_m4])
        rows.append(dict(term=k_sim, M6_INR=a, M4_INR=b, rel_diff=abs(a - b) / b,
                         passed=abs(a - b) / b < 1e-5))
    return pd.DataFrame(rows)
