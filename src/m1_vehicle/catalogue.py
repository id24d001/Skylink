"""Snap a continuous M1 design to real parts (components.csv) and re-check every constraint.

Parts:
  battery  1-4 identical 6S packs in parallel, E = n x mAh x 22.2 V, total E <= 2000 Wh;
           the catalogue pack mass replaces E / e_b.
  motor    the five catalogue motors; the catalogue mass replaces 0.12 + 0.004 T_max, and
           T_max = 2 m g / 4 must not exceed the motor's rating.
  tube     the 28 catalogue tubes (mass per metre x L replaces rho_cf A L).
  rotor    D in whole inches (14-31 in), since propellers are sold in inch sizes.
  speed    V is an operating setting, re-chosen on a 0.5 m/s grid for each part set.

Every combination is evaluated (about 0.6 M designs x 29 speeds, vectorised), and the lightest
one that meets every constraint is kept. With catalogue motor masses there is no mass coupling
left, but we still close m with the same fixed-point MDA (it converges in one step).
"""
import itertools

import numpy as np
import pandas as pd

from common import load_csv
from .mdo import Problem, T_ORACLE
from .model import G, Params, analyse, mda, LB, UB

INCH = 0.0254
V_GRID = np.arange(8.0, 22.0 + 1e-9, 0.5)
D_GRID = np.arange(14, 32) * INCH          # 0.356-0.787 m, inside 0.35-0.80


def load_parts():
    c = load_csv("components.csv")
    bat = c[c.category == "battery"].copy()
    bat["E_Wh"] = bat.capacity * bat.nominal_voltage_V / 1000
    rows = []
    for _, b in bat.iterrows():
        for k in range(1, 5):
            if k * b.E_Wh <= 2000 + 1e-9:
                rows.append(dict(battery=f"{k}x{b.part_id}", E=k * b.E_Wh, m_batt=k * b.mass_kg,
                                 cost_batt=k * b.unit_cost_inr))
    batteries = pd.DataFrame(rows)
    motors = c[c.category == "motor"][["part_id", "max_thrust_N", "mass_kg", "unit_cost_inr"]]
    tubes = c[c.category == "tube"][["part_id", "outer_dia_mm", "wall_mm", "mass_kg", "unit_cost_inr"]]
    return batteries, motors.reset_index(drop=True), tubes.reset_index(drop=True)


def _evaluate_grid(prob, batteries, motors, tubes):
    """Vectorised evaluation of every (D, battery, motor, tube, V) combination."""
    p = prob.p
    Di, Bi, Mi, Ti, Vi = np.meshgrid(np.arange(len(D_GRID)), np.arange(len(batteries)),
                                     np.arange(len(motors)), np.arange(len(tubes)),
                                     np.arange(len(V_GRID)), indexing="ij")
    D = D_GRID[Di]
    E = batteries.E.values[Bi]
    d = tubes.outer_dia_mm.values[Ti] / 1000
    t = tubes.wall_mm.values[Ti] / 1000
    V = V_GRID[Vi]
    L = 0.78 * D - 0.08
    m_arm = tubes.mass_kg.values[Ti] * L
    m_mot = motors.mass_kg.values[Mi]
    m = mda([D, E, d, t, V], p, m_motor=m_mot, m_batt=batteries.m_batt.values[Bi], m_arm=m_arm,
            tol=np.inf)   # no coupling with catalogue motors: one pass is exact
    r = analyse([D, E, d, t, V], m, p, prob.wind, m_motor=m_mot, m_arm=m_arm)
    g = prob.g_from(r)
    g[5] = 1 - r["T_max"] / motors.max_thrust_N.values[Mi]   # chosen motor's own rating
    feasible = np.all(g >= -1e-9, axis=0)
    return dict(idx=(Di, Bi, Mi, Ti, Vi), r=r, g=g, feasible=feasible, m=m)


def snap(prob, x_cont=None):
    """Lightest catalogue design meeting every constraint of `prob`. Returns a dict row."""
    batteries, motors, tubes = load_parts()
    grid = _evaluate_grid(prob, batteries, motors, tubes)
    if not grid["feasible"].any():
        return None
    m = np.where(grid["feasible"], grid["m"], np.inf)
    k = np.unravel_index(np.argmin(m), m.shape)
    Di, Bi, Mi, Ti, Vi = (a[k] for a in grid["idx"])
    b, mo, tu = batteries.iloc[Bi], motors.iloc[Mi], tubes.iloc[Ti]
    x = np.array([D_GRID[Di], b.E, tu.outer_dia_mm / 1000, tu.wall_mm / 1000, V_GRID[Vi]])
    parts = dict(m_motor=mo.mass_kg, m_batt=b.m_batt, m_arm=tu.mass_kg * (0.78 * x[0] - 0.08))
    row = recheck(prob, x, parts, mo.max_thrust_N)
    L = 0.78 * x[0] - 0.08
    row.update(battery=b.battery, motor=mo.part_id, tube=tu.part_id,
               prop_in=int(round(x[0] / INCH)),
               parts_cost_inr=float(b.cost_batt + 4 * mo.unit_cost_inr + 4 * L * tu.unit_cost_inr),
               n_feasible_combos=int(grid["feasible"].sum()))
    return row


def recheck(prob, x, parts, motor_rating):
    """Re-run every constraint on a snapped design, scalar code path (independent of the grid)."""
    p = prob.p
    m = mda(x, p, **parts)
    r = analyse(x, m, p, prob.wind, m_motor=parts["m_motor"], m_arm=parts["m_arm"])
    g = prob.g_from(r)
    g[5] = 1 - r["T_max"] / motor_rating
    row = {k: float(np.real(v)) for k, v in r.items()}
    row.update(D=x[0], E=x[1], d=x[2], t=x[3], V=x[4], motor_rating_N=motor_rating,
               min_margin=float(g.min()), all_feasible=bool(g.min() >= -1e-9),
               binding=prob.con_names[int(np.argmin(g))])
    for name, gi in zip(prob.con_names, g):
        row["g: " + name] = float(gi)
    return row


def snap_max_radius(prob):
    """Catalogue design with the longest wind-adjusted radius that meets every other constraint."""
    batteries, motors, tubes = load_parts()
    grid = _evaluate_grid(prob, batteries, motors, tubes)
    ok = np.all(grid["g"][1:] >= -1e-9, axis=0)
    R = np.where(ok, np.real(grid["r"]["R_wind_km"]), -np.inf)
    return float(R.max())
