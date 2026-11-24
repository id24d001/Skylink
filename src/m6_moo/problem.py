"""M6 problem: decision vector -> M5 simulation on common random numbers -> objectives.

Brief, M6: "Decisions: vehicle design (from your M1 set), drones and bays per open hub, spare
batteries per hub, emergency reserve fraction. Objectives: annual cost (minimise), S45
(maximise), road-fallback share (minimise). Report the 95th-percentile delivery time too."

Decision vector for one design with H open hubs (the M4 hubs and assignments, fixed):
    [x_1, s_1, b_1, ..., x_H, s_H, b_H, r]
    x_h  extra drones over the M4 count       integer 0..3       -> drones n_h = n_h^M4 + x_h
    s_h  spare packs at hub h                 integer 0..2 n_h    (repair clips to 2 n_h)
    b_h  charging bays at hub h               integer 1..n_h+s_h (repair clips)
    r    emergency reserve fraction           continuous 0..0.5  -> k_h = round-half-up(r n_h)
The vehicle design (C, D or E) is the categorical variable; one NSGA-II run per design, fronts
merged (each design has its own M4 network, so the vector length differs).

Annual cost (INR/yr), the M4 cost model (costs.yaml), with the variable parts from the simulation:
    hubs       H (capex x CRF(10 %, 10 y) + opex)
    drones     N (200 000 + 45 000 m) (CRF(10 %, 3 y) + 0.15)
    bays       B 90 000 CRF(10 %, 5 y)
    operators  sum_h max(1, ceil(n_h / 4)) x 3 shifts x 1.2 relief x 420 000
    pack wear  365 x sorties/day x 22 INR/Wh x E / 500 cycles          (as M4)
    spares     S x 22 INR/Wh x E x 10 %  (capital tied up; wear is already per sortie; user-confirmed)
    energy     365 x (grid kWh/day x 9 + genset kWh/day x 28)          (simulated)
    road       365 x (emergency road trips x 2 500 + routine/lab road trips x 500) per day (simulated)
Constraint: cost <= 5.57 cr/yr (M0 cap).
"""
import math
from dataclasses import dataclass
from multiprocessing import get_context

import numpy as np
import pandas as pd

from common import RESULTS, load_costs
from m4_network.model import crf
from m5_simulation.model import (HubSpec, Rules, hourly_profile, network_from_m4, reserve_count,
                                 simulate_day, vehicle_for)

DESIGNS = ("C cover-all", "D 4-hub", "E 3-hub")
SHORT = {"C cover-all": "C", "D 4-hub": "D", "E 3-hub": "E"}
X_MAX, SPARE_PER_DRONE, R_MAX = 3, 2, 0.5
COST_CAP = 5.57e7                     # M0: INR 5.57 cr/yr


@dataclass(frozen=True)
class Config:
    """One repaired operating configuration (the cache key)."""
    design: str
    drones: tuple
    spares: tuple
    bays: tuple
    reserve: tuple                    # reserved drones per hub (from r)

    @property
    def label(self):
        j = lambda t: "-".join(map(str, t))
        return (f"{SHORT[self.design]} n{j(self.drones)} s{j(self.spares)} "
                f"b{j(self.bays)} k{j(self.reserve)}")


class Problem:
    def __init__(self, design, ctx=None):
        self.design = design
        self.base = network_from_m4(design, ctx, reserve_frac=0.25)
        self.H = len(self.base)
        self.n0 = np.array([h.drones for h in self.base])
        nmax = self.n0 + X_MAX
        lo, hi, isint = [], [], []
        for h in range(self.H):
            lo += [0, 0, 1]
            hi += [X_MAX, SPARE_PER_DRONE * nmax[h], nmax[h] * (1 + SPARE_PER_DRONE)]
            isint += [True, True, True]
        self.lo, self.hi = np.array(lo + [0.0], float), np.array(hi + [R_MAX], float)
        self.is_int = np.array(isint + [False])

    def repair(self, x):
        x = x.copy()
        for h in range(self.H):
            n = self.n0[h] + x[3 * h]
            x[3 * h + 1] = min(x[3 * h + 1], SPARE_PER_DRONE * n)
            x[3 * h + 2] = min(max(x[3 * h + 2], 1), n + x[3 * h + 1])
        return x

    def config(self, x):
        n = tuple(int(self.n0[h] + x[3 * h]) for h in range(self.H))
        s = tuple(int(x[3 * h + 1]) for h in range(self.H))
        b = tuple(int(x[3 * h + 2]) for h in range(self.H))
        k = tuple(reserve_count(nh, float(x[-1])) for nh in n)
        return Config(self.design, n, s, b, k)

    def genome(self, n=None, s=None, b=None, r=0.25):
        """Genome for a given configuration (defaults: M4 sizing, no spares, r = 0.25)."""
        n = self.n0 if n is None else np.asarray(n)
        s = np.zeros(self.H, int) if s is None else np.asarray(s)
        b = np.array([h.bays for h in self.base]) if b is None else np.asarray(b)
        x = []
        for h in range(self.H):
            x += [n[h] - self.n0[h], s[h], b[h]]
        return np.array(x + [r], float)

    def hubs(self, cfg):
        return [HubSpec(h.name, cfg.drones[i], cfg.bays[i], cfg.spares[i], cfg.reserve[i], h.phcs)
                for i, h in enumerate(self.base)]


# ---------------------------------------------------------------- cost model
def cost_terms(design, cfg, sim_means, ctx=None):
    """Annual cost breakdown (INR/yr). sim_means: per-day means of sorties, kWh, road trips."""
    c = load_costs()
    v = (ctx or {}).get("m1_fm_designs")
    v = pd.read_csv(RESULTS / "m1_fm_designs.csv") if v is None else v
    row = v[v.design == design].iloc[0]
    m, E = float(row.m), float(row.E)
    r = c["discount_rate"]
    hub, dr, bay, st = c["hub"], c["drone"], c["charging_bay"], c["staff"]
    pack, en, road = c["battery_pack"], c["energy"], c["road_fallback"]["cost_per_trip"]
    H, N, B, S = len(cfg.drones), sum(cfg.drones), sum(cfg.bays), sum(cfg.spares)
    ops = sum(max(1, math.ceil(n / st["drones_per_operator"])) for n in cfg.drones)
    pack_price = pack["cost_per_Wh"] * E
    return {
        "hubs": H * (hub["capex"] * crf(r, hub["life_years"]) + hub["opex_per_year"]),
        "drones": N * (dr["capex_fixed"] + dr["capex_per_kg_mtow"] * m)
                    * (crf(r, dr["life_years"]) + dr["maintenance_frac_of_capex_per_year"]),
        "bays": B * bay["capex"] * crf(r, bay["life_years"]),
        "operators": ops * st["shifts_per_day"] * st["relief_factor"] * st["salary_per_person_year"],
        "pack wear (500 cycles)": 365 * sim_means["sorties"] * pack_price / pack["cycle_life"],
        "spare packs (capital, 10 %)": S * pack_price * r,
        "energy": 365 * (sim_means["grid_kWh"] * en["grid_per_kWh"]
                         + sim_means["genset_kWh"] * en["genset_per_kWh"]),
        "road fallback": 365 * (sim_means["em_road"] * road["emergency"]
                                + sim_means["road_routine"] * road["routine"]
                                + sim_means["road_lab"] * road["lab"]),
    }


# ---------------------------------------------------------------- simulation (parallel, cached)
_W = {}


def _init_worker(designs):
    _W["rules"], _W["profile"] = Rules.from_costs(), hourly_profile()
    _W["veh"] = {d: vehicle_for(d) for d in designs}
    _W["prob"] = {d: Problem(d) for d in designs}


def _simulate(job):
    cfg, days, seeds = job
    p = _W["prob"][cfg.design]
    hubs = p.hubs(cfg)
    kp = [simulate_day(hubs, _W["veh"][cfg.design], _W["rules"], _W["profile"], d, s)[0]
          for d, s in zip(days, seeds)]
    return reduce_days(kp)


def reduce_days(kp):
    """Per-configuration KPIs over days, with the same definitions as M5's summarise()."""
    s = np.array([k["S45"] for k in kp], float)
    n = len(s)
    t = np.concatenate([k["em_times"] for k in kp])
    tot = lambda key: float(sum(k[key] for k in kp))
    return dict(days=n, S45=float(s.mean()), S45_sd=float(s.std(ddof=1)) if n > 1 else np.nan,
                S45_day=s, S45_pooled=float(np.mean(t <= 45)), P95=float(np.percentile(t, 95)),
                em_road_share=tot("em_road") / tot("emergencies"),
                road_share=tot("road") / tot("orders"),
                utilisation=float(np.mean([k["utilisation"] for k in kp])),
                means={key: tot(key) / n for key in ("sorties", "grid_kWh", "genset_kWh", "em_road",
                                                    "road_routine", "road_lab", "orders")})


class Evaluator:
    """Caches results by configuration; runs uncached ones in a process pool."""

    def __init__(self, days, seeds, ctx=None, workers=4, designs=DESIGNS):
        self.days, self.seeds, self.ctx = list(days), list(seeds), ctx
        self.cache = {}
        self.n_sim = 0
        self.pool = get_context("fork").Pool(workers, _init_worker, (designs,)) if workers > 1 else None
        if self.pool is None:
            _init_worker(designs)

    def close(self):
        if self.pool:
            self.pool.close()
            self.pool.join()

    def run(self, cfgs, days=None, seeds=None):
        days, seeds = days or self.days, seeds or self.seeds
        tag = (tuple(days), tuple(seeds))
        todo = list({c for c in cfgs if (c, tag) not in self.cache})
        jobs = [(c, days, seeds) for c in todo]
        out = self.pool.map(_simulate, jobs, chunksize=1) if self.pool else [_simulate(j) for j in jobs]
        for c, o in zip(todo, out):
            terms = cost_terms(c.design, c, o["means"], self.ctx)
            o["cost_terms"] = terms
            o["cost"] = sum(terms.values())
            self.cache[(c, tag)] = o
        self.n_sim += len(todo)
        return [self.cache[(c, tag)] for c in cfgs]

    def records(self):
        """Every configuration evaluated on the search days (the archive)."""
        tag = (tuple(self.days), tuple(self.seeds))
        return [(c, o) for (c, t), o in self.cache.items() if t == tag]


def objectives(o):
    """Minimised: cost (INR cr/yr), -S45, road share; constraint violation cost - cap."""
    return np.array([o["cost"] / 1e7, -o["S45"], o["road_share"]]), max(0.0, o["cost"] - COST_CAP) / 1e7


def crn_days(ctx=None):
    """M5's days and demand seeds (common random numbers)."""
    m5 = (ctx or {}).get("m5")
    if m5 is not None:
        return list(m5["days"]), list(m5["seeds"])
    # same draw as m5_simulation.verify.choose_days (seed 2026), 73 days as M5 settled on
    n = int(pd.read_csv(RESULTS / "m5_days_ci.csv").days.iloc[-1])
    pool = np.random.default_rng(2026).integers(1, 366, 730)
    return [int(d) for d in pool[:n]], [2026 + i for i in range(n)]
