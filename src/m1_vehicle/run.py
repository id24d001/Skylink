"""M1 pipeline step for run_all.py: vehicle MDO, design set, verification.

Writes (results/)
  m1_flight_distances.csv     hub x PHC flight distance around the no-fly zones (km)
  m1_coverage_radius.csv      smallest radius that lets k hubs reach every PHC
  m1_designs_continuous.csv   continuous min-mass optimum for each required radius
  m1_designs.csv              the same designs snapped to catalogue parts, re-checked (feeds M3/M4/M6)
  m1_mdf_vs_idf.csv           verification: MDF vs IDF agreement
  m1_gradient_check.csv       verification: FD at two step sizes vs complex step
  m1_hand_check.csv           verification: code vs independent hand calculation
  m1_mda_convergence.csv      verification: iterations to 1e-8 from three starting masses
  m1_active_constraints.csv   active constraints and Lagrange multipliers (baseline design)
  m1_payload_sensitivity.csv  payload multiplier as radius lost per extra 100 g, with checks
Figures (figures/)
  m1_mda_convergence.png, m1_gradient_check.png, m1_tradeoff.png
Returns ctx["m1_designs"] (snapped designs), ctx["m1_problem"] and ctx["m1_baseline"].
"""
import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from common import FIGURES, RESULTS, load_csv
from .catalogue import snap, snap_max_radius
from .mdo import CON_NAMES, Problem, design_table_row, solve_mdf
from .model import Params, VARS, analyse, mda
from .sensitivity import kkt_multipliers, payload_sensitivity
from .target_radius import hub_phc_distances, min_cover_radius
from . import verify

BASELINE = "C cover-all"


def ceil1(v):
    return math.ceil(v * 10 - 1e-9) / 10


SIZING_WIND = 0.0   # M1 sizes in still air, as eq. (5) defines R; wind is applied in M4/M5/M7


def design_wind(q=0.90, gust_limit=14.0):
    """P90 of the hourly mean wind in flyable hours (6.4 m/s). Not used for M1 sizing; a
    reference value for M4/M7 when they apply the brief's headwind model (model.radius_in_wind)."""
    w = load_csv("wind_days.csv")
    return float(np.quantile(w.loc[w.gust_ms < gust_limit, "mean_wind_ms"], q))


def required_radii(dist, wind, p):
    """The design set's required radii, read off the network geometry (sites.csv + no-fly zones)."""
    nearest = dist.min(axis=0).sort_values(ascending=False)
    r4, hubs4 = min_cover_radius(dist, 4)
    R_reach = math.floor(snap_max_radius(Problem(1.0, wind, p)) * 10) / 10
    r3, hubs3 = min_cover_radius(dist, 3)
    last = (("E 3-hub", (ceil1(r3), f"every PHC from only 3 hubs ({', '.join(hubs3)})"))
            if ceil1(r3) <= R_reach else
            ("E reach", (R_reach, "longest radius any catalogue build reaches with hover thrust <= 40 N")))
    return {
        "A light": (10.0, "light end: reaches PHCs within 10 km of a hub, more hubs or road for the rest"),
        "B all-but-one": (ceil1(nearest.iloc[1]),
                          f"every PHC but {nearest.index[0]} from its nearest candidate hub"),
        "C cover-all": (ceil1(nearest.iloc[0]),
                        f"every PHC from its nearest candidate hub (bottleneck {nearest.index[0]})"),
        "D 4-hub": (ceil1(r4), f"every PHC from only 4 hubs ({', '.join(hubs4)})"),
        last[0]: last[1],
    }


def run(ctx):
    p = Params(m_plate=ctx.get("m2_plate_mass", 0.25))
    wind = SIZING_WIND
    print(f"  sizing in still air (eq. 5); plate mass {p.m_plate:.3f} kg")

    # ---- target radius from the network geometry
    dist = hub_phc_distances()
    dist.round(3).to_csv(RESULTS / "m1_flight_distances.csv")
    cov = pd.DataFrame([dict(k_hubs=k, radius_km=r, hubs=" ".join(h))
                        for k in range(1, 6) for r, h in [min_cover_radius(dist, k)]])
    cov.to_csv(RESULTS / "m1_coverage_radius.csv", index=False, float_format="%.3f")
    targets = required_radii(dist, wind, p)

    # ---- continuous optimum and catalogue snap for each required radius
    cont, snapped, problems = [], [], {}
    for name, (R, why) in targets.items():
        prob = Problem(R, wind, p)
        problems[name] = prob
        best, _ = solve_mdf(prob)
        row = dict(design=name, R_req_km=R, rationale=why, **design_table_row(prob, prob.to_x(best.x)))
        cont.append(row)
        s = snap(prob)
        snapped.append(dict(design=name, R_req_km=R, rationale=why, m_continuous=row["m"], **s))
        print(f"  {name:<14} R_req {R:5.1f} km  m* {row['m']:6.3f} kg  snapped {s['m']:6.3f} kg "
              f"({s['battery']}, {s['motor']}, {s['tube']}, {s['prop_in']} in, V {s['V']:.1f})")
    cont = pd.DataFrame(cont)
    snapped = pd.DataFrame(snapped)
    costs = ctx.get("costs") or __import__("common").load_costs()
    snapped["drone_capex_inr"] = costs["drone"]["capex_fixed"] + costs["drone"]["capex_per_kg_mtow"] * snapped.m
    snapped["FM_extrapolated"] = (snapped.T_hover_loaded > 40) | (snapped.T_hover_empty < 8)
    cont.to_csv(RESULTS / "m1_designs_continuous.csv", index=False, float_format="%.6g")
    snapped.to_csv(RESULTS / "m1_designs.csv", index=False, float_format="%.6g")

    # ---- verification
    base = problems[BASELINE]
    zb = base.to_z(cont.set_index("design").loc[BASELINE, VARS].values.astype(float))
    print("  verification: MDA convergence, hand check, gradients, MDF vs IDF")
    verify.mda_convergence(base.to_x(zb), FIGURES / "m1_mda_convergence.png", p).to_csv(
        RESULTS / "m1_mda_convergence.csv", index=False)
    hc = verify.hand_check_table({n: cont.set_index("design").loc[n, VARS].values.astype(float)
                                  for n in targets} | {"max-radius (review)": np.array([0.8, 2000, 0.04, 0.0025, 22])})
    hc.to_csv(RESULTS / "m1_hand_check.csv", index=False, float_format="%.6g")
    rng = np.random.default_rng(1)
    pts = {"baseline optimum": zb, "random interior point": rng.uniform(0.2, 0.8, 5)}
    gc = pd.concat([verify.gradient_check(base, pts),
                    verify.gradient_check(Problem(base.R_req_km, wind, p, mda_tol=1e-8), pts)])
    gc.to_csv(RESULTS / "m1_gradient_check.csv", index=False, float_format="%.3e")
    verify.gradient_step_sweep(base, pts["random interior point"], FIGURES / "m1_gradient_check.png")
    mi = verify.mdf_vs_idf(problems)
    mi.to_csv(RESULTS / "m1_mdf_vs_idf.csv", index=False, float_format="%.6g")
    print(mi[["design", "m_MDF", "m_IDF", "m_rel_diff_pct"]].round(5).to_string(index=False))

    # ---- active constraints, multipliers, payload sensitivity (baseline)
    mult, resid = kkt_multipliers(base, zb)
    g = base.mdf_g(zb)
    act = pd.DataFrame([dict(constraint=k, multiplier=l, note="objective is m/10 kg; constraints normalised")
                        for k, (_, _, l) in mult.items()])
    act.to_csv(RESULTS / "m1_active_constraints.csv", index=False, float_format="%.6g")
    sens, _ = payload_sensitivity(base, zb)
    pd.Series(sens).to_csv(RESULTS / "m1_payload_sensitivity.csv", header=["value"], float_format="%.6g")
    print(f"  active at baseline: {', '.join(mult)}  (KKT residual {resid:.1e})")
    print(f"  payload: {sens['km_lost_per_100g_multiplier']:.3f} km lost per extra 100 g at fixed MTOW "
          f"(re-solve {sens['km_lost_per_100g_resolve']:.3f}); "
          f"+{1000 * 0.1 * sens['dm_dm_pay']:.0f} g MTOW per 100 g payload at fixed radius")

    tradeoff_figure(problems, snapped, cov, wind, p, FIGURES / "m1_tradeoff.png")
    return {"m1_designs": snapped, "m1_problem": base, "m1_baseline": BASELINE,
            "m1_design_wind": wind, "m1_flight_distances": dist}


def resolve(R_req_km, fm=None, m_plate=0.25, wind=None, complex_safe=None, n_starts=8):
    """Re-solve one M1 design with a new FM model (M3) or plate mass (M2).

    fm(D, T) -> FM must accept numpy arrays; pass complex_safe=False for a GP/RBF surrogate
    (gradients then fall back to central differences). Returns (x, analysis dict)."""
    from .model import fm_constant
    p = Params(m_plate=m_plate, fm=fm or fm_constant,
               complex_safe=(fm is None) if complex_safe is None else complex_safe)
    prob = Problem(R_req_km, SIZING_WIND if wind is None else wind, p)
    best, _ = solve_mdf(prob, n_starts=n_starts)
    x = prob.to_x(best.x)
    return x, analyse(x, mda(x, p), p, prob.wind)


def payload_radius_curve(row, wind, p, payloads):
    """Frozen catalogue hardware flown with a different payload (V kept at the design value)."""
    from .catalogue import load_parts
    bats, motors, tubes = load_parts()
    mo = motors.set_index("part_id").loc[row.motor]
    tu = tubes.set_index("part_id").loc[row.tube]
    b = bats.set_index("battery").loc[row.battery]
    x = np.array([row.D, row.E, row.d, row.t, row.V])
    L = 0.78 * row.D - 0.08
    parts = dict(m_motor=mo.mass_kg, m_batt=b.m_batt, m_arm=tu.mass_kg * L)
    R, ok = [], []
    for mp in payloads:
        pp = p.with_(m_pay=mp)
        m = mda(x, pp, **parts)
        r = analyse(x, m, pp, wind, m_motor=parts["m_motor"], m_arm=parts["m_arm"])
        R.append(float(r["R_wind_km"]))
        ok.append(r["T_max"] <= mo.max_thrust_N and r["f1_Hz"] >= r["f1_req_Hz"])
    return np.array(R), np.array(ok)


def tradeoff_figure(problems, snapped, cov, wind, p, path):
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.2))
    payloads = np.linspace(0, 3.5, 36)
    for _, row in snapped.iterrows():
        R, ok = payload_radius_curve(row, wind, p, payloads)
        line, = a1.plot(np.where(ok, payloads, np.nan), np.where(ok, R, np.nan), label=row.design)
        a1.plot(np.where(~ok, payloads, np.nan), np.where(~ok, R, np.nan), ":", color=line.get_color())
        a1.plot(2.5, row.R_wind_km, "o", color=line.get_color())
    a1.axvline(2.5, color="grey", lw=0.8, ls=":")
    a1.set_xlabel("payload (kg)")
    a1.set_ylabel("still-air radius R (km)")
    a1.set_title("Payload-radius trade-off of the 5 built designs\n(dotted: motor or frequency limit exceeded)", fontsize=9)
    lo, hi = a1.get_ylim()
    a1.set_ylim(lo, hi + 0.18 * (hi - lo))
    a1.legend(fontsize=8, loc="upper center", ncol=3)

    Rs = np.linspace(8, max(pr.R_req_km for pr in problems.values()), 30)
    ms = []
    for R in Rs:
        try:
            ms.append(10 * solve_mdf(Problem(R, wind, p), n_starts=4)[0].fun)
        except RuntimeError:
            ms.append(np.nan)
    a2.plot(Rs, ms, "k-", lw=1.2, label="continuous optimum")
    a2.plot(snapped.R_req_km, snapped.m, "s", color="C3", label="snapped to catalogue")
    for _, row in snapped.iterrows():
        a2.annotate(row.design.split()[0], (row.R_req_km, row.m), textcoords="offset points", xytext=(-10, 6), fontsize=8)
    for _, c in cov.iterrows():
        if c.radius_km < 26:
            a2.axvline(c.radius_km, color="grey", lw=0.6, ls="--")
            a2.text(c.radius_km, 6.6, f" {int(c.k_hubs)} hubs", rotation=90, fontsize=7, color="grey")
    a2.set_ylim(6.2, snapped.m.max() + 1.0)
    a2.set_xlabel("required still-air radius (km)")
    a2.set_ylabel("take-off mass (kg)")
    a2.set_title("Lightest drone for each required radius (2.5 kg payload)", fontsize=9)
    a2.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
