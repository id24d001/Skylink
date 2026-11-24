"""Finite elements on a regular grid of square elements (element e = (ix, iy), row-major in x).

Two element types share the same SIMP code:
  * Q4 plane stress (2 dof/node: u, v), the element of Andreassen et al. (2011) "top88".
    Used only for the MBB-beam benchmark.
  * ACM rectangle (Adini-Clough-Melosh), a Kirchhoff plate-bending element with
    3 dof/node: w, dw/dx, dw/dy. Used for the hub plate, which is loaded out of plane.

Nodes are numbered n = iy * (nx + 1) + ix, with ix to the right and iy upwards.
"""
import numpy as np
import scipy.sparse as sp


# ---------------------------------------------------------------- element matrices
def ke_q4(nu=0.3):
    """top88 plane-stress Q4 stiffness for a unit square element, E = 1, unit thickness.
    dof order per element (top88): node lower-left, lower-right, upper-right, upper-left, (u, v) each."""
    k = np.array([1/2 - nu/6, 1/8 + nu/8, -1/4 - nu/12, -1/8 + 3*nu/8,
                  -1/4 + nu/12, -1/8 - nu/8, nu/6, 1/8 - 3*nu/8])
    KE = 1 / (1 - nu**2) * np.array([
        [k[0], k[1], k[2], k[3], k[4], k[5], k[6], k[7]],
        [k[1], k[0], k[7], k[6], k[5], k[4], k[3], k[2]],
        [k[2], k[7], k[0], k[5], k[6], k[3], k[4], k[1]],
        [k[3], k[6], k[5], k[0], k[7], k[2], k[1], k[4]],
        [k[4], k[5], k[6], k[7], k[0], k[1], k[2], k[3]],
        [k[5], k[4], k[3], k[2], k[1], k[0], k[7], k[6]],
        [k[6], k[3], k[4], k[1], k[2], k[7], k[0], k[5]],
        [k[7], k[2], k[1], k[4], k[3], k[6], k[5], k[0]]])
    return KE


_GP = np.array([-np.sqrt(3 / 5), 0.0, np.sqrt(3 / 5)])
_GW = np.array([5 / 9, 8 / 9, 5 / 9])
_CORNERS = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], float)   # lower-left, lower-right, upper-right, upper-left


def _acm_basis(h):
    """ACM: w = sum a_i p_i with the 12 terms 1, x, y, x2, xy, y2, x3, x2y, xy2, y3, x3y, xy3.
    Returns callables for p, its second derivatives, and C^-1 (nodal dof -> coefficients)."""
    def p(x, y):
        return np.array([1, x, y, x*x, x*y, y*y, x**3, x*x*y, x*y*y, y**3, x**3*y, x*y**3])

    def px(x, y):
        return np.array([0, 1, 0, 2*x, y, 0, 3*x*x, 2*x*y, y*y, 0, 3*x*x*y, y**3])

    def py(x, y):
        return np.array([0, 0, 1, 0, x, 2*y, 0, x*x, 2*x*y, 3*y*y, x**3, 3*x*y*y])

    def d2(x, y):   # rows: w_xx, w_yy, 2 w_xy
        return np.array([[0, 0, 0, 2, 0, 0, 6*x, 2*y, 0, 0, 6*x*y, 0],
                         [0, 0, 0, 0, 0, 2, 0, 0, 2*x, 6*y, 0, 6*x*y],
                         [0, 0, 0, 0, 2, 0, 0, 4*x, 4*y, 0, 6*x*x, 6*y*y]], float)

    C = np.vstack([np.vstack([p(*(h * c)), px(*(h * c)), py(*(h * c))]) for c in _CORNERS])
    return p, d2, np.linalg.inv(C)


def ke_acm(h, E=1.0, t=1.0, nu=0.3):
    """ACM plate-bending stiffness (12 x 12) for a square element of side h.
    dof order: for each corner (LL, LR, UR, UL): w, dw/dx, dw/dy."""
    p, d2, Ci = _acm_basis(h)
    Dm = E * t**3 / (12 * (1 - nu**2)) * np.array([[1, nu, 0], [nu, 1, 0], [0, 0, (1 - nu) / 2]])
    K = np.zeros((12, 12))
    for xi, wx in zip(_GP, _GW):
        for eta, wy in zip(_GP, _GW):
            x, y = h * (xi + 1) / 2, h * (eta + 1) / 2
            B = d2(x, y) @ Ci
            K += B.T @ Dm @ B * wx * wy * (h / 2) ** 2
    return K


def fe_acm_pressure(h):
    """Consistent nodal load of a unit pressure on one ACM element (12,)."""
    p, _, Ci = _acm_basis(h)
    f = np.zeros(12)
    for xi, wx in zip(_GP, _GW):
        for eta, wy in zip(_GP, _GW):
            x, y = h * (xi + 1) / 2, h * (eta + 1) / 2
            f += p(x, y) @ Ci * wx * wy * (h / 2) ** 2
    return f


# ---------------------------------------------------------------- mesh
class Grid:
    """nx x ny square elements of side h; ndn dofs per node (2 = plane stress, 3 = plate)."""

    def __init__(self, nx, ny, h, ndn):
        self.nx, self.ny, self.h, self.ndn = nx, ny, h, ndn
        self.nel = nx * ny
        self.nnodes = (nx + 1) * (ny + 1)
        self.ndof = ndn * self.nnodes
        iy, ix = np.divmod(np.arange(self.nel), nx)
        self.ex, self.ey = ix, iy                          # element indices
        self.xc, self.yc = (ix + 0.5) * h, (iy + 0.5) * h  # element centres
        n0 = iy * (nx + 1) + ix
        corners = np.stack([n0, n0 + 1, n0 + nx + 2, n0 + nx + 1], axis=1)   # LL, LR, UR, UL
        self.edof = (ndn * corners[:, :, None] + np.arange(ndn)).reshape(self.nel, -1)
        self.iK = np.repeat(self.edof, self.edof.shape[1], axis=1).ravel()
        self.jK = np.tile(self.edof, (1, self.edof.shape[1])).ravel()
        nyy, nxx = np.divmod(np.arange(self.nnodes), nx + 1)
        self.node_x, self.node_y = nxx * h, nyy * h

    def stiffness(self, KE, scale):
        """Global K = sum_e scale_e * KE (sparse CSC)."""
        sK = (KE.ravel()[None, :] * scale[:, None]).ravel()
        K = sp.coo_matrix((sK, (self.iK, self.jK)), shape=(self.ndof, self.ndof)).tocsc()
        return K


def density_filter(grid, rmin, mirror_x=False, mirror_y=False):
    """Linear 'hat' density filter (Bruns & Tortorelli 2001; top88 ft = 2), rmin in element units.
    mirror_x / mirror_y: the right / top edge is a symmetry line, so neighbours beyond it are the
    mirror images of elements inside (the filter then equals the full-plate filter exactly).
    Returns sparse H (rows normalised) so that x_filtered = H @ x and dc/dx = H.T @ dc/dx_filtered."""
    nx, ny = grid.nx, grid.ny
    r = int(np.ceil(rmin)) - 1
    rows, cols, vals = [], [], []
    for dx in range(-r, r + 1):
        for dy in range(-r, r + 1):
            w = rmin - np.hypot(dx, dy)
            if w <= 0:
                continue
            ix, iy = grid.ex + dx, grid.ey + dy
            if mirror_x:
                ix = np.where(ix >= nx, 2 * nx - 1 - ix, ix)
            if mirror_y:
                iy = np.where(iy >= ny, 2 * ny - 1 - iy, iy)
            ok = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
            rows.append(np.arange(grid.nel)[ok])
            cols.append((iy * nx + ix)[ok])
            vals.append(np.full(ok.sum(), w))
    H = sp.csr_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
                      shape=(grid.nel, grid.nel))
    Hs = np.asarray(H.sum(axis=1)).ravel()
    return sp.diags(1 / Hs) @ H


def acm_curvature_B(h, x=None, y=None):
    """B (3 x 12) mapping element dofs to (w_xx, w_yy, 2 w_xy) at local point (x, y); default centre."""
    _, d2, Ci = _acm_basis(h)
    x = h / 2 if x is None else x
    y = h / 2 if y is None else y
    return d2(x, y) @ Ci
