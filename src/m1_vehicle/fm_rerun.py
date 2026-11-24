"""M3's last step: re-run M1 with the surrogate FM(D, T) in place of FM0 = 0.65.

Same five required radii as the FM0 run (so the comparison is like for like), same constraints,
plus FM-domain constraints: the loaded and the empty hover point (D, T) must stay inside the convex
hull of the oracle samples, so the optimiser cannot exploit RBF extrapolation (e.g. near the
high-disk-loading cliff at small D and high T, or just outside the sampled edge at D = 0.80 m).

Writes
  results/m1_fm_designs_continuous.csv   continuous optimum with the surrogate FM
  results/m1_fm_designs.csv              snapped to catalogue parts, every constraint re-checked
  results/m1_fm_comparison.csv           how far each optimum moved vs FM0
  results/m1_fm_hover_points.csv         loaded / empty hover (D, T) of every design: request-3 targets
  figures/m1_fm_rerun.png                FM map, sampled region, FM0 vs surrogate hover points
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from common import FIGURES, RESULTS
from .catalogue import snap
from .mdo import Problem, design_table_row, solve_mdf
from .model import Params, VARS
from .target_radius import hub_phc_distances

KEEP = ["m", "D", "E", "d", "t", "V", "FM_loaded", "FM_empty", "T_hover_loaded", "T_hover_empty",
        "P_hover_loaded", "P_cruise_loaded", "R_km"]


def _targets(ctx):
    if "m1_designs" in ctx:
        d = ctx["m1_designs"]
        return dict(zip(d.design, d.R_req_km)), ctx.get("m1_design_wind", 0.0)
    from .run import required_radii, SIZING_WIND
    t = required_radii(hub_phc_distances(), SIZING_WIND, Params())
    return {k: v[0] for k, v in t.items()}, SIZING_WIND


def _fm0_results(targets, wind):
    """FM0 = 0.65 optimum and snap for the same radii (read from M1's files when present)."""
    try:
        c = pd.read_csv(RESULTS / "m1_designs_continuous.csv").set_index("design")
        s = pd.read_csv(RESULTS / "m1_designs.csv").set_index("design")
        if set(targets) <= set(c.index):
            return c, s
    except FileNotFoundError:
        pass
    rows_c, rows_s = [], []
    for name, R in targets.items():
        prob = Problem(R, wind, Params())
        b, _ = solve_mdf(prob)
        rows_c.append(dict(design=name, **design_table_row(prob, prob.to_x(b.x))))
        rows_s.append(dict(design=name, **snap(prob)))
    return pd.DataFrame(rows_c).set_index("design"), pd.DataFrame(rows_s).set_index("design")


def rerun(ctx, fm, domain, gp=None, m_plate=None):
    """fm: callable FM(D, T); domain: FMDomain; gp: optional GP for the uncertainty at each point."""
    targets, wind = _targets(ctx)
    p = Params(m_plate=ctx.get("m2_plate_mass", 0.25) if m_plate is None else m_plate,
               fm=fm, complex_safe=False)
    fm0_c, fm0_s = _fm0_results(targets, wind)

    cont, snapped, comp = [], [], []
    for name, R in targets.items():
        prob = Problem(R, wind, p, fm_domain=domain)
        try:
            best, runs = solve_mdf(prob, n_starts=10)
        except RuntimeError as e:
            print(f"  {name}: {e}")
            continue
        row = dict(design=name, R_req_km=R, starts_ok=sum(r["success"] for r in runs),
                   **design_table_row(prob, prob.to_x(best.x)))
        g = prob.mdf_g(best.x)
        row["active"] = "; ".join(n for n, gi in zip(prob.con_names, g) if gi < 1e-6)
        row["bounds_active"] = "; ".join(f"{v} {'lower' if zi < 1e-6 else 'upper'}"
                                         for v, zi in zip(VARS, best.x) if zi < 1e-6 or zi > 1 - 1e-6)
        cont.append(row)
        s = snap(prob)
        if s is None:
            print(f"  {name}: no catalogue build meets every constraint with the surrogate FM")
            continue
        snapped.append(dict(design=name, R_req_km=R, m_continuous=row["m"], **s))
        f0 = fm0_c.loc[name]
        x0 = np.array([f0[v] for v in VARS], float)
        R_fm0_design = float(np.real(design_table_row(prob, x0)["R_km"]))   # FM0 drone, real FM
        m_gp = np.nan
        if gp is not None:   # surrogate-choice check: same problem with the GP mean as FM
            from m3_surrogate import FMFunction
            pg = p.with_(fm=FMFunction(gp))
            m_gp = 10 * solve_mdf(Problem(R, wind, pg, fm_domain=domain), n_starts=4)[0].fun
        comp.append(dict(design=name, R_req_km=R,
                         m_FM0=f0.m, m_surr=row["m"], dm_pct=100 * (row["m"] / f0.m - 1),
                         D_FM0=f0.D, D_surr=row["D"], E_FM0=f0.E, E_surr=row["E"],
                         V_FM0=f0.V, V_surr=row["V"],
                         T_loaded_FM0=f0.T_hover_loaded, T_loaded_surr=row["T_hover_loaded"],
                         T_empty_FM0=f0.T_hover_empty, T_empty_surr=row["T_hover_empty"],
                         FM_loaded_surr=row["FM_loaded"], FM_empty_surr=row["FM_empty"],
                         R_of_FM0_design_with_surr_FM=R_fm0_design,
                         m_with_GP_FM=m_gp, m_GP_vs_RBF_pct=100 * (m_gp / row["m"] - 1),
                         m_snapped_FM0=fm0_s.loc[name, "m"], m_snapped_surr=s["m"],
                         parts_FM0=f"{fm0_s.loc[name, 'battery']}, {fm0_s.loc[name, 'motor']}, "
                                   f"{fm0_s.loc[name, 'tube']}, {int(fm0_s.loc[name, 'prop_in'])} in",
                         parts_surr=f"{s['battery']}, {s['motor']}, {s['tube']}, {s['prop_in']} in",
                         active_surr=row["active"], bounds_active_surr=row["bounds_active"]))
        print(f"  {name:<14} R {R:4.1f} km  m {f0.m:6.3f} -> {row['m']:6.3f} kg ({comp[-1]['dm_pct']:+.1f} %)  "
              f"D {f0.D:.3f} -> {row['D']:.3f}  FM {row['FM_loaded']:.3f}/{row['FM_empty']:.3f}  "
              f"snapped {s['m']:.3f} kg ({comp[-1]['parts_surr']})")

    cont, snapped, comp = pd.DataFrame(cont), pd.DataFrame(snapped), pd.DataFrame(comp)
    cont.to_csv(RESULTS / "m1_fm_designs_continuous.csv", index=False, float_format="%.6g")
    snapped.to_csv(RESULTS / "m1_fm_designs.csv", index=False, float_format="%.6g")
    comp.to_csv(RESULTS / "m1_fm_comparison.csv", index=False, float_format="%.5g")

    # hover points (request-3 exploitation targets) with domain slack and GP sigma
    pts = []
    for kind, df in (("continuous", cont), ("snapped", snapped)):
        for _, r in df.iterrows():
            for state in ("loaded", "empty"):
                D, T = float(r["D"]), float(r[f"T_hover_{state}"])
                pt = dict(design=r.design, version=kind, state=state, D_m=D, T_N=T,
                          DL=T / (np.pi * D**2 / 4), FM_surr=float(fm(D, T)),
                          in_8_40N=8 <= T <= 40, inside_sampled_hull=bool(domain.inside(D, T, 1e-7)),
                          hull_slack=float(domain(D, T).min()))
                if gp is not None:
                    from m3_surrogate import make_features
                    mu, sd = gp.predict(make_features(D, T), return_std=True)
                    pt.update(FM_gp=float(mu[0]), gp_sigma=float(sd[0]))
                pts.append(pt)
    pts = pd.DataFrame(pts)
    pts.to_csv(RESULTS / "m1_fm_hover_points.csv", index=False, float_format="%.5g")
    figure(fm, domain, fm0_c, cont, snapped, FIGURES / "m1_fm_rerun.png")
    return {"m1_fm_designs": snapped, "m1_fm_comparison": comp, "m1_fm_hover_points": pts}


def figure(fm, domain, fm0_c, cont, snapped, path):
    D, T = np.meshgrid(np.linspace(0.35, 0.80, 120), np.linspace(8, 40, 120))
    F = fm(D, T)
    fig, ax = plt.subplots(figsize=(7.2, 6.4))
    cs = ax.contourf(D, T, F, levels=np.arange(0.15, 0.76, 0.025), cmap="viridis")
    fig.colorbar(cs, ax=ax, label="FM (cubic RBF surrogate)")
    ax.plot(domain.points[:, 0], domain.points[:, 1], "x", color="r", ms=5, label="oracle points")
    from scipy.spatial import ConvexHull
    order = ConvexHull(domain.vertices).vertices
    hv = domain.vertices[np.r_[order, order[:1]]]
    ax.plot(hv[:, 0], hv[:, 1], "w--", lw=1, label="sampled region (hull of oracle points)")
    for df, mk, lab in ((fm0_c.reset_index(), "o", "FM0 = 0.65 optimum"),
                        (cont, "s", "surrogate-FM optimum"), (snapped, "^", "surrogate, snapped")):
        ax.scatter(df.D, df.T_hover_loaded, marker=mk, facecolor="w", edgecolor="k", s=36, zorder=3,
                   label=f"{lab}: loaded")
        ax.scatter(df.D, df.T_hover_empty, marker=mk, facecolor="k", edgecolor="w", s=36, zorder=3,
                   label=f"{lab}: empty")
    for _, r in cont.iterrows():
        ax.annotate(r.design.split()[0], (r.D, r.T_hover_loaded), xytext=(4, 3),
                    textcoords="offset points", color="w", fontsize=8)
    ax.set_xlabel("rotor diameter D (m)")
    ax.set_ylabel("hover thrust per rotor T (N)")
    ax.set_title("M1 re-run with surrogate FM: where the designs hover", fontsize=10)
    ax.legend(fontsize=7, loc="upper center", bbox_to_anchor=(0.5, -0.11), ncol=2, frameon=False)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
