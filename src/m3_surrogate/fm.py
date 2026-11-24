"""Adapters that let M1 call the M3 surrogate as FM(D, T).

    fm = FMFunction(rbf)            # fm(D, T) with numpy arrays of any (matching) shape
    dom = FMDomain(df.D_m, df.T_N)  # slacks >= 0 inside the convex hull of the oracle points

M1 evaluates FM at the loaded and the empty hover thrust per rotor. FMDomain becomes extra M1
constraints so the optimiser cannot wander into corners the oracle never sampled, where an RBF
extrapolates with its linear tail and could promise an FM the rotor does not have.
"""
import numpy as np
from scipy.spatial import ConvexHull

from .data import make_features, D_MIN, D_MAX, T_MIN, T_MAX


class FMFunction:
    def __init__(self, model, name="surrogate"):
        self.model, self.name = model, name

    def __call__(self, D, T):
        D, T = np.broadcast_arrays(np.asarray(D, float), np.asarray(T, float))
        out = self.model.predict(make_features(D.ravel(), T.ravel()))
        return np.asarray(out).reshape(D.shape) if D.ndim else float(out[0])


class FMDomain:
    """Convex hull of the sampled (D, T) points in scaled coordinates. Each facet gives one
    slack s = -(a . x + b) >= 0 (distance inside the hull, in units of the oracle box)."""

    def __init__(self, D, T):
        X = make_features(D, T)
        hull = ConvexHull(X)
        self.A, self.b = hull.equations[:, :2], hull.equations[:, 2]
        self.points = np.column_stack([np.asarray(D, float), np.asarray(T, float)])
        self.vertices = np.column_stack([np.asarray(D)[hull.vertices], np.asarray(T)[hull.vertices]])
        self.names = [f"facet {i}" for i in range(len(self.b))]

    def __call__(self, D, T):
        D, T = np.broadcast_arrays(np.asarray(D, float), np.asarray(T, float))
        x1 = (D - D_MIN) / (D_MAX - D_MIN)
        x2 = (T - T_MIN) / (T_MAX - T_MIN)
        return -(self.A[:, 0].reshape((-1,) + (1,) * D.ndim) * x1
                 + self.A[:, 1].reshape((-1,) + (1,) * D.ndim) * x2
                 + self.b.reshape((-1,) + (1,) * D.ndim))

    def inside(self, D, T, tol=1e-9):
        return np.all(self(D, T) >= -tol, axis=0)
