"""Flight distances around the no-fly zones, used to choose M1's required radius.

The brief says straight-line distance is not flight distance: drones must go around the
airstrip circle and the reserve-forest polygon. The shortest path around polygonal
obstacles runs along a visibility graph (start, goal and obstacle corners), so we build
that graph and run Dijkstra. Obstacle corners are pushed out by 0.1 % so paths may touch
a zone's edge but never cross it. M4 can reuse `flight_distance_matrix`.
"""
import json

import numpy as np
from scipy.sparse.csgraph import dijkstra

from common import DATA, load_csv


def load_nofly(path=DATA / "nofly.geojson", inflate=1.001):
    polys = []
    for f in json.load(open(path))["features"]:
        ring = np.array(f["geometry"]["coordinates"][0], float)
        if np.allclose(ring[0], ring[-1]):
            ring = ring[:-1]
        c = ring.mean(axis=0)
        polys.append(c + inflate * (ring - c))
    return polys


def _inside(p, poly):
    x, y = p
    xs, ys = poly[:, 0], poly[:, 1]
    xs2, ys2 = np.roll(xs, -1), np.roll(ys, -1)
    cross = ((ys > y) != (ys2 > y)) & (x < (xs2 - xs) * (y - ys) / (ys2 - ys + 1e-300) + xs)
    return bool(np.count_nonzero(cross) % 2)


def _proper_cross(a, b, poly):
    """True if segment ab properly crosses any edge of poly."""
    p, q = poly, np.roll(poly, -1, axis=0)

    def orient(u, v, w):
        return (v[..., 0] - u[..., 0]) * (w[..., 1] - u[..., 1]) - (v[..., 1] - u[..., 1]) * (w[..., 0] - u[..., 0])

    d1, d2 = orient(p, q, a), orient(p, q, b)
    d3, d4 = orient(a, b, p), orient(a, b, q)
    eps = 1e-9
    return bool(np.any((d1 * d2 < -eps) & (d3 * d4 < -eps)))


def _visible(a, b, polys):
    for poly in polys:
        if _proper_cross(a, b, poly):
            return False
        for s in (0.25, 0.5, 0.75):  # catches chords between corners of the same zone
            if _inside(a + s * (b - a), poly):
                return False
    return True


def flight_distance_matrix(origins, targets, polys=None):
    """Shortest obstacle-avoiding distance (km) from each origin to each target."""
    polys = load_nofly() if polys is None else polys
    origins, targets = np.asarray(origins, float), np.asarray(targets, float)
    corners = np.vstack(polys)
    nodes = np.vstack([origins, targets, corners])
    n = len(nodes)
    W = np.full((n, n), np.inf)
    for i in range(n):
        for j in range(i + 1, n):
            if _visible(nodes[i], nodes[j], polys):
                # csgraph treats 0 as "no edge", so coincident points get 1 mm (scipy drops weights below ~1e-8)
                W[i, j] = W[j, i] = max(np.hypot(*(nodes[i] - nodes[j])), 1e-6)
    W[np.isinf(W)] = 0
    dist = dijkstra(W, directed=False, indices=range(len(origins)))
    no, nt = len(origins), len(targets)
    return dist[:, no:no + nt]


def hub_phc_distances():
    """DataFrame of flight distance (km), rows = candidate hubs, columns = PHCs."""
    import pandas as pd
    s = load_csv("sites.csv")
    hubs = s[s.candidate_hub == 1]
    phcs = s[s.type == "phc"]
    D = flight_distance_matrix(hubs[["x_km", "y_km"]].values, phcs[["x_km", "y_km"]].values)
    return pd.DataFrame(D, index=hubs.site_id.values, columns=phcs.site_id.values)


def min_cover_radius(D, k):
    """Smallest radius r such that some set of k hubs reaches every PHC within r (brute force)."""
    from itertools import combinations
    best = (np.inf, None)
    for combo in combinations(range(D.shape[0]), k):
        r = D.values[list(combo)].min(axis=0).max()
        if r < best[0]:
            best = (r, [D.index[c] for c in combo])
    return best
