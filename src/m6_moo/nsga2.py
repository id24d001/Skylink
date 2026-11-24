"""A small NSGA-II (Deb et al. 2002) for mixed integer / continuous variables.

Written here (about 150 lines) rather than adding pymoo, so every step can be read and defended:
  - fast non-dominated sorting and crowding distance;
  - constrained domination (Deb): a feasible point beats an infeasible one; two infeasible
    points are compared by total constraint violation;
  - binary tournament on (rank, crowding);
  - simulated binary crossover (SBX, eta_c = 5) and polynomial mutation (eta_m = 10) on the real
    encoding; integer variables are rounded after variation; a problem-specific `repair`
    then maps the genome to a valid design (e.g. bays <= drones + spares);
  - duplicate offspring (same repaired key) are re-mutated, so the population stays diverse.
All objectives are minimised. Verified on ZDT1 (verify.zdt1_check).
"""
import numpy as np


def dominates(a, b):
    return np.all(a <= b) and np.any(a < b)


def nondominated_sort(F, cv=None):
    """Front index (0 = best) for each row of F (n, m); constrained domination if cv given."""
    n = len(F)
    cv = np.zeros(n) if cv is None else np.asarray(cv, float)
    feas = cv <= 0
    # dom[i, j] = i dominates j
    le = np.all(F[:, None, :] <= F[None, :, :], axis=2)
    lt = np.any(F[:, None, :] < F[None, :, :], axis=2)
    dom = le & lt
    both = feas[:, None] & feas[None, :]
    dom = np.where(both, dom, False)
    dom |= feas[:, None] & ~feas[None, :]
    inf = ~feas[:, None] & ~feas[None, :]
    dom |= inf & (cv[:, None] < cv[None, :])
    n_dom = dom.sum(axis=0)
    rank = np.full(n, -1)
    front, r = np.where(n_dom == 0)[0], 0
    while len(front):
        rank[front] = r
        n_dom = n_dom - dom[front].sum(axis=0)
        n_dom[rank >= 0] = -1
        front, r = np.where(n_dom == 0)[0], r + 1
    return rank


def crowding(F):
    n, m = F.shape
    d = np.zeros(n)
    if n <= 2:
        return np.full(n, np.inf)
    for k in range(m):
        o = np.argsort(F[:, k])
        span = F[o[-1], k] - F[o[0], k]
        d[o[0]] = d[o[-1]] = np.inf
        if span > 0:
            d[o[1:-1]] += (F[o[2:], k] - F[o[:-2], k]) / span
    return d


def _rank_crowd(F, cv):
    rank = nondominated_sort(F, cv)
    cd = np.zeros(len(F))
    for r in np.unique(rank):
        idx = np.where(rank == r)[0]
        cd[idx] = crowding(F[idx])
    return rank, cd


def _sbx(p1, p2, lo, hi, eta, rng, pc=0.9):
    c1, c2 = p1.copy(), p2.copy()
    if rng.random() > pc:
        return c1, c2
    for i in range(len(p1)):
        if rng.random() > 0.5 or abs(p1[i] - p2[i]) < 1e-12:
            continue
        u = rng.random()
        beta = (2 * u) ** (1 / (eta + 1)) if u <= 0.5 else (1 / (2 * (1 - u))) ** (1 / (eta + 1))
        a, b = 0.5 * ((1 + beta) * p1[i] + (1 - beta) * p2[i]), 0.5 * ((1 - beta) * p1[i] + (1 + beta) * p2[i])
        c1[i], c2[i] = a, b
    return np.clip(c1, lo, hi), np.clip(c2, lo, hi)


def _pm(x, lo, hi, eta, rng, pm):
    y = x.copy()
    for i in range(len(x)):
        if rng.random() > pm:
            continue
        u, span = rng.random(), hi[i] - lo[i]
        if span <= 0:
            continue
        d = (2 * u) ** (1 / (eta + 1)) - 1 if u < 0.5 else 1 - (2 * (1 - u)) ** (1 / (eta + 1))
        y[i] = x[i] + d * span
    return np.clip(y, lo, hi)


def nsga2(evaluate, lo, hi, is_int, pop=40, gens=40, seed=0, repair=None, key=None,
          eta_c=5, eta_m=10, pm=None, init=None, callback=None):
    """Minimise evaluate(X) -> (F (n, m), cv (n,)) over the box [lo, hi].

    evaluate gets a whole batch (so it can run in parallel). repair(x) -> x maps a rounded
    genome to a valid one; key(x) -> hashable identifies duplicates. init: optional rows
    to seed the first population. Returns dict(X, F, cv, rank, history)."""
    rng = np.random.default_rng(seed)
    lo, hi, is_int = np.asarray(lo, float), np.asarray(hi, float), np.asarray(is_int, bool)
    pm = 1.0 / len(lo) if pm is None else pm
    repair = repair or (lambda x: x)
    key = key or (lambda x: tuple(np.round(x, 9)))

    def fix(x):
        x = np.where(is_int, np.round(x), x)
        return repair(np.clip(x, lo, hi))

    X = [fix(lo + rng.random(len(lo)) * (hi - lo)) for _ in range(pop)]
    if init is not None:
        for i, x in enumerate(init[:pop]):
            X[i] = fix(np.asarray(x, float))
    X = np.array(X)
    F, cv = evaluate(X)
    history = []
    for g in range(gens):
        rank, cd = _rank_crowd(F, cv)
        seen = {key(x) for x in X}
        kids = []
        tries = 0
        while len(kids) < pop:
            i, j = rng.integers(0, pop, 2), rng.integers(0, pop, 2)
            pick = lambda a, b: a if (rank[a] < rank[b] or (rank[a] == rank[b] and cd[a] > cd[b])) else b
            p1, p2 = X[pick(*i)], X[pick(*j)]
            for c in _sbx(p1, p2, lo, hi, eta_c, rng):
                c = fix(_pm(c, lo, hi, eta_m, rng, pm))
                k, t = key(c), 0
                while k in seen and t < 20:          # re-mutate duplicates
                    c = fix(_pm(c, lo, hi, eta_m, rng, max(pm, 0.3)))
                    k, t = key(c), t + 1
                tries += 1
                if k not in seen or tries > 50 * pop:
                    seen.add(k)
                    kids.append(c)
        Xk = np.array(kids[:pop])
        Fk, cvk = evaluate(Xk)
        XA, FA, cvA = np.vstack([X, Xk]), np.vstack([F, Fk]), np.concatenate([cv, cvk])
        rank, cd = _rank_crowd(FA, cvA)
        order = np.lexsort((-cd, rank))[:pop]
        X, F, cv = XA[order], FA[order], cvA[order]
        history.append(dict(gen=g + 1, n_front=int((rank[order] == 0).sum())))
        if callback:
            callback(g + 1, X, F, cv)
    rank = nondominated_sort(F, cv)
    return dict(X=X, F=F, cv=cv, rank=rank, history=history)


def pareto_mask(F):
    """Non-dominated rows of F (all minimised), duplicates kept once."""
    F = np.asarray(F, float)
    _, first = np.unique(F, axis=0, return_index=True)
    keep = np.zeros(len(F), bool)
    keep[first] = True
    rank = nondominated_sort(F)
    return keep & (rank == 0)
