"""The SkyLink hub plate as a plate-bending (out-of-plane) SIMP problem.

Geometry and material (brief): 160 x 160 mm, t = 3 mm, 6061-T6 (E = 68.9 GPa, nu = 0.33,
rho = 2700 kg/m3, yield 276 MPa). Units in the FE model: mm, N, MPa.

Boundary conditions (stated explicitly, see the M2 card and decision log):
  * Supports: the four arm roots. Each arm is clamped to the plate over a corner patch of
    `corner` x `corner` mm (default 10 mm). In hover the arms push the plate up there, so the
    patch nodes get w = 0 (vertical displacement); rotations are left free (simply supported:
    conservative, since a tube clamp gives little rotational restraint to a 3 mm plate).
    Four supported patches remove all three out-of-plane rigid-body modes.
  * Load: the battery weight acts downwards (-z) as a uniform pressure over the central
    40 x 40 mm, F = n_z * g * m_batt with n_z = 2 (the motors are sized for T_max = 2 m g / n,
    so the steepest climb is 2 g). The load magnitude scales u and c linearly; it does not change
    the topology or any compliance ratio, only the deflection and stress check.
  * In-plane loads (arm drag, yaw torque) are an order of magnitude smaller and are carried by
    membrane action, which is far stiffer than bending for a 3 mm plate; they are not modelled.
  * Passive (always solid) elements: the four corner support patches (material is needed under
    the arm clamps).

Symmetry: loads and supports are symmetric about both mid-lines, so only the lower-left quarter
[0, 80] x [0, 80] mm is modelled, with dw/dx = 0 on x = 80 and dw/dy = 0 on y = 80, and the
density filter mirrored across those lines. Whole-plate compliance = 4 x quarter compliance.
`full=True` builds the whole plate instead (used to verify the symmetry reduction).
"""
from dataclasses import dataclass

import numpy as np

from .fe import Grid, ke_acm, fe_acm_pressure
from .simp import Problem

G = 9.81


@dataclass
class PlateSpec:
    L: float = 160.0          # mm, plate side
    t: float = 3.0            # mm
    E: float = 68900.0        # MPa, 6061-T6
    nu: float = 0.33
    density: float = 2700.0   # kg/m3
    sigma_y: float = 276.0    # MPa, 6061-T6 yield
    load_side: float = 40.0   # mm, battery footprint
    corner: float = 10.0      # mm, arm-clamp support patch
    m_batt: float = 1.5       # kg, from the M1 baseline design (set by run.py)
    n_z: float = 2.0          # load factor (T_max = 2 m g / n)
    m_fasteners: float = 0.04 # kg

    @property
    def F(self):
        return self.n_z * G * self.m_batt

    def mass(self, vf):
        """Brief: plate mass = vf * 0.16 * 0.16 * 0.003 * 2700 + 0.04 kg."""
        return vf * (self.L / 1000) ** 2 * (self.t / 1000) * self.density + self.m_fasteners


def build(spec, n, full=False):
    """Plate problem on n x n elements covering the quarter (or the whole plate if full).
    Returns (Problem, factor) with whole-plate compliance = factor * Problem compliance."""
    side = spec.L if full else spec.L / 2
    h = side / n
    g = Grid(n, n, h, 3)
    KE = ke_acm(h, spec.E, spec.t, spec.nu)
    c = spec.L / 2
    a = spec.load_side / 2
    inload = (np.abs(g.xc - c) < a) & (np.abs(g.yc - c) < a)
    q = spec.F / spec.load_side ** 2
    f = np.zeros(g.ndof)
    np.add.at(f, g.edof[inload].ravel(), -q * np.tile(fe_acm_pressure(h), inload.sum()))

    def in_corner(x, y):
        s, eps = spec.corner, 1e-9
        near = lambda v: (v <= s + eps) | (v >= spec.L - s - eps)
        return near(x) & near(y)

    sup = np.where(in_corner(g.node_x, g.node_y))[0]
    fixed = [3 * sup]
    if not full:   # symmetry lines: zero slope across them
        fixed.append(3 * np.where(np.isclose(g.node_x, side))[0] + 1)
        fixed.append(3 * np.where(np.isclose(g.node_y, side))[0] + 2)
    p = Problem(g, KE, f, np.unique(np.concatenate(fixed)), solid=in_corner(g.xc, g.yc),
                mirror=(not full, not full))
    p.inload = inload
    return p, (1.0 if full else 4.0)


def quarter_to_full(a):
    """Quarter field (ny, nx) on [0, L/2]^2 -> whole plate (2ny, 2nx) by mirroring."""
    top = np.hstack([a, a[:, ::-1]])
    return np.vstack([top, top[::-1, :]])
