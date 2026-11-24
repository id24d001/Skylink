"""M1 verification required by the brief:
  1. MDA convergence plot,
  2. gradients checked by finite differences at two step sizes,
  3. MDF and IDF agree within 1 %,
plus a hand check of the analysis against an independent scalar re-implementation
(reviews/m1_independent_check.py, which caught the partner's radius error).
"""
import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .mdo import Problem, solve_idf, solve_mdf
from .model import G, Params, VARS, analyse, mda, mda_closed_form


# ---------------------------------------------------------------- 0. hand check
def hand_check(x):
    """Scalar, line-by-line evaluation of eqs. (1)-(6) with math only (no shared code)."""
    D, E, d, t, V = x
    rho, n, mpay, mfix, mplate, eb, eta, Pav, CdA, FM, Vtip = 1.225, 4, 2.5, 0.6, 0.25, 150, 0.85, 30, 0.08, 0.65, 110
    L = 0.78 * D - 0.08
    A_t = math.pi / 4 * (d**2 - (d - 2 * t) ** 2)
    # eq. (1) is linear in m: m = a + 0.008 g m  ->  m = a / (1 - 0.008 g)
    a = mpay + mfix + mplate + E / eb + n * 1550 * A_t * L + n * (0.12 + 0.05 * (D / 0.6) ** 2.5)
    m = a / (1 - 0.008 * 9.81)
    out = {"m": m}
    Ar = math.pi * D**2 / 4
    P = {}
    for tag, mm in (("loaded", m), ("empty", m - mpay)):
        W = mm * 9.81
        vh = math.sqrt(W / (2 * rho * n * Ar))
        P["h" + tag] = W * vh / (FM * eta) + Pav
        vi = math.sqrt(-V**2 / 2 + math.sqrt(V**4 / 4 + vh**4))
        mu = V / Vtip
        P["c" + tag] = (W * vi + W * vh * (1 / FM - 1) * (1 + 4.65 * mu**2) + 0.5 * rho * CdA * V**3) / eta + Pav
    Euse = 3600 * 0.8 * 0.85 * E
    out.update(P_hover_loaded=P["hloaded"], P_hover_empty=P["hempty"],
               P_cruise_loaded=P["cloaded"], P_cruise_empty=P["cempty"],
               R_km=V * (Euse - 60 * (P["hloaded"] + P["hempty"])) / (P["cloaded"] + P["cempty"]) / 1000)
    return out


def hand_check_table(designs):
    rows = []
    for name, x in designs.items():
        ref = hand_check(x)
        r = analyse(x, mda(x), Params())
        for k, v in ref.items():
            rows.append(dict(design=name, quantity=k, hand=v, code=float(np.real(r[k])),
                             rel_err=abs(float(np.real(r[k])) - v) / abs(v)))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 1. MDA convergence
def mda_convergence(x, path, p=Params(), starts=(1.0, 10.0, 40.0), tol=1e-8):
    m_star = mda_closed_form(x, p)
    slope = 0.008 * G
    fig, ax = plt.subplots(figsize=(5.6, 3.8))
    rows = []
    for m0 in starts:
        _, hist = mda(x, p, m0=m0, tol=1e-14, history=True)
        err = np.abs(hist - m_star)
        err[err == 0] = 1e-16
        ax.semilogy(range(len(hist)), err, "o-", ms=3, label=f"start m0 = {m0:g} kg")
        k_tol = int(np.argmax(np.abs(np.diff(hist)) < tol)) + 1
        rows.append(dict(m0=m0, iterations_to_1e8=k_tol, m_final=hist[-1], m_exact=m_star))
    k = np.arange(0, 16)
    ax.semilogy(k, abs(40 - m_star) * slope**k, "k--", lw=1, label=f"theory: x{slope:.3f} per step")
    ax.axhline(tol, color="grey", lw=0.8, ls=":")
    ax.text(0.3, tol * 1.6, "tolerance 1e-8 kg", fontsize=8, color="grey")
    ax.set_xlabel("iteration k")
    ax.set_ylabel("|m_k - m*|  (kg)")
    ax.set_title(f"M1 MDA: fixed-point iteration on mass (m* = {m_star:.4f} kg)", fontsize=10)
    ax.set_ylim(1e-15, 1e2)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 2. gradient check
def _fd(fun, z, h, kind):
    f0 = np.atleast_1d(fun(z))
    J = np.zeros((f0.size, z.size))
    for i in range(z.size):
        e = np.zeros_like(z); e[i] = h
        if kind == "forward":
            J[:, i] = (np.atleast_1d(fun(z + e)) - f0) / h
        else:
            J[:, i] = (np.atleast_1d(fun(z + e)) - np.atleast_1d(fun(z - e))) / (2 * h)
    return J


def gradient_check(prob, points, steps=(1e-4, 1e-6)):
    """Complex-step gradients (what SLSQP is given) against forward and central differences.
    Error = max |FD - CS| / max(|CS|, 1e-2) over every entry of the objective and constraint Jacobians."""
    rows = []
    for label, z in points.items():
        z = np.asarray(z, float)
        for fname, fun in (("objective m", prob.mdf_f), ("constraints g", prob.mdf_g)):
            J_cs = np.atleast_2d(prob.jac(fun, z))
            scale = np.maximum(np.abs(J_cs), 1e-2)
            for h in steps:
                for kind in ("forward", "central"):
                    J_fd = _fd(fun, z, h, kind)
                    rows.append(dict(point=label, function=fname, step=h, scheme=kind,
                                     max_rel_err=float(np.max(np.abs(J_fd - J_cs) / scale)),
                                     mda_tol=prob.mda_tol))
    return pd.DataFrame(rows)


def gradient_step_sweep(prob, z, path):
    """Error of forward FD vs step size: truncation (slope 1) on the right, round-off and
    MDA-tolerance noise (slope -1) on the left. Shown for MDA tolerances 1e-12 and 1e-8."""
    z = np.asarray(z, float)
    hs = np.logspace(-12, -1, 45)
    fig, ax = plt.subplots(figsize=(5.6, 3.8))
    for tol, style in ((1e-12, "-"), (1e-8, "--")):
        pr = Problem(prob.R_req_km, prob.wind, prob.p, prob.oracle_range, mda_tol=tol)
        J_cs = np.atleast_2d(pr.jac(pr.mdf_f, z))
        errs = [np.max(np.abs(_fd(pr.mdf_f, z, h, "forward") - J_cs)) / np.max(np.abs(J_cs)) for h in hs]
        ax.loglog(hs, errs, style, label=f"forward FD, MDA tol {tol:g}")
    for h in (1e-4, 1e-6):
        ax.axvline(h, color="grey", lw=0.8, ls=":")
    ax.set_xlabel("finite-difference step h (scaled variables)")
    ax.set_ylabel("max rel. error of dm/dz vs complex step")
    ax.set_title("M1 gradient check: objective (take-off mass)", fontsize=10)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------- 3. MDF vs IDF
def mdf_vs_idf(problems):
    rows = []
    for name, prob in problems.items():
        bm, runs_m = solve_mdf(prob)
        bi, runs_i = solve_idf(prob)
        xm, xi = prob.to_x(bm.x), prob.to_x(bi.x[:5])
        mm, mi = 10 * bm.fun, 10 * bi.x[5]
        row = dict(design=name, R_req_km=prob.R_req_km, m_MDF=mm, m_IDF=mi,
                   m_rel_diff_pct=100 * abs(mm - mi) / mm,
                   x_max_rel_diff_pct=100 * float(np.max(np.abs(xm - xi) / (prob.to_x(np.ones(5)) - prob.to_x(np.zeros(5))))),
                   IDF_consistency=float(abs(prob.idf_h(bi.x)[0]) * 10),
                   MDF_starts_ok=sum(r["success"] for r in runs_m), IDF_starts_ok=sum(r["success"] for r in runs_i),
                   n_starts=len(runs_m), MDF_iters=bm.nit, IDF_iters=bi.nit)
        row.update({f"{v}_MDF": float(a) for v, a in zip(VARS, xm)})
        row.update({f"{v}_IDF": float(a) for v, a in zip(VARS, xi)})
        rows.append(row)
    return pd.DataFrame(rows)
