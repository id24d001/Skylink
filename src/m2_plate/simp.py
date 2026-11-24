"""Minimum-compliance topology optimisation: SIMP + density filter + optimality criteria.

    min_x  c(x) = f' u(x)      s.t.  K(x~) u = f,   mean(x~) = vf,   0 <= x <= 1
    E(x~_e) = Emin + x~_e^p (E0 - Emin),   x~ = H x  (density filter)

Follows Andreassen et al. (2011) "top88" with ft = 2 (density filter), generalised to any element
on a regular grid (plane-stress Q4 or ACM plate) and to passive elements (forced solid or void).
"""
from dataclasses import dataclass, field

import numpy as np
import scipy.sparse.linalg as spla

from .fe import density_filter


@dataclass
class Problem:
    grid: object
    KE: np.ndarray            # element stiffness at E = 1 (solid)
    f: np.ndarray             # global load vector
    fixed: np.ndarray         # fixed dof indices
    solid: np.ndarray = None  # bool mask of elements forced solid (passive)
    void: np.ndarray = None   # bool mask of elements forced void
    Emin: float = 1e-9
    mirror: tuple = (False, False)   # symmetry lines at the right / top edge (for the filter)
    free: np.ndarray = field(init=False)

    def __post_init__(self):
        n = self.grid.nel
        self.solid = np.zeros(n, bool) if self.solid is None else self.solid
        self.void = np.zeros(n, bool) if self.void is None else self.void
        self.free = np.setdiff1d(np.arange(self.grid.ndof), self.fixed)

    def solve(self, rho, penal):
        """FE solve for element densities rho (already filtered). Returns u, c, dc/drho."""
        E = self.Emin + rho ** penal * (1 - self.Emin)
        K = self.grid.stiffness(self.KE, E)
        u = np.zeros(self.grid.ndof)
        fr = self.free
        u[fr] = spla.splu(K[fr][:, fr], permc_spec="MMD_AT_PLUS_A").solve(self.f[fr])
        ue = u[self.grid.edof]
        ce = np.einsum("ij,jk,ik->i", ue, self.KE, ue)
        c = float(E @ ce)
        dc = -penal * rho ** (penal - 1) * (1 - self.Emin) * ce
        return u, c, dc

    def compliance(self, rho, penal=1.0):
        return self.solve(rho, penal)[1]


def _fix(p, x):
    x = x.copy()
    x[p.solid] = 1.0
    x[p.void] = 0.0
    return x


def optimise(p, vf, penal=3.0, rmin=1.5, filt=True, maxit=300, tol=0.01, move=0.2, x0=None,
             verbose=False):
    """SIMP with the density filter (filt=False: no filter at all). rmin in element units.
    Returns dict(x=design variables, rho=physical (filtered) densities, c, history)."""
    g = p.grid
    H = density_filter(g, rmin, *p.mirror) if filt else None
    design = ~(p.solid | p.void)
    x = _fix(p, np.full(g.nel, vf) if x0 is None else x0.copy())
    # volume target for the designable elements so that the total mean density is vf
    hist = []
    for it in range(maxit):
        rho = _fix(p, H @ x) if filt else x
        u, c, dc = p.solve(rho, penal)
        dv = np.ones(g.nel)
        dc[~design] = 0.0
        dv[~design] = 0.0
        if filt:
            dc = H.T @ dc
            dv = H.T @ dv
        # optimality criteria with bisection on the Lagrange multiplier
        l1, l2 = 0.0, 1e9
        xd = x[design]
        B = np.sqrt(np.maximum(-dc[design], 0) / np.maximum(dv[design], 1e-12))
        while (l2 - l1) / (l1 + l2 + 1e-30) > 1e-4:
            lm = 0.5 * (l1 + l2)
            xn = np.clip(xd * B / np.sqrt(lm), np.maximum(0, xd - move), np.minimum(1, xd + move))
            xt = x.copy()
            xt[design] = xn
            vol = (_fix(p, H @ xt) if filt else xt).mean()
            if vol > vf:
                l1 = lm
            else:
                l2 = lm
        change = np.abs(xn - xd).max()
        x[design] = xn
        hist.append(dict(it=it + 1, c=c, vol=rho.mean(), change=change))
        if verbose and (it % 20 == 0):
            print(f"    it {it+1:3d}  c {c:.5g}  vol {rho.mean():.3f}  change {change:.3f}")
        if change < tol:
            break
    rho = _fix(p, H @ x) if filt else x
    _, c, _ = p.solve(rho, penal)
    return dict(x=x, rho=rho, c=c, history=hist, iters=len(hist))


def grey_index(rho):
    """Measure of non-discreteness, 4 rho (1 - rho) averaged (0 = black/white, 1 = all 0.5)."""
    return float(np.mean(4 * rho * (1 - rho)))
