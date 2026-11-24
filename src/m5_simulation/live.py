"""M5 live view (brief: "A live view (map animation or dashboard) of drones, queues and charging
over a simulated day"). A matplotlib animation, saved as a GIF (no extra dependency):
left, the district map with no-fly zones, hubs, PHCs and every drone in the air; right, per hub,
the queues, idle drones, bays charging and charged spare packs; top, clock, wind, grounding,
grid outage and the running S45."""
import numpy as np
from scipy.sparse.csgraph import dijkstra

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter

from common import load_csv
from m1_vehicle.target_radius import _visible, load_nofly

CLASS_COL = ("#d62728", "#1f77b4", "#2ca02c")
CLASS_NAME = ("emergency", "routine", "lab")


def flight_path(a, b, polys):
    """Shortest polyline a -> b around the no-fly zones (same visibility graph as M1/M4)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    nodes = np.vstack([a, b, np.vstack(polys)])
    n = len(nodes)
    W = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            if _visible(nodes[i], nodes[j], polys):
                W[i, j] = W[j, i] = max(np.hypot(*(nodes[i] - nodes[j])), 1e-6)
    _, pred = dijkstra(W, directed=False, indices=0, return_predecessors=True)
    path, k = [1], 1
    while k != 0:
        k = pred[k]
        path.append(k)
    return nodes[path[::-1]]


def _along(path, s):
    """Point at fraction s (0..1) of a polyline's length."""
    seg = np.hypot(*np.diff(path, axis=0).T)
    cum = np.concatenate([[0], np.cumsum(seg)])
    d = np.clip(s, 0, 1) * cum[-1]
    k = min(np.searchsorted(cum, d, side="right") - 1, len(seg) - 1)
    f = (d - cum[k]) / seg[k] if seg[k] > 0 else 0
    return path[k] + f * (path[k + 1] - path[k])


def animate_day(sim, hubs, day, wd, path_out, step_min=8, fps=10):
    rules = sim.rules
    sites = load_csv("sites.csv").set_index("site_id")
    polys = load_nofly(inflate=1.0)
    xy = lambda s: sites.loc[s, ["x_km", "y_km"]].values.astype(float)
    paths = {}
    for o in sim.orders:
        if o.takeoff is not None and (o.hub, o.phc) not in paths:
            paths[(o.hub, o.phc)] = flight_path(xy(o.hub), xy(o.phc), load_nofly())
    flights = [(o.takeoff, o.takeoff + 2 * sim.veh.leg_min(o.L, sim.wind[sim.hour(o.dispatch + rules.load_takeoff)])
                + 2 * rules.hover + rules.drop, o) for o in sim.orders if o.takeoff is not None]
    snaps = [e for e in sim.trace if e["kind"] == "snap"]
    snap_t = np.array([e["t"] for e in snaps])
    names = [h.name for h in hubs]
    em = sorted([o for o in sim.orders if o.c == 0], key=lambda o: o.t)

    fig = plt.figure(figsize=(11, 5.4))
    ax = fig.add_axes([0.04, 0.13, 0.52, 0.75])
    axq = fig.add_axes([0.63, 0.55, 0.34, 0.33])
    axb = fig.add_axes([0.63, 0.10, 0.34, 0.33])
    for p in polys:
        ax.fill(p[:, 0], p[:, 1], color="0.85", lw=0)
    ph = sites[sites.type == "phc"]
    ax.plot(ph.x_km, ph.y_km, "o", color="0.55", ms=3.5)
    for h in names:
        ax.plot(*xy(h), "s", color="k", ms=8)
        ax.annotate(h, xy(h), xytext=(4, 4), textcoords="offset points", fontsize=8)
    ax.set_xlim(0, 62); ax.set_ylim(0, 46); ax.set_aspect("equal")
    ax.set_xlabel("x (km)"); ax.set_ylabel("y (km)")
    dots = ax.scatter([], [], s=28, edgecolor="k", linewidths=0.4, zorder=5)
    for c in range(3):
        ax.plot([], [], "o", color=CLASS_COL[c], mec="k", label=f"drone, {CLASS_NAME[c]}")
    fig.legend(*ax.get_legend_handles_labels(), loc="lower left", ncol=3, fontsize=7,
               bbox_to_anchor=(0.04, 0.0), frameon=False)
    title = fig.suptitle("", fontsize=11, x=0.04, ha="left")
    xs = np.arange(len(names))
    ymax = max(max(h.drones for h in hubs), max(h.bays for h in hubs)) + 2.5

    def frame(i):
        t = i * step_min
        pts, cols = [], []
        for t0, t1, o in flights:
            if t0 <= t < t1:
                leg = (t1 - t0 - 2 * rules.hover - rules.drop) / 2
                u = t - t0
                s = u / leg if u < leg else (1 if u < leg + rules.hover + rules.drop
                                             else 1 - (u - leg - rules.hover - rules.drop) / (leg + rules.hover))
                pts.append(_along(paths[(o.hub, o.phc)], s))
                cols.append(CLASS_COL[o.c])
        dots.set_offsets(np.array(pts) if pts else np.empty((0, 2)))
        dots.set_facecolor(cols)
        k = max(np.searchsorted(snap_t, t, side="right") - 1, 0)
        st = snaps[k]["hubs"]
        axq.cla(); axb.cla()
        eq = [st[h]["em"] for h in names]; oq = [st[h]["ot"] for h in names]
        axq.bar(xs, eq, color=CLASS_COL[0], label="emergency waiting")
        axq.bar(xs, oq, bottom=eq, color="#7f7f7f", label="routine + lab waiting")
        axq.set_xticks(xs, names); axq.set_ylim(0, max(10, max(np.add(eq, oq)) + 2))
        axq.set_title("queues at each hub", fontsize=9); axq.legend(fontsize=7, loc="upper left")
        w = 0.2
        axb.bar(xs - 1.5 * w, [st[h]["idle"] for h in names], w, color="#2ca02c", label="idle drones")
        axb.bar(xs - 0.5 * w, [st[h]["wait_pack"] for h in names], w, color="#8c564b",
                label="drones waiting for a charged pack")
        axb.bar(xs + 0.5 * w, [st[h]["charging"] for h in names], w, color="#ff7f0e", label="bays charging")
        axb.bar(xs + 1.5 * w, [st[h]["packs"] for h in names], w, color="#9467bd", label="charged spare packs")
        axb.set_xticks(xs, names); axb.set_ylim(0, ymax)
        axb.set_title("drones, bays and packs", fontsize=9); axb.legend(fontsize=7, loc="upper left", ncol=1)
        hr = min(int(t // 60), 23)
        grounded = sim.gust[hr] >= rules.gust_limit
        outage = sim.in_outage(t)
        done = [o for o in em if o.delivered is not None and o.delivered <= t]
        ok = sum(o.delivered - o.t <= rules.em_target for o in done)
        road = sum(o.mode == "road" and o.delivered - rules.road_min[0] <= t for o in em)
        title.set_text(
            f"Day {day} ({wd})  {int(t // 60):02d}:{int(t % 60):02d}   wind {sim.wind[hr]:.1f} m/s, "
            f"gust {sim.gust[hr]:.1f} m/s{'  GROUNDED' if grounded else ''}"
            f"{'  GRID OUTAGE (genset, 2 bays)' if outage else ''}\n"
            f"emergencies delivered so far: {len(done)}, within 45 min: {ok}"
            f"{f' ({ok / len(done):.0%})' if done else ''}, sent by road: {road}")
        title.set_color("crimson" if grounded else ("darkorange" if outage else "k"))
        return dots,

    anim = FuncAnimation(fig, frame, frames=int(1440 // step_min) + 1, interval=1000 / fps)
    anim.save(path_out, writer=PillowWriter(fps=fps), dpi=72)
    frame(int(13.5 * 60 / step_min))                      # a still near 13:30 for the report
    still = str(path_out).replace(".gif", "_still.png")
    fig.savefig(still, dpi=120)
    plt.close(fig)
    return still
