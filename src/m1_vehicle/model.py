"""M1 vehicle analysis: the brief's "Vehicle models and constants", coded as written.

Design vector x = [D, E, d, t, V] in SI units except E (Wh):
    D rotor diameter (m), E battery capacity (Wh), d arm tube outer diameter (m),
    t tube wall (m), V cruise speed (m/s).

Every function here works with complex numbers too, so derivatives can be taken by the
complex-step method (exact to machine precision). Keep it that way: no abs(), max() or
np.real() on quantities that depend on x.

Equation numbers refer to the brief (end_sem_ED6002_PROJECT.pdf, pp. 9-10).
"""
from dataclasses import dataclass, field, replace
from typing import Callable

import numpy as np

G = 9.81

VARS = ["D", "E", "d", "t", "V"]
UNITS = ["m", "Wh", "m", "m", "m/s"]
LB = np.array([0.35, 200.0, 0.015, 0.0005, 8.0])
UB = np.array([0.80, 2000.0, 0.040, 0.0025, 22.0])


def fm_constant(D, T, value=0.65):
    """M1 uses FM0 = 0.65. M3 replaces this with fm(D, T) from its surrogate."""
    return value + 0.0 * D


@dataclass(frozen=True)
class Params:
    rho: float = 1.225          # air density, kg/m3
    n: int = 4                  # rotors
    m_pay: float = 2.5          # payload incl. insulated box, kg
    m_fixed: float = 0.60       # avionics, frame hub, payload pod, kg
    m_plate: float = 0.25       # hub plate until M2 replaces it, kg
    e_b: float = 150.0          # pack specific energy, Wh/kg
    dod: float = 0.80           # depth of discharge
    reserve: float = 0.15       # energy reserve
    eta: float = 0.85           # ESC + motor efficiency
    P_av: float = 30.0          # avionics power, W
    CdA: float = 0.08           # drag area, m2
    V_tip: float = 110.0        # hover tip speed, m/s
    E_cf: float = 70e9          # CFRP Young's modulus, Pa
    rho_cf: float = 1550.0      # CFRP density, kg/m3
    sigma_allow: float = 300e6 / 1.5   # 200 MPa
    t_hover: float = 60.0       # hover at each end of the sortie, s
    fm: Callable = field(default=fm_constant)   # FM(D, T_per_rotor)
    complex_safe: bool = True   # False if fm cannot take complex input (e.g. a GP)

    def with_(self, **kw):
        return replace(self, **kw)


# ---------------------------------------------------------------- geometry
def arm_length(D):
    return 0.78 * D - 0.08


def tube_area(d, t):
    return np.pi / 4 * (d**2 - (d - 2 * t) ** 2)


def tube_inertia(d, t):
    return np.pi / 64 * (d**4 - (d - 2 * t) ** 4)


def prop_mass(D):
    return 0.05 * (D / 0.6) ** 2.5


def motor_mass(T_max):
    return 0.12 + 0.004 * T_max


# ---------------------------------------------------------------- mass (eq. 1) and MDA
def mass_update(x, m, p=Params(), m_motor=None, m_batt=None, m_arm=None):
    """One pass of eq. (1): total mass implied by design x if the drone weighs m.

    The coupling: motor mass depends on T_max = 2 m g / n.  m_motor, m_batt and m_arm
    override the model with catalogue part masses (per motor, whole battery, per arm)
    once the design is snapped to real parts.
    """
    D, E, d, t, V = x
    L = arm_length(D)
    T_max = 2 * m * G / p.n
    mb = E / p.e_b if m_batt is None else m_batt
    ma = p.rho_cf * tube_area(d, t) * L if m_arm is None else m_arm
    mm = motor_mass(T_max) if m_motor is None else m_motor
    return p.m_pay + p.m_fixed + p.m_plate + mb + p.n * ma + p.n * (mm + prop_mass(D))


def mda(x, p=Params(), m0=10.0, tol=1e-12, maxit=200, history=False, **parts):
    """MDA as a fixed-point iteration on mass: m_{k+1} = F(m_k).

    Converges when |m_{k+1} - m_k| < tol (kg). F is linear in m with slope 0.008 g = 0.078,
    so the error shrinks ~13x per iteration. Returns m (and the list of iterates).
    """
    m, hist = m0, [m0]
    for _ in range(maxit):
        m_new = mass_update(x, m, p, **parts)
        hist.append(m_new)
        if np.all(np.abs(np.real(m_new - m)) < tol):
            m = m_new
            break
        m = m_new
    else:
        raise RuntimeError("MDA did not converge")
    return (m, np.array(hist)) if history else m


def mda_closed_form(x, p=Params()):
    """Exact solution of the linear fixed point, used only to verify the iteration."""
    F0 = mass_update(x, 0.0, p)
    slope = p.n * 0.004 * 2 * G / p.n
    return F0 / (1 - slope)


# ---------------------------------------------------------------- power (eqs. 2-4)
def hover_velocity(m, D, p=Params()):
    W = m * G
    A = np.pi * D**2 / 4
    return np.sqrt(W / (2 * p.rho * p.n * A))


def hover_power(m, D, p=Params()):
    """Eq. (2), FM evaluated at this state's hover thrust per rotor."""
    W = m * G
    FM = p.fm(D, W / p.n)
    return W * hover_velocity(m, D, p) / (FM * p.eta) + p.P_av


def cruise_power(m, D, V, p=Params()):
    """Eqs. (3)-(4). FM taken at the hover thrust of the same state (loaded or empty)."""
    W = m * G
    vh = hover_velocity(m, D, p)
    vi = np.sqrt(-V**2 / 2 + np.sqrt(V**4 / 4 + vh**4))
    mu = V / p.V_tip
    FM = p.fm(D, W / p.n)
    P = W * vi + W * vh * (1 / FM - 1) * (1 + 4.65 * mu**2) + 0.5 * p.rho * p.CdA * V**3
    return P / p.eta + p.P_av


def radius_in_wind(R_km, V, w):
    """Brief's headwind model for M4/M5/M7: out-and-back time is 2LV/(V^2 - w^2) instead of 2L/V,
    so with the same energy the reachable one-way distance becomes R (1 - w^2/V^2)."""
    return R_km * (1 - np.asarray(w) ** 2 / V**2)


# ---------------------------------------------------------------- full analysis
def analyse(x, m, p=Params(), wind=0.0, m_motor=None, m_arm=None):
    """All outputs for design x at total mass m (m from the MDA, or IDF's coupling variable)."""
    D, E, d, t, V = x
    L = arm_length(D)
    m_empty = m - p.m_pay
    T_max = 2 * m * G / p.n

    E_use = 3600 * p.dod * (1 - p.reserve) * E                       # eq. (6), J
    Ph_l, Ph_e = hover_power(m, D, p), hover_power(m_empty, D, p)
    Pc_l, Pc_e = cruise_power(m, D, V, p), cruise_power(m_empty, D, V, p)
    R = V * (E_use - p.t_hover * (Ph_l + Ph_e)) / (Pc_l + Pc_e)       # eq. (5), m
    # Brief's headwind model: out-and-back time 2LV/(V^2 - w^2) instead of 2L/V,
    # so for the same energy the reachable radius shrinks by (1 - w^2/V^2).
    R_wind = R * (1 - wind**2 / V**2)

    I = tube_inertia(d, t)
    mtip = (motor_mass(T_max) if m_motor is None else m_motor) + prop_mass(D)
    marm = p.rho_cf * tube_area(d, t) * L if m_arm is None else m_arm
    sigma = T_max * L * (d / 2) / I
    defl = T_max * L**3 / (3 * p.E_cf * I)
    f1 = 1 / (2 * np.pi) * np.sqrt(3 * p.E_cf * I / (L**3 * (mtip + 0.24 * marm)))
    f_rot = p.V_tip / (np.pi * D)

    return dict(
        m=m, m_empty=m_empty, T_max=T_max,
        T_hover_loaded=m * G / p.n, T_hover_empty=m_empty * G / p.n,
        P_hover_loaded=Ph_l, P_hover_empty=Ph_e,
        P_cruise_loaded=Pc_l, P_cruise_empty=Pc_e,
        E_use_Wh=E_use / 3600, R_km=R / 1000, R_wind_km=R_wind / 1000,
        sigma_MPa=sigma / 1e6, defl_mm=defl * 1e3, defl_lim_mm=0.01 * L * 1e3,
        f1_Hz=f1, f1_req_Hz=1.15 * f_rot, d_over_t=d / t, L=L,
        FM_loaded=p.fm(D, m * G / p.n), FM_empty=p.fm(D, m_empty * G / p.n),
    )


def evaluate(x, p=Params(), wind=0.0, **parts):
    """MDA, then analysis: what MDF sees for one design."""
    m = mda(x, p, **{k: v for k, v in parts.items() if k in ("m_motor", "m_batt", "m_arm")})
    return analyse(x, m, p, wind, **{k: v for k, v in parts.items() if k in ("m_motor", "m_arm")})
