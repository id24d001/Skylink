"""Choosing adaptive oracle points (requests 2 and 3).

Idea: the GP's standard deviation sigma(x) says where the surrogate is least sure. We pick
points one at a time ("greedy"):
  1. among the allowed candidates of a group, take the one with the largest sigma,
     provided it is at least d_min away (in scaled D, T) from every existing and chosen point;
  2. add it to the design and recompute sigma (with the GP hyperparameters kept fixed).
     sigma does not depend on the FM value that will come back, so this needs no guess of y.
Step 2 stops the next pick from landing next to the last one.
"""
import numpy as np
import pandas as pd
from sklearn.gaussian_process import GaussianProcessRegressor

from .data import D_MIN, D_MAX, T_MIN, T_MAX, disk_loading, make_features


def candidate_grid(n=81):
    """Regular grid of (D, T) candidates over the oracle box, with D, T and DL columns."""
    D, T = np.meshgrid(np.linspace(D_MIN, D_MAX, n), np.linspace(T_MIN, T_MAX, n))
    c = pd.DataFrame({"D_m": D.ravel(), "T_N": T.ravel()})
    c["DL"] = disk_loading(c.D_m, c.T_N)
    return c


def trend_std(gp, X_design, X_query):
    """GP trend std at X_query if the design were X_design (fitted hyperparameters kept fixed)."""
    g = GaussianProcessRegressor(kernel=gp.gp_.kernel_, optimizer=None, normalize_y=False)
    g.fit(X_design, np.zeros(len(X_design)))           # y does not affect the std
    _, sd = g.predict(X_query, return_std=True)
    nugget = gp.hyperparameters()["nugget"]
    return np.sqrt(np.maximum(sd**2 - nugget, 0.0))


def greedy_select(gp, X_existing, cand, groups, d_min=0.10):
    """Pick points group by group. Each group is a dict with 'name', 'why' and either
      'n' + 'mask'  : choose n points by largest sigma among candidates where mask(cand) is True
      'points'      : fixed list of (D, T) chosen by hand (e.g. repeats, an iso-DL line);
                      the distance rule is not applied to these (a repeat has distance 0 on purpose)
    Returns one row per chosen point, in the order chosen."""
    X_design = np.asarray(X_existing, dtype=float).copy()
    Xc = make_features(cand.D_m, cand.T_N)
    rows = []
    for grp in groups:
        if "points" in grp:
            for D, T in grp["points"]:
                xq = make_features(D, T)
                sd = trend_std(gp, X_design, xq)[0]
                dist = np.min(np.linalg.norm(X_design - xq, axis=1))
                rows.append({"group": grp["name"], "D_m": D, "T_N": T,
                             "DL": float(disk_loading(D, T)), "sigma_before": sd,
                             "min_dist": dist, "why": grp["why"]})
                X_design = np.vstack([X_design, xq])
            continue
        allowed = np.asarray(grp["mask"](cand), dtype=bool)
        for _ in range(grp["n"]):
            sd = trend_std(gp, X_design, Xc)
            dist = np.min(np.linalg.norm(Xc[:, None, :] - X_design[None, :, :], axis=2), axis=1)
            ok = allowed & (dist >= d_min)
            if not ok.any():
                raise ValueError(f"group '{grp['name']}': no candidate left with d_min={d_min}")
            k = np.flatnonzero(ok)[np.argmax(sd[ok])]
            rows.append({"group": grp["name"], "D_m": cand.D_m[k], "T_N": cand.T_N[k],
                         "DL": cand.DL[k], "sigma_before": sd[k], "min_dist": dist[k],
                         "why": grp["why"]})
            X_design = np.vstack([X_design, Xc[k]])
    return pd.DataFrame(rows)


def write_request_csv(points, path, student_id="ROLL_NUMBER"):
    """Oracle request format from the brief: columns student_id,D_m,T_N (one row per point)."""
    out = pd.DataFrame({"student_id": student_id,
                        "D_m": np.round(points.D_m.values, 3),
                        "T_N": np.round(points.T_N.values, 2)})
    bad = ~(out.D_m.between(D_MIN, D_MAX) & out.T_N.between(T_MIN, T_MAX))
    if bad.any():
        raise ValueError(f"points outside the oracle box:\n{out[bad]}")
    out.to_csv(path, index=False)
    return out
