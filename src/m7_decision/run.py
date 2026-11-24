"""M7 pipeline step for run_all.py: decision and defense.

Brief, M7:
  * Robustness: evaluate your top 3 candidates on calm, typical, and worst-monsoon-week wind.
    Report mean and worst case.
  * Reliability: probability that S45 stays at or above 90 % with demand +-25 % and drone
    availability between 90 % and 98 % (Monte Carlo).
  * Sensitivity: what changes if battery specific energy rises 20 %, drone price falls 30 %, or
    demand grows 15 % a year?
  * State your decision rule (knee point, minimax regret, best service within budget) and why.
  Hand in: a two-page decision memo for the DHO (report/decision_memo.md).

Candidates (M6, results/m6_resim_365.csv): the knee, the highest-S45 point and the cheaper
no-reserve point, all design E (3 hubs H1 H2 H4). Each is what one decision rule would pick.

Steps
  1. Wind weeks from the data; cost model check against M6.
  2. Robustness: calm week, typical year (M6's 365 days and seeds), worst monsoon week.
  3. Reliability Monte Carlo (demand ~ U(0.75, 1.25), availability ~ U(0.90, 0.98)).
  4. Sensitivity: battery +20 % (two variants), drone price -30 % (archive re-cost), demand
     +15 %/yr for 5 years (fixed fleet, then the smallest top-up that holds year-0 service).
  5. Decision: knee rule, tested by minimax regret over every scenario above.
  6. Verification table and predicted-vs-actual table (predictions are in the decision log).

Writes results/m7_*.csv and figures/m7_robustness.png, m7_reliability.png, m7_sensitivity.png.
Returns ctx["m7"].
"""
import math
import os
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from common import FIGURES, RESULTS
from m6_moo.problem import COST_CAP, cost_terms
from m6_moo.run import knee_index
from m6_moo.verify import front_2d
from . import scenarios as sc
from .scenarios import CANDIDATES, parse_label

TARGET = 0.90
GROWTH = 1.15
YEARS = range(0, 6)
COL = {"knee": "#1baf7a", "best S45": "#2a78d6", "no reserve": "#eb6834"}
INK, MUTED = "#0b0b0b", "#8a8984"


def budget(ctx):
    fast = bool(ctx.get("fast")) or os.environ.get("SKYLINK_FAST") == "1"
    return dict(mc=120 if fast else 500, mc_days=30, mc_nominal=40 if fast else 120,
                week_seeds=8 if fast else 20, topup_steps=12 if fast else 25, fast=fast)


def run(ctx):
    B = budget(ctx)
    t0 = time.time()
    R = sc.Runner()
    try:
        out = _run(ctx, B, R)
    finally:
        R.close()
    print(f"  {R.n} simulated days in {time.time() - t0:.0f} s")
    return {"m7": out}


def _run(ctx, B, R):
    cfgs = {k: parse_label(v) for k, v in CANDIDATES.items()}
    row0 = sc.vehicle_row("E 3-hub", ctx)
    veh0 = sc.vehicle_from(row0)
    m0, E0 = float(row0.m), float(row0.E)
    cost = lambda cfg, red, m=m0, E=E0, price=1.0: sum(sc.annual_cost(cfg, red["means"], m, E, price).values()) / 1e7
    ver = []

    # ---------------------------------------------------------------- 1. wind weeks
    ww = sc.wind_weeks()
    assert ww["calm"] == sc.CALM_WEEK and ww["worst"] == sc.WORST_WEEK
    pd.DataFrame([dict(scenario="calm week", days=f"{ww['calm'][0]}-{ww['calm'][-1]}",
                       rule="7 consecutive days with the lowest mean wind (whole year)",
                       mean_wind_ms=ww["daily"].loc[ww["calm"], "mean"].mean(),
                       grounded_h=int(ww["daily"].loc[ww["calm"], "grounded"].sum()), hours=168),
                  dict(scenario="typical year", days="1-365", rule="every day of wind_days.csv",
                       mean_wind_ms=ww["daily"]["mean"].mean(), grounded_h=ww["year_grounded_h"], hours=8760),
                  dict(scenario="worst monsoon week", days=f"{ww['worst'][0]}-{ww['worst'][-1]}",
                       rule="7 consecutive days ending Jun-Sep with the most gust-grounded hours",
                       mean_wind_ms=ww["daily"].loc[ww["worst"], "mean"].mean(),
                       grounded_h=ww["worst_grounded_h"], hours=168)]
                 ).to_csv(RESULTS / "m7_wind_weeks.csv", index=False, float_format="%.3f")
    print(f"  calm week days {ww['calm'][0]}-{ww['calm'][-1]} (mean {ww['calm_mean_ms']:.1f} m/s), "
          f"worst monsoon week days {ww['worst'][0]}-{ww['worst'][-1]} ({ww['worst_grounded_h']}/168 h grounded)")

    # ---------------------------------------------------------------- 2. robustness
    def week_jobs(label, days, base):
        return [(label, veh0, 1.0, 1.0, d, base + 100 * d + j, 0) for d in days for j in range(B["week_seeds"])]

    rob, typical = [], {}
    for name, label in CANDIDATES.items():
        for scen, jobs in (("calm week", week_jobs(label, sc.CALM_WEEK, 700000)),
                           ("typical year", [(label, veh0, 1.0, 1.0, d, s, 0)
                                             for d, s in zip(sc.TYPICAL, sc.TYPICAL_SEEDS)]),
                           ("worst monsoon week", week_jobs(label, sc.WORST_WEEK, 800000))):
            kp = R.run(jobs)
            red = sc.reduce(kp)
            per_day = pd.DataFrame({"day": [j[4] for j in jobs], "S45": [k["S45"] for k in kp]}
                                   ).groupby("day").S45.mean()
            rob.append(dict(candidate=name, config=label, scenario=scen, days=len(per_day),
                            demand_seeds_per_day=len(jobs) // len(per_day), S45_mean=red["S45"],
                            S45_ci=red["S45_ci"], S45_worst_day=float(per_day.min()),
                            worst_day=int(per_day.idxmin()), P95_min=red["P95"],
                            road_share=red["road_share"], em_road_share=red["em_road_share"],
                            utilisation=red["utilisation"], cost_cr_annualised=cost(cfgs[name], red)))
            if scen == "typical year":
                typical[name] = red
    rob = pd.DataFrame(rob)
    rob.to_csv(RESULTS / "m7_robustness.csv", index=False, float_format="%.4f")
    print(rob[["candidate", "scenario", "S45_mean", "S45_worst_day", "P95_min", "road_share",
               "cost_cr_annualised"]].round(3).to_string(index=False))

    # V1: typical year reproduces M6's 365-day numbers (same days, same seeds)
    rs = pd.read_csv(RESULTS / "m6_resim_365.csv").set_index("config")
    for name, label in CANDIDATES.items():
        if label in rs.index:
            dS, dC = typical[name]["S45"] - rs.loc[label, "S45_365"], cost(cfgs[name], typical[name]) - rs.loc[label, "cost_cr_365"]
            ver.append(dict(check="V1 typical year = M6 365-day re-check", item=name,
                            value=f"dS45 {dS:+.5f}, dcost {dC:+.5f} cr", passed=abs(dS) < 1e-4 and abs(dC) < 1e-4))
    # V2a: M7's cost function = M6's at nominal
    t6 = cost_terms("E 3-hub", cfgs["knee"], typical["knee"]["means"], ctx)
    t7 = sc.annual_cost(cfgs["knee"], typical["knee"]["means"], m0, E0)
    rel = max(abs(t7[k] / t6[k] - 1) for k in t6 if t6[k])
    ver.append(dict(check="V2 cost model = M6 cost_terms (knee, every term)", item="knee",
                    value=f"max rel. diff {rel:.1e}", passed=rel < 1e-12))

    # ---------------------------------------------------------------- 3. reliability (Monte Carlo)
    rng = np.random.default_rng(7007)
    N, nd = B["mc"], B["mc_days"]
    draws = pd.DataFrame({"sample": range(N), "demand": rng.uniform(0.75, 1.25, N),
                          "availability": rng.uniform(0.90, 0.98, N)})
    days = rng.integers(1, 366, (N, nd))
    samples = []
    for name, label in CANDIDATES.items():
        jobs = [(label, veh0, float(draws.demand[i]), float(draws.availability[i]), int(days[i, j]),
                 900000 + 100 * i + j, 3000000 + 100 * i + j) for i in range(N) for j in range(nd)]
        kp = R.run(jobs)
        s = np.array([k["S45"] for k in kp]).reshape(N, nd)
        road = np.array([k["road"] / max(k["orders"], 1) for k in kp]).reshape(N, nd)
        samples.append(draws.assign(candidate=name, S45_30d=np.nanmean(s, axis=1),
                                    road_share_30d=road.mean(axis=1),
                                    S45_days_ge_090=(s >= TARGET).mean(axis=1)))
    samples = pd.concat(samples, ignore_index=True)
    samples.to_csv(RESULTS / "m7_reliability_samples.csv", index=False, float_format="%.5f")
    rel_rows = []
    for name, g in samples.groupby("candidate", sort=False):
        k = int((g.S45_30d >= TARGET).sum())
        lo, hi = sc.wilson(k, len(g))
        X = np.c_[np.ones(len(g)), g.demand - 1, g.availability - 0.94]
        beta = np.linalg.lstsq(X, g.S45_30d.values, rcond=None)[0]
        rel_rows.append(dict(candidate=name, samples=len(g), days_per_sample=nd,
                             P_S45_ge_090=k / len(g), wilson_lo=lo, wilson_hi=hi, half_width=(hi - lo) / 2,
                             P_day_S45_ge_090=g.S45_days_ge_090.mean(),
                             P_S45_ge_088=float((g.S45_30d >= 0.88).mean()),
                             P_S45_ge_085=float((g.S45_30d >= 0.85).mean()),
                             S45_mean=g.S45_30d.mean(), S45_p05=g.S45_30d.quantile(0.05),
                             S45_p95=g.S45_30d.quantile(0.95),
                             dS45_per_10pct_demand=beta[1] * 0.1, dS45_per_1pct_availability=beta[2] * 0.01))
    relt = pd.DataFrame(rel_rows)
    relt.to_csv(RESULTS / "m7_reliability.csv", index=False, float_format="%.4f")
    print(relt[["candidate", "P_S45_ge_090", "wilson_lo", "wilson_hi", "P_day_S45_ge_090", "S45_mean",
                "S45_p05", "dS45_per_10pct_demand", "dS45_per_1pct_availability"]].round(4).to_string(index=False))
    for r in relt.itertuples():
        ver.append(dict(check="V4 Monte Carlo precision: Wilson 95 % half-width <= 5 pp", item=r.candidate,
                        value=f"{100 * r.half_width:.1f} pp (N = {r.samples})", passed=r.half_width <= 0.05))

    # V3: Monte Carlo machinery with demand 1.0 and availability 1.0 reproduces the 365-day S45
    Nn = B["mc_nominal"]
    dn = rng.integers(1, 366, (Nn, nd))
    jobs = [(CANDIDATES["knee"], veh0, 1.0, 1.0, int(dn[i, j]), 950000 + 100 * i + j, 0)
            for i in range(Nn) for j in range(nd)]
    s = np.array([k["S45"] for k in R.run(jobs)]).reshape(Nn, nd)
    mc_nom = np.nanmean(s, axis=1)
    hw = 1.96 * mc_nom.std(ddof=1) / math.sqrt(Nn)
    d = mc_nom.mean() - typical["knee"]["S45"]
    tol = math.hypot(hw, typical["knee"]["S45_ci"])
    ver.append(dict(check="V3 Monte Carlo at demand 1.0, availability 1.0 = 365-day S45 (knee)", item="knee",
                    value=f"{mc_nom.mean():.4f} vs {typical['knee']['S45']:.4f} (diff {d:+.4f}, tol {tol:.4f})",
                    passed=abs(d) <= tol))

    # ---------------------------------------------------------------- 4. sensitivity
    sens = []

    def add(scen, name, red, c, note=""):
        sens.append(dict(scenario=scen, candidate=name, S45=red["S45"], S45_ci=red["S45_ci"],
                         road_share=red["road_share"], em_road_share=red["em_road_share"], P95=red["P95"],
                         utilisation=red["utilisation"], cost_cr=c, note=note))

    for name in CANDIDATES:
        add("nominal", name, typical[name], cost(cfgs[name], typical[name]))

    # 4a. battery specific energy +20 %
    R_req = float(row0.R_req_km)
    rowB = sc.resize_vehicle(R_req, 1.2, ctx)
    rowB_nom = sc.resize_vehicle(R_req, 1.0, ctx)        # same call at 150 Wh/kg = M1's design E
    ver.append(dict(check="V2 M1 re-size at 150 Wh/kg reproduces M1 design E", item="E",
                    value=f"m {rowB_nom['m']:.4f} vs {m0:.4f} kg", passed=abs(rowB_nom["m"] - m0) < 1e-3))   # CSV keeps 6 significant figures
    vehA = sc.vehicle_from(row0, 1.2, "E battery +20 % same drone")
    vehB = sc.vehicle_from(dict(rowB, design="E 3-hub"), 1.0, "E battery +20 % lighter")
    vt = pd.DataFrame([
        dict(variant="nominal (150 Wh/kg)", m_kg=m0, E_Wh=E0, E_use_Wh=veh0.E_use, V_ms=veh0.V, D_m=row0.D,
             R_still_air_km=row0.R_km, battery=row0.battery, motor=row0.motor),
        dict(variant="A: same drone, packs +20 % energy", m_kg=m0, E_Wh=1.2 * E0, E_use_Wh=vehA.E_use,
             V_ms=vehA.V, D_m=row0.D, R_still_air_km=np.nan, battery=f"{row0.battery} at 180 Wh/kg", motor=row0.motor),
        dict(variant="B: M1 re-sized for the same radius (180 Wh/kg)", m_kg=rowB["m"], E_Wh=rowB["E"],
             E_use_Wh=rowB["E_use_Wh"], V_ms=rowB["V"], D_m=rowB["D"], R_still_air_km=rowB["R_km"],
             battery=rowB["battery"], motor=rowB["motor"])])
    vt.to_csv(RESULTS / "m7_battery_vehicles.csv", index=False, float_format="%.4f")
    for vname, veh, m, E in (("battery +20 %, same drone", vehA, m0, 1.2 * E0),
                             ("battery +20 %, lighter drone", vehB, float(rowB["m"]), float(rowB["E"]))):
        for name, label in CANDIDATES.items():
            red = sc.reduce(R.run([(label, veh, 1.0, 1.0, d, s, 0) for d, s in zip(sc.TYPICAL, sc.TYPICAL_SEEDS)]))
            add(vname, name, red, cost(cfgs[name], red, m, E))

    # 4b. drone price -30 %: re-cost M6's whole archive (no re-simulation) and re-pick the knee
    arch = pd.read_csv(RESULTS / "m6_archive.csv")
    arch["cost_cr_cheap"] = arch.cost_cr - 0.3 * arch.cost_drones
    kn = {}
    for tag, col in (("nominal", "cost_cr"), ("drone price -30 %", "cost_cr_cheap")):
        a = arch[arch[col] <= COST_CAP / 1e7].reset_index(drop=True)
        idx = front_2d(a[col].values, a.S45.values)
        f = a.iloc[idx].reset_index(drop=True)
        k = knee_index(f[col].values, f.S45.values)
        kn[tag] = dict(price=tag, knee=f.config[k], cost_cr_73=f[col][k], S45_73=f.S45[k], front_points=len(f),
                       drones=int(f.drones[k]), spares=int(f.spares[k]), bays=int(f.bays[k]))
    pk = pd.DataFrame(kn.values())
    ver.append(dict(check="V2 archive re-pick at nominal price = M6 knee", item="knee",
                    value=kn["nominal"]["knee"], passed=kn["nominal"]["knee"] == CANDIDATES["knee"]))
    # hand check: drone term of the knee
    c = sc.annual_cost(cfgs["knee"], typical["knee"]["means"], m0, E0)
    cc = sc.annual_cost(cfgs["knee"], typical["knee"]["means"], m0, E0, 0.7)
    hand = 11 * (200000 + 45000 * m0) * 0.7 * (0.1 / (1 - 1.1 ** -3) + 0.15)
    ver.append(dict(check="V2 drone-price hand check (11 drones x 0.7 x capex x (CRF + 15 %))", item="knee",
                    value=f"{cc['drones']:.0f} vs hand {hand:.0f} INR/yr", passed=abs(cc["drones"] / hand - 1) < 1e-9))
    for name in CANDIDATES:
        add("drone price -30 %", name, typical[name], cost(cfgs[name], typical[name], price=0.7))
    new_knee = kn["drone price -30 %"]["knee"]
    if new_knee != CANDIDATES["knee"]:
        cfgN = parse_label(new_knee)
        red = sc.reduce(R.run([(new_knee, veh0, 1.0, 1.0, d, s, 0) for d, s in zip(sc.TYPICAL, sc.TYPICAL_SEEDS)]))
        pk.loc[pk.price == "drone price -30 %", ["S45_365", "cost_cr_365"]] = [red["S45"], cost(cfgN, red, price=0.7)]
        add("drone price -30 %", "re-picked knee", red, cost(cfgN, red, price=0.7), new_knee)
    pk.loc[pk.price == "nominal", ["S45_365", "cost_cr_365"]] = [typical["knee"]["S45"], cost(cfgs["knee"], typical["knee"])]
    pk.to_csv(RESULTS / "m7_price_knee.csv", index=False, float_format="%.4f")
    print(pk.round(4).to_string(index=False))

    # 4c. demand +15 %/yr: fixed fleet, then the smallest top-up for the knee
    growth = []
    for y in YEARS:
        dm = GROWTH ** y
        for name, label in CANDIDATES.items():
            red = typical[name] if y == 0 else sc.reduce(R.run(
                [(label, veh0, dm, 1.0, d, s, 0) for d, s in zip(sc.TYPICAL, sc.TYPICAL_SEEDS)]))
            growth.append(dict(year=y, demand=dm, candidate=name, fleet="fixed", config=label, S45=red["S45"],
                               S45_ci=red["S45_ci"], road_share=red["road_share"], P95=red["P95"],
                               utilisation=red["utilisation"], cost_cr=cost(cfgs[name], red)))
            if y in (1, 3, 5):
                add(f"demand year {y} (x{dm:.2f}), fleet fixed", name, red, cost(cfgs[name], red))
    topup = topup_knee(R, veh0, cost, B, typical["knee"]["S45"])
    growth = pd.concat([pd.DataFrame(growth), topup], ignore_index=True)
    growth.to_csv(RESULTS / "m7_demand_growth.csv", index=False, float_format="%.4f")
    print(growth[["year", "candidate", "fleet", "config", "S45", "road_share", "cost_cr"]].round(3).to_string(index=False))

    sens = pd.DataFrame(sens)
    sens.to_csv(RESULTS / "m7_sensitivity.csv", index=False, float_format="%.4f")

    # ---------------------------------------------------------------- 5. decision: knee + regret
    regret, lam = regret_table(rob, relt, sens, typical["knee"]["means"]["emergencies"])
    regret.to_csv(RESULTS / "m7_regret.csv", index=False, float_format="%.4f")
    print(regret.round(4).to_string(index=False))

    # ---------------------------------------------------------------- 6. verification + predictions
    pva = predictions_vs_actual(rob, relt, sens, growth, pk, typical)
    pva.to_csv(RESULTS / "m7_predictions_vs_results.csv", index=False)
    ver = pd.DataFrame(ver)
    ver.to_csv(RESULTS / "m7_verification.csv", index=False)
    print(ver.to_string(index=False))
    print(pva[["id", "change", "result", "as_predicted"]].to_string(index=False))

    fig_robustness(rob, FIGURES / "m7_robustness.png")
    fig_reliability(samples, relt, FIGURES / "m7_reliability.png")
    fig_sensitivity(sens, growth, FIGURES / "m7_sensitivity.png")
    return dict(robustness=rob, reliability=relt, sensitivity=sens, growth=growth, price_knee=pk,
                regret=regret, lam=lam, verification=ver, predictions=pva)


# ---------------------------------------------------------------- demand growth top-up
def topup_knee(R, veh, cost, B, s45_target):
    """Re-tune the knee each year as demand grows (the M6 cost objective, greedy, one unit at a time).

    Run on M5/M6's 73 CRN days, from last year's configuration. Moves: +1 drone, +1 spare pack or
    +1 bay at one hub. While S45 is more than 1 pp below the knee's year-0 value, take the move with
    the most S45 per rupee; otherwise take the move that lowers annual cost the most (road trips are
    expensive), keeping S45 within 1 pp of year 0. Stop when no move saves at least INR 0.5 lakh/yr.
    The chosen configuration is then re-checked on all 365 days."""
    import dataclasses
    from m6_moo.problem import crn_days
    days, seeds = crn_days()
    cfg = parse_label(CANDIDATES["knee"])
    s0 = sc.reduce(R.run([(cfg.label, veh, 1.0, 1.0, d, s, 0) for d, s in zip(days, seeds)]))["S45"]
    floor = s0 - 0.01
    rows = []
    for y in YEARS:
        if y == 0:
            continue
        dm = GROWTH ** y

        def ev(cs):
            jobs = [(c.label, veh, dm, 1.0, d, s, 0) for c in cs for d, s in zip(days, seeds)]
            kp = R.run(jobs)
            n = len(days)
            return [sc.reduce(kp[i * n:(i + 1) * n]) for i in range(len(cs))]

        cur = ev([cfg])[0]
        steps = 0
        while steps < B["topup_steps"]:
            moves = []
            for h in range(len(cfg.drones)):
                for kind in ("drones", "spares", "bays"):
                    v = list(getattr(cfg, kind))
                    v[h] += 1
                    moves.append(dataclasses.replace(cfg, **{kind: tuple(v)}))
            outs = ev(moves)
            c0 = cost(cfg, cur)
            if cur["S45"] < floor:
                gain = [(o["S45"] - cur["S45"]) / max(cost(c, o) - c0, 1e-4) for c, o in zip(moves, outs)]
                i = int(np.argmax(gain))
            else:
                cs = [cost(c, o) if o["S45"] >= floor else np.inf for c, o in zip(moves, outs)]
                i = int(np.argmin(cs))
                if not cs[i] < c0 - 0.005:
                    break
            cfg, cur = moves[i], outs[i]
            steps += 1
        red = sc.reduce(R.run([(cfg.label, veh, dm, 1.0, d, s, 0) for d, s in zip(sc.TYPICAL, sc.TYPICAL_SEEDS)]))
        rows.append(dict(year=y, demand=dm, candidate="knee", fleet="topped up", config=cfg.label,
                         S45=red["S45"], S45_ci=red["S45_ci"], road_share=red["road_share"], P95=red["P95"],
                         utilisation=red["utilisation"], cost_cr=cost(cfg, red), S45_73=cur["S45"],
                         S45_73_floor=floor, steps_this_year=steps))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- regret
def regret_table(rob, relt, sens, em_per_day):
    """Regret of each candidate in each scenario, on S45 (pp) and on annual cost (INR cr/yr).

    S45 regret uses every scenario. Cost regret uses whole-year scenarios only (a week's cost
    scaled to a year is not a real annual cost). Two scenario sets are reported:
      "within drone life" - demand growth up to year 2 only: drones last 3 years (costs.yaml), so the
                            fleet is re-bought and re-tuned at year 3 (shown by the top-up study);
      "all"               - also years 3 and 5 with the year-0 fleet frozen.
    Combined regret = cost regret + lambda x S45 regret, for three prices lambda of one S45 point."""
    sc_rows = []
    for r in rob.itertuples():
        sc_rows.append((f"wind: {r.scenario}", r.candidate, r.S45_mean,
                        r.cost_cr_annualised if r.scenario == "typical year" else np.nan))
    for r in relt.itertuples():
        sc_rows.append(("reliability Monte Carlo (mean)", r.candidate, r.S45_mean, np.nan))
    for r in sens[sens.candidate.isin(CANDIDATES)].itertuples():
        if r.scenario != "nominal":
            sc_rows.append((r.scenario, r.candidate, r.S45, r.cost_cr))
    df = pd.DataFrame(sc_rows, columns=["scenario", "candidate", "S45", "cost_cr"])
    typ = df[df.scenario == "wind: typical year"].set_index("candidate")
    lam = (typ.cost_cr["knee"] - typ.cost_cr["no reserve"]) / max(100 * (typ.S45["knee"] - typ.S45["no reserve"]), 1e-6)
    df["S45_regret_pp"] = 100 * (df.groupby("scenario").S45.transform("max") - df.S45)
    df["cost_regret_cr"] = df.cost_cr - df.groupby("scenario").cost_cr.transform("min")
    lam_road = 365 * em_per_day * 0.01 * 2500 / 1e7    # 1 pp of emergencies by road at INR 2,500
    late = df.scenario.str.contains("demand year 3|demand year 5")
    df.to_csv(RESULTS / "m7_scenario_table.csv", index=False, float_format="%.4f")
    out = []
    for set_name, d in (("within drone life", df[~late]), ("all", df)):
        for lam_name, l in (("implied by knee vs no reserve", lam), ("road-trip value of a pp", lam_road),
                            ("high: 0.20 cr per pp", 0.20)):
            comb = d.S45_regret_pp * l + d.cost_regret_cr.fillna(0)
            g = d.assign(combined=comb).groupby("candidate", sort=False)
            t = pd.DataFrame({"max_S45_regret_pp": g.S45_regret_pp.max(),
                              "max_cost_regret_cr": g.cost_regret_cr.max(),
                              "max_combined_regret_cr": g.combined.max(),
                              "worst_scenario": g.apply(lambda x: x.loc[x.combined.idxmax(), "scenario"],
                                                        include_groups=False)}).reset_index()
            t.insert(0, "lambda_cr_per_pp", l)
            t.insert(0, "lambda", lam_name)
            t.insert(0, "scenario_set", set_name)
            t["minimax_pick"] = t.max_combined_regret_cr == t.max_combined_regret_cr.min()
            out.append(t)
    # break-even price of one S45 point between the knee and each other candidate (scan)
    grid = np.round(np.arange(0, 0.5001, 0.0005), 4)
    be = []
    for set_name, d in (("within drone life", df[~late]), ("all", df)):
        mx = {c: np.array([(d[d.candidate == c].S45_regret_pp * l + d[d.candidate == c].cost_regret_cr.fillna(0)).max()
                           for l in grid]) for c in CANDIDATES}
        knee_best = (mx["knee"] <= np.minimum(mx["best S45"], mx["no reserve"]) + 1e-12)
        be.append(dict(scenario_set=set_name,
                       knee_minimax_from_cr_per_pp=float(grid[knee_best].min()) if knee_best.any() else np.nan,
                       knee_minimax_to_cr_per_pp=float(grid[knee_best].max()) if knee_best.any() else np.nan))
    pd.DataFrame(be).to_csv(RESULTS / "m7_regret_breakeven.csv", index=False, float_format="%.4f")
    return pd.concat(out, ignore_index=True), lam


# ---------------------------------------------------------------- predicted vs actual
def predictions_vs_actual(rob, relt, sens, growth, pk, typical):
    g = lambda scen, cand="knee": rob[(rob.scenario == scen) & (rob.candidate == cand)].iloc[0]
    s = lambda scen, cand="knee": sens[(sens.scenario == scen) & (sens.candidate == cand)].iloc[0]
    nom = s("nominal")
    rank = lambda scen: list(rob[rob.scenario == scen].sort_values("S45_mean").candidate)
    calm, worst = g("calm week"), g("worst monsoon week")
    k = relt.set_index("candidate")
    A, Bv, P = s("battery +20 %, same drone"), s("battery +20 %, lighter drone"), s("drone price -30 %")
    fx = growth[(growth.candidate == "knee") & (growth.fleet == "fixed")].set_index("year")
    brk = [y for y in fx.index if y > 0 and fx.S45[y] < fx.S45[0] - 0.015]
    tu = growth[growth.fleet == "topped up"]
    rows = [
        ("P1", "calm week", "S45 about 0.97, ranking unchanged",
         f"S45 {calm.S45_mean:.3f}; ranking {rank('calm week')} vs typical {rank('typical year')}",
         calm.S45_mean >= 0.95 and rank("calm week") == rank("typical year")),
        ("P2", "worst monsoon week", "S45 0.65-0.75, worst day < 0.4",
         f"S45 {worst.S45_mean:.3f}, worst day {worst.S45_worst_day:.3f}",
         0.65 <= worst.S45_mean <= 0.75 and worst.S45_worst_day < 0.4),
        ("P3", "Monte Carlo", "P(S45 >= 0.90) 0.2-0.35, similar for all",
         "; ".join(f"{c} {k.P_S45_ge_090[c]:.2f}" for c in k.index),
         bool(((k.P_S45_ge_090 >= 0.2) & (k.P_S45_ge_090 <= 0.35)).all())),
        ("P4", "battery +20 %, same drone", "S45 +2 to +4 pp, cost about +0.5 %",
         f"S45 {100 * (A.S45 - nom.S45):+.1f} pp, cost {100 * (A.cost_cr / nom.cost_cr - 1):+.1f} %",
         2 <= 100 * (A.S45 - nom.S45) <= 4),
        ("P5", "battery +20 %, lighter drone", "S45 within +-1 pp, cost -2 to -4 %",
         f"S45 {100 * (Bv.S45 - nom.S45):+.1f} pp, cost {100 * (Bv.cost_cr / nom.cost_cr - 1):+.1f} %",
         abs(100 * (Bv.S45 - nom.S45)) <= 1 and -4 <= 100 * (Bv.cost_cr / nom.cost_cr - 1) <= -2),
        ("P6", "drone price -30 %", "cost about -0.1 cr; knee moves to more drones",
         f"cost {P.cost_cr - nom.cost_cr:+.3f} cr; re-picked knee {pk.knee.iloc[1]} "
         f"({int(pk.drones.iloc[1])} vs {int(pk.drones.iloc[0])} drones)",
         abs(P.cost_cr - nom.cost_cr + 0.1) <= 0.05 and pk.drones.iloc[1] > pk.drones.iloc[0]),
        ("P7", "demand +15 %/yr, fleet fixed", "S45 holds within 1.5 pp for 1-2 years, then falls",
         "S45 by year " + ", ".join(f"{y}: {fx.S45[y]:.3f}" for y in fx.index)
         + (f"; first year more than 1.5 pp down: {brk[0]}" if brk else "; never more than 1.5 pp down"),
         bool(brk) and brk[0] in (2, 3)),
        ("P8", "demand +15 %/yr, top-up", "about 1 drone + 2 spares per hub every 2 years",
         "; ".join(f"y{r.year}: {r.config}" for r in tu.itertuples()), None),
    ]
    df = pd.DataFrame(rows, columns=["id", "change", "prediction", "result", "as_predicted"])
    df["as_predicted"] = df.as_predicted.map({True: "yes", False: "no", None: "see text"})
    return df


# ---------------------------------------------------------------- figures
def _style(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.grid(axis="y", color="#e6e5e0", lw=0.8)
    ax.set_axisbelow(True)


def fig_robustness(rob, path):
    scen = ["calm week", "typical year", "worst monsoon week"]
    fig, ax = plt.subplots(figsize=(8.2, 4.8))
    w = 0.26
    for i, (name, c) in enumerate(COL.items()):
        r = rob[rob.candidate == name].set_index("scenario").loc[scen]
        x = np.arange(3) + (i - 1) * w
        ax.bar(x, r.S45_mean, w * 0.92, color=c, label=f"{name} ({CANDIDATES[name]})")
        ax.plot(x, r.S45_worst_day, "v", color=INK, ms=6, label="worst day" if i == 0 else None)
        for xi, v in zip(x, r.S45_mean):
            ax.text(xi, v + 0.01, f"{v:.2f}", ha="center", fontsize=7.5)
    ax.axhline(TARGET, color=MUTED, ls="--", lw=1)
    ax.text(2.45, TARGET + 0.01, "M0 target 0.90", color=MUTED, fontsize=8, ha="right")
    ax.set_xticks(range(3), ["calm week\n(days 40-46)", "typical year\n(365 days)", "worst monsoon week\n(days 209-215)"])
    ax.set_ylabel("S45 (share of emergencies within 45 min)")
    ax.set_ylim(0, 1.08)
    ax.legend(fontsize=7.5, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=2)
    ax.set_title("M7 robustness: mean S45 (bars) and worst day (triangles) by wind scenario", fontsize=10)
    _style(ax)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_reliability(samples, relt, path):
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 4))
    x = np.linspace(0.75, 0.97, 200)
    for name, c in COL.items():
        g = samples[samples.candidate == name]
        a1.plot(x, [(g.S45_30d >= v).mean() for v in x], color=c, label=name)
        p = np.cumsum(g.S45_30d.values >= TARGET) / np.arange(1, len(g) + 1)
        a2.plot(np.arange(1, len(g) + 1), p, color=c, label=name)
        r = relt.set_index("candidate").loc[name]
        a2.fill_between([len(g) * 0.9, len(g)], r.wilson_lo, r.wilson_hi, color=c, alpha=0.25, lw=0)
    a1.axvline(TARGET, color=MUTED, ls="--", lw=1)
    a1.set_xlabel("S45 threshold x (30-day mean)")
    a1.set_ylabel("P(S45 >= x)")
    a1.set_title("Exceedance: demand U(0.75, 1.25), availability U(0.90, 0.98)", fontsize=9)
    a1.legend(frameon=False, fontsize=8)
    a2.set_xlabel("Monte Carlo samples")
    a2.set_ylabel("running estimate of P(S45 >= 0.90)")
    a2.set_title("Convergence (shaded: final Wilson 95 % CI)", fontsize=9)
    for a in (a1, a2):
        _style(a)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_sensitivity(sens, growth, path):
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.3), gridspec_kw=dict(width_ratios=[1.15, 1]))
    k = sens[sens.candidate == "knee"].set_index("scenario")
    nom = k.loc["nominal"]
    scen = [s for s in k.index if s != "nominal"]
    y = np.arange(len(scen))
    a1.barh(y - 0.2, 100 * (k.loc[scen].S45 - nom.S45), 0.38, color="#1baf7a", label="S45 change (pp)")
    a1.barh(y + 0.2, 10 * (k.loc[scen].cost_cr - nom.cost_cr), 0.38, color="#8a8984",
            label="cost change (units of INR 10 lakh/yr)")
    a1.set_yticks(y, scen, fontsize=8)
    a1.axvline(0, color=INK, lw=0.8)
    a1.invert_yaxis()
    a1.set_title(f"Knee vs nominal (S45 {nom.S45:.3f}, INR {nom.cost_cr:.2f} cr/yr)", fontsize=9)
    a1.set_xlabel("S45 change (pp)  |  cost change (INR 10 lakh/yr)")
    a1.legend(frameon=False, fontsize=7.5, loc="upper right")
    for name, c in COL.items():
        g = growth[(growth.candidate == name) & (growth.fleet == "fixed")]
        a2.plot(g.year, g.cost_cr, "o-", color=c, label=f"{name}, fleet fixed", ms=4)
    t = growth[growth.fleet == "topped up"]
    if len(t):
        t = pd.concat([growth[(growth.candidate == "knee") & (growth.fleet == "fixed") & (growth.year == 0)], t])
        a2.plot(t.year, t.cost_cr, "s--", color=INK, ms=4, label="knee, re-tuned each year")
        for r in t.itertuples():
            a2.annotate(f"{100 * r.road_share:.0f} %", (r.year, r.cost_cr), textcoords="offset points",
                        xytext=(0, -13), fontsize=7, ha="center")
    a2.axhline(COST_CAP / 1e7, color=MUTED, ls="--", lw=1)
    a2.text(0, COST_CAP / 1e7 + 0.05, "M0 cost cap 5.57", color=MUTED, fontsize=8)
    a2.set_xlabel("year (demand x 1.15 per year)")
    a2.set_ylabel("annual cost, INR cr/yr (365 days)")
    a2.set_title("Demand growth: S45 stays 0.88-0.89; cost and road trips grow\n"
                 "(labels: road share of the re-tuned knee)", fontsize=9)
    a2.legend(frameon=False, fontsize=7.5, loc="upper left", bbox_to_anchor=(0, 0.93))
    for a in (a1, a2):
        _style(a)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
