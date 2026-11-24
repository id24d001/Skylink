"""M5 discrete-event simulator (SimPy) for one day of SkyLink operations.

Brief, M5: "The MILP uses averages. Build a simulator (SimPy or similar) for a day of operations.
Entities: orders (Poisson by class and hour), drones, batteries, charging bays, hubs. Events: order
arrival, dispatch, take-off, delivery, return, battery swap, charge complete, grounding by gust,
outage start and end. Dispatch: emergencies first, with a reserve fraction of idle drones held
back for them."

Rules used, with their source:
  orders      Poisson; rate of PHC i, class c, hour h = demand.csv[i, c] x profile[c, h]
              (hourly_profile.csv rescaled to sum to 1 per class in M0).            brief, data
  sortie      one order = one sortie from the PHC's M4 hub (as in M4).               M4
  ground      load and take-off 3 min; drop or pick-up 1 min; battery swap and
              turnaround 6 min.                                                      costs.yaml
  hover       60 s at each end of the sortie (eq. 5).                                brief eq. 5
  wind        out-and-back time 2LV/(V^2 - w^2), used for both time and energy,
              w = that hour's mean wind.                                             brief, wind
  grounding   no take-offs in any hour with gust >= 14 m/s.                         costs.yaml
  range       a sortie takes off only if its energy at that hour's wind fits the
              usable energy E_use (eq. 6, includes the 15 % reserve).               brief eq. 5-6
  emergency   still waiting after 30 min -> road, delivered 120 min after leaving.  costs.yaml
  S45         share of emergencies delivered within 45 min of the order, any mode.  brief M5
  charging    bay 1.5 kW, 90 % efficient; a pack is recharged by the energy used.   costs.yaml
  outage      Mon, Wed, Fri 14:00-17:00: the 3 kW genset runs at most 2 bays.       costs.yaml
  dispatch    emergencies first (FIFO), then routine and lab (FIFO); a routine or
              lab order may not take one of the last k idle drones (the reserve).   brief M5
Choices the brief leaves open (agreed with the user, listed on the M5 card):
  - each simulated day starts at 00:00 with drones idle, packs full and queues empty;
  - wind_days.csv day 1 is a Monday (sets the outage days);
  - routine and lab orders still waiting at 24:00 go by road ("same day");
  - spare packs per hub = 0 by default (as M4 costed); a drone waits for a charged pack;
  - reserve k = round-half-up(0.25 x drones at the hub);
  - a sortie that cannot make the trip in that hour's wind waits, like a grounded one;
  - a PHC that is itself the hub (P09 at P09) is served by hand-over, no sortie (as in M4).
"""
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import simpy

from common import load_costs, load_csv

CLASSES = ("emergency", "routine", "lab")
DAY = 1440.0
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


# ---------------------------------------------------------------- inputs
@dataclass
class Vehicle:
    """One M1 design (surrogate-FM set)."""
    name: str
    V: float              # cruise speed, m/s
    E_use: float          # usable energy per pack, Wh (eq. 6, with the 15 % reserve)
    P_hl: float           # hover power loaded / empty, W
    P_he: float
    P_cl: float           # cruise power loaded / empty, W
    P_ce: float

    @classmethod
    def from_row(cls, r):
        return cls(r["design"], float(r["V"]), float(r["E_use_Wh"]), float(r["P_hover_loaded"]),
                   float(r["P_hover_empty"]), float(r["P_cruise_loaded"]), float(r["P_cruise_empty"]))

    def leg_min(self, L_km, w):
        """One-way leg time (min): half of the brief's out-and-back 2LV/(V^2 - w^2)."""
        if w >= self.V:
            return math.inf
        return L_km * 1000 * self.V / (self.V ** 2 - w ** 2) / 60

    def sortie_Wh(self, L_km, w):
        """Energy of one sortie: cruise one leg loaded and one empty, 60 s hover at each end."""
        t = self.leg_min(L_km, w) * 60
        return ((self.P_cl + self.P_ce) * t + 60 * (self.P_hl + self.P_he)) / 3600


@dataclass
class HubSpec:
    name: str
    drones: int
    bays: int
    spares: int = 0                       # spare packs beyond the one in each drone
    reserve: int = 0                      # idle drones held back for emergencies
    phcs: list = field(default_factory=list)   # [(phc, L_km, (em, routine, lab) per day)]


def reserve_count(n, frac):
    """Drones held back: round-half-up(frac x n)."""
    return int(math.floor(frac * n + 0.5))


def network_from_m4(design, ctx=None, spares=0, reserve_frac=0.25, extra_drones=None, bays=None):
    """HubSpecs for one M4 design, from ctx["m4_network"] or results/m4_*.csv."""
    from common import RESULTS
    net = (ctx or {}).get("m4_network", {}).get(design)
    if net is not None:
        hubs, asg = net["hubs"], net["assignments"]
    else:
        hubs = pd.read_csv(RESULTS / "m4_hubs.csv").query("design == @design")
        asg = pd.read_csv(RESULTS / "m4_assignments.csv").query("design == @design")
    out = []
    for _, h in hubs.iterrows():
        n = int(h.drones) + (extra_drones or {}).get(h.hub, 0)
        b = int(h.bays) if bays is None else int(bays.get(h.hub, h.bays))
        sp = spares if np.isscalar(spares) else spares.get(h.hub, 0)
        a = asg[asg.hub == h.hub]
        phcs = [(r.phc, float(r.flight_km), (r.emergency_per_day, r.routine_per_day, r.lab_per_day))
                for r in a.itertuples()]
        out.append(HubSpec(h.hub, n, b, int(sp), reserve_count(n, reserve_frac), phcs))
    return out


def vehicle_for(design, ctx=None):
    from common import RESULTS
    d = (ctx or {}).get("m1_fm_designs")
    d = pd.read_csv(RESULTS / "m1_fm_designs.csv") if d is None else d
    return Vehicle.from_row(d[d.design == design].iloc[0])


def hourly_profile(ctx=None):
    """(3, 24) fractions per class, each summing to 1 (M0 rescaling of the planted error)."""
    hp = (ctx or {}).get("hourly_profile")
    if hp is None:
        from m0_framing.run import clean_hourly_profile
        hp = clean_hourly_profile(load_csv("hourly_profile.csv"))
    hp = hp.set_index("hour") if "hour" in hp.columns else hp
    f = np.stack([hp[f"{c}_frac"].values for c in CLASSES]).astype(float)
    assert np.allclose(f.sum(axis=1), 1, atol=1e-9)
    return f


_WIND = None


def wind_day(day):
    """(mean wind, gust) for the 24 hours of day 1..365 of wind_days.csv."""
    global _WIND
    if _WIND is None:
        _WIND = load_csv("wind_days.csv").sort_values(["day", "hour"])
    w = _WIND[_WIND.day == day]
    assert len(w) == 24
    return w.mean_wind_ms.values.astype(float), w.gust_ms.values.astype(float)


def weekday(day):
    """Assumption (M5 card): day 1 of wind_days.csv is a Monday."""
    return WEEKDAYS[(day - 1) % 7]


@dataclass
class Rules:
    """Operating rules from costs.yaml; switches let the verification turn parts off."""
    load_takeoff: float = 3.0
    drop: float = 1.0
    hover: float = 1.0                    # 60 s at each end (eq. 5)
    swap: float = 6.0
    gust_limit: float = 14.0
    em_target: float = 45.0
    em_road_after: float = 30.0
    road_min: tuple = (120.0, 180.0, 180.0)
    bay_kW: float = 1.5
    charge_eff: float = 0.9
    outage_bays: int = 2                  # 3 kW genset / 1.5 kW bays
    outage_days: tuple = ("Mon", "Wed", "Fri")
    outage_start: float = 14 * 60.0
    outage_end: float = 17 * 60.0
    road_rule: bool = True                # 30 min emergency -> road
    end_of_day_road: bool = True          # routine/lab left at 24:00 -> road
    batteries: bool = True                # packs and bays modelled
    mmc_service_mean: float = None        # verification: exponential drone time, nothing else

    @classmethod
    def from_costs(cls):
        c = load_costs()
        o, b, g = c["operations"], c["charging_bay"], c["grid_outage"]
        rt = c["road_fallback"]["delivery_time_min"]
        return cls(load_takeoff=o["load_and_takeoff_min"], drop=o["drop_or_pickup_min"],
                   swap=o["battery_swap_turnaround_min"], gust_limit=o["gust_grounding_limit_ms"],
                   em_target=o["emergency_target_min"],
                   em_road_after=o["emergency_max_wait_before_road_min"],
                   road_min=(rt["emergency"], rt["routine"], rt["lab"]), bay_kW=b["power_kW"],
                   charge_eff=b["charge_efficiency"],
                   outage_bays=int(g["genset_kW_per_hub"] // b["power_kW"]),
                   outage_days=tuple(g["days"]), outage_start=60.0 * int(g["start"][:2]),
                   outage_end=60.0 * int(g["end"][:2]))


# ---------------------------------------------------------------- orders
def poisson_orders(hubs, profile, rng, horizon_h=24):
    """Orders as (time_min, hub, phc, L_km, class); Poisson count per (PHC, class, hour), uniform
    times within the hour = a non-homogeneous Poisson process with piecewise-constant rate."""
    rows = []
    for h in hubs:
        for phc, L, lam in h.phcs:
            for c in range(3):
                rate = lam[c] * profile[c]                       # (24,) orders per hour
                for hr in range(horizon_h):
                    k = rng.poisson(rate[hr % 24])
                    for t in rng.uniform(60 * hr, 60 * (hr + 1), k):
                        rows.append((float(t), h.name, phc, L, c))
    rows.sort()
    return rows


# ---------------------------------------------------------------- simulator
class Order:
    __slots__ = ("id", "t", "hub", "phc", "L", "c", "dispatch", "delivered", "mode", "takeoff",
                 "cause")

    def __init__(self, i, t, hub, phc, L, c):
        self.id, self.t, self.hub, self.phc, self.L, self.c = i, t, hub, phc, L, c
        self.dispatch = self.delivered = self.takeoff = None
        self.mode = self.cause = None


class Hub:
    def __init__(self, sim, spec):
        env, r = sim.env, sim.rules
        self.sim, self.spec, self.name = sim, spec, spec.name
        self.idle = spec.drones
        self.em_q, self.ot_q = [], []
        self.busy_min = 0.0
        self.fly_min = 0.0
        self.sorties = 0
        self.wait_pack_min = 0.0
        if r.batteries and r.mmc_service_mean is None:
            self.bays = simpy.PreemptiveResource(env, capacity=max(spec.bays, 1))
            self.charged = simpy.Container(env, init=spec.spares, capacity=10 ** 6)
        self.charging = 0
        self.n_wait_pack = 0


class DaySim:
    """One simulated day (or a long run, for the M/M/c check)."""

    def __init__(self, hubs, vehicle, rules, wind=None, gust=None, outage=False, trace=False):
        self.env = simpy.Environment()
        self.rules, self.veh = rules, vehicle
        self.wind = np.zeros(24) if wind is None else np.asarray(wind, float)
        self.gust = np.zeros(24) if gust is None else np.asarray(gust, float)
        self.outage = outage
        self.hubs = {s.name: Hub(self, s) for s in hubs}
        self.orders = []
        self.closed = False                       # 24:00 passed: routine/lab no longer flown
        self.grid_Wh = self.genset_Wh = 0.0
        self.trace = [] if trace else None
        self.rng = None

    # ---- helpers
    def hour(self, t):
        return min(int(t // 60), 23) if t < DAY else 23

    def flyable(self, t):
        return self.gust[self.hour(t)] < self.rules.gust_limit

    def in_outage(self, t):
        return self.outage and self.rules.outage_start <= t < self.rules.outage_end

    def log(self, kind, **kw):
        if self.trace is not None:
            self.trace.append(dict(t=self.env.now, kind=kind, **kw))

    # ---- dispatch (emergencies first; reserve held back from routine and lab)
    def can_fly(self, o, t):
        r = self.rules
        if r.mmc_service_mean is not None or o.L <= 0:
            return True
        t_off = t + r.load_takeoff
        if not (self.flyable(t) and self.flyable(t_off)):
            return False
        return self.veh.sortie_Wh(o.L, self.wind[self.hour(t_off)]) <= self.veh.E_use + 1e-9

    def road_cause(self, o):
        """Why an emergency went by road: gust grounding during its 30 min wait, else the
        headwind put the PHC out of range, else no drone or charged pack was free."""
        ts = np.arange(o.t, o.t + self.rules.em_road_after + 1e-9, 1.0)
        if any(not self.flyable(t) for t in ts):
            return "gust grounding"
        if any(self.veh.sortie_Wh(o.L, self.wind[self.hour(t)]) > self.veh.E_use for t in ts):
            return "headwind range"
        return "no drone or pack free"

    def try_dispatch(self, hub):
        now = self.env.now
        while hub.idle > 0:
            pick = next((o for o in hub.em_q if self.can_fly(o, now)), None)
            q = hub.em_q
            if pick is None and not self.closed and hub.idle > hub.spec.reserve:
                pick = next((o for o in hub.ot_q if self.can_fly(o, now)), None)
                q = hub.ot_q
            if pick is None:
                return
            q.remove(pick)
            hub.idle -= 1
            pick.dispatch = now
            self.env.process(self.sortie(hub, pick))
        self.log("queue", hub=hub.name, em=len(hub.em_q), ot=len(hub.ot_q), idle=hub.idle)

    # ---- processes
    def arrivals(self, orders):
        for i, (t, hub, phc, L, c) in enumerate(orders):
            yield self.env.timeout(t - self.env.now)
            o = Order(i, t, hub, phc, L, c)
            self.orders.append(o)
            h = self.hubs[hub]
            if L <= 0 and self.rules.mmc_service_mean is None:        # hub's own PHC: hand-over
                o.dispatch = o.delivered = t
                o.mode = "handover"
                continue
            (h.em_q if c == 0 else h.ot_q).append(o)
            self.log("order", hub=hub, phc=phc, c=c)
            if c == 0 and self.rules.road_rule:
                self.env.process(self.emergency_timer(h, o))
            self.try_dispatch(h)

    def emergency_timer(self, hub, o):
        yield self.env.timeout(self.rules.em_road_after)
        if o in hub.em_q:                                              # still waiting -> road
            hub.em_q.remove(o)
            o.mode = "road"
            o.cause = self.road_cause(o)
            o.delivered = self.env.now + self.rules.road_min[0]
            self.log("road", hub=hub.name, phc=o.phc, c=0)

    def hour_ticks(self, until):
        """Grounding and wind change on the hour: re-try dispatch at every hub."""
        t = 60.0
        while t <= until:
            yield self.env.timeout(t - self.env.now)
            if self.trace is not None:
                self.log("hour", grounded=not self.flyable(self.env.now),
                         wind=float(self.wind[self.hour(self.env.now)]))
            for h in self.hubs.values():
                self.try_dispatch(h)
            t += 60.0

    def end_of_day(self):
        yield self.env.timeout(DAY - self.env.now)
        if not self.rules.end_of_day_road:
            return
        self.closed = True
        for h in self.hubs.values():
            for o in h.ot_q:
                o.mode = "road"
                o.delivered = DAY + self.rules.road_min[o.c]
            h.ot_q.clear()

    def outage_proc(self):
        r = self.rules
        if not (self.outage and r.batteries and r.mmc_service_mean is None):
            return
        yield self.env.timeout(r.outage_start - self.env.now)
        self.log("outage", on=True)
        blocks = []
        for h in self.hubs.values():
            extra = h.spec.bays - r.outage_bays
            for _ in range(max(extra, 0)):
                req = h.bays.request(priority=0, preempt=True)
                blocks.append((h, req))
        yield self.env.timeout(r.outage_end - self.env.now)
        for h, req in blocks:
            h.bays.release(req)
        self.log("outage", on=False)

    def charge(self, hub, Wh_pack):
        """Recharge a pack by the energy it used; bay 1.5 kW at 90 % efficiency."""
        r = self.rules
        remaining = Wh_pack / r.charge_eff                              # Wh from the grid/genset
        rate = r.bay_kW * 1000 / 60                                     # Wh per minute
        while remaining > 1e-9:
            with hub.bays.request(priority=1, preempt=False) as req:
                yield req
                hub.charging += 1
                self.log("bays", hub=hub.name, charging=hub.charging)
                try:
                    while remaining > 1e-9:
                        now = self.env.now
                        bounds = [b for b in (r.outage_start, r.outage_end) if self.outage and b > now]
                        dt = min([remaining / rate] + [b - now for b in bounds])
                        t0 = now
                        yield self.env.timeout(dt)
                        used = (self.env.now - t0) * rate
                        remaining -= used
                        self._energy(t0, used)
                except simpy.Interrupt:                                 # outage pre-empted the bay
                    used = (self.env.now - t0) * rate
                    remaining -= used
                    self._energy(t0, used)
                finally:
                    hub.charging -= 1
                    self.log("bays", hub=hub.name, charging=hub.charging)
        hub.charged.put(1)
        self.log("charge_done", hub=hub.name)

    def _energy(self, t0, Wh):
        if self.in_outage(t0):
            self.genset_Wh += Wh
        else:
            self.grid_Wh += Wh

    def sortie(self, hub, o):
        env, r, v = self.env, self.rules, self.veh
        t0 = env.now
        if r.mmc_service_mean is not None:                             # verification mode
            yield env.timeout(self.rng.exponential(r.mmc_service_mean))
            o.delivered, o.mode = env.now, "drone"
        else:
            w = self.wind[self.hour(t0 + r.load_takeoff)]
            leg = v.leg_min(o.L, w)
            yield env.timeout(r.load_takeoff)
            o.takeoff = env.now
            self.log("takeoff", hub=hub.name, phc=o.phc, c=o.c, leg=leg, id=o.id)
            yield env.timeout(leg + r.hover + r.drop)                  # out, hover, drop/pick-up
            if o.c != 2:
                o.delivered, o.mode = env.now, "drone"
                self.log("delivered", hub=hub.name, phc=o.phc, c=o.c)
            yield env.timeout(leg + r.hover)                           # back, hover at the hub
            if o.c == 2:
                o.delivered, o.mode = env.now, "drone"
            hub.fly_min += 2 * leg + 2 * r.hover
            hub.sorties += 1
            self.log("return", hub=hub.name, phc=o.phc, id=o.id)
            if r.batteries:
                env.process(self.charge(hub, v.sortie_Wh(o.L, w)))    # pack goes on charge
                tw = env.now
                hub.n_wait_pack += 1
                yield hub.charged.get(1)
                hub.n_wait_pack -= 1                               # battery swap needs a charged pack
                hub.wait_pack_min += env.now - tw
                yield env.timeout(r.swap)
            else:
                yield env.timeout(r.swap)
        hub.busy_min += env.now - t0
        hub.idle += 1
        self.log("idle", hub=hub.name, idle=hub.idle)
        self.try_dispatch(hub)

    def sampler(self, every=2.0, until=DAY + 30):
        """Trace mode only: state of every hub every 2 min, for the live view."""
        while self.env.now <= until:
            self.log("snap", hubs={h.name: dict(em=len(h.em_q), ot=len(h.ot_q), idle=h.idle,
                                                charging=h.charging, wait_pack=h.n_wait_pack,
                                                packs=h.charged.level if hasattr(h, "charged") else 0)
                                   for h in self.hubs.values()})
            yield self.env.timeout(every)

    def run(self, orders, horizon=DAY, rng=None):
        self.rng = rng
        env = self.env
        if self.trace is not None:
            env.process(self.sampler())
        env.process(self.arrivals(orders))
        env.process(self.hour_ticks(horizon + 120))
        env.process(self.end_of_day())
        env.process(self.outage_proc())
        env.run()
        return self


# ---------------------------------------------------------------- one day -> KPIs
def simulate_day(hubs, vehicle, rules, profile, day, seed, trace=False):
    """Simulate wind day `day` (1..365) with demand stream `seed`. Returns (kpis dict, sim)."""
    wind, gust = wind_day(day)
    wd = weekday(day)
    rng = np.random.default_rng(seed)
    orders = poisson_orders(hubs, profile, rng)
    sim = DaySim(hubs, vehicle, rules, wind, gust, outage=wd in rules.outage_days, trace=trace)
    sim.run(orders, rng=rng)
    return day_kpis(sim, day, wd, gust, rules), sim


def day_kpis(sim, day, wd, gust, rules):
    od = sim.orders
    em = [o for o in od if o.c == 0]
    t_em = np.array([o.delivered - o.t for o in em])
    n_drones = sum(h.spec.drones for h in sim.hubs.values())
    busy = sum(h.busy_min for h in sim.hubs.values())
    k = dict(day=day, weekday=wd, grounded_h=int((gust >= rules.gust_limit).sum()),
             orders=len(od), emergencies=len(em),
             S45=float(np.mean(t_em <= rules.em_target)) if len(em) else np.nan,
             em_road=sum(o.mode == "road" for o in em),
             road=sum(o.mode == "road" for o in od),
             road_routine=sum(o.mode == "road" and o.c == 1 for o in od),
             road_lab=sum(o.mode == "road" and o.c == 2 for o in od),
             sorties=sum(h.sorties for h in sim.hubs.values()),
             utilisation=busy / (n_drones * DAY),
             flying_share=sum(h.fly_min for h in sim.hubs.values()) / (n_drones * DAY),
             wait_pack_min=sum(h.wait_pack_min for h in sim.hubs.values()),
             grid_kWh=sim.grid_Wh / 1000, genset_kWh=sim.genset_Wh / 1000)
    k["road_share"] = k["road"] / max(k["orders"], 1)
    k["em_times"] = t_em
    k["by_hub"] = {h.name: dict(busy_min=h.busy_min, fly_min=h.fly_min, sorties=h.sorties,
                                drones=h.spec.drones, wait_pack_min=h.wait_pack_min,
                                em=sum(o.hub == h.name for o in em),
                                em45=sum(o.hub == h.name and o.delivered - o.t <= rules.em_target
                                         for o in em),
                                road=sum(o.hub == h.name and o.mode == "road" for o in od))
                   for h in sim.hubs.values()}
    return k


def simulate_days(hubs, vehicle, rules, profile, days, seeds):
    return [simulate_day(hubs, vehicle, rules, profile, d, s)[0] for d, s in zip(days, seeds)]


def summarise(kp, label=""):
    """Network KPIs over simulated days: mean daily S45 with its 95 % CI, pooled P95 etc."""
    from scipy import stats
    s = np.array([k["S45"] for k in kp], float)
    n = len(s)
    hw = stats.t.ppf(0.975, n - 1) * s.std(ddof=1) / math.sqrt(n) if n > 1 else np.nan
    t_all = np.concatenate([k["em_times"] for k in kp])
    tot = lambda key: sum(k[key] for k in kp)
    return dict(label=label, days=n, S45_mean=s.mean(), S45_ci_half=hw,
                S45_pooled=float(np.mean(t_all <= 45)),
                P95_em_min=float(np.percentile(t_all, 95)), mean_em_min=float(t_all.mean()),
                em_road_share=tot("em_road") / tot("emergencies"),
                road_share=tot("road") / tot("orders"),
                road_routine_lab_share=(tot("road_routine") + tot("road_lab")) / max(
                    tot("orders") - tot("emergencies"), 1),
                utilisation=np.mean([k["utilisation"] for k in kp]),
                flying_share=np.mean([k["flying_share"] for k in kp]),
                grid_kWh_day=tot("grid_kWh") / n, genset_kWh_day=tot("genset_kWh") / n,
                orders_day=tot("orders") / n, sorties_day=tot("sorties") / n)
