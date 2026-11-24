"""M5 verification.

1. Brief: "reduce to one hub, exponential service times, no wind, no outage; compare mean waiting
   time with the M/M/c (Erlang C) formula."  -> mmc_table()
2. Brief: "Run enough simulated days that the 95% confidence interval on S45 is narrower than
   +-3 percentage points, and explain how you chose the days."  -> choose_days()
3. Extra checks of the event logic (not asked for, cheap): a sortie timed by hand, the reserve
   rule, no take-off in a grounded hour, at most 2 bays charging in an outage, every order ends
   delivered.  -> hand_check(), rule_checks()
"""
import math

import numpy as np
import pandas as pd
from scipy import stats

from .model import (DAY, DaySim, HubSpec, Rules, simulate_day, summarise, weekday, wind_day)


# ---------------------------------------------------------------- 1. Erlang C
def erlang_c_wq(lam, mu, c):
    """Mean wait in queue of an M/M/c queue (min), Erlang C."""
    a = lam / mu
    rho = a / c
    assert rho < 1
    top = a ** c / math.factorial(c) / (1 - rho)
    pw = top / (sum(a ** k / math.factorial(k) for k in range(c)) + top)
    return pw / (c * mu - lam), pw


def mmc_sim(c, rho, service_mean, n_orders, seed, n_batches=20, warmup=0.02):
    """One hub, c drones, Poisson arrivals, exponential drone time, nothing else (no wind, no
    outage, no batteries, no road rule, no reserve, one class). Mean wait by batch means."""
    lam = rho * c / service_mean
    rng = np.random.default_rng(seed)
    t = np.cumsum(rng.exponential(1 / lam, n_orders))
    hub = HubSpec("X", c, 1, 0, 0, [("Y", 1.0, (0, 1, 0))])
    rules = Rules(mmc_service_mean=service_mean, road_rule=False, end_of_day_road=False,
                  batteries=False)
    orders = [(float(ti), "X", "Y", 1.0, 1) for ti in t]
    sim = DaySim([hub], None, rules).run(orders, horizon=float(t[-1]), rng=rng)
    w = np.array([o.dispatch - o.t for o in sim.orders])[int(warmup * n_orders):]
    b = np.array([x.mean() for x in np.array_split(w, n_batches)])
    hw = stats.t.ppf(0.975, n_batches - 1) * b.std(ddof=1) / math.sqrt(n_batches)
    return w.mean(), hw, float(np.mean(w > 1e-9))


def mmc_table(service_mean, cases=((1, 0.5), (1, 0.7), (2, 0.5), (2, 0.7), (2, 0.9),
                                   (3, 0.5), (3, 0.7), (3, 0.9)), n_orders=60000, seed=7):
    rows = []
    for k, (c, rho) in enumerate(cases):
        n = n_orders * (3 if rho >= 0.9 else 1)
        wq_sim, hw, pw_sim = mmc_sim(c, rho, service_mean, n, seed + k)
        wq, pw = erlang_c_wq(rho * c / service_mean, 1 / service_mean, c)
        rows.append(dict(drones_c=c, utilisation_rho=rho, service_mean_min=service_mean,
                         orders=n, Wq_erlangC_min=wq, Wq_sim_min=wq_sim, Wq_sim_ci95_half=hw,
                         rel_error=(wq_sim - wq) / wq, P_wait_erlangC=pw, P_wait_sim=pw_sim,
                         within_ci=abs(wq_sim - wq) <= hw))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 2. how many days
def choose_days(hubs, veh, rules, profile, target=0.03, pilot=30, seed=2026, max_days=730):
    """Sequential rule: days are drawn at random (with replacement) from the 365 wind days, each
    with its own demand stream. Run a pilot, estimate the day-to-day SD of S45, set
    n = ceil((t_{0.975,n-1} s / target)^2), run the extra days, repeat until the half-width of
    the 95 % CI on mean daily S45 is below the target."""
    rng = np.random.default_rng(seed)
    pool = rng.integers(1, 366, max_days)
    kp, steps, n = [], [], pilot
    while True:
        for i in range(len(kp), n):
            kp.append(simulate_day(hubs, veh, rules, profile, int(pool[i]), seed + i)[0])
        s = np.array([k["S45"] for k in kp])
        sd = s.std(ddof=1)
        hw = stats.t.ppf(0.975, n - 1) * sd / math.sqrt(n)
        need = math.ceil((stats.t.ppf(0.975, n - 1) * sd / target) ** 2)
        steps.append(dict(step=len(steps) + 1, days=n, S45_mean=s.mean(), S45_sd=sd,
                          ci95_half=hw, days_needed=need, met=hw < target))
        if hw < target or n >= max_days:
            break
        n = min(max(need, n + 1), max_days)
    days = [int(d) for d in pool[:n]]
    seeds = [seed + i for i in range(n)]
    extend = lambda m: ([int(d) for d in pool[:m]], [seed + i for i in range(m)])
    return days, seeds, kp, pd.DataFrame(steps), extend


def days_needed(s, target=0.03):
    """Days for a 95 % CI half-width below target, from the daily S45 values s."""
    n = len(s)
    return math.ceil((stats.t.ppf(0.975, n - 1) * np.std(s, ddof=1) / target) ** 2)


# ---------------------------------------------------------------- 3. event-logic checks
def hand_check(veh, rules, L=10.0):
    """One routine order at 00:00, calm air, one drone, no spare pack: times by hand vs sim."""
    hub = HubSpec("X", 1, 1, 0, 0, [("Y", L, (0, 1, 0))])
    sim = DaySim([hub], veh, rules).run([(0.0, "X", "Y", L, 1)])
    o = sim.orders[0]
    h = sim.hubs["X"]
    leg = L * 1000 / veh.V / 60
    Wh = veh.sortie_Wh(L, 0.0)
    charge = Wh / rules.charge_eff / (rules.bay_kW * 1000 / 60)
    back = rules.load_takeoff + 2 * leg + 2 * rules.hover + rules.drop
    rows = [
        ("delivery time (min)", rules.load_takeoff + leg + rules.hover + rules.drop, o.delivered - o.t),
        ("sortie energy (Wh)", ((veh.P_cl + veh.P_ce) * leg * 60 + 60 * (veh.P_hl + veh.P_he)) / 3600, Wh),
        ("grid energy (Wh)", Wh / rules.charge_eff, sim.grid_Wh),
        ("drone busy, incl. recharge of its own pack + swap (min)", back + charge + rules.swap, h.busy_min),
        ("wait for a charged pack (min)", charge, h.wait_pack_min),
    ]
    df = pd.DataFrame(rows, columns=["quantity", "by_hand", "simulator"])
    df["abs_diff"] = (df.by_hand - df.simulator).abs()
    return df


def rule_checks(veh, rules, profile, hubs, days):
    rows = []

    def add(check, value, ok):
        rows.append(dict(check=check, value=value, status="ok" if ok else "FAIL"))

    # reserve: 2 drones, reserve 1 -> two routine orders at t=0 take one drone; an emergency at
    # t=1 takes the second
    hub = HubSpec("X", 2, 2, 2, 1, [("Y", 5.0, (1, 1, 0))])
    sim = DaySim([hub], veh, rules).run([(0.0, "X", "Y", 5.0, 1), (0.0, "X", "Y", 5.0, 1),
                                         (1.0, "X", "Y", 5.0, 0)])
    o = sim.orders
    add("reserve: 2nd routine order waits while 1 drone is held back (dispatch min)",
        o[1].dispatch, o[0].dispatch == 0.0 and o[1].dispatch > 1.0)
    add("reserve: emergency at t=1 takes the held-back drone at once (wait min)",
        o[2].dispatch - o[2].t, o[2].dispatch == 1.0)

    # grounding: on real days with grounded hours, no take-off in a grounded hour
    bad, n_off, n_days = 0, 0, 0
    for d in days:
        _, g = wind_day(d)
        if (g >= rules.gust_limit).any():
            n_days += 1
            _, s = simulate_day(hubs, veh, rules, profile, d, d)
            for x in s.orders:
                if x.takeoff is not None:
                    n_off += 1
                    bad += g[min(int(x.takeoff // 60), 23)] >= rules.gust_limit
    add(f"no take-off in a grounded hour ({n_days} days with grounding, {n_off} take-offs)", bad, bad == 0)

    # outage: 4 bays, 6 drones with spares, busy at 14:00 -> at most 2 bays charging 14-17
    hub = HubSpec("X", 6, 4, 6, 0, [("Y", 12.0, (0, 1, 0))])
    orders = [(800.0 + 5 * k, "X", "Y", 12.0, 1) for k in range(40)]
    sim = DaySim([hub], veh, rules, outage=True, trace=True).run(orders)
    during = [e["charging"] for e in sim.trace if e["kind"] == "bays"
              and rules.outage_start <= e["t"] < rules.outage_end]
    before = [e["charging"] for e in sim.trace if e["kind"] == "bays" and e["t"] < rules.outage_start]
    add("outage: max bays charging 14:00-17:00 (genset 3 kW / 1.5 kW)", max(during), max(during) <= 2)
    add("outage: max bays charging before 14:00 (all 4 usable)", max(before), max(before) <= 4)
    cap = 3.0 * (rules.outage_end - rules.outage_start) / 60
    add(f"outage: genset energy <= 3 kW x 3 h = {cap:.0f} kWh", round(sim.genset_Wh / 1000, 3),
        sim.genset_Wh / 1000 <= cap + 1e-9)
    add("conservation: every order delivered by drone, hand-over or road",
        sum(x.mode is None for x in sim.orders), all(x.mode for x in sim.orders))

    # conservation on real days
    miss = 0
    for d in days[:10]:
        _, s = simulate_day(hubs, veh, rules, profile, d, d)
        miss += sum(x.mode is None or x.delivered is None for x in s.orders)
    add("conservation on 10 real days: orders without an outcome", miss, miss == 0)

    # common random numbers: same day + seed -> identical result
    k1, _ = simulate_day(hubs, veh, rules, profile, days[0], 1)
    k2, _ = simulate_day(hubs, veh, rules, profile, days[0], 1)
    add("same day and seed give the same S45 (reproducible, CRN for M6)", k1["S45"], k1["S45"] == k2["S45"])
    return pd.DataFrame(rows)
