"""Active constraints, Lagrange multipliers, and the payload multiplier as
"radius lost per extra 100 g".

With L = f - sum(lambda_i g_i) (g >= 0, lambda >= 0), the envelope theorem gives the
change of the optimal objective with any problem parameter p:
    d f*/d p = dL/dp = df/dp - sum(lambda_i dg_i/dp).
We take p = payload mass and p = required radius, then convert:
    radius lost per +100 g at the same take-off mass = 0.1 * (dm*/dm_pay) / (dm*/dR_req).
Both multipliers are also checked by re-solving the problem with the parameter changed.
"""
import numpy as np

from .mdo import Problem, solve_mdf, solve_max_radius, CON_NAMES
from .model import VARS, analyse, mda


def active_set(prob, z, tol=1e-6):
    g = prob.mdf_g(z)
    act_g = [i for i, gi in enumerate(g) if gi < tol]
    act_lb = [i for i, zi in enumerate(z) if zi < tol]
    act_ub = [i for i, zi in enumerate(z) if zi > 1 - tol]
    return act_g, act_lb, act_ub


def kkt_multipliers(prob, z):
    """Solve grad f = sum lambda_i grad g_i + mu_lb - mu_ub over the active set (least squares).
    Gradients are complex-step exact. Returns a dict and the KKT residual norm."""
    act_g, act_lb, act_ub = active_set(prob, z)
    gf = prob.jac(prob.mdf_f, z)
    Jg = prob.jac(prob.mdf_g, z)
    cols, names = [], []
    for i in act_g:
        cols.append(Jg[i]); names.append(("con", i))
    for i in act_lb:
        e = np.zeros(5); e[i] = 1; cols.append(e); names.append(("lb", i))
    for i in act_ub:
        e = np.zeros(5); e[i] = -1; cols.append(e); names.append(("ub", i))
    A = np.array(cols).T
    lam, *_ = np.linalg.lstsq(A, gf, rcond=None)
    resid = np.linalg.norm(A @ lam - gf) / max(np.linalg.norm(gf), 1e-300)
    out = {}
    for (kind, i), l in zip(names, lam):
        label = prob.con_names[i] if kind == "con" else f"{VARS[i]} at {'lower' if kind == 'lb' else 'upper'} bound"
        out[label] = (kind, i, l)
    return out, resid


def _param_derivative(prob, z, name, h=1e-30):
    """Complex-step d/dp of (f, g) at fixed z, for p = 'm_pay' or 'R_req_km'."""
    if name == "m_pay":
        pr = Problem(prob.R_req_km, prob.wind, prob.p.with_(m_pay=prob.p.m_pay + 1j * h),
                     prob.oracle_range)
    else:
        pr = Problem(prob.R_req_km + 1j * h, prob.wind, prob.p, prob.oracle_range)
    zc = np.asarray(z, complex)
    return np.imag(pr.mdf_f(zc)) / h, np.imag(pr.mdf_g(zc)) / h


def payload_sensitivity(prob, z, dpay=0.1, dR=0.1):
    """Payload multiplier from the KKT conditions, converted to km per 100 g, and three checks."""
    mult, resid = kkt_multipliers(prob, z)
    lam = np.zeros(len(prob.con_names))
    for kind, i, l in mult.values():
        if kind == "con":
            lam[i] = l

    out = {"kkt_residual": resid}
    for p in ("m_pay", "R_req_km"):
        df, dg = _param_derivative(prob, z, p)
        out[f"dm_d{p}"] = 10 * (df - lam @ dg)       # objective is m/10
    dm_dpay, dm_dR = out["dm_dm_pay"], out["dm_dR_req_km"]
    out["km_lost_per_100g_multiplier"] = 0.1 * dm_dpay / dm_dR

    # check 1: re-solve min-mass with +100 g payload (same radius)
    m0 = 10 * prob.mdf_f(z)
    p2 = Problem(prob.R_req_km, prob.wind, prob.p.with_(m_pay=prob.p.m_pay + dpay), prob.oracle_range)
    b2, _ = solve_mdf(p2, z0=z)
    out["dm_dm_pay_resolve"] = (10 * b2.fun - m0) / dpay
    # check 2: re-solve min-mass with +dR radius
    p3 = Problem(prob.R_req_km + dR, prob.wind, prob.p, prob.oracle_range)
    b3, _ = solve_mdf(p3, z0=z)
    out["dm_dR_resolve"] = (10 * b3.fun - m0) / dR
    # check 3: hold take-off mass at m*, add 100 g payload, maximise radius
    xa, Ra = solve_max_radius(prob, m_cap=m0)
    xb, Rb = solve_max_radius(p2, m_cap=m0)
    out["km_lost_per_100g_resolve"] = (Ra - Rb) * 0.1 / dpay
    out["R_at_mcap_base"], out["R_at_mcap_plus100g"] = Ra, Rb
    # check 4: the built drone (design frozen) simply carries 100 g more
    x = prob.to_x(z)
    r0 = analyse(x, mda(x, prob.p), prob.p, prob.wind)
    r1 = analyse(x, mda(x, p2.p), p2.p, prob.wind)
    out["km_lost_per_100g_frozen_design"] = (r0["R_wind_km"] - r1["R_wind_km"]) * 0.1 / dpay
    return out, mult
