"""M5 pipeline step for run_all.py: discrete-event simulation of the M4 networks.

Steps
  1. Verification (brief): one hub, exponential drone time, no wind, no outage -> Erlang C.
     Plus a hand-timed sortie and rule checks (reserve, grounding, outage bays, conservation).
  2. Days (brief: 95 % CI on S45 narrower than +-3 pp): sequential rule on design D as M4
     sized it; the same days and demand streams (common random numbers) are then used for
     every configuration below.
  3. KPIs for D (M4's cheapest), C and E as M4 sized them, plus two what-ifs on D: one spare
     pack per drone, and an unlimited fleet (10 more drones, bays and spare packs per hub) that
     shows the S45 ceiling set by wind alone.
  4. Live view: one day of design D as an animated map (GIF) and a still.

Writes (results/)
  m5_mmc_check.csv, m5_hand_check.csv, m5_rule_checks.csv     verification
  m5_days_ci.csv                                              how the number of days was chosen
  m5_kpis.csv          per configuration: S45 (mean, 95 % CI), P95 emergency time, utilisation,
                       road-fallback share, energy (grid / genset)
  m5_kpis_by_hub.csv   per hub of each configuration
  m5_road_causes.csv   why emergencies missed 45 min (grounding, headwind range, no drone/pack, late)
  m5_days.csv          one row per simulated day and configuration
  m5_wind_ceiling_year.csv  S45 with an unlimited fleet over all 365 wind days, designs C, D, E
Figures (figures/)
  m5_mmc_check.png, m5_s45_days.png, m5_results.png, m5_live_day.gif, m5_live_day_still.png
Returns ctx["m5"]: {"simulate": simulate_day, "network": network_from_m4, "vehicle": vehicle_for,
"rules", "profile", "days", "seeds", "kpis"} for M6/M7.
"""
import collections

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from common import FIGURES, RESULTS
from . import verify
from .model import (Rules, hourly_profile, network_from_m4, simulate_day, summarise, vehicle_for,
                    weekday)

BASE = "D 4-hub"
LIVE_DAY = 164            # a Wednesday (grid outage 14-17) with gusts grounding 14:00-17:00


def configurations(ctx):
    big = {h: 10 for h in ("H0", "H1", "H2", "H3", "H4", "H5", "H6", "P09")}
    return [
        ("D as M4 (baseline)", BASE, dict()),
        ("C as M4", "C cover-all", dict()),
        ("E as M4", "E 3-hub", dict()),
        ("D + 1 spare pack per drone", BASE, dict(spares="per_drone")),
        ("D unlimited fleet (wind ceiling)", BASE, dict(spares=10, bays=big, extra_drones=big)),
    ]


def build(ctx, design, kw):
    kw = dict(kw)
    if kw.get("spares") == "per_drone":
        hubs0 = network_from_m4(design, ctx)
        kw["spares"] = {h.name: h.drones for h in hubs0}
    return network_from_m4(design, ctx, **kw)



def evaluate(ctx, label, design, kw, days, seeds, rules, profile):
    """Simulate one configuration on the given days and demand streams."""
    hs, v = build(ctx, design, kw), vehicle_for(design, ctx)
    causes = collections.Counter()
    kp = []
    for d, s in zip(days, seeds):
        k, sim = simulate_day(hs, v, rules, profile, d, s)
        kp.append(k)
        for o in sim.orders:
            if o.c != 0:
                continue
            if o.mode == "road":
                causes["road: " + o.cause] += 1
            elif o.delivered - o.t > rules.em_target:
                causes["drone, later than 45 min"] += 1
            else:
                causes["within 45 min"] += 1
    sm = summarise(kp, label)
    sm.update(design=design, drones=sum(h.drones for h in hs), bays=sum(h.bays for h in hs),
              spare_packs=sum(h.spares for h in hs), reserve=sum(h.reserve for h in hs))
    n_em = sum(causes.values())
    cause_row = dict(config=label, emergencies=n_em,
                     **{c: causes[c] / n_em for c in
                        ("within 45 min", "drone, later than 45 min", "road: gust grounding",
                         "road: headwind range", "road: no drone or pack free")})
    hub_rows = []
    for h in hs:
        agg = {key: sum(k["by_hub"][h.name][key] for k in kp)
               for key in ("busy_min", "fly_min", "sorties", "em", "em45", "road", "wait_pack_min")}
        hub_rows.append(dict(config=label, hub=h.name, drones=h.drones, bays=h.bays,
                             spares=h.spares, reserve=h.reserve,
                             S45=agg["em45"] / max(agg["em"], 1),
                             utilisation=agg["busy_min"] / (h.drones * 1440 * len(kp)),
                             flying_share=agg["fly_min"] / (h.drones * 1440 * len(kp)),
                             sorties_per_day=agg["sorties"] / len(kp),
                             road_per_day=agg["road"] / len(kp),
                             wait_pack_min_per_day=agg["wait_pack_min"] / len(kp)))
    day_rows = [dict(config=label, **{a: b for a, b in k.items() if a not in ("em_times", "by_hub")})
                for k in kp]
    return dict(sm=sm, causes=cause_row, hubs=hub_rows, days=day_rows, kp=kp,
                times=np.concatenate([k["em_times"] for k in kp]))


def run(ctx):
    rules, profile = Rules.from_costs(), hourly_profile(ctx)
    veh = vehicle_for(BASE, ctx)
    hubs = network_from_m4(BASE, ctx)
    print(f"  design {BASE}: " + ", ".join(f"{h.name} {h.drones} drones/{h.bays} bay(s)/reserve {h.reserve}"
                                           for h in hubs))

    # ---- 1. verification
    m4h = (ctx.get("m4_network", {}).get(BASE, {}).get("hubs") if ctx.get("m4_network") else None)
    if m4h is None:
        m4h = pd.read_csv(RESULTS / "m4_hubs.csv").query("design == @BASE")
    h2 = m4h.set_index("hub").loc["H2"]
    svc = float(h2.busy_min_per_day / h2.orders_per_day)        # M4 mean drone time per order at H2
    mmc = verify.mmc_table(svc, n_orders=150000)
    mmc.to_csv(RESULTS / "m5_mmc_check.csv", index=False, float_format="%.4f")
    print(f"  Erlang C check (mean drone time {svc:.1f} min): max |rel. error| "
          f"{mmc.rel_error.abs().max():.1%}, inside the 95 % CI in {mmc.within_ci.sum()}/{len(mmc)} cases")
    hc = verify.hand_check(veh, rules)
    hc.to_csv(RESULTS / "m5_hand_check.csv", index=False, float_format="%.6f")
    assert hc.abs_diff.max() < 1e-6, "M5 hand check failed"

    # ---- 2. number of days
    days, seeds, kp_base, steps, extend = verify.choose_days(hubs, veh, rules, profile)
    print(f"  days: pilot {steps.days.iloc[0]} -> {len(days)} days for D, S45 95 % CI half-width "
          f"{steps.ci95_half.iloc[-1]:.3f} (target < 0.030)")
    rc = verify.rule_checks(veh, rules, profile, hubs, sorted(set(days)))
    rc.to_csv(RESULTS / "m5_rule_checks.csv", index=False)
    print(f"  rule checks: {(rc.status == 'ok').sum()}/{len(rc)} ok")
    assert (rc.status == "ok").all(), rc[rc.status != "ok"]

    # ---- 3. configurations on common random numbers; extend the days until every
    #         configuration's S45 CI is below +-3 pp (the same days for all: CRN)
    while True:
        res = [evaluate(ctx, label, design, kw, days, seeds, rules, profile)
               for label, design, kw in configurations(ctx)]
        need = max(verify.days_needed([k["S45"] for k in r["kp"]]) for r in res)
        worst = max(r["sm"]["S45_ci_half"] for r in res)
        if worst < 0.03:
            break
        n_new = max(need, len(days) + 1)
        steps.loc[len(steps)] = dict(step=len(steps) + 1, days=n_new, S45_mean=np.nan, S45_sd=np.nan,
                                     ci95_half=worst, days_needed=need, met=False)
        print(f"  widest CI among configurations ±{worst:.3f} -> extend to {n_new} days")
        days, seeds = extend(n_new)
    steps.loc[len(steps)] = dict(step=len(steps) + 1, days=len(days), S45_mean=np.nan, S45_sd=np.nan,
                                 ci95_half=worst, days_needed=need, met=True)
    steps.to_csv(RESULTS / "m5_days_ci.csv", index=False, float_format="%.4f")
    rows, hub_rows, cause_rows, day_rows, times = [], [], [], [], {}
    for r in res:
        sm = r["sm"]
        rows.append(sm); cause_rows.append(r["causes"]); hub_rows += r["hubs"]; day_rows += r["days"]
        times[sm["label"]] = r["times"]
        print(f"  {sm['label']:<34} S45 {sm['S45_mean']:.3f} ± {sm['S45_ci_half']:.3f}  P95 {sm['P95_em_min']:5.1f} min  "
              f"util {sm['utilisation']:.2f}  road {sm['road_share']:.1%} (emerg. {sm['em_road_share']:.1%})  "
              f"energy {sm['grid_kWh_day']:.0f}+{sm['genset_kWh_day']:.1f} kWh/day")
    kp_base = res[0]["kp"]

    kpis = pd.DataFrame(rows)
    kpis.to_csv(RESULTS / "m5_kpis.csv", index=False, float_format="%.4f")
    pd.DataFrame(hub_rows).to_csv(RESULTS / "m5_kpis_by_hub.csv", index=False, float_format="%.4f")
    causes = pd.DataFrame(cause_rows)
    causes.to_csv(RESULTS / "m5_road_causes.csv", index=False, float_format="%.4f")
    pd.DataFrame(day_rows).to_csv(RESULTS / "m5_days.csv", index=False, float_format="%.4f")

    # ---- wind ceiling over the whole year: every wind day once, unlimited fleet, per design
    big = {h: 10 for h in ("H0", "H1", "H2", "H3", "H4", "H5", "H6", "P09")}
    year = list(range(1, 366))
    ceil_rows = []
    for design in ("C cover-all", BASE, "E 3-hub"):
        r = evaluate(ctx, f"{design} unlimited fleet", design, dict(spares=10, bays=big, extra_drones=big),
                     year, [10000 + d for d in year], rules, profile)
        ceil_rows.append(dict(design=design, days=365, S45_mean=r["sm"]["S45_mean"],
                              S45_ci_half=r["sm"]["S45_ci_half"],
                              **{k: v for k, v in r["causes"].items() if k not in ("config",)}))
        print(f"  wind ceiling, all 365 days, {design:<12} S45 {r['sm']['S45_mean']:.3f} "
              f"(grounding {r['causes']['road: gust grounding']:.1%}, headwind range "
              f"{r['causes']['road: headwind range']:.1%})")
    pd.DataFrame(ceil_rows).to_csv(RESULTS / "m5_wind_ceiling_year.csv", index=False, float_format="%.4f")

    # ---- figures
    fig_mmc(mmc, FIGURES / "m5_mmc_check.png")
    fig_days(steps, kp_base, FIGURES / "m5_s45_days.png")
    fig_results(times, causes, FIGURES / "m5_results.png")

    # ---- 4. live view
    from .live import animate_day
    _, sim = simulate_day(hubs, veh, rules, profile, LIVE_DAY, LIVE_DAY, trace=True)
    animate_day(sim, hubs, LIVE_DAY, weekday(LIVE_DAY), FIGURES / "m5_live_day.gif")
    print(f"  live view: figures/m5_live_day.gif (day {LIVE_DAY}, {weekday(LIVE_DAY)})")

    return {"m5": dict(simulate=simulate_day, network=network_from_m4, vehicle=vehicle_for,
                       rules=rules, profile=profile, days=days, seeds=seeds, kpis=kpis)}


# ---------------------------------------------------------------- figures
def fig_mmc(mmc, path):
    fig, ax = plt.subplots(figsize=(7.5, 3.6))
    x = np.arange(len(mmc))
    ax.bar(x - 0.2, mmc.Wq_erlangC_min, 0.4, color="0.7", label="Erlang C (M/M/c formula)")
    ax.bar(x + 0.2, mmc.Wq_sim_min, 0.4, yerr=mmc.Wq_sim_ci95_half, capsize=3, color="#1f77b4",
           label="simulator (95 % CI, batch means)")
    ax.set_xticks(x, [f"c={r.drones_c}\nρ={r.utilisation_rho}" for r in mmc.itertuples()], fontsize=8)
    ax.set_yscale("log")
    ax.set_ylabel("mean wait in queue Wq (min)")
    ax.set_title(f"M5 check: one hub, exponential drone time (mean {mmc.service_mean_min.iloc[0]:.1f} min), "
                 "no wind, no outage", fontsize=9)
    for k, r in mmc.iterrows():
        ax.text(k + 0.2, r.Wq_sim_min * 1.25, f"{r.rel_error:+.1%}", ha="center", fontsize=7)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_days(steps, kp, path):
    s = np.array([k["S45"] for k in kp])
    n = np.arange(1, len(s) + 1)
    m = np.cumsum(s) / n
    sd = np.array([s[:i].std(ddof=1) if i > 1 else np.nan for i in n])
    from scipy import stats
    hw = stats.t.ppf(0.975, np.maximum(n - 1, 1)) * sd / np.sqrt(n)
    fig, (a, b) = plt.subplots(1, 2, figsize=(10, 3.6))
    a.plot(n, m, color="k", lw=1.2, label="running mean of daily S45")
    a.fill_between(n, m - hw, m + hw, color="#1f77b4", alpha=0.25, label="95 % CI")
    a.axvline(steps.days.iloc[0], color="0.5", ls=":", lw=1)
    a.text(steps.days.iloc[0], 0.61, " pilot", fontsize=8, color="0.4")
    a.axhline(0.90, color="crimson", ls="--", lw=1, label="M0 target 0.90")
    a.set_ylim(0.6, 1.0); a.set_xlabel("simulated days"); a.set_ylabel("S45")
    a.set_title(f"D as M4: {len(s)} days give ±{steps.ci95_half.iloc[-1] * 100:.1f} pp", fontsize=9)
    a.legend(fontsize=7, loc="lower right")
    b.hist(s, bins=np.linspace(0, 1, 21), color="#1f77b4", edgecolor="w")
    b.axvline(0.90, color="crimson", ls="--", lw=1)
    b.set_xlabel("daily S45"); b.set_ylabel("days")
    b.set_title("day-to-day spread (wind days drawn at random)", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_results(times, causes, path):
    fig, (a, b) = plt.subplots(1, 2, figsize=(11.5, 4.2), gridspec_kw=dict(width_ratios=[1, 1.25]))
    for label, t in times.items():
        ts = np.sort(t)
        a.plot(ts, np.arange(1, len(ts) + 1) / len(ts), lw=1.3, label=label)
    a.axvline(45, color="crimson", ls="--", lw=1)
    a.axhline(0.90, color="0.5", ls=":", lw=1)
    a.set_xlim(0, 160); a.set_xlabel("emergency delivery time from order (min)")
    a.set_ylabel("share of emergencies delivered")
    a.set_title("emergency delivery time (road = 30 + 120 = 150 min)", fontsize=9)
    a.legend(fontsize=7, loc="center right")
    cols = ["within 45 min", "drone, later than 45 min", "road: gust grounding", "road: headwind range",
            "road: no drone or pack free"]
    colours = ["#2ca02c", "#bcbd22", "#7f7f7f", "#17becf", "#d62728"]
    left = np.zeros(len(causes))
    y = np.arange(len(causes))[::-1]
    for c, col in zip(cols, colours):
        b.barh(y, causes[c], left=left, color=col, label=c)
        left += causes[c].values
    for k, r in causes.iterrows():
        b.text(r["within 45 min"] / 2, y[k], f"{r['within 45 min']:.1%}", ha="center", va="center",
               fontsize=8, color="w")
    b.set_yticks(y, causes.config, fontsize=8)
    b.axvline(0.90, color="k", ls="--", lw=1)
    b.set_xlim(0, 1); b.set_xlabel("share of emergency orders")
    b.set_title("what happens to emergency orders", fontsize=9)
    b.legend(fontsize=7, loc="upper center", bbox_to_anchor=(0.45, -0.15), ncol=3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
