"""M6 pipeline step for run_all.py: simulation-based multi-objective optimisation.

Brief, M6: tune the operating system on top of the M4 network. Decisions: vehicle design (M1 set),
drones and bays per open hub, spare batteries per hub, emergency reserve fraction. Objectives:
annual cost (min), S45 (max), road-fallback share (min); report P95 too. NSGA-II with common
random numbers (user-confirmed choice over a 50-run surrogate). Pareto front, knee, weighted sum.

Steps
  1. Engine check: NSGA-II on ZDT1.
  2. One NSGA-II per design (C, D, E; A and B are infeasible in M4) on M5's 73 (wind day, demand
     seed) pairs, the same for every candidate (CRN). Fronts merged into one non-dominated set.
  3. Knee on the cost-S45 view of the merged front.
  4. Two-objective slice (design D, cost vs S45): NSGA-II front vs weighted-sum GA for 9 weights.
  5. Re-simulate front points, the knee and the M4/M5 baselines on all 365 wind days with fresh
     demand seeds (out-of-sample check; final numbers are quoted on 365 days).

Writes (results/)
  m6_zdt1_check.csv, m6_archive.csv (every configuration simulated on the search days),
  m6_front.csv (merged 3-objective front), m6_front_2d.csv (cost-S45 view, with the knee),
  m6_ws_slice.csv, m6_slice_front.csv, m6_resim_365.csv, m6_cost_hand_check.csv,
  m6_design_summary.csv, m6_convergence.csv
Figures (figures/)
  m6_pareto.png, m6_ws_slice.png, m6_resim.png
Returns ctx["m6"] = {"front", "front_2d", "knee", "resim", "problem": Problem, "evaluator"}.
"""
import os
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from common import FIGURES, RESULTS
from . import verify
from .nsga2 import nsga2, pareto_mask
from .problem import COST_CAP, DESIGNS, SHORT, Evaluator, Problem, crn_days, objectives

COL = {"C cover-all": "#2a78d6", "D 4-hub": "#eb6834", "E 3-hub": "#1baf7a"}
MK = {"C cover-all": "s", "D 4-hub": "o", "E 3-hub": "^"}
INK, MUTED = "#0b0b0b", "#8a8984"
TARGET = 0.90                         # M0 target S45 (reference line, not a constraint)
WEIGHTS = np.round(np.linspace(0.1, 0.9, 9), 2)


def budget(ctx):
    fast = bool(ctx.get("fast")) or os.environ.get("SKYLINK_FAST") == "1"
    if fast:
        return dict(pop=16, gens=6, slice_pop=16, slice_gens=6, ws_pop=10, ws_gens=5, resim_max=8,
                    fast=True)
    return dict(pop=32, gens=40, slice_pop=40, slice_gens=60, ws_pop=16, ws_gens=12, resim_max=24,
                fast=False)


def run(ctx):
    B = budget(ctx)
    t0 = time.time()
    print(f"  budget: population {B['pop']} x {B['gens']} generations per design"
          f"{' (FAST mode)' if B['fast'] else ''}")

    # ---- 1. engine check
    z = verify.zdt1_check()
    z.to_csv(RESULTS / "m6_zdt1_check.csv", index=False, float_format="%.5f")
    print(f"  NSGA-II on ZDT1: mean gap to true front {z.mean_gap_to_true_front[0]:.4f} "
          f"({'pass' if z.passed[0] else 'FAIL'})")

    # ---- 2. NSGA-II per design on common random numbers
    days, seeds = crn_days(ctx)
    ev = Evaluator(days, seeds, ctx, workers=os.cpu_count() or 1)
    probs = {d: Problem(d, ctx) for d in DESIGNS}
    conv = []
    try:
        for d, p in probs.items():
            def evaluate(X, p=p):
                cfgs = [p.config(x) for x in X]
                outs = ev.run(cfgs)
                F, cv = zip(*(objectives(o) for o in outs))
                return np.array(F), np.array(cv)

            init = [p.genome(), p.genome(s=p.n0)]          # M4 sizing; + one spare per drone
            cb = lambda g, X, F, cv, d=d: conv.append(dict(design=d, gen=g, sims=ev.n_sim,
                                                           best_cost=F[:, 0].min(),
                                                           best_S45=-F[:, 1].min(),
                                                           best_road=F[:, 2].min()))
            nsga2(evaluate, p.lo, p.hi, p.is_int, pop=B["pop"], gens=B["gens"], seed=6,
                  repair=p.repair, key=lambda x, p=p: p.config(x), init=init, callback=cb)
            print(f"  {d}: {ev.n_sim} configurations simulated so far ({time.time() - t0:.0f} s)")
        pd.DataFrame(conv).to_csv(RESULTS / "m6_convergence.csv", index=False, float_format="%.5f")

        # archive and merged front
        arch = archive_frame(ev.records())
        arch.to_csv(RESULTS / "m6_archive.csv", index=False, float_format="%.5f")
        feas = arch[arch.cost_cr <= COST_CAP / 1e7].reset_index(drop=True)
        F = np.c_[feas.cost_cr, -feas.S45, feas.road_share]
        front = feas[pareto_mask(F)].sort_values("cost_cr").reset_index(drop=True)
        front.to_csv(RESULTS / "m6_front.csv", index=False, float_format="%.5f")
        f2 = front.iloc[verify.front_2d(front.cost_cr.values, front.S45.values)].reset_index(drop=True)
        k = knee_index(f2.cost_cr.values, f2.S45.values)
        f2["knee"] = False
        f2.loc[k, "knee"] = True
        f2.to_csv(RESULTS / "m6_front_2d.csv", index=False, float_format="%.5f")
        knee = f2.iloc[k]
        print(f"  merged front: {len(front)} points (3 objectives), {len(f2)} on the cost-S45 view; "
              f"knee {knee.config}: {knee.cost_cr:.2f} cr/yr, S45 {knee.S45:.3f}, road {knee.road_share:.3f}")
        summ = design_summary(arch, front)
        summ.to_csv(RESULTS / "m6_design_summary.csv", index=False, float_format="%.5f")

        # ---- 4. two-objective slice: design D, cost vs S45
        pD = probs["D 4-hub"]

        def ev2(X, p=pD):
            outs = ev.run([p.config(x) for x in X])
            F = np.array([[o["cost"] / 1e7, -o["S45"]] for o in outs])
            return F, np.array([max(0, o["cost"] - COST_CAP) / 1e7 for o in outs])

        nsga2(ev2, pD.lo, pD.hi, pD.is_int, pop=B["slice_pop"], gens=B["slice_gens"], seed=12,
                     repair=pD.repair, key=lambda x: pD.config(x))
        # NSGA-II front on the slice = non-dominated (cost, S45) points among every design-D
        # configuration the two NSGA-II runs (3-objective and 2-objective) simulated, frozen
        # before the weighted-sum runs start
        dD = [(o["cost"] / 1e7, o["S45"]) for c, o in ev.records()
              if c.design == "D 4-hub" and o["cost"] <= COST_CAP]
        dD = np.array(dD)
        sf = dD[verify.front_2d(dD[:, 0], dD[:, 1])]
        sf = np.unique(sf, axis=0)
        pd.DataFrame(sf, columns=["cost_cr", "S45"]).to_csv(RESULTS / "m6_slice_front.csv", index=False,
                                                            float_format="%.5f")
        cmin, cmax, smin, smax = sf[:, 0].min(), sf[:, 0].max(), sf[:, 1].min(), sf[:, 1].max()
        scale = (max(cmax - cmin, 1e-9), max(smax - smin, 1e-9))
        ws_pts = []
        for i, w in enumerate(WEIGHTS):
            def evw(X, p=pD, w=w):
                outs = ev.run([p.config(x) for x in X])
                f = [w * (o["cost"] / 1e7 - cmin) / scale[0] + (1 - w) * (smax - o["S45"]) / scale[1]
                     for o in outs]
                return np.array(f)[:, None], np.array([max(0, o["cost"] - COST_CAP) / 1e7 for o in outs])

            r = nsga2(evw, pD.lo, pD.hi, pD.is_int, pop=B["ws_pop"], gens=B["ws_gens"], seed=100 + i,
                      repair=pD.repair, key=lambda x: pD.config(x))
            best = r["X"][np.argmin(r["F"][:, 0])]
            o = ev.run([pD.config(best)])[0]
            ws_pts.append((float(w), o["cost"] / 1e7, o["S45"], pD.config(best).label))
        ws = verify.ws_table(ws_pts, sf, scale)
        ws.to_csv(RESULTS / "m6_ws_slice.csv", index=False, float_format="%.5f")
        print(f"  weighted sum (design D, cost-S45): {int(ws.on_or_behind.sum())}/{len(ws)} points on or "
              f"behind the NSGA-II front (tolerance 0.01 normalised); largest lead {ws.S45_ahead_pp.max():.2f} pp of S45")
        fig_ws(sf, ws, FIGURES / "m6_ws_slice.png")

        # ---- 4b. weighted-sum result on the full problem: equal weights on the three objectives,
        #          each scaled to [0, 1] over the merged front; one GA per design, best kept
        lo3 = front[["cost_cr", "S45", "road_share"]].min().values
        hi3 = front[["cost_cr", "S45", "road_share"]].max().values
        sc3 = np.maximum(hi3 - lo3, 1e-9)
        ws3_rows = []
        for i, (d, p) in enumerate(probs.items()):
            def ev3(X, p=p):
                outs = ev.run([p.config(x) for x in X])
                f = [((o["cost"] / 1e7 - lo3[0]) / sc3[0] + (hi3[1] - o["S45"]) / sc3[1]
                      + (o["road_share"] - lo3[2]) / sc3[2]) / 3 for o in outs]
                return np.array(f)[:, None], np.array([max(0, o["cost"] - COST_CAP) / 1e7 for o in outs])

            r = nsga2(ev3, p.lo, p.hi, p.is_int, pop=B["ws_pop"], gens=B["ws_gens"], seed=200 + i,
                      repair=p.repair, key=lambda x, p=p: p.config(x))
            c = p.config(r["X"][np.argmin(r["F"][:, 0])])
            o = ev.run([c])[0]
            dom = ((front.cost_cr <= o["cost"] / 1e7) & (front.S45 >= o["S45"])
                   & (front.road_share <= o["road_share"])).any()
            ws3_rows.append(dict(design=d, config=c.label, cost_cr=o["cost"] / 1e7, S45=o["S45"],
                                 road_share=o["road_share"], P95=o["P95"], ws_value=float(r["F"][:, 0].min()),
                                 on_or_behind_front=bool(dom)))
        ws3 = pd.DataFrame(ws3_rows).sort_values("ws_value").reset_index(drop=True)
        ws3["chosen"] = ws3.index == 0
        ws3.to_csv(RESULTS / "m6_ws_3obj.csv", index=False, float_format="%.5f")
        w3 = ws3.iloc[0]
        print(f"  weighted sum, equal weights, all designs: {w3.config}: {w3.cost_cr:.2f} cr/yr, "
              f"S45 {w3.S45:.3f}, road {w3.road_share:.3f} "
              f"({'on or behind' if w3.on_or_behind_front else 'AHEAD of'} the NSGA-II front)")
        fig_convergence(pd.DataFrame(conv), FIGURES / "m6_convergence.png")

        # ---- 5. re-simulation on 365 days, fresh seeds
        base = [(probs[d].config(probs[d].genome()), f"{SHORT[d]} as M4") for d in DESIGNS]
        pDs = probs["D 4-hub"]
        base.append((pDs.config(pDs.genome(s=pDs.n0)), "D + 1 spare per drone (M5)"))
        pick = f2 if len(f2) <= B["resim_max"] else f2.iloc[np.unique(np.r_[
            np.round(np.linspace(0, len(f2) - 1, B["resim_max"] - 1)).astype(int), k])]
        cfg_by_label = {c.label: (c, o) for c, o in ev.records()}
        recs = [cfg_by_label[l] for l in pick.config]
        roles = ["knee" if kk else "front" for kk in pick.knee]
        base.append((cfg_by_label_all(ev)[w3.config], "weighted sum (equal weights)"))
        for c, role in base:
            recs.append((c, ev.run([c])[0]))
            roles.append(role)
        d365 = list(range(1, 366))
        s365 = [500000 + d for d in d365]
        rs, rho = verify.resim_table(ev, recs, d365, s365)
        rs.insert(0, "role", roles)
        rs.to_csv(RESULTS / "m6_resim_365.csv", index=False, float_format="%.5f")
        print(f"  365-day re-simulation: {int(rs.within_73_ci.sum())}/{len(rs)} within the CI; "
              f"mean change in S45 {rs.dS45.mean():+.3f}; Spearman rank correlation {rho:.3f}")
        fig_resim(rs, rho, FIGURES / "m6_resim.png")

        # ---- cost hand check (fixed terms of D as M4 = M4's breakdown)
        m4c = ctx.get("m4_cost")
        m4c = pd.read_csv(RESULTS / "m4_cost_vs_design.csv") if m4c is None else m4c
        row = m4c[m4c.design == "D 4-hub"].iloc[0]
        hc = verify.cost_hand_check(ev.run([base[1][0]])[0]["cost_terms"], row)
        hc.to_csv(RESULTS / "m6_cost_hand_check.csv", index=False, float_format="%.6f")
        print(f"  cost hand check vs M4 (hubs, drones, bays, operators): "
              f"{'pass' if hc.passed.all() else 'FAIL'}")

        fig_pareto(arch, front, f2, knee, ws3, rs, FIGURES / "m6_pareto.png")
        print(f"  {ev.n_sim} simulator runs in total ({time.time() - t0:.0f} s)")
        return {"m6": dict(front=front, front_2d=f2, knee=knee, resim=rs, archive=arch, ws_3obj=ws3,
                           problem=Problem, days=days, seeds=seeds)}
    finally:
        ev.close()


# ---------------------------------------------------------------- helpers
def cfg_by_label_all(ev):
    return {c.label: c for c, _ in ev.records()}


def archive_frame(recs):
    rows = []
    for c, o in recs:
        rows.append(dict(config=c.label, design=c.design, drones=sum(c.drones), bays=sum(c.bays),
                         spares=sum(c.spares), reserve=sum(c.reserve),
                         drones_h=" ".join(map(str, c.drones)), bays_h=" ".join(map(str, c.bays)),
                         spares_h=" ".join(map(str, c.spares)), reserve_h=" ".join(map(str, c.reserve)),
                         cost_cr=o["cost"] / 1e7, S45=o["S45"], S45_sd=o["S45_sd"],
                         road_share=o["road_share"], P95=o["P95"], em_road_share=o["em_road_share"],
                         utilisation=o["utilisation"],
                         **{f"cost_{k}": v / 1e7 for k, v in o["cost_terms"].items()}))
    return pd.DataFrame(rows)


def knee_index(cost, s45):
    """Front point farthest from the chord joining the cheapest and the highest-S45 point, with
    both objectives scaled to [0, 1] over the front."""
    c = (cost - cost.min()) / max(np.ptp(cost), 1e-12)
    s = (s45 - s45.min()) / max(np.ptp(s45), 1e-12)
    a, b = np.array([c[0], s[0]]), np.array([c[-1], s[-1]])
    u = (b - a) / max(np.linalg.norm(b - a), 1e-12)
    d = np.abs((c - a[0]) * u[1] - (s - a[1]) * u[0])
    return int(np.argmax(d))


def design_summary(arch, front):
    rows = []
    for d in DESIGNS:
        a, f = arch[arch.design == d], front[front.design == d]
        rows.append(dict(design=d, simulated=len(a), on_merged_front=len(f),
                         max_S45=a.S45.max(), cost_at_max_S45=a.loc[a.S45.idxmax(), "cost_cr"],
                         min_cost=a.cost_cr.min(), min_road_share=a.road_share.min(),
                         reaches_090=bool((a.S45 >= TARGET).any())))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- figures
def _style(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.grid(alpha=0.25, lw=0.6)
    ax.tick_params(labelsize=8)


def fig_pareto(arch, front, f2, knee, ws, rs, path):
    fig, axs = plt.subplots(1, 3, figsize=(15, 4.8))
    ax = axs[0]
    for d in DESIGNS:
        a, f = arch[arch.design == d], front[front.design == d]
        ax.scatter(a.cost_cr, a.S45, s=6, color=COL[d], alpha=0.12, lw=0)
        ax.scatter(f.cost_cr, f.S45, s=34, marker=MK[d], color=COL[d], edgecolor="white", lw=0.8,
                   label=f"design {SHORT[d]} on the front ({len(f)})", zorder=3)
    ax.plot(f2.cost_cr, f2.S45, color=INK, lw=1, alpha=0.6, zorder=2, label="cost-S45 front")
    ax.scatter([knee.cost_cr], [knee.S45], s=260, marker="*", color="#e34948", edgecolor=INK, lw=0.8,
               zorder=5, label=f"knee: {knee.config}")
    w3 = ws[ws.chosen].iloc[0]
    ax.scatter([w3.cost_cr], [w3.S45], s=120, marker="D", facecolor="none", edgecolor="#6250d6", lw=1.8,
               zorder=5, label=f"weighted sum, equal weights: {w3.config}")
    b = rs[rs.role.str.contains("as M4|M5")]
    ax.scatter(b.cost_cr_73, b.S45_73, s=50, marker="x", color=INK, zorder=4, label="M4 sizing / M5 what-if")
    for _, r in b.iterrows():
        ax.annotate(r.role, (r.cost_cr_73, r.S45_73), fontsize=7, xytext=(4, -9), textcoords="offset points")
    ax.axhline(TARGET, color=MUTED, ls="--", lw=1)
    ax.text(front.cost_cr.max(), TARGET + 0.002, "M0 target S45 = 0.90",
            fontsize=7, color=MUTED, ha="right")
    ax.set_xlabel("annual cost (INR crore/yr)")
    ax.set_ylabel("S45 (mean over 73 CRN days)")
    ax.set_title("(a) cost vs S45: merged front, knee", fontsize=10)
    ax.legend(fontsize=6.5, loc="lower left")
    _style(ax)

    ax = axs[1]
    for d in DESIGNS:
        f = front[front.design == d]
        ax.scatter(f.cost_cr, f.road_share, s=30, marker=MK[d], color=COL[d], edgecolor="white", lw=0.8,
                   label=f"design {SHORT[d]}")
    ax.scatter([knee.cost_cr], [knee.road_share], s=260, marker="*", color="#e34948", edgecolor=INK, zorder=5,
               label="knee")
    ax.scatter([w3.cost_cr], [w3.road_share], s=120, marker="D", facecolor="none", edgecolor="#6250d6",
               lw=1.8, zorder=5, label="weighted sum (equal weights)")
    ax.set_xlabel("annual cost (INR crore/yr)")
    ax.set_ylabel("road-fallback share of all orders")
    ax.set_title("(b) cost vs road share (front points)", fontsize=10)
    ax.legend(fontsize=7)
    _style(ax)

    ax = axs[2]
    for d in DESIGNS:
        f = front[front.design == d]
        ax.scatter(f.em_road_share, f.P95, s=30, marker=MK[d], color=COL[d], edgecolor="white", lw=0.8,
                   label=f"design {SHORT[d]}")
    ax.axvline(0.05, color=MUTED, ls="--", lw=1)
    ax.set_xlim(0, max(0.15, front.em_road_share.max() + 0.02))
    ax.set_ylim(0, 160)
    ax.text(0.051, 20, " P95 can fall below 150 min only\n left of this line (< 5 % of\n emergencies by road)",
            fontsize=7, color=MUTED)
    ax.set_xlabel("share of emergencies sent by road")
    ax.set_ylabel("P95 emergency delivery time (min)")
    ax.set_title("(c) why P95 stays at 150 min (30 wait + 120 road)", fontsize=10)
    ax.legend(fontsize=7)
    _style(ax)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_ws(sf, ws, path):
    fig, ax = plt.subplots(figsize=(6.6, 4.4))
    ax.step(sf[:, 0], sf[:, 1], where="post", color=INK, lw=1.2, label="NSGA-II front (2 objectives)")
    ax.scatter(sf[:, 0], sf[:, 1], s=14, color=INK)
    ok = ws[ws.on_or_behind]
    bad = ws[~ws.on_or_behind]
    ax.scatter(ok.cost_cr, ok.S45, s=70, facecolor="none", edgecolor="#2a78d6", lw=1.6,
               label=f"weighted sum, on or behind ({len(ok)})")
    if len(bad):
        ax.scatter(bad.cost_cr, bad.S45, s=70, marker="X", color="#e34948", label=f"weighted sum, ahead ({len(bad)})")
    for _, r in ws.iterrows():
        ax.annotate(f"w={r.w_cost:.1f}", (r.cost_cr, r.S45), fontsize=6.5, xytext=(4, 3),
                    textcoords="offset points", color="#52514e")
    ax.set_xlabel("annual cost (INR crore/yr)")
    ax.set_ylabel("S45 (73 CRN days)")
    ax.set_title("Design D slice: weighted-sum points vs the NSGA-II front", fontsize=10)
    ax.legend(fontsize=7, loc="lower right")
    _style(ax)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_resim(rs, rho, path):
    fig, ax = plt.subplots(figsize=(6.2, 5))
    lo, hi = min(rs.S45_73.min(), rs.S45_365.min()) - 0.01, max(rs.S45_73.max(), rs.S45_365.max()) + 0.01
    ax.plot([lo, hi], [lo, hi], color=MUTED, lw=1, ls="--", label="y = x")
    for role, mk, col in (("front", "o", "#2a78d6"), ("knee", "*", "#e34948")):
        r = rs[rs.role == role]
        ax.errorbar(r.S45_73, r.S45_365, xerr=r.S45_73_ci, yerr=r.S45_365_ci, fmt=mk, ms=8 if role == "knee" else 5,
                    color=col, ecolor=col, elinewidth=0.6, capsize=2, label="knee" if role == "knee" else "front points")
    r = rs[~rs.role.isin(["front", "knee"])]
    ax.errorbar(r.S45_73, r.S45_365, xerr=r.S45_73_ci, yerr=r.S45_365_ci, fmt="x", ms=7, color=INK,
                elinewidth=0.6, capsize=2, label="M4 sizing, M5 what-if, weighted sum")
    ax.axhline(TARGET, color="#e34948", lw=0.8, ls=":")
    ax.text(lo + 0.002, TARGET + 0.002, "M0 target 0.90", fontsize=7, color="#52514e")
    ax.set_xlim(lo, max(hi, TARGET + 0.01))
    ax.set_ylim(lo, max(hi, TARGET + 0.01))
    ax.set_xlabel("S45 on the 73 search days (CRN), 95 % CI")
    ax.set_ylabel("S45 on all 365 days, fresh demand seeds, 95 % CI")
    ax.set_title(f"Front points re-simulated off the search days\n"
                 f"{int(rs.within_73_ci.sum())}/{len(rs)} agree within the CI; Spearman rho {rho:.2f}",
                 fontsize=10)
    ax.legend(fontsize=7, loc="upper left")
    _style(ax)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_convergence(conv, path):
    fig, axs = plt.subplots(1, 3, figsize=(13, 3.6))
    for d in DESIGNS:
        c = conv[conv.design == d]
        for ax, col, lab in zip(axs, ("best_cost", "best_S45", "best_road"),
                                ("lowest cost in population (INR cr/yr)", "highest S45 in population",
                                 "lowest road share in population")):
            ax.plot(c.gen, c[col], color=COL[d], marker=MK[d], ms=3, lw=1.5, label=f"design {SHORT[d]}")
            ax.set_xlabel("generation")
            ax.set_title(lab, fontsize=9)
    for ax in axs:
        _style(ax)
    axs[0].legend(fontsize=7)
    fig.suptitle("NSGA-II convergence per design (73 CRN days): extremes of the population", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
