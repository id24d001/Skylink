"""M2 verification.

  mbb_benchmark      top88 MBB half-beam (60 x 20, vf 0.5, rmin 1.5) with the density filter and
                     p = 3, then with no filter, then with p = 1 (the brief's required check).
  plate_element      ACM element vs the Navier series for a simply supported square plate under
                     uniform pressure (w_max = 0.00406 q a^4 / D for nu = 0.3), 4 meshes.
  symmetry_check     solid hub plate: quarter model x 4 vs whole-plate model.
  gradient_check     adjoint dc/dx (through the density filter) vs central differences.
  mesh_check         the chosen volume fraction re-optimised on a finer mesh.
"""
import numpy as np
import pandas as pd

from .fe import Grid, ke_q4, ke_acm, fe_acm_pressure
from .simp import Problem, optimise, grey_index
from .plate import build


def mbb_problem(nx=60, ny=20):
    g = Grid(nx, ny, 1.0, 2)
    nodes = np.arange(g.nnodes)
    left = nodes[np.isclose(g.node_x, 0)]
    br = nodes[np.isclose(g.node_x, nx) & np.isclose(g.node_y, 0)][0]
    tl = nodes[np.isclose(g.node_x, 0) & np.isclose(g.node_y, ny)][0]
    f = np.zeros(g.ndof)
    f[2 * tl + 1] = -1.0
    return Problem(g, ke_q4(0.3), f, np.r_[2 * left, 2 * br + 1])


def checkerboard_index(rho, nx, ny):
    """Share of 2 x 2 element patches that look like a checkerboard (diagonals solid/void)."""
    r = rho.reshape(ny, nx) > 0.5
    a, b, c, d = r[:-1, :-1], r[:-1, 1:], r[1:, :-1], r[1:, 1:]
    return float(np.mean((a == d) & (b == c) & (a != b)))


def mbb_benchmark(vf=0.5, rmin=1.5):
    p = mbb_problem()
    cases = {"density filter, p = 3 (top88 ft = 2)": dict(penal=3.0, filt=True),
             "no filter, p = 3": dict(penal=3.0, filt=False),
             "density filter, p = 1": dict(penal=1.0, filt=True)}
    rows, fields = [], {}
    for name, kw in cases.items():
        r = optimise(p, vf, rmin=rmin, maxit=300, **kw)
        rows.append(dict(case=name, compliance=r["c"], iterations=r["iters"],
                         grey_index=grey_index(r["rho"]),
                         checkerboard_pct=100 * checkerboard_index(r["rho"], 60, 20),
                         compliance_p3_of_result=p.compliance(r["rho"], 3.0)))
        fields[name] = r["rho"].reshape(20, 60)
    return pd.DataFrame(rows), fields


def plate_element(meshes=(8, 16, 32, 64), nu=0.3):
    exact = 0.00406235   # Navier series, a/b = 1, nu = 0.3 (Timoshenko & Woinowsky-Krieger, Table 8)
    rows = []
    for n in meshes:
        g = Grid(n, n, 1.0 / n, 3)
        f = np.zeros(g.ndof)
        np.add.at(f, g.edof.ravel(), np.tile(fe_acm_pressure(g.h), g.nel))
        edge = np.where(np.isclose(g.node_x, 0) | np.isclose(g.node_x, 1) |
                        np.isclose(g.node_y, 0) | np.isclose(g.node_y, 1))[0]
        p = Problem(g, ke_acm(g.h, 1.0, 1.0, nu), f, 3 * edge)
        u, _, _ = p.solve(np.ones(g.nel), 1.0)
        D = 1.0 / (12 * (1 - nu ** 2))
        mid = (n // 2) * (n + 1) + n // 2
        coef = u[3 * mid] * D
        rows.append(dict(check="ACM element: SS square plate, w_max D / (q a^4)", mesh=f"{n} x {n}",
                         value=coef, reference=exact, rel_error_pct=100 * (coef / exact - 1)))
    return pd.DataFrame(rows)


def symmetry_check(spec, n_quarter):
    pq, kq = build(spec, n_quarter)
    pf, kf = build(spec, 2 * n_quarter, full=True)
    cq = kq * pq.compliance(np.ones(pq.grid.nel))
    cf = kf * pf.compliance(np.ones(pf.grid.nel))
    return pd.DataFrame([dict(check="symmetry: solid plate compliance, 4 x quarter vs whole (N mm)",
                              mesh=f"{n_quarter}^2 quarter / {2 * n_quarter}^2 whole", value=cq,
                              reference=cf, rel_error_pct=100 * (cq / cf - 1))])


def gradient_check(spec, n=8, vf=0.4, rmin=1.6, seed=0, steps=(1e-4, 1e-6)):
    """Adjoint sensitivity of the plate compliance wrt design variables x (through the filter)."""
    from .fe import density_filter
    p, k = build(spec, n)
    H = density_filter(p.grid, rmin, *p.mirror)
    rng = np.random.default_rng(seed)
    x = np.clip(vf + 0.3 * rng.standard_normal(p.grid.nel), 0.05, 1)

    def c_of(xx):
        rho = H @ xx
        rho[p.solid] = 1.0
        return p.solve(rho, 3.0)

    _, c0, dc = c_of(x)
    dc[p.solid] = 0.0
    adj = H.T @ dc
    idx = rng.choice(np.where(~p.solid)[0], 6, replace=False)
    rows = []
    for h in steps:
        fd = np.array([(c_of(x + h * np.eye(p.grid.nel)[i])[1] - c_of(x - h * np.eye(p.grid.nel)[i])[1]) / (2 * h)
                       for i in idx])
        err = np.abs(fd - adj[idx]) / np.abs(adj[idx])
        rows.append(dict(check="adjoint dc/dx vs central difference (6 random elements), max rel. error",
                         mesh=f"{n}^2 quarter, step {h:g}", value=float(err.max()), reference=0.0,
                         rel_error_pct=100 * float(err.max())))
    return pd.DataFrame(rows)
