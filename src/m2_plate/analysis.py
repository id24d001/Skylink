"""Analyse a cut outline as the real part: binary pixels -> fine FE mesh (1 mm) -> compliance,
deflection and bending stress, compared with the solid plate on the same mesh."""
import numpy as np

from .fe import acm_curvature_B
from .plate import build


def element_fractions(binary_full, n_quarter):
    """Solid area fraction of each element of the quarter mesh (lower-left quarter of the part)."""
    m = binary_full.shape[0] // 2
    q = binary_full[:m, :m].astype(float)
    k = m // n_quarter
    assert k * n_quarter == m, "pixel grid must divide the analysis mesh"
    return q.reshape(n_quarter, k, n_quarter, k).mean(axis=(1, 3)).ravel()


def analyse_cut(spec, binary_full, n_quarter=80):
    """Compliance of the cut part (element stiffness proportional to solid area fraction, which is
    exact for a plate strip cut in-plane), its ratio to the solid plate, max deflection and the
    peak von Mises bending stress (surface) in fully solid elements outside the support patches."""
    p, k = build(spec, n_quarter)
    frac = element_fractions(binary_full, n_quarter)
    frac[p.solid] = 1.0
    u, c, _ = p.solve(frac, 1.0)
    u0, c0, _ = p.solve(np.ones(p.grid.nel), 1.0)
    st = bending_stress(spec, p, u)
    st0 = bending_stress(spec, p, u0)
    ok = (frac > 0.999) & ~p.solid
    return dict(c_cut=k * c, c_solid=k * c0, ratio=c / c0,
                w_max_mm=float(-u[0::3].min()), w_max_solid_mm=float(-u0[0::3].min()),
                sigma_vm_max_MPa=float(st[ok].max()), sigma_vm_max_solid_MPa=float(st0[~p.solid].max()),
                frac=frac, u=u, problem=p)


def bending_stress(spec, p, u):
    """Surface von Mises stress at each element centre from the plate moments, sigma = 6 M / t^2."""
    B = acm_curvature_B(p.grid.h)
    kap = -(u[p.grid.edof] @ B.T)                     # (nel, 3): kappa_x, kappa_y, 2 kappa_xy
    nu = spec.nu
    Dm = spec.E * spec.t ** 3 / (12 * (1 - nu ** 2)) * np.array([[1, nu, 0], [nu, 1, 0], [0, 0, (1 - nu) / 2]])
    M = kap @ Dm.T
    s = 6 * M / spec.t ** 2
    return np.sqrt(s[:, 0] ** 2 - s[:, 0] * s[:, 1] + s[:, 1] ** 2 + 3 * s[:, 2] ** 2)
