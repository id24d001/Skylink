"""M4 pipeline step for run_all.py: hub network and fleet MILP, solved once per M1 design.

Choices confirmed by the user (decision log): surrogate-FM designs (m1_fm_designs, post-M2);
design headwind 6.4 m/s (P90 of flyable hours); road allowed for unreachable PHCs if they carry
at most 5 % of emergencies; fleets sized for every hour of the day (peak hour binds).

Writes (results/)
  m4_cost_vs_design.csv     annual cost and breakdown per design (feasible or not under the guard)
  m4_hubs.csv               open hubs: PHCs, drones, bays, operators, peak utilisation (per design)
  m4_assignments.csv        PHC -> hub (or road) per design, with flight distance
  m4_bruteforce.csv         verification: brute force vs MILP on 4 hubs x 8 PHCs, all designs
  m4_lp_gap.csv             verification: LP-relaxation gap and big-M values
  m4_bigm_check.csv         verification: optimum unchanged with big-M doubled
  m4_hand_check.csv         verification: cost coefficients by hand
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
from m1_vehicle.run import design_wind
from .model import Settings, assignment_table, build_instance, hub_table, solve
from . import verify


def _designs(ctx):
    d = ctx.get("m1_fm_designs")
    return d.reset_index(drop=True) if d is not None else pd.read_csv(RESULTS / "m1_fm_designs.csv")


def _dist(ctx):
    p = RESULTS / "m1_flight_distances.csv"
    return pd.read_csv(p, index_col=0)


def run(ctx):
    designs, dist = _designs(ctx), _dist(ctx)
    w = round(design_wind(), 1)
    settings = Settings(wind=w)
    print(f"  design headwind {w} m/s (P90 of flyable hours); emergency road cap "
          f"{settings.emerg_road_max:.0%}; utilisation cap {settings.util_max:.0%}")

    net, cost_rows, hubs_all, assign_all, insts = {}, [], [], [], {}
    for _, d in designs.iterrows():
        name = d.design
        inst = build_instance(d.to_dict(), dist, settings)
        insts[name] = inst
        s = solve(inst)
        feasible = np.isfinite(s["cost"])
        shown = s
        if not feasible:     # show what the design could do without the guard, for the figure
            shown = solve(build_instance(d.to_dict(), dist, Settings(wind=w, emerg_road_max=1.0)))
        hubs, assign = hub_table(inst, shown), assignment_table(inst, shown)
        road_e = float(inst.emerg @ shown["r"]) / inst.emerg.sum()
        row = dict(design=name, feasible_with_guard=feasible, m_kg=d.m, R_km=d.R_km,
                   R_wind_km=inst.design["R_wind_km"], PHCs_reachable=int(inst.reach.any(axis=0).sum()),
                   cost_INR=shown["cost"], cost_crore=shown["cost"] / 1e7,
                   hubs_open=" ".join(hubs.hub), n_hubs=len(hubs), drones=int(shown["n"].sum()),
                   bays=int(shown["b"].sum()), operators_per_shift=int(shown["o"].sum()),
                   road_PHCs=" ".join(assign.loc[assign.hub == "road", "phc"]) or "-",
                   emergency_share_by_road=road_e,
                   **{f"{k}_INR": v for k, v in shown["breakdown"].items()})
        cost_rows.append(row)
        hubs_all.append(hubs.assign(design=name, feasible_with_guard=feasible))
        assign_all.append(assign.assign(design=name, feasible_with_guard=feasible))
        net[name] = dict(instance=inst, solution=shown, hubs=hubs, assignments=assign, feasible=feasible)
        tag = "" if feasible else "  (infeasible with the 5% guard; shown without it)"
        print(f"  {name:<14} R_wind {inst.design['R_wind_km']:5.1f} km  cost ₹{shown['cost']/1e7:.3f} cr  "
              f"hubs {' '.join(hubs.hub)}  drones {int(shown['n'].sum())}  road {row['road_PHCs']}{tag}")

    cost = pd.DataFrame(cost_rows)
    cost.to_csv(RESULTS / "m4_cost_vs_design.csv", index=False, float_format="%.4f")
    pd.concat(hubs_all).to_csv(RESULTS / "m4_hubs.csv", index=False)
    pd.concat(assign_all).to_csv(RESULTS / "m4_assignments.csv", index=False)

    # ---- verification
    bf = verify.brute_force_table(designs, dist)
    bf.to_csv(RESULTS / "m4_bruteforce.csv", index=False, float_format="%.2f")
    print(f"  brute force vs MILP (4 hubs x 8 PHCs, {len(bf)} cases): max rel diff "
          f"{bf.rel_diff.max():.1e}, same assignment in {bf.same_assignment.sum()}/{len(bf)}")
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
        status = "" if v["feasible"] else "\n(fails 5% emergency guard)"
        ax.set_title(f"{name}: ₹{sol['cost']/1e7:.2f} cr/yr{status}", fontsize=9)
        ax.set_aspect("equal")
        ax.set_xlim(0, 62); ax.set_ylim(0, 46)
        ax.set_xlabel("x (km)")
    axes[0].set_ylabel("y (km)")
    axes[0].plot([], [], "x", color="crimson", label="road (out of reach)")
    axes[0].plot([], [], "s", mfc="none", mec="0.6", label="candidate hub")
    axes[0].legend(fontsize=6.5, loc="upper right")
    fig.tight_layout()
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
        ax.text(k, bottom[k] + 0.05, f"{r.cost_crore:.2f}" + ("" if r.feasible_with_guard else "\n✗ guard"),
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
