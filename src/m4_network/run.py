"""M4 pipeline step for run_all.py: hub network and fleet MILP, solved once per M1 design,
under the brief's M4 rules (see model.py): every PHC served by a hub; reach = still-air M1
radius with its 15 % reserve; drone busy time <= 70 % on mean daily demand ("the MILP uses
averages", brief M5); cost terms from costs.yaml. Designs: surrogate-FM set (m1_fm_designs).

A design that cannot reach every PHC from some candidate hub is infeasible. For those (A, B) a
what-if run lets the unreachable PHCs go by road, only to price full coverage (M0 decision).

Writes (results/)
  m4_cost_vs_design.csv     annual cost and breakdown per design (what-if rows flagged)
  m4_hubs.csv               open hubs: PHCs, drones, bays, operators, utilisation (per design)
  m4_assignments.csv        PHC -> hub (or road in a what-if) per design, with flight distance
  m4_bruteforce.csv         verification: brute force vs MILP on 4 hubs x 8 PHCs, all designs
  m4_lp_gap.csv             verification: LP-relaxation gap and big-M values
  m4_bigm_check.csv         verification: optimum unchanged with big-M doubled
  m4_hand_check.csv         verification: coefficients by hand
Figures (figures/)
  m4_networks.png, m4_cost_vs_design.png
Returns ctx["m4_network"]: {design: {"instance", "solution", "hubs", "assignments", "feasible"}}
and ctx["m4_cost"] (the cost table).
"""
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from common import DATA, FIGURES, RESULTS, load_costs, load_csv
from .model import Settings, assignment_table, build_instance, hub_table, solve
from . import verify


def _designs(ctx):
    d = ctx.get("m1_fm_designs")
    return d.reset_index(drop=True) if d is not None else pd.read_csv(RESULTS / "m1_fm_designs.csv")


def _dist(ctx):
    return pd.read_csv(RESULTS / "m1_flight_distances.csv", index_col=0)


def run(ctx):
    designs, dist = _designs(ctx), _dist(ctx)
    settings = Settings()
    print(f"  reach = still-air M1 radius (15 % reserve); utilisation cap {settings.util_max:.0%} "
          f"on mean daily demand; every PHC served by a hub")

    net, cost_rows, hubs_all, assign_all, insts = {}, [], [], [], {}
    for _, d in designs.iterrows():
        name = d.design
        inst = build_instance(d.to_dict(), dist, settings)
        insts[name] = inst
        s = solve(inst)
        feasible = np.isfinite(s["cost"])
        shown, sinst = s, inst
        if not feasible:     # what-if: unreachable PHCs by road, to price full coverage
            sinst = build_instance(d.to_dict(), dist, Settings(road_whatif=True))
            shown = solve(sinst)
        hubs, assign = hub_table(sinst, shown), assignment_table(sinst, shown)
        unreach = [p for p, ok in zip(inst.phcs, inst.reach.any(axis=0)) if not ok]
        row = dict(design=name, feasible=feasible, m_kg=d.m, R_km=d.R_km,
                   PHCs_reachable=int(inst.reach.any(axis=0).sum()),
                   unreachable=" ".join(unreach) or "-",
                   cost_INR=shown["cost"], cost_crore=shown["cost"] / 1e7,
                   hubs_open=" ".join(hubs.hub), n_hubs=len(hubs), drones=int(shown["n"].sum()),
                   bays=int(shown["b"].sum()), operators_per_shift=int(shown["o"].sum()),
                   max_utilisation=float(hubs.utilisation.max()),
                   mean_headwind_ms=inst.design["w_mean"], wind_time_factor=inst.design["k_wind"],
                   longest_delivery_min=inst.design["max_delivery_min"],
                   **{f"{k}_INR": v for k, v in shown["breakdown"].items()})
        cost_rows.append(row)
        hubs_all.append(hubs.assign(design=name, feasible=feasible))
        assign_all.append(assign.assign(design=name, feasible=feasible))
        net[name] = dict(instance=sinst, solution=shown, hubs=hubs, assignments=assign, feasible=feasible)
        tag = "" if feasible else f"  INFEASIBLE ({row['unreachable']} out of reach); what-if with road"
        print(f"  {name:<14} R {d.R_km:5.1f} km  ₹{shown['cost']/1e7:.3f} cr  hubs {' '.join(hubs.hub)}  "
              f"drones {int(shown['n'].sum())}  bays {int(shown['b'].sum())}{tag}")

    cost = pd.DataFrame(cost_rows)
    cost.to_csv(RESULTS / "m4_cost_vs_design.csv", index=False, float_format="%.4f")
    pd.concat(hubs_all).to_csv(RESULTS / "m4_hubs.csv", index=False)
    pd.concat(assign_all).to_csv(RESULTS / "m4_assignments.csv", index=False)

    # ---- verification
    bf = verify.brute_force_table(designs, dist)
    bf.to_csv(RESULTS / "m4_bruteforce.csv", index=False, float_format="%.2f")
    print(f"  brute force vs MILP (4 hubs x 8 PHCs, {len(bf)} cases): max rel diff "
          f"{bf.rel_diff.max():.1e}, same assignment in {bf.same_assignment.sum()}/{len(bf)}")
    assert bf.rel_diff.max() < 1e-9, "M4 brute force disagrees with the MILP"
    gap = verify.lp_gap_table(insts)
    gap.to_csv(RESULTS / "m4_lp_gap.csv", index=False, float_format="%.4f")
    print("  LP-relaxation gap: " + ", ".join(f"{r.design[0]} {r.gap:.1%}" for r in gap.itertuples()
                                               if np.isfinite(r.gap)))
    bm = pd.DataFrame([dict(design=n, cost=c1, cost_bigM_doubled=c2)
                       for n, inst in insts.items() if net[n]["feasible"]
                       for c1, c2 in [verify.big_m_check(inst)]])
    bm.to_csv(RESULTS / "m4_bigm_check.csv", index=False, float_format="%.2f")
    hc = verify.hand_check(insts[designs.design.iloc[2]])
    hc.to_csv(RESULTS / "m4_hand_check.csv", index=False, float_format="%.4f")

    fig_networks(net, FIGURES / "m4_networks.png")
    fig_cost(cost, FIGURES / "m4_cost_vs_design.png")
    return {"m4_network": net, "m4_cost": cost, "m4_settings": settings}


# ---------------------------------------------------------------- figures
def fig_networks(net, path):
    sites = load_csv("sites.csv").set_index("site_id")
    zones = [np.array(f["geometry"]["coordinates"][0])
             for f in json.load(open(DATA / "nofly.geojson"))["features"]]
    fig, axes = plt.subplots(1, len(net), figsize=(3.6 * len(net), 3.9), sharey=True)
    colours = plt.cm.tab10.colors
    for ax, (name, v) in zip(axes, net.items()):
        for z in zones:
            ax.fill(z[:, 0], z[:, 1], color="0.85", lw=0)
        cand = sites[sites.candidate_hub == 1]
        ax.plot(cand.x_km, cand.y_km, "s", mfc="none", mec="0.6", ms=6)
        hubs = list(v["hubs"].hub)
        for _, a in v["assignments"].iterrows():
            p = sites.loc[a.phc]
            if a.hub == "road":
                ax.plot(p.x_km, p.y_km, "x", color="crimson", ms=7, mew=2)
                continue
            h = sites.loc[a.hub]
            col = colours[hubs.index(a.hub) % 10]
            ax.plot([h.x_km, p.x_km], [h.y_km, p.y_km], color=col, lw=1)
            ax.plot(p.x_km, p.y_km, "o", color=col, ms=4)
        for k, hr in v["hubs"].iterrows():
            h = sites.loc[hr.hub]
            ax.plot(h.x_km, h.y_km, "s", color=colours[k % 10], ms=9, mec="k")
            ax.annotate(f"{hr.hub}\n{hr.drones}d/{hr.bays}b", (h.x_km, h.y_km), fontsize=6.5,
                        xytext=(4, 4), textcoords="offset points")
        sol = v["solution"]
        status = "" if v["feasible"] else "\nINFEASIBLE; what-if with road"
        ax.set_title(f"{name}: ₹{sol['cost']/1e7:.2f} cr/yr{status}", fontsize=9)
        ax.set_aspect("equal")
        ax.set_xlim(0, 62); ax.set_ylim(0, 46)
        ax.set_xlabel("x (km)")
    axes[0].set_ylabel("y (km)")
    axes[0].plot([], [], "x", color="crimson", label="road (out of reach)")
    axes[0].plot([], [], "s", mfc="none", mec="0.6", label="candidate hub")
    fig.legend(*axes[0].get_legend_handles_labels(), fontsize=7, loc="lower center", ncol=2)
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_cost(cost, path):
    parts = [c for c in cost.columns if c.endswith("_INR") and c != "cost_INR"]
    fig, ax = plt.subplots(figsize=(9, 4))
    bottom = np.zeros(len(cost))
    labels = [d.split()[0] for d in cost.design]
    for k, c in enumerate(parts):
        vals = cost[c].values / 1e7
        ax.bar(labels, vals, bottom=bottom, label=c[:-4], color=plt.cm.tab10.colors[k])
        bottom += vals
    for k, r in cost.iterrows():
        ax.text(k, bottom[k] + 0.05, f"{r.cost_crore:.2f}" + ("" if r.feasible else "\nwhat-if"),
                ha="center", fontsize=8)
    road_only = load_csv("demand.csv")
    c = load_costs()["road_fallback"]["cost_per_trip"]
    ro = 365 * (road_only.emergency_per_day.sum() * c["emergency"]
                + (road_only.routine_per_day.sum() + road_only.lab_per_day.sum()) * c["routine"]) / 1e7
    ax.axhline(ro, color="k", ls="--", lw=1)
    ax.text(-0.4, ro, f"all-road ₹{ro:.2f} cr (M0 cost cap)", ha="left", va="bottom", fontsize=8)
    ax.set_ylabel("annual cost (₹ crore)")
    ax.set_xlabel("M1 design (surrogate FM)")
    ax.set_ylim(0, max(bottom.max(), ro) * 1.15)
    ax.legend(fontsize=7, loc="upper left", bbox_to_anchor=(1.01, 1.0))
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
