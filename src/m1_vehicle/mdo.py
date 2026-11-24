"""M1 optimisation: minimise take-off mass for a required radius, by MDF and by IDF.

Optimiser variables are scaled to [0, 1] between the brief's bounds, so SLSQP sees
variables of similar size. Constraints are written as g(x) >= 0, each normalised by its
limit so a value of 0.01 means "1 % of margin left".
"""
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize
from scipy.stats import qmc

from .model import LB, UB, VARS, Params, analyse, mass_update, mda

T_MOTOR_MAX = 100.0      # largest motor in components.csv (M100), N
T_ORACLE = (8.0, 40.0)   # thrust range the M3 FM oracle accepts, N

CON_NAMES = [
    "radius >= required",
    "bending stress <= 200 MPa",
    "tip deflection <= 0.01 L",
    "f1 >= 1.15 x rotor frequency",
    "d/t <= 40",
    "T_max <= 100 N (largest motor)",
    "loaded hover thrust <= 40 N (M3 oracle)",
    "empty hover thrust >= 8 N (M3 oracle)",
]


@dataclass
class Problem:
    R_req_km: float              # required one-way radius in the design wind, km
    wind: float = 0.0            # headwind for the range check, m/s (0 = still air, as M1 uses)
    p: Params = field(default_factory=Params)
    oracle_range: bool = True    # keep hover thrust inside the oracle's 8-40 N
    mda_tol: float = 1e-12       # MDA stopping tolerance on |m_k+1 - m_k|, kg

    # -- scaling
    @staticmethod
    def to_x(z):
        return LB + np.asarray(z) * (UB - LB)

    @staticmethod
    def to_z(x):
        return (np.asarray(x) - LB) / (UB - LB)

    # -- constraint vector from analysis outputs
    def g_from(self, r):
        g = [
            r["R_wind_km"] / self.R_req_km - 1,
            1 - r["sigma_MPa"] / (self.p.sigma_allow / 1e6),
            1 - r["defl_mm"] / r["defl_lim_mm"],
            r["f1_Hz"] / r["f1_req_Hz"] - 1,
            1 - r["d_over_t"] / 40,
            1 - r["T_max"] / T_MOTOR_MAX,
        ]
        if self.oracle_range:
            g += [1 - r["T_hover_loaded"] / T_ORACLE[1], r["T_hover_empty"] / T_ORACLE[0] - 1]
        return np.array(g)

    @property
    def con_names(self):
        return CON_NAMES if self.oracle_range else CON_NAMES[:6]

    # ---------------------------------------------------------------- MDF
    def mdf_f(self, z):
        return mda(self.to_x(z), self.p, tol=self.mda_tol) / 10.0

    def mdf_g(self, z):
        x = self.to_x(z)
        return self.g_from(analyse(x, mda(x, self.p, tol=self.mda_tol), self.p, self.wind))

    # ---------------------------------------------------------------- IDF
    # w = [z (5 scaled design vars), m/10 (coupling variable)]
    def idf_f(self, w):
        return w[5]

    def idf_g(self, w):
        x, m = self.to_x(w[:5]), 10.0 * w[5]
        return self.g_from(analyse(x, m, self.p, self.wind))

    def idf_h(self, w):
        x, m = self.to_x(w[:5]), 10.0 * w[5]
        return np.atleast_1d((m - mass_update(x, m, self.p)) / 10.0)   # consistency m - m(x) = 0

    # ---------------------------------------------------------------- derivatives
    def jac(self, fun, w, h=1e-30):
        """Complex-step derivative: exact to machine precision, no step-size trade-off.
        Falls back to central differences if FM cannot take complex input (M3 surrogate)."""
        w = np.asarray(w, float)
        f0 = np.atleast_1d(fun(w))
        J = np.zeros((f0.size, w.size))
        for i in range(w.size):
            if self.p.complex_safe:
                wc = w.astype(complex)
                wc[i] += 1j * h
                J[:, i] = np.imag(np.atleast_1d(fun(wc))) / h
            else:
                e = np.zeros_like(w); e[i] = 1e-6
                J[:, i] = (np.atleast_1d(fun(w + e)) - np.atleast_1d(fun(w - e))) / 2e-6
        return J if J.shape[0] > 1 else J[0]


def _starts(n, dim, seed=0):
    return qmc.LatinHypercube(d=dim, seed=seed).random(n)


def solve_mdf(prob, n_starts=8, seed=0, z0=None):
    """MDF: the MDA runs inside every objective/constraint call. Multi-start SLSQP."""
    starts = [z0] if z0 is not None else _starts(n_starts, 5, seed)
    runs, best = [], None
    for s in starts:
        res = minimize(prob.mdf_f, s, jac=lambda z: prob.jac(prob.mdf_f, z), method="SLSQP",
                       bounds=[(0, 1)] * 5,
                       constraints=[{"type": "ineq", "fun": prob.mdf_g,
                                     "jac": lambda z: prob.jac(prob.mdf_g, z)}],
                       options={"ftol": 1e-12, "maxiter": 300})
        feas = res.success and prob.mdf_g(res.x).min() > -1e-7
        runs.append(dict(start=s, x=prob.to_x(res.x), m=10 * res.fun, success=feas, nit=res.nit))
        if feas and (best is None or res.fun < best.fun):
            best = res
    if best is None:
        raise RuntimeError(f"MDF found no feasible design for R_req = {prob.R_req_km} km")
    return best, runs


def solve_idf(prob, n_starts=8, seed=0):
    """IDF: mass is an optimiser variable; the consistency constraint m - m(x) = 0 closes the loop
    only at convergence, so no MDA is run inside the optimiser."""
    zs = _starts(n_starts, 5, seed)
    runs, best = [], None
    for s in zs:
        w0 = np.r_[s, 1.0]   # start every run at a 10 kg mass guess, deliberately inconsistent
        res = minimize(prob.idf_f, w0, jac=lambda w: prob.jac(prob.idf_f, w), method="SLSQP",
                       bounds=[(0, 1)] * 5 + [(0.2, 4.0)],
                       constraints=[{"type": "ineq", "fun": prob.idf_g,
                                     "jac": lambda w: prob.jac(prob.idf_g, w)},
                                    {"type": "eq", "fun": prob.idf_h,
                                     "jac": lambda w: np.atleast_2d(prob.jac(prob.idf_h, w))}],
                       options={"ftol": 1e-12, "maxiter": 500})
        feas = (res.success and prob.idf_g(res.x).min() > -1e-7
                and abs(prob.idf_h(res.x)[0]) < 1e-8)
        runs.append(dict(start=s, x=prob.to_x(res.x[:5]), m=10 * res.x[5], success=feas, nit=res.nit))
        if feas and (best is None or res.fun < best.fun):
            best = res
    if best is None:
        raise RuntimeError(f"IDF found no feasible design for R_req = {prob.R_req_km} km")
    return best, runs


def design_table_row(prob, x):
    r = analyse(x, mda(x, prob.p), prob.p, prob.wind)
    row = {v: float(xi) for v, xi in zip(VARS, x)}
    row.update({k: float(np.real(v)) for k, v in r.items()})
    return row


def solve_max_radius(prob, m_cap=None, n_starts=8, seed=0):
    """Maximise radius (in the problem's headwind, 0 = still air) (optionally with take-off mass <= m_cap).
    Used for the 'reach' end of the design set and to check the payload multiplier."""
    def f(z):
        x = prob.to_x(z)
        return -analyse(x, mda(x, prob.p), prob.p, prob.wind)["R_wind_km"] / 10

    def g(z):
        x = prob.to_x(z)
        r = analyse(x, mda(x, prob.p), prob.p, prob.wind)
        gg = prob.g_from(r)[1:]                          # drop the radius constraint
        if m_cap is not None:
            gg = np.r_[gg, 1 - r["m"] / m_cap]
        return gg

    best = None
    for s in _starts(n_starts, 5, seed):
        res = minimize(f, s, jac=lambda z: prob.jac(f, z), method="SLSQP", bounds=[(0, 1)] * 5,
                       constraints=[{"type": "ineq", "fun": g, "jac": lambda z: prob.jac(g, z)}],
                       options={"ftol": 1e-12, "maxiter": 300})
        if res.success and g(res.x).min() > -1e-7 and (best is None or res.fun < best.fun):
            best = res
    return prob.to_x(best.x), -10 * best.fun
