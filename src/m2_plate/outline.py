"""From a grey SIMP density field to a waterjet-cuttable outline with a proven 6 mm minimum member.

Steps (pixels of `px` mm, default 0.25 mm):
  1. Interpolate the element densities (bilinear between element centres) to the pixel grid.
  2. Threshold at the level that keeps the SIMP volume fraction (volume-preserving threshold).
  3. Minimum member width. A shape has "every member >= 6 mm" when every solid point lies in a
     6 mm disk that is entirely solid, i.e. when a morphological opening with a 6 mm disk leaves
     it unchanged. Opening the thresholded shape splits it into members that already pass and
     thinner ones. The thin ones are widened (dilated by 3 mm), not deleted: deleting them cut the
     load path at low vf. A dilated point lies in a 6 mm disk around its thin-member point, so the
     union passes the test by construction.
  4. Morphological closing with the same disk fills slots and holes narrower than 6 mm (a waterjet
     can cut them, but they add nothing and leave fragile tabs). The arm-clamp corner patches are
     added back as solid, and a last opening rounds every convex corner to R 3 mm. Since opening is
     idempotent, the final shape passes the 6 mm test by construction; it is checked again anyway
     (opening it must remove nothing), and the narrowest member is measured on a 0.25 mm ladder.
  Opening/closing use the exact Euclidean distance transform (scipy.ndimage).
  5. Contours of the final shape -> polylines (mm) -> DXF (R12, closed POLYLINEs) and SVG.
"""
import numpy as np
from scipy import ndimage
from scipy.interpolate import RegularGridInterpolator


def to_pixels(rho_full, L, px):
    """rho_full: (n, n) element densities on the whole plate, row = y. Returns (m, m) pixel field."""
    n = rho_full.shape[0]
    h = L / n
    c = (np.arange(n) + 0.5) * h
    f = RegularGridInterpolator((c, c), rho_full, bounds_error=False, fill_value=None)
    m = int(round(L / px))
    pc = (np.arange(m) + 0.5) * px
    Y, X = np.meshgrid(pc, pc, indexing="ij")
    return np.clip(f(np.stack([Y, X], -1)), 0, 1)


def volume_threshold(field, vf):
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if (field > mid).mean() > vf:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _erode(b, r_px):
    """Erosion by a disk of radius r_px via the exact Euclidean distance transform
    (outside the plate counts as void)."""
    bp = np.pad(b, 1, constant_values=False)
    return (ndimage.distance_transform_edt(bp) > r_px)[1:-1, 1:-1]


def _dilate(b, r_px):
    return ndimage.distance_transform_edt(~b) <= r_px


def _open(b, r_px):
    return _dilate(_erode(b, r_px), r_px)


def _close(b, r_px):
    return ~_open(~b, r_px)   # closing = complement of the opening of the void


def corner_mask(m, px, L, corner):
    c = (np.arange(m) + 0.5) * px
    near = (c <= corner) | (c >= L - corner)
    return near[:, None] & near[None, :]


def make_outline(rho_full, vf, L=160.0, px=0.25, w_min=6.0, corner=10.0):
    """Returns dict(binary, threshold, area_fraction, removed_by_check_mm2, min_width_mm, contours)."""
    field = to_pixels(rho_full, L, px)
    thr = volume_threshold(field, vf)
    b = field > thr
    r = w_min / 2 / px
    thick = _open(b, r)                     # members already >= 6 mm wide
    thin = b & ~thick                       # members narrower than 6 mm (and grey fringes)
    # keep thin members but widen them: every point of dilate(thin, 3 mm) lies in a 6 mm disk
    # centred on a point of `thin`, so the union stays "6 mm-open". Removing them instead
    # can cut the load path (it did at low vf), so the thin members are thickened, not deleted.
    b = thick | _dilate(thin, r)
    b = _open(b, r)
    b = _close(b, r)                        # fill slots and holes narrower than 6 mm
    b |= corner_mask(b.shape[0], px, L, corner)
    b = _open(b, r)                         # rounds every convex corner to R 3 mm (cuttable, no cusps)
    removed = (b & ~_open(b, r)).sum() * px * px
    return dict(binary=b, field=field, threshold=thr, area_fraction=float(b.mean()),
                removed_by_check_mm2=float(removed), min_width_mm=min_member_width(b, px), contours=contours(b, px), px=px, L=L, w_min=w_min)


def min_member_width(b, px, widths=None):
    """Largest disk diameter d (0.25 mm ladder) such that opening with d removes < 0.1 % of the
    solid area: the narrowest member the shape contains, measured, not assumed."""
    widths = np.arange(6.0, 9.01, 0.25) if widths is None else widths
    area = b.sum()
    best = np.nan   # nan: fails already at 6 mm
    for d in widths:
        if (b & ~_open(b, d / 2 / px)).sum() / area < 1e-3:
            best = d
        else:
            break
    return float(best)


def contours(b, px):
    """Closed boundary polylines (mm) of the solid, outer edge and holes, via contourpy."""
    import contourpy
    z = np.pad(ndimage.gaussian_filter(b.astype(float), 0.7), 1)
    m = b.shape[0]
    xs = (np.arange(m + 2) - 0.5) * px
    gen = contourpy.contour_generator(xs, xs, z, line_type=contourpy.LineType.Separate)
    return [simplify(line, 0.02) for line in gen.lines(0.5) if len(line) > 4]


def simplify(pts, tol):
    """Ramer-Douglas-Peucker on a polyline (keeps the first and last point)."""
    if len(pts) < 3:
        return pts
    a, b = pts[0], pts[-1]
    ab = b - a
    nrm = np.hypot(*ab)
    d = (np.abs(ab[0] * (pts[:, 1] - a[1]) - ab[1] * (pts[:, 0] - a[0])) / nrm) if nrm > 1e-12 else np.hypot(*(pts - a).T)
    i = int(np.argmax(d))
    if d[i] > tol:
        return np.vstack([simplify(pts[:i + 1], tol)[:-1], simplify(pts[i:], tol)])
    return np.vstack([a, b])


def write_dxf(lines, path):
    """Minimal ASCII DXF (R12): one closed POLYLINE per contour, units mm, layer CUT."""
    out = ["0", "SECTION", "2", "HEADER", "9", "$INSUNITS", "70", "4", "0", "ENDSEC",
           "0", "SECTION", "2", "ENTITIES"]
    for ln in lines:
        out += ["0", "POLYLINE", "8", "CUT", "66", "1", "70", "1"]
        for x, y in ln[:-1] if np.allclose(ln[0], ln[-1]) else ln:
            out += ["0", "VERTEX", "8", "CUT", "10", f"{x:.3f}", "20", f"{y:.3f}"]
        out += ["0", "SEQEND"]
    out += ["0", "ENDSEC", "0", "EOF"]
    path.write_text("\n".join(out) + "\n")


def write_svg(lines, L, path):
    d = " ".join("M " + " L ".join(f"{x:.3f},{L - y:.3f}" for x, y in ln) + " Z" for ln in lines)
    path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="{L}mm" height="{L}mm" '
                    f'viewBox="0 0 {L} {L}"><path d="{d}" fill="#bbb" fill-rule="evenodd" '
                    f'stroke="black" stroke-width="0.2"/></svg>\n')
